"""Job store and queue worker for the query server.

Each job is one orchestration run, executed as a subprocess of the existing
entry point (`python -m aiprover_orchestration.orchestrator.run`). A submitted job awaits approval;
approved jobs start in submission order. Jobs belong to GPU groups
(vista.GPU_GROUPS), each with its own model server; a group runs up to
MAX_CONCURRENT_RUNS of its jobs at once (environment QUERY_SERVER_MAX_RUNS),
which share its server. Runs of the same problem may overlap (each writes and
builds its Lean modules under the workspace's build lock).

The worker keeps each group's model server in step with demand: it submits a
Vista server job for the group when an approved run of it is waiting and none
is queued or running, and cancels the server jobs it submitted for the group
after IDLE_SECONDS without work there. A group may borrow the servers of
other groups (`GpuGroup.borrows`): its runs use every server of the pool
that serves their checkpoint, and the borrowed servers stay up while it has
work. A run that loses all its model servers stops as an infrastructure
failure and is put back at the head of the queue, to resume on the next
server job of its group.

Runs are started in their own session, so a server restart does not end
them; on start, the worker adopts a run whose process is still alive and
resumes (`--resume`) one whose process is gone.
"""

import json
import logging
import os
import re
import signal
import sqlite3
import subprocess
import sys
import threading
import time
import tomllib
import urllib.request
from pathlib import Path

from ..paths import (
    AIPROVER_CLI,
    CONFIG_DIR,
    DATA_DIR,
    RESULTS_DIR,
    ROOT,
    STATE_DIR,
    TEMP_DIR,
)
from . import models, vista
from .vista import run_command

DATABASE = STATE_DIR / "jobs.db"
PROBLEM_DIR = STATE_DIR / "problems"
# Every JSONL file in data/ is offered as a source of problems.
DATASET = DATA_DIR / "val_JiatuBook_unlabelled.jsonl"
CONFIG = CONFIG_DIR / "orchestrator" / "aiprover_vista_served.json"

POLL_SECONDS = 5
BACKEND_RETRY_SECONDS = 60
# Idle time after which a group's server jobs are cancelled; 0 keeps them
# until their wall time.
IDLE_SECONDS = int(os.environ.get("QUERY_SERVER_IDLE_SECONDS", str(30 * 60)))
MAX_CONCURRENT_RUNS = int(os.environ.get("QUERY_SERVER_MAX_RUNS", "2"))
MAINTAIN_SECONDS = 300  # interval of the server checks for running runs
SUCCESSOR_RETRY_SECONDS = 600  # between successor submissions of a group
# Minimum interval between renders of a running run's trace pages.
PAGE_REFRESH_SECONDS = 60
# A run that loses its model server is requeued and resumed without limit;
# it ends only after this many resumptions in a row with no progress.
MAX_STALLED_RESUMPTIONS = 3
PROGRESS_EVENTS = (
    "formalization_compiled",
    "audit_verdict",
    "sketch_accepted",
    "lemma_proved",
    "replan",
)
OPEN_STATES = ("awaiting_approval", "queued", "running", "cancelling")
# Per-run options the worker uses itself rather than passing to run.py:
# `after` names a run that must finish before this one starts (a run that
# builds on a library entry the other adds). `aiprover_timeout` replaces the
# wall clock of the run's AIProver sessions, in seconds; 0 removes it, so a
# session ends only at its turn limit.
SERVER_OPTIONS = ("after", "aiprover_timeout")
# A clock no session reaches: the harness scales its own time fences with it.
NO_TIME_LIMIT = 30 * 24 * 3600

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    run_id      TEXT PRIMARY KEY,
    owner       TEXT NOT NULL,
    uuid        TEXT NOT NULL,
    title       TEXT NOT NULL,
    dataset     TEXT NOT NULL,
    -- awaiting_approval, queued, running, cancelling, finished, cancelled, rejected
    state       TEXT NOT NULL,
    status      TEXT,           -- summary.json status of a finished run
    pid         INTEGER,
    submitted   REAL NOT NULL,
    started     REAL,
    ended       REAL,
    approved_by TEXT,
    resumptions INTEGER NOT NULL DEFAULT 0,
    progress    INTEGER NOT NULL DEFAULT 0,  -- progress events at the last requeue
    stalled     INTEGER NOT NULL DEFAULT 0,  -- requeues in a row without progress
    options     TEXT,                        -- JSON {config field: value} for this run
    agents      TEXT,                        -- JSON models of the run, see models.py
    gpu_group   TEXT,                        -- GPU group (vista.GPU_GROUPS); NULL = main
    hidden      INTEGER NOT NULL DEFAULT 0   -- 1: left out of the page's run list
);
-- Vista server jobs submitted by the worker (only these are cancelled when idle).
CREATE TABLE IF NOT EXISTS vista_jobs (
    job_id      TEXT PRIMARY KEY,
    submitted   REAL NOT NULL,
    checkpoint  TEXT,                        -- checkpoint the job serves
    gpu_group   TEXT                         -- GPU group of the job; NULL = main
);
"""
# Columns added after the first deployment, for databases created before them.
MIGRATIONS = [
    "ALTER TABLE jobs ADD COLUMN approved_by TEXT",
    "ALTER TABLE jobs ADD COLUMN resumptions INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE jobs ADD COLUMN progress INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE jobs ADD COLUMN stalled INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE jobs ADD COLUMN options TEXT",
    "ALTER TABLE jobs ADD COLUMN agents TEXT",
    "ALTER TABLE vista_jobs ADD COLUMN checkpoint TEXT",
    "ALTER TABLE jobs ADD COLUMN gpu_group TEXT",
    "ALTER TABLE jobs ADD COLUMN hidden INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE vista_jobs ADD COLUMN gpu_group TEXT",
]


def proxy_up(group: vista.GpuGroup) -> bool:
    """True if the group's reasoning proxy (server/reasoning_proxy.py) forwards
    to the model."""
    try:
        with urllib.request.urlopen(
            group.endpoint + "/models", timeout=20
        ) as reply:
            return reply.status == 200
    except OSError:
        return False


def endpoint_status(
    bring_up: bool,
    group: vista.GpuGroup = vista.GPU_GROUPS[vista.DEFAULT_GROUP],
) -> tuple[bool, str]:
    """Probe the group's AIProver endpoint; with `bring_up`, open its tunnel
    first. Served runs reach the model through the reasoning proxy, so it is
    required as well."""
    action = "up" if bring_up else "status"
    code, output = run_command(
        [str(AIPROVER_CLI), "tunnel", action],
        timeout=180 if bring_up else 20,
        aiprover_config=group.tunnel_config,
    )
    message = output.splitlines()[-1] if output else ""
    if code == 0 and not proxy_up(group):
        return (
            False,
            f"reasoning proxy not answering on 127.0.0.1:{group.proxy_port}",
        )
    return code == 0, message


def tunnel_job(group: vista.GpuGroup) -> str | None:
    """Server job the group's open tunnel leads to (the harness records it
    in <work_root>/tunnel.json); None if no tunnel is recorded."""
    with open(group.tunnel_config, "rb") as config_file:
        work_root = tomllib.load(config_file)["runtime"]["work_root"]
    state = Path(work_root).expanduser() / "tunnel.json"
    try:
        source = json.loads(state.read_text()).get("source", "")
    except (OSError, ValueError):
        return None
    match = re.search(r"\(job (\d+)\)", source)
    return match.group(1) if match else None


def tunnel_down(group: vista.GpuGroup) -> None:
    run_command(
        [str(AIPROVER_CLI), "tunnel", "down"],
        timeout=60,
        aiprover_config=group.tunnel_config,
    )


def job_group(job: dict) -> vista.GpuGroup:
    """GPU group of a job; jobs without one belong to the default group."""
    return vista.GPU_GROUPS.get(
        job.get("gpu_group") or vista.DEFAULT_GROUP,
        vista.GPU_GROUPS[vista.DEFAULT_GROUP],
    )


def group_config(config: dict, group: vista.GpuGroup) -> dict:
    """A run config whose AIProver agents use the group's model server;
    agents of a persistent version keep their own server."""
    persistent = set(models.HARNESS_CONFIGS.values()) | set(
        models.VERSION_ENDPOINTS.values()
    )
    for spec in config["agents"].values():
        if (
            spec.get("config") in persistent
            or spec.get("base_url") in persistent
        ):
            continue
        if spec.get("backend") == "aiprover":
            spec["config"] = group.solver_config
        elif (
            spec.get("backend") == "openai_compatible"
            and spec.get("model") == models.AIPROVER_SERVED_MODEL
        ):
            spec["base_url"] = group.endpoint
    return config


def job_selection(job: dict) -> dict:
    """Models chosen for a job; empty for a job submitted before the choice
    existed, which runs with the served config."""
    return json.loads(job.get("agents") or "null") or {}


def job_checkpoint(job: dict) -> str | None:
    """Checkpoint the model server must serve for `job`; None if it needs none.
    The served config's solver uses the trained model."""
    selection = job_selection(job)
    return (
        models.checkpoint_of(selection)
        if selection
        else vista.CHECKPOINTS["trained"]
    )


def process_alive(pid: int | None, run_id: str) -> bool:
    """True if `pid` is a live orchestrator process of run `run_id`."""
    if not pid:
        return False
    try:
        cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().split(b"\0")
        state = (
            Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()[0]
        )
    except OSError:
        return False
    return state != "Z" and run_id.encode() in cmdline


def run_process_alive(run_id: str) -> bool:
    """True if any live orchestrator process belongs to run `run_id`."""
    return any(
        process_alive(int(entry.name), run_id)
        for entry in Path("/proc").iterdir()
        if entry.name.isdigit()
    )


def benchmark_command(result_dir: Path) -> list[str]:
    """Command that writes a run's benchmark.json from its trace and
    metrics samples."""
    return [
        sys.executable,
        "-m",
        "aiprover_orchestration.orchestrator.benchmark",
        str(result_dir),
    ]


def trace_pages_command(trace_path: Path) -> list[str]:
    """Command that renders a run's trace pages next to its trace."""
    return [
        sys.executable,
        "-m",
        "aiprover_orchestration.orchestrator.trace_view",
        str(trace_path),
    ]


def read_summary(run_id: str) -> dict:
    path = RESULTS_DIR / run_id / "summary.json"
    return json.loads(path.read_text()) if path.exists() else {}


def progress_count(run_id: str) -> int:
    """Durable steps of a run (decisions a resume restores) in its trace."""
    path = RESULTS_DIR / run_id / "trace.json"
    try:
        steps = json.loads(path.read_text()).get("steps", [])
    except (OSError, json.JSONDecodeError):
        return 0
    return sum(step.get("event") in PROGRESS_EVENTS for step in steps)


class JobStore:
    """SQLite-backed job records; safe to share between threads."""

    def __init__(self, path: Path = DATABASE):
        path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock:
            self._connection.executescript(SCHEMA)
            for statement in MIGRATIONS:
                try:
                    self._connection.execute(statement)
                except sqlite3.OperationalError:
                    pass  # column exists
            self._connection.commit()

    def _execute(self, query: str, parameters: tuple = ()) -> list[dict]:
        with self._lock:
            rows = self._connection.execute(query, parameters).fetchall()
            self._connection.commit()
        return [dict(row) for row in rows]

    def add(self, job: dict) -> None:
        columns = ", ".join(job)
        placeholders = ", ".join("?" for _ in job)
        self._execute(
            f"INSERT INTO jobs ({columns}) VALUES ({placeholders})",
            tuple(job.values()),
        )

    def update(self, run_id: str, **values) -> None:
        assignments = ", ".join(f"{column} = ?" for column in values)
        self._execute(
            f"UPDATE jobs SET {assignments} WHERE run_id = ?",
            (*values.values(), run_id),
        )

    def get(self, run_id: str) -> dict | None:
        rows = self._execute("SELECT * FROM jobs WHERE run_id = ?", (run_id,))
        return rows[0] if rows else None

    def jobs(self, owner: str | None = None) -> list[dict]:
        if owner is None:
            return self._execute("SELECT * FROM jobs ORDER BY submitted DESC")
        return self._execute(
            "SELECT * FROM jobs WHERE owner = ? ORDER BY submitted DESC",
            (owner,),
        )

    def in_states(self, *states: str) -> list[dict]:
        marks = ", ".join("?" for _ in states)
        return self._execute(
            f"SELECT * FROM jobs WHERE state IN ({marks}) "
            "ORDER BY submitted",
            states,
        )

    def queue_position(self, run_id: str) -> int | None:
        """1-based position among queued jobs, or None if not queued."""
        queued = [job["run_id"] for job in self.in_states("queued")]
        return queued.index(run_id) + 1 if run_id in queued else None

    def vista_job_checkpoints(self, group: str) -> dict[str, str | None]:
        rows = self._execute(
            "SELECT job_id, checkpoint FROM vista_jobs "
            "WHERE COALESCE(gpu_group, ?) = ?",
            (vista.DEFAULT_GROUP, group),
        )
        return {row["job_id"]: row["checkpoint"] for row in rows}

    def add_vista_job(self, job_id: str, checkpoint: str, group: str) -> None:
        self._execute(
            "INSERT OR IGNORE INTO vista_jobs (job_id, submitted, checkpoint, gpu_group) "
            "VALUES (?, ?, ?, ?)",
            (job_id, time.time(), checkpoint, group),
        )

    def remove_vista_job(self, job_id: str) -> None:
        self._execute("DELETE FROM vista_jobs WHERE job_id = ?", (job_id,))


class Worker(threading.Thread):
    """Runs approved jobs and manages the Vista model servers. Each GPU group
    (vista.GPU_GROUPS) has its own model server and runs up to
    MAX_CONCURRENT_RUNS of its jobs at once."""

    def __init__(self, store: JobStore):
        super().__init__(name="job-worker", daemon=True)
        self.store = store
        # Run id -> its orchestrator process; None for a run adopted after a
        # server restart, which is followed by its pid.
        self.running: dict[str, subprocess.Popen | None] = {}
        # Per group: last server status, start of its idle period, and the
        # checkpoint its endpoint was last confirmed to serve.
        self.vista_messages: dict[str, str] = {}
        self.idle_since: dict[str, float] = {}
        self.serving: dict[str, str] = {}
        self.maintained = 0.0  # last _maintain_servers pass
        self.successor_tried: dict[str, float] = {}  # per group
        # Run id -> the process rendering its trace pages.
        self.page_renders: dict[str, subprocess.Popen] = {}

    @property
    def vista_message(self) -> str:
        return (
            "; ".join(
                f"{group}: {message}"
                for group, message in self.vista_messages.items()
            )
            or "not checked"
        )

    def run(self) -> None:
        for job in self.store.in_states("running", "cancelling"):
            self._recover(job)
        while True:
            try:
                self._step()
            except Exception:
                logger.exception("worker step failed")
                time.sleep(BACKEND_RETRY_SECONDS)

    def _step(self) -> None:
        self._reap()
        self._refresh_pages()
        queued = self.store.in_states("queued")
        running = [self.store.get(run_id) for run_id in self.running]
        busy_groups = {
            member.name
            for job in queued + running
            for member in vista.server_pool(job_group(job))
        }
        for name, group in vista.GPU_GROUPS.items():
            if name in busy_groups:
                self.idle_since.pop(name, None)
            else:
                self._release_idle_servers(group)
        started = False
        for name in sorted(busy_groups):
            candidate = self._next_to_start(name, queued, running)
            if candidate is None:
                continue
            checkpoint = job_checkpoint(candidate)
            if checkpoint is not None and not self._pool_serving(
                checkpoint, job_group(candidate)
            ):
                # A run that needs no model server does not wait for
                # another run's server.
                candidate = self._serverless_job(name, queued)
            if candidate is not None:
                self._execute(candidate)
                started = True
        self._maintain_servers(running)
        if not started:
            time.sleep(POLL_SECONDS)

    def _next_to_start(
        self, group: str, queued: list[dict], running: list[dict]
    ) -> dict | None:
        """The group's first queued job that may start now: the group has a
        free slot, the run it waits for (`after`) has finished, and the
        group's model server serves the checkpoint the job needs or may
        change. The group's running jobs share one checkpoint; a job waiting
        for another one holds back later jobs that use a model server, so
        that it is not starved."""
        running = [job for job in running if job_group(job).name == group]
        if len(running) >= MAX_CONCURRENT_RUNS:
            return None
        in_use = {job_checkpoint(job) for job in running} - {None}
        held_back = False
        for job in queued:
            if job_group(job).name != group or self._waiting_for_dependency(
                job
            ):
                continue
            # A run still shutting down would share its trace with a resume.
            if run_process_alive(job["run_id"]):
                continue
            checkpoint = job_checkpoint(job)
            if checkpoint is None:
                return job
            if held_back:
                continue
            if not in_use or checkpoint in in_use:
                return job
            held_back = True
        return None

    def _serverless_job(self, group: str, queued: list[dict]) -> dict | None:
        """The group's first queued job that needs no model server."""
        return next(
            (
                job
                for job in queued
                if job_group(job).name == group
                and job_checkpoint(job) is None
                and not self._waiting_for_dependency(job)
            ),
            None,
        )

    def _waiting_for_dependency(self, job: dict) -> bool:
        """True until the run named by `after` has finished; a cancelled or
        rejected dependency holds the job until it is resubmitted or the
        option is removed."""
        after = json.loads(job.get("options") or "{}").get("after")
        dependency = self.store.get(after) if after else None
        return dependency is not None and dependency["state"] != "finished"

    def _reap(self) -> None:
        """Finish the runs whose process has exited."""
        for run_id, process in list(self.running.items()):
            if process is not None:
                done = process.poll() is not None
            else:
                done = not process_alive(self.store.get(run_id)["pid"], run_id)
            if done:
                del self.running[run_id]
                self._finish(self.store.get(run_id))

    def _refresh_pages(self) -> None:
        """Render the trace pages of running runs whose trace has steps the
        pages lack, so a run can be viewed in progress: one render per run at
        a time, at most every PAGE_REFRESH_SECONDS."""
        for run_id in list(self.running):
            render = self.page_renders.get(run_id)
            if render is not None and render.poll() is None:
                continue
            trace = RESULTS_DIR / run_id / "trace.json"
            page = RESULTS_DIR / run_id / "trace.html"
            if not trace.exists():
                continue
            rendered = page.stat().st_mtime if page.exists() else 0.0
            if (
                trace.stat().st_mtime > rendered
                and time.time() - rendered >= PAGE_REFRESH_SECONDS
            ):
                self.page_renders[run_id] = subprocess.Popen(
                    trace_pages_command(trace),
                    cwd=ROOT,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

    # Vista servers --------------------------------------------------------

    def _serving(self, checkpoint: str, group: vista.GpuGroup) -> bool:
        """True if the group's model endpoint is up and serves `checkpoint`;
        otherwise bring the group's server in line with it: replace a server
        job of another checkpoint (none of the group's running jobs uses it),
        or submit one."""
        if self.serving.get(group.name) == checkpoint:
            endpoint_up, message = endpoint_status(bring_up=True, group=group)
            if endpoint_up:
                self.vista_messages[group.name] = message
                return True
            self.serving.pop(group.name, None)
        jobs = vista.server_jobs(group)
        if jobs is None:
            self.vista_messages[group.name] = (
                "Vista unreachable: reopen the ControlMaster "
                "(~/.ssh/vista.sock) with MFA"
            )
            logger.warning(self.vista_messages[group.name])
            return False
        checkpoints = self.store.vista_job_checkpoints(group.name)
        for job_id in checkpoints:
            if job_id not in jobs:
                self.store.remove_vista_job(job_id)
        # A job the server did not submit has no recorded checkpoint: the
        # default.
        serves = {
            job_id: checkpoints.get(job_id) or vista.CHECKPOINTS["trained"]
            for job_id in jobs
        }
        for job_id in [
            job_id for job_id, served in serves.items() if served != checkpoint
        ]:
            if job_id not in checkpoints:
                self.vista_messages[group.name] = (
                    f"waiting for server job {job_id}, "
                    "which serves another checkpoint"
                )
                return False
            logger.info(
                f"cancelling server job {job_id} ({group.name}, serves {serves[job_id]}) "
                f"for a run that needs {checkpoint}"
            )
            if vista.cancel_server(job_id):
                self.store.remove_vista_job(job_id)
            del serves[job_id]
        if not serves:
            job_id = vista.submit_server(checkpoint, group)
            if job_id:
                self.store.add_vista_job(job_id, checkpoint, group.name)
                self.vista_messages[group.name] = (
                    f"submitted server job {job_id}"
                )
            else:
                self.vista_messages[group.name] = (
                    "server job submission failed (see server log)"
                )
            return False
        if not self._tunnel_current(group, jobs):
            return False
        endpoint_up, message = endpoint_status(bring_up=True, group=group)
        if endpoint_up:
            self._keep_successor(checkpoint, group, jobs, checkpoints)
            self.serving[group.name] = checkpoint
            self.vista_messages[group.name] = message
            return True
        summary = ", ".join(
            f"{job_id} {state}" for job_id, state in jobs.items()
        )
        self.vista_messages[group.name] = f"waiting for server job {summary}"
        return False

    def _tunnel_current(self, group: vista.GpuGroup, jobs: dict) -> bool:
        """Point the group's tunnel at the server job its handoff file names,
        once that job's server answers; False if the handoff names no
        running job. The handoff of an ended job still names its node, where
        another job's server may answer, and an open tunnel is not moved by
        `tunnel up`."""
        running = [
            job_id for job_id, state in jobs.items() if state == "RUNNING"
        ]
        named = vista.handoff(group)
        current = tunnel_job(group)
        if named is None or named["job"] not in running:
            if current is not None:
                logger.info(
                    f"{group.name}: closing tunnel to ended job {current}"
                )
                tunnel_down(group)
            self.vista_messages[group.name] = (
                "handoff names no running server job; waiting for "
                + (", ".join(f"{i} {s}" for i, s in jobs.items()) or "none")
            )
            return False
        if current == named["job"]:
            return True
        if current in running and not vista.server_answers(
            named["node"], named["port"]
        ):
            return True  # the new job's server is loading; keep the old one
        logger.info(
            f"{group.name}: moving tunnel from job {current} to {named['job']}"
        )
        tunnel_down(group)
        for job_id in running:
            if job_id != named[
                "job"
            ] and job_id in self.store.vista_job_checkpoints(group.name):
                logger.info(f"{group.name}: cancelling superseded job {job_id}")
                if vista.cancel_server(job_id):
                    self.store.remove_vista_job(job_id)
        return True

    def _keep_successor(
        self,
        checkpoint: str,
        group: vista.GpuGroup,
        jobs: dict,
        checkpoints: dict,
    ) -> None:
        """Keep one server job queued behind the running one, so that the
        group's server is replaced without a wait in the Slurm queue; at
        most one submission per group every SUCCESSOR_RETRY_SECONDS."""
        if any(state == "PENDING" for state in jobs.values()):
            return
        if time.time() - self.successor_tried.get(group.name, 0.0) < (
            SUCCESSOR_RETRY_SECONDS
        ):
            return
        self.successor_tried[group.name] = time.time()
        job_id = vista.submit_server(checkpoint, group)
        if job_id:
            logger.info(f"{group.name}: queued successor server job {job_id}")
            self.store.add_vista_job(job_id, checkpoint, group.name)

    def _pool_serving(self, checkpoint: str, group: vista.GpuGroup) -> bool:
        """True if a model server of the group's pool serves `checkpoint`;
        every server of the pool is brought in line with it."""
        return any(
            [
                self._serving(checkpoint, member)
                for member in vista.server_pool(group)
            ]
        )

    def _maintain_servers(self, running: list[dict]) -> None:
        """Every MAINTAIN_SECONDS, bring the server pools of running runs in
        line, so that a server lost under a run that has others to use is
        replaced."""
        if time.time() - self.maintained < MAINTAIN_SECONDS:
            return
        self.maintained = time.time()
        pools = {(job_checkpoint(job), job_group(job).name) for job in running}
        for checkpoint, group in pools:
            if checkpoint is None:
                continue
            # Recheck the server jobs and handoffs, not the cached state.
            for member in vista.server_pool(vista.GPU_GROUPS[group]):
                self.serving.pop(member.name, None)
            self._pool_serving(checkpoint, vista.GPU_GROUPS[group])

    def _release_idle_servers(self, group: vista.GpuGroup) -> None:
        """Cancel the server jobs this worker submitted for `group` after
        IDLE_SECONDS without queued or running jobs in the group."""
        managed = list(self.store.vista_job_checkpoints(group.name))
        if not managed or not IDLE_SECONDS:
            self.idle_since.pop(group.name, None)
            return
        since = self.idle_since.setdefault(group.name, time.time())
        if time.time() - since < IDLE_SECONDS:
            return
        for job_id in managed:
            logger.info(
                f"{group.name}: no queued runs for {IDLE_SECONDS // 60} min; "
                f"cancelling {job_id}"
            )
            if vista.cancel_server(job_id):
                self.store.remove_vista_job(job_id)
        self.serving.pop(group.name, None)
        self.vista_messages[group.name] = (
            "server jobs cancelled after idle period"
        )
        self.idle_since.pop(group.name, None)

    # Runs -----------------------------------------------------------------

    def _recover(self, job: dict) -> None:
        """Finish a job left running by a previous server process."""
        if process_alive(job["pid"], job["run_id"]):
            logger.info(
                f"adopting running job {job['run_id']} (pid {job['pid']})"
            )
            self.running[job["run_id"]] = None
        elif job["state"] == "cancelling" or self._completed_while_away(job):
            self._finish(job)
        else:
            logger.info(f"requeueing interrupted job {job['run_id']}")
            self.store.update(job["run_id"], state="queued", pid=None)

    @staticmethod
    def _completed_while_away(job: dict) -> bool:
        """True if the run wrote a final summary after the server stopped
        watching it; an interrupted run's summary does not count."""
        summary_path = RESULTS_DIR / job["run_id"] / "summary.json"
        if not summary_path.exists() or summary_path.stat().st_mtime < (
            job["started"] or 0
        ):
            return False
        return read_summary(job["run_id"]).get("error_kind") != "interrupted"

    def _execute(self, job: dict) -> None:
        run_id = job["run_id"]
        # Restarts after infrastructure failures are made by the worker, which
        # waits for a new model server; run.py's own restarts would not.
        config_path = TEMP_DIR / f"{run_id}.config.json"
        config_path.parent.mkdir(exist_ok=True)
        group = job_group(job)
        config = group_config(
            models.build_config(CONFIG, job_selection(job)), group
        )
        options = json.loads(job.get("options") or "{}")
        if "aiprover_timeout" in options:
            timeout = int(options["aiprover_timeout"]) or NO_TIME_LIMIT
            for spec in config["agents"].values():
                if spec.get("backend") == "aiprover":
                    spec["timeout"] = timeout
        config_path.write_text(json.dumps(config, indent=1))
        arguments = [
            sys.executable,
            "-m",
            "aiprover_orchestration.orchestrator.run",
            "--dataset",
            job["dataset"],
            "--config",
            str(config_path),
            "--run-id",
            run_id,
            "--max-restarts",
            "0",
        ]
        # Per-run settings over the served config, e.g. more attempts per lemma.
        for name, value in json.loads(job.get("options") or "{}").items():
            if name in SERVER_OPTIONS:
                continue
            arguments += ["--" + name.replace("_", "-"), str(value)]
        if (RESULTS_DIR / run_id / "trace.json").exists():
            arguments.append("--resume")
        else:
            arguments += ["--problem-uuid", job["uuid"]]
        output_path = TEMP_DIR / f"{run_id}.out"
        output_path.parent.mkdir(exist_ok=True)
        with open(output_path, "a") as output:
            process = subprocess.Popen(
                arguments,
                cwd=ROOT,
                stdout=output,
                stderr=subprocess.STDOUT,
                env={
                    **os.environ,
                    "PYTHONPATH": str(ROOT),
                    "AIPROVER_CONFIG": str(group.tunnel_config),
                },
                start_new_session=True,
            )
        self.running[run_id] = process
        self.store.update(
            run_id,
            state="running",
            pid=process.pid,
            started=job["started"] or time.time(),
        )
        logger.info(
            f"started {run_id} (pid {process.pid}, {group.name}); "
            f"{len(self.running)} runs in progress"
        )

    def _finish(self, job: dict) -> None:
        run_id = job["run_id"]
        summary = read_summary(run_id)
        if (
            job["state"] != "cancelling"
            and summary.get("error_kind") == "infrastructure"
        ):
            progress = progress_count(run_id)
            stalled = job["stalled"] + 1 if progress <= job["progress"] else 0
            if stalled < MAX_STALLED_RESUMPTIONS:
                logger.info(
                    f"{run_id}: infrastructure failure; requeued to resume "
                    f"(resumption {job['resumptions'] + 1}"
                    f"{f', {stalled} without progress' if stalled else ''})"
                )
                self.store.update(
                    run_id,
                    state="queued",
                    pid=None,
                    progress=progress,
                    stalled=stalled,
                    resumptions=job["resumptions"] + 1,
                )
                return
            logger.info(
                f"{run_id}: {stalled} resumptions in a row without progress; ended"
            )
        trace_path = RESULTS_DIR / run_id / "trace.json"
        if trace_path.exists():
            run_command(trace_pages_command(trace_path), timeout=300)
            run_command(benchmark_command(trace_path.parent), timeout=300)
        state = "cancelled" if job["state"] == "cancelling" else "finished"
        status = summary.get("status") or "no_summary"
        self.store.update(
            run_id, state=state, status=status, pid=None, ended=time.time()
        )
        logger.info(f"{run_id}: {state} ({status})")

    def approve(self, run_id: str, approver: str) -> bool:
        job = self.store.get(run_id)
        if job is None or job["state"] != "awaiting_approval":
            return False
        self.store.update(run_id, state="queued", approved_by=approver)
        logger.info(f"{run_id} approved by {approver}")
        return True

    def reject(self, run_id: str, approver: str) -> bool:
        job = self.store.get(run_id)
        if job is None or job["state"] != "awaiting_approval":
            return False
        self.store.update(
            run_id, state="rejected", approved_by=approver, ended=time.time()
        )
        logger.info(f"{run_id} rejected by {approver}")
        return True

    def cancel(self, run_id: str) -> bool:
        """Withdraw or dequeue a job, or stop a running one; False if it is not
        open."""
        job = self.store.get(run_id)
        if job is None or job["state"] not in (
            "awaiting_approval",
            "queued",
            "running",
        ):
            return False
        if job["state"] != "running":
            self.store.update(run_id, state="cancelled", ended=time.time())
            return True
        self.store.update(run_id, state="cancelling")
        # SIGTERM to the orchestrator only: it unwinds, cancels its AIProver
        # jobs and records the interruption in the trace.
        try:
            os.kill(job["pid"], signal.SIGTERM)
        except (OSError, TypeError):
            pass
        return True

"""Job store and queue worker for the query server.

Each job is one orchestration run, executed as a subprocess of the existing
entry point (`python -m aiprover_orchestration.orchestrator.run`). A submitted job awaits approval;
approved jobs start in submission order. Jobs belong to GPU groups
(model_servers.GPU_GROUPS), each with its own model server; a group runs up to
MAX_CONCURRENT_RUNS of its jobs at once (environment QUERY_SERVER_MAX_RUNS),
which share its server. Runs of the same problem may overlap (each writes and
builds its Lean modules under the workspace's build lock).

The worker keeps each group's model server in step with demand: it submits a
server job on the cluster for the group when an approved run of it is waiting and none
is queued or running, and cancels the server jobs it submitted for the group
after IDLE_SECONDS without work there. A group may borrow the servers of
other groups (`GpuGroup.borrows`): its runs use every server of the pool
that serves their checkpoint while it is up, but a group's servers are
submitted, renewed and kept only for its own runs. A run that loses all its
model servers stops as an infrastructure failure and is put back at the head
of the queue, to resume on the next server job of its group.

Runs are started in their own session, so a server restart does not end
them; on start, the worker adopts a run whose process is still alive and
resumes (`--resume`) one whose process is gone.

With AIPROVER_RUN_EXECUTOR=slurm (server/slurm.py), each run is a Slurm job
instead of a subprocess, followed by its job id (column `slurm_job`); a run
whose job ends at its wall time or with its node is resumed like one that
lost its model server.
"""

import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
import tomllib
import urllib.request
from pathlib import Path

import psutil

from ..paths import (
    AIPROVER_CLI,
    CONFIG_DIR,
    DATA_DIR,
    RESULTS_DIR,
    ROOT,
    SITE,
    STATE_DIR,
    TEMP_DIR,
    locate,
)
from ..lean import releases
from ..orchestrator import libraries
from ..orchestrator.config import current_names
from ..utils.sqlite import SqliteStore
from ..utils.vocabulary import modernize
from . import accounts, model_servers, models, slurm
from .model_servers import run_command

DATABASE = STATE_DIR / "jobs.db"
PROBLEM_DIR = STATE_DIR / "problems"
# Every JSONL file in data/ is offered as a source of problems.
DATASET = DATA_DIR / "val_JiatuBook_unlabelled.jsonl"
# The run configuration of the page, before the selected models: the site's,
# else the generic one, whose prover uses the model server of ./setup.
CONFIG = ROOT / SITE.get("served_config", "configs/orchestrator/default.json")
# Per-phase orchestrator settings applied to a run whose options set
# `orchestrator_phases` (our own runs); other runs use the selected model as is.
ORCHESTRATOR_PHASES = CONFIG_DIR / "orchestrator" / "orchestrator_phases.json"

POLL_SECONDS = 5
BACKEND_RETRY_SECONDS = 60
# Idle time after which a group's server jobs are cancelled; 0 keeps them
# until their wall time.
IDLE_SECONDS = int(os.environ.get("QUERY_SERVER_IDLE_SECONDS", str(30 * 60)))
MAX_CONCURRENT_RUNS = int(os.environ.get("QUERY_SERVER_MAX_RUNS", "2"))
MAINTAIN_SECONDS = 300  # interval of the server checks for running runs
SUCCESSOR_RETRY_SECONDS = 600  # between successor submissions of a group
# Minimum interval between renders of a running run's trace pages, and the
# interval at which they are rendered without new steps, so that the
# sessions of AIProver jobs in flight stay current.
PAGE_REFRESH_SECONDS = 60
LIVE_PAGE_REFRESH_SECONDS = 300
# A run that loses its model server is requeued and resumed without limit;
# it ends only after this many resumptions in a row with no progress.
MAX_STALLED_RESUMPTIONS = 3
PROGRESS_EVENTS = (
    "formalization_compiled",
    "audit_verdict",
    "sketch_accepted",
    "lemma_proved",
    "replan",
    "backjump",
)
OPEN_STATES = ("awaiting_approval", "queued", "running", "cancelling")
# Per-run options the worker uses itself rather than passing to run.py:
# `after` names a run that must finish before this one starts (a run that
# builds on a library entry the other adds). `aiprover_timeout` replaces the
# wall clock of the run's AIProver sessions, in seconds; 0 removes it, so a
# session ends only at its turn limit.
SERVER_OPTIONS = (
    "after",
    "aiprover_timeout",
    "orchestrator_phases",
    "documents_only",
    "lean_release",
    "source_dir",
)
# The state of a run that stopped for a decision on its proof plan.
PLAN_REVIEW = "plan_review"
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
    gpu_group   TEXT,                        -- GPU group (model_servers.GPU_GROUPS); NULL = main
    hidden      INTEGER NOT NULL DEFAULT 0,  -- 1: left out of the page's run list
    page        TEXT,                        -- page submitting (app.PAGES); NULL = dev
    problem_group TEXT,                      -- group named at submission; NULL = by dataset
    imported    TEXT,                        -- imported run: "resume" or "view"; else NULL
    slurm_job   TEXT                         -- Slurm job of the run (slurm executor); else NULL
);
-- Server jobs submitted by the worker (only these are cancelled when idle).
CREATE TABLE IF NOT EXISTS server_jobs (
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
    "ALTER TABLE jobs ADD COLUMN imported TEXT",
    "ALTER TABLE jobs ADD COLUMN problem_group TEXT",
    "ALTER TABLE vista_jobs ADD COLUMN gpu_group TEXT",
    "ALTER TABLE jobs ADD COLUMN page TEXT",
    # The table of server jobs, under its earlier name.
    "INSERT OR IGNORE INTO server_jobs SELECT job_id, submitted, checkpoint, gpu_group FROM vista_jobs",
    "DROP TABLE vista_jobs",
    "ALTER TABLE jobs ADD COLUMN slurm_job TEXT",
]


def proxy_up(group: model_servers.GpuGroup) -> bool:
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
    group: model_servers.GpuGroup = model_servers.GPU_GROUPS[model_servers.DEFAULT_GROUP],
) -> tuple[bool, str]:
    """Probe the group's AIProver endpoint; with `bring_up`, open its tunnel
    first. Served runs reach the model through the reasoning proxy, so it is
    required as well."""
    if model_servers.LOCAL_CLUSTER:
        return local_endpoint_status(group)
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


def local_endpoint_status(group: model_servers.GpuGroup) -> tuple[bool, str]:
    """A server job on this cluster: up if its handoff names a running job
    whose server answers. Runs reach it from their own jobs (server/slurm.py),
    so there is no tunnel or proxy here."""
    named = model_servers.handoff(group)
    jobs = model_servers.server_jobs(group) or {}
    if not named or jobs.get(named["job"]) != "RUNNING":
        return False, "no running server job named by " + group.handoff
    if not model_servers.server_answers(named["node"], named["port"]):
        return False, f"server job {named['job']} on {named['node']} is loading"
    return True, f"server job {named['job']} at {named['node']}:{named['port']}"


def tunnel_job(group: model_servers.GpuGroup) -> str | None:
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


def tunnel_down(group: model_servers.GpuGroup) -> None:
    run_command(
        [str(AIPROVER_CLI), "tunnel", "down"],
        timeout=60,
        aiprover_config=group.tunnel_config,
    )


def job_group(job: dict) -> model_servers.GpuGroup:
    """GPU group of a job; jobs without one belong to the default group."""
    return model_servers.GPU_GROUPS.get(
        job.get("gpu_group") or model_servers.DEFAULT_GROUP,
        model_servers.GPU_GROUPS[model_servers.DEFAULT_GROUP],
    )


def group_config(config: dict, group: model_servers.GpuGroup) -> dict:
    """A run config whose AIProver agents use the group's model server;
    agents of a persistent version keep their own server."""
    persistent = set(models.HARNESS_CONFIGS.values()) | set(
        models.VERSION_ENDPOINTS.values()
    )
    for spec in config["agents"].values():
        if (
            spec.get("config") in persistent
            or spec.get("base_url") in persistent
            or str(spec.get("config")).startswith(
                str(model_servers.CUSTOM_DIR)
            )
        ):
            continue
        if spec.get("backend") == "aiprover":
            spec["config"] = group.prover_config
        elif (
            spec.get("backend") == "openai_compatible"
            and spec.get("model") == models.AIPROVER_SERVED_MODEL
        ):
            spec["base_url"] = group.endpoint
    return config


def job_selection(job: dict) -> dict:
    """Models chosen for a job; empty for a job submitted before the choice
    existed, which runs with the served config."""
    return modernize(json.loads(job.get("agents") or "null") or {})


def library_search(environment: dict, agents: dict, name: str) -> None:
    """Point the AIProver configs of a run on package library `name` at the
    search of Mathlib and that library (search/server.py)."""
    def searching(path: str) -> str:
        source = locate(path)
        target = TEMP_DIR / "library_search" / name / source.name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            re.sub(
                r'^leanfinder_url = "(http[^"]*?)/?"',
                rf'leanfinder_url = "\1/library/{name}"',
                source.read_text(),
                flags=re.M,
            )
        )
        return str(target)

    environment["AIPROVER_CONFIG"] = searching(environment["AIPROVER_CONFIG"])
    for spec in agents.values():
        if spec.get("backend") == "aiprover" and spec.get("config"):
            spec["config"] = searching(spec["config"])


def release_environment(
    release: str, tunnel_config: Path | None, agents: dict
) -> dict:
    """Variables of a run on Lean `release`: its AIProver configs, in
    `agents` and in the variables, use the release's Lean project. A group
    without a server has no tunnel config."""
    environment = {"AIPROVER_LEAN_RELEASE": release}
    if tunnel_config:
        environment["AIPROVER_CONFIG"] = str(
            releases.harness_config(tunnel_config, release)
        )
    for spec in agents.values():
        if spec.get("backend") == "aiprover":
            spec["config"] = str(
                releases.harness_config(locate(spec["config"]), release)
            )
    return environment


def job_checkpoint(job: dict) -> str | None:
    """Checkpoint the model server must serve for `job`; None if it needs none.
    The served config's prover uses the trained model."""
    if json.loads(job.get("options") or "{}").get("documents_only"):
        return None
    selection = job_selection(job)
    return (
        models.checkpoint_of(selection)
        if selection
        else next(iter(model_servers.CHECKPOINTS.values()), None)
    )


def process_alive(pid: int | None, run_id: str) -> bool:
    """True if `pid` is a live orchestrator process of run `run_id`."""
    if not pid:
        return False
    try:
        process = psutil.Process(pid)
        return (
            process.status() != psutil.STATUS_ZOMBIE
            and run_id in process.cmdline()
        )
    except psutil.Error:
        return False


def run_process_alive(run_id: str) -> bool:
    """True if a live orchestrator process of this account belongs to run
    `run_id`; servers of other users on the host may use the same run id."""
    uid = os.getuid()
    return any(
        process_alive(process.pid, run_id)
        for process in psutil.process_iter(["uids"])
        if process.info["uids"] and process.info["uids"].real == uid
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


def retire_summary(run_id: str) -> None:
    """Keep the summary of a run about to resume as summary.previous.json:
    it is not the outcome of the new process."""
    path = RESULTS_DIR / run_id / "summary.json"
    if path.exists():
        path.replace(path.with_name("summary.previous.json"))


def progress_count(run_id: str) -> int:
    """Durable steps of a run (decisions a resume restores) in its trace."""
    path = RESULTS_DIR / run_id / "trace.json"
    try:
        steps = modernize(json.loads(path.read_text())).get("steps", [])
    except (OSError, json.JSONDecodeError):
        return 0
    return sum(step.get("event") in PROGRESS_EVENTS for step in steps)


class JobStore(SqliteStore):
    """SQLite-backed job records; safe to share between threads."""

    def __init__(self, path: Path = DATABASE, readonly: bool = False):
        super().__init__(path, SCHEMA, MIGRATIONS, readonly)

    def add(self, job: dict) -> None:
        self._insert("jobs", job)

    def update(self, run_id: str, **values) -> None:
        self._update("jobs", {"run_id": run_id}, values)

    def transition(self, run_id: str, expected: str, /, **values) -> bool:
        """Update the job only if its state is `expected`; False if it is
        not (another thread changed it first)."""
        where = {"run_id": run_id, "state": expected}
        return self._update("jobs", where, values) == 1

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

    def server_job_checkpoints(self, group: str) -> dict[str, str | None]:
        rows = self._execute(
            "SELECT job_id, checkpoint FROM server_jobs "
            "WHERE COALESCE(gpu_group, ?) = ?",
            (model_servers.DEFAULT_GROUP, group),
        )
        return {row["job_id"]: row["checkpoint"] for row in rows}

    def add_server_job(self, job_id: str, checkpoint: str, group: str) -> None:
        self._execute(
            "INSERT OR IGNORE INTO server_jobs (job_id, submitted, checkpoint, gpu_group) "
            "VALUES (?, ?, ?, ?)",
            (job_id, time.time(), checkpoint, group),
        )

    def remove_server_job(self, job_id: str) -> None:
        self._execute("DELETE FROM server_jobs WHERE job_id = ?", (job_id,))


class Worker(threading.Thread):
    """Runs approved jobs and manages the model server jobs. Each GPU group
    (model_servers.GPU_GROUPS) has its own model server and runs up to
    MAX_CONCURRENT_RUNS of its jobs at once."""

    def __init__(self, store: JobStore):
        super().__init__(name="job-worker", daemon=True)
        self.store = store
        # Run id -> its orchestrator process; None for a run adopted after a
        # server restart, which is followed by its pid.
        self.running: dict[str, subprocess.Popen | None] = {}
        # Per group: last server status, start of its idle period, and the
        # checkpoint its endpoint was last confirmed to serve.
        self.server_messages: dict[str, str] = {}
        self.idle_since: dict[str, float] = {}
        self.serving: dict[str, str] = {}
        self.maintained = 0.0  # last _maintain_servers pass
        self.successor_tried: dict[str, float] = {}  # per group
        # Run id -> the process rendering its trace pages.
        self.page_renders: dict[str, subprocess.Popen] = {}

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
        # A group's servers are kept for its own runs; a group that only
        # lends them is idle.
        busy_groups = {job_group(job).name for job in queued + running}
        for name, group in model_servers.GPU_GROUPS.items():
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
            group = job_group(candidate)
            if checkpoint is not None and not self._pool_serving(
                checkpoint, group
            ):
                # A run that needs no model server does not wait for
                # another run's server.
                candidate = self._serverless_job(name, queued)
            elif not group.job_name and not group.serverless:
                # A persistent server's runs wait until it answers, rather
                # than failing on it.
                endpoint_up, message = endpoint_status(False, group)
                self.server_messages[name] = message
                if not endpoint_up:
                    candidate = None
            if candidate is not None:
                started = self._execute(candidate) or started
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
            if run_process_alive(job["run_id"]) or self._job_active(job):
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

    def _job_active(self, job: dict, states: dict | None = None) -> bool:
        """True while the run's Slurm job is queued or running; an unknown
        state (squeue failed) counts as active."""
        if not job.get("slurm_job"):
            return False
        states = slurm.job_states() if states is None else states
        return states is None or job["slurm_job"] in states

    def _reap(self) -> None:
        """Finish the runs whose process, or Slurm job, has ended."""
        states = slurm.job_states() if slurm.enabled() and self.running else None
        for run_id, process in list(self.running.items()):
            job = self.store.get(run_id)
            if process is not None:
                done = process.poll() is not None
            elif job.get("slurm_job"):
                done = not self._job_active(job, states)
            else:
                done = not process_alive(job["pid"], run_id)
            if done:
                del self.running[run_id]
                self._finish(self.store.get(run_id))

    def _refresh_pages(self) -> None:
        """Render the trace pages of running runs whose trace has steps the
        pages lack, or every LIVE_PAGE_REFRESH_SECONDS for the jobs in
        flight, so a run can be viewed in progress: one render per run at a
        time, at most every PAGE_REFRESH_SECONDS."""
        for run_id in list(self.running):
            render = self.page_renders.get(run_id)
            if render is not None and render.poll() is None:
                continue
            trace = RESULTS_DIR / run_id / "trace.json"
            page = RESULTS_DIR / run_id / "trace.html"
            if not trace.exists():
                continue
            rendered = page.stat().st_mtime if page.exists() else 0.0
            age = time.time() - rendered
            if age >= LIVE_PAGE_REFRESH_SECONDS or (
                trace.stat().st_mtime > rendered
                and age >= PAGE_REFRESH_SECONDS
            ):
                self.page_renders[run_id] = subprocess.Popen(
                    trace_pages_command(trace),
                    cwd=ROOT,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                )

    # Server jobs --------------------------------------------------------

    def _serving(
        self, checkpoint: str, group: model_servers.GpuGroup, own: bool = True
    ) -> bool:
        """True if the group's model endpoint is up and serves `checkpoint`;
        otherwise bring the group's server in line with it: replace a server
        job of another checkpoint (none of the group's running jobs uses it),
        or submit one. For a borrowed server (`own` false) the jobs are only
        followed: none is submitted, replaced or queued as a successor."""
        if self.serving.get(group.name) == checkpoint:
            endpoint_up, message = endpoint_status(bring_up=True, group=group)
            if endpoint_up:
                self.server_messages[group.name] = message
                return True
            self.serving.pop(group.name, None)
        jobs = model_servers.server_jobs(group)
        if jobs is None:
            self.server_messages[group.name] = (
                "cluster unreachable: reopen the SSH ControlMaster "
                f"({model_servers.CONTROL_SOCKET})"
            )
            logger.warning(self.server_messages[group.name])
            return False
        checkpoints = self.store.server_job_checkpoints(group.name)
        for job_id in checkpoints:
            if job_id not in jobs:
                self.store.remove_server_job(job_id)
        # A job the server did not submit has no recorded checkpoint: the
        # default.
        serves = {
            job_id: checkpoints.get(job_id) or next(iter(model_servers.CHECKPOINTS.values()), None)
            for job_id in jobs
        }
        for job_id in [
            job_id for job_id, served in serves.items() if served != checkpoint
        ]:
            if not own:
                return False
            if job_id not in checkpoints:
                self.server_messages[group.name] = (
                    f"waiting for server job {job_id}, "
                    "which serves another checkpoint"
                )
                return False
            logger.info(
                f"cancelling server job {job_id} ({group.name}, serves {serves[job_id]}) "
                f"for a run that needs {checkpoint}"
            )
            if model_servers.cancel_server(job_id):
                self.store.remove_server_job(job_id)
            del serves[job_id]
        if not serves:
            if not own:
                return False
            job_id = model_servers.submit_server(checkpoint, group)
            if job_id:
                self.store.add_server_job(job_id, checkpoint, group.name)
                self.server_messages[group.name] = (
                    f"submitted server job {job_id}"
                )
            else:
                self.server_messages[group.name] = (
                    "server job submission failed (see server log)"
                )
            return False
        if not self._tunnel_current(group, jobs):
            return False
        endpoint_up, message = endpoint_status(bring_up=True, group=group)
        if endpoint_up:
            if own:
                self._keep_successor(checkpoint, group, jobs, checkpoints)
            self.serving[group.name] = checkpoint
            self.server_messages[group.name] = message
            return True
        summary = ", ".join(
            f"{job_id} {state}" for job_id, state in jobs.items()
        )
        self.server_messages[group.name] = f"waiting for server job {summary}"
        return False

    def _tunnel_current(self, group: model_servers.GpuGroup, jobs: dict) -> bool:
        """Point the group's tunnel at the server job its handoff file names,
        once that job's server answers; False if the handoff names no
        running job. The handoff of an ended job still names its node, where
        another job's server may answer, and an open tunnel is not moved by
        `tunnel up`."""
        running = [
            job_id for job_id, state in jobs.items() if state == "RUNNING"
        ]
        named = model_servers.handoff(group)
        if model_servers.LOCAL_CLUSTER:
            # No tunnel: runs read the handoff themselves.
            if named is None or named["job"] not in running:
                self.vista_messages[group.name] = (
                    "handoff names no running server job; waiting for "
                    + (", ".join(f"{i} {s}" for i, s in jobs.items()) or "none")
                )
                return False
            return True
        current = tunnel_job(group)
        # An unreadable handoff (a failed remote read) leaves a tunnel to a
        # running job in place.
        if named is None and current in running:
            return True
        if named is None or named["job"] not in running:
            if current is not None:
                logger.info(
                    f"{group.name}: closing tunnel to ended job {current}"
                )
                tunnel_down(group)
            self.server_messages[group.name] = (
                "handoff names no running server job; waiting for "
                + (", ".join(f"{i} {s}" for i, s in jobs.items()) or "none")
            )
            return False
        if current == named["job"]:
            return True
        if current in running and not model_servers.server_answers(
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
            ] and job_id in self.store.server_job_checkpoints(group.name):
                logger.info(f"{group.name}: cancelling superseded job {job_id}")
                if model_servers.cancel_server(job_id):
                    self.store.remove_server_job(job_id)
        return True

    def _keep_successor(
        self,
        checkpoint: str,
        group: model_servers.GpuGroup,
        jobs: dict,
        checkpoints: dict,
    ) -> None:
        """Keep one server job queued behind the running one, so that the
        group's server is replaced without a wait in the Slurm queue; at
        most one submission per group every SUCCESSOR_RETRY_SECONDS."""
        if any(state == "PENDING" for state in jobs.values()):
            return
        # Runs read the handoff only when they start, so a successor would hold
        # a second GPU node beside the first; a run that loses its server
        # resumes on the next one.
        if model_servers.LOCAL_CLUSTER:
            return
        if time.time() - self.successor_tried.get(group.name, 0.0) < (
            SUCCESSOR_RETRY_SECONDS
        ):
            return
        self.successor_tried[group.name] = time.time()
        job_id = model_servers.submit_server(checkpoint, group)
        if job_id:
            logger.info(f"{group.name}: queued successor server job {job_id}")
            self.store.add_server_job(job_id, checkpoint, group.name)

    def _pool_serving(self, checkpoint: str, group: model_servers.GpuGroup) -> bool:
        """True if a model server of the group's pool serves `checkpoint`;
        every server of the pool is brought in line with it."""
        return any(
            [
                self._serving(checkpoint, member, own=member is group)
                for member in model_servers.server_pool(group)
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
            for member in model_servers.server_pool(model_servers.GPU_GROUPS[group]):
                self.serving.pop(member.name, None)
            self._pool_serving(checkpoint, model_servers.GPU_GROUPS[group])

    def _release_idle_servers(self, group: model_servers.GpuGroup) -> None:
        """Cancel the server jobs this worker submitted for `group` after
        IDLE_SECONDS without queued or running jobs in the group."""
        managed = list(self.store.server_job_checkpoints(group.name))
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
            if model_servers.cancel_server(job_id):
                self.store.remove_server_job(job_id)
        self.serving.pop(group.name, None)
        self.server_messages[group.name] = (
            "server jobs cancelled after idle period"
        )
        self.idle_since.pop(group.name, None)

    # Runs -----------------------------------------------------------------

    def _recover(self, job: dict) -> None:
        """Finish a job left running by a previous server process."""
        if self._job_active(job):
            logger.info(
                f"adopting running job {job['run_id']} (Slurm job {job['slurm_job']})"
            )
            self.running[job["run_id"]] = None
        elif process_alive(job["pid"], job["run_id"]):
            logger.info(
                f"adopting running job {job['run_id']} (pid {job['pid']})"
            )
            self.running[job["run_id"]] = None
        elif job["state"] == "cancelling" or self._completed_while_away(job):
            self._finish(job)
        else:
            logger.info(f"requeueing interrupted job {job['run_id']}")
            self.store.update(job["run_id"], state="queued", pid=None, slurm_job=None)

    @staticmethod
    def _completed_while_away(job: dict) -> bool:
        """True if the run wrote a final summary after the server stopped
        watching it; an interrupted run's summary does not count."""
        summary = read_summary(job["run_id"])
        return bool(summary) and summary.get("error_kind") != "interrupted"

    def _execute(self, job: dict) -> bool:
        """Start a queued job; False if it left the queue meanwhile (a
        cancellation while the worker probed the servers)."""
        run_id = job["run_id"]
        # Restarts after infrastructure failures are made by the worker, which
        # waits for a new model server; run.py's own restarts would not.
        config_path = TEMP_DIR / f"{run_id}.config.json"
        config_path.parent.mkdir(exist_ok=True)
        group = job_group(job)
        config = group_config(
            models.build_config(CONFIG, job_selection(job)), group
        )
        options = current_names(json.loads(job.get("options") or "{}"))
        if "aiprover_timeout" in options:
            timeout = int(options["aiprover_timeout"]) or NO_TIME_LIMIT
            for spec in config["agents"].values():
                if spec.get("backend") == "aiprover":
                    spec["timeout"] = timeout
        orchestrator = config["agents"]["orchestrator"]
        if "source_dir" in options and orchestrator.get("tools"):
            orchestrator["source_dir"] = options["source_dir"]
        phases = options.get("orchestrator_phases")
        if phases:
            # `true` selects the default phases, a name the file
            # orchestrator_phases_<name>.json.
            path = (
                ORCHESTRATOR_PHASES
                if phases is True
                else ORCHESTRATOR_PHASES.with_name(f"orchestrator_phases_{phases}.json")
            )
            # The selection's choices (the judges' tools) are kept. The files
            # name Claude models, which another backend's phases do not take.
            selected = orchestrator.get("phases") or {}
            orchestrator["phases"] = {
                prefix: {
                    key: value
                    for key, value in overrides.items()
                    if key != "model" or orchestrator["backend"] == "claude"
                }
                for prefix, overrides in json.loads(path.read_text()).items()
            }
            for prefix, overrides in selected.items():
                orchestrator["phases"][prefix] = {
                    **orchestrator["phases"].get(prefix, {}),
                    **overrides,
                }
        environment = {"AIPROVER_CONFIG": str(group.tunnel_config)}
        library = options.get("library") or ""
        if library and libraries.is_package(library):
            environment["AIPROVER_LIBRARY_PACKAGE"] = str(
                libraries.resolve(library)
            )
        release = options.get("lean_release")
        if release:
            environment |= release_environment(
                release,
                group.tunnel_config,
                config["agents"],
            )
        if "AIPROVER_LIBRARY_PACKAGE" in environment:
            library_search(environment, config["agents"], Path(library).name)
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
        options_now = current_names(json.loads(job.get("options") or "{}"))
        for name, value in options_now.items():
            if name in SERVER_OPTIONS:
                continue
            # One argument, since a reviewer's comment may start with a dash.
            arguments.append(f"--{name.replace('_', '-')}={value}")
        if (RESULTS_DIR / run_id / "trace.json").exists():
            arguments.append("--resume")
            retire_summary(run_id)
        else:
            arguments += ["--problem-uuid", job["uuid"]]
        output_path = TEMP_DIR / f"{run_id}.out"
        output_path.parent.mkdir(exist_ok=True)
        if not self.store.transition(
            run_id,
            "queued",
            state="running",
            pid=None,
            started=job["started"] or time.time(),
        ):
            logger.info(f"{run_id} left the queue before it started")
            return False
        if slurm.enabled():
            return self._submit(run_id, arguments, environment, output_path, group)
        try:
            with open(output_path, "a") as output:
                process = subprocess.Popen(
                    arguments,
                    cwd=ROOT,
                    stdout=output,
                    stderr=subprocess.STDOUT,
                    env={
                        **os.environ,
                        "PYTHONPATH": str(ROOT),
                        **accounts.environment(),
                        **environment,
                    },
                    start_new_session=True,
                )
        except OSError:
            self.store.transition(run_id, "running", state="queued")
            raise
        self.running[run_id] = process
        self.store.update(run_id, pid=process.pid)
        # A cancellation between the start and the recorded pid found no
        # process to signal.
        if self.store.get(run_id)["state"] == "cancelling":
            os.kill(process.pid, signal.SIGTERM)
        logger.info(
            f"started {run_id} (pid {process.pid}, {group.name}); "
            f"{len(self.running)} runs in progress"
        )
        return True

    def _submit(
        self,
        run_id: str,
        arguments: list[str],
        environment: dict,
        output_path: Path,
        group: model_servers.GpuGroup,
    ) -> bool:
        """Start a run as a Slurm job (server/slurm.py), which finds the model
        server of the group from the job's node."""
        try:
            job_id = slurm.submit_run(
                run_id,
                arguments,
                {"PYTHONPATH": str(ROOT), **accounts.environment(), **environment},
                output_path,
                group.name,
            )
        except RuntimeError as error:
            logger.error(f"{run_id}: {error}")
            self.store.transition(run_id, "running", state="queued")
            time.sleep(BACKEND_RETRY_SECONDS)
            return False
        self.running[run_id] = None
        self.store.update(run_id, slurm_job=job_id)
        if self.store.get(run_id)["state"] == "cancelling":
            slurm.cancel(job_id)
        logger.info(
            f"started {run_id} (Slurm job {job_id}, {group.name}); "
            f"{len(self.running)} runs in progress"
        )
        return True

    def _finish(self, job: dict) -> None:
        run_id = job["run_id"]
        summary = read_summary(run_id)
        infrastructure = summary.get("error_kind") == "infrastructure" or (
            bool(job.get("slurm_job"))
            and summary.get("error_kind") in (None, "interrupted")
            and slurm.infrastructure_end(run_id, job["slurm_job"])
        )
        if job["state"] != "cancelling" and infrastructure:
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
                    slurm_job=None,
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
        status = summary.get("status") or "no_summary"
        state = "cancelled" if job["state"] == "cancelling" else "finished"
        # A plan written as a cancellation arrived still awaits a decision.
        if status == PLAN_REVIEW:
            state = PLAN_REVIEW
        self.store.update(
            run_id, state=state, status=status, pid=None, slurm_job=None, ended=time.time()
        )
        logger.info(f"{run_id}: {state} ({status})")

    def approve(self, run_id: str, approver: str) -> bool:
        if not self.store.transition(
            run_id, "awaiting_approval", state="queued", approved_by=approver
        ):
            return False
        logger.info(f"{run_id} approved by {approver}")
        return True

    def resume(self, run_id: str) -> bool:
        """Queue an ended run again; the worker continues it from its trace
        (`--resume`). False if it has not ended."""
        for state in ("cancelled", "finished", PLAN_REVIEW):
            if self.store.transition(
                run_id, state, state="queued", status=None, ended=None, pid=None
            ):
                logger.info(f"{run_id} queued to resume")
                return True
        return False

    def reject(self, run_id: str, approver: str) -> bool:
        if not self.store.transition(
            run_id,
            "awaiting_approval",
            state="rejected",
            approved_by=approver,
            ended=time.time(),
        ):
            return False
        logger.info(f"{run_id} rejected by {approver}")
        return True

    def cancel(self, run_id: str) -> bool:
        """Withdraw or dequeue a job, or stop a running one; False if it is not
        open."""
        for state in ("awaiting_approval", "queued"):
            if self.store.transition(
                run_id, state, state="cancelled", ended=time.time()
            ):
                return True
        if not self.store.transition(run_id, "running", state="cancelling"):
            return False
        # SIGTERM to the orchestrator only: it unwinds, cancels its AIProver
        # jobs and records the interruption in the trace. A run whose pid is
        # not yet recorded is signalled by the worker that starts it.
        slurm_job = self.store.get(run_id).get("slurm_job")
        if slurm_job:
            slurm.cancel(slurm_job)
            return True
        pid = self.store.get(run_id)["pid"]
        if pid:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        return True

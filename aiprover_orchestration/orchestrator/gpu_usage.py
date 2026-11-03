"""GPU time of a run, counted only while the model server is computing.

The server is active between two metrics samples when vLLM reports a running
request at either sample or completes a request between them. A run is
charged for active time during which it has AIProver sessions open. When
runs on one server overlap, each is charged in proportion to its open
sessions, so the charges of overlapping runs sum to the server's active time.

`refresh` writes two files in a run's directory: gpu_spans.json holds the
evidence (open sessions and active spans), and gpu_usage.json holds the
charged hours. A run that ends also charges again the runs it overlaps, and
sessions outside any run take their share of the DGX, the only server they
use. Hardware and prices lie in configs/orchestrator/gpu_hardware.json and
are read on display, so a correction applies to every run at once. A
server's list there may hold several entries; a run uses the latest one
whose `since` date is not after the run's start. The table describes one
model server, whereas the metrics of a pool of servers are read as one, so
the figures of a pooled run are lower bounds.

    python -m aiprover_orchestration.orchestrator.gpu_usage [results ...]
recomputes every run of the given results directories.
"""

import datetime
import itertools
import json
import os
import sys
from pathlib import Path

from ..paths import CONFIG_DIR, ROOT
from ..server.model_servers import GpuGroup, group_of_solver, sharing_key
from ..utils.files import read_json, read_jsonl, write_atomic
from .trace import Trace

HARDWARE = CONFIG_DIR / "orchestrator" / "gpu_hardware.json"
RUNNING = "vllm:num_requests_running"
FINISHED = "vllm:e2e_request_latency_seconds_count"
# Consecutive samples further apart than this do not bound an active span.
MAX_SAMPLE_GAP = 90
# Below this share of a run's session time sampled, its figures are bounds.
MIN_COVERAGE = 0.9
SPANS_FILE = "gpu_spans.json"
USAGE_FILE = "gpu_usage.json"
# The spans' evidence, which the usage record leaves out.
EVIDENCE = ("sessions", "active", "jobs")
# The server of sessions outside any run; job directories do not name it.
UNTRACED_SERVER = "dgx"
# Files of a session directory that its harness keeps writing.
SESSION_OUTPUT = ("agent.log", ".hevo_ticks")


def solver_of(trace: dict) -> dict:
    """The run configuration's solver agent."""
    agents = (trace.get("config") or {}).get("agents") or {}
    return agents.get("solver") or {}


def solver_group(trace: dict) -> GpuGroup | None:
    return group_of_solver(solver_of(trace).get("config") or "")


def server_of(trace: dict) -> str:
    """The key in the hardware table of the run's model server; empty for a
    run without one. A config of no GPU group is identified by its file name,
    and a server missing from the table leaves the run unrecorded."""
    solver = solver_of(trace)
    if solver.get("backend") != "aiprover":
        return ""
    group = solver_group(trace)
    if group and group.hardware:
        return group.hardware
    name = Path(solver.get("config") or "").stem
    return next((key for key in read_json(HARDWARE) if key in name), name)


def sharing_of(trace: dict) -> str:
    """Key of the model servers that the run's sessions compete for."""
    group = solver_group(trace)
    return sharing_key(group) if group else server_of(trace)


def stamp(text: str) -> float:
    return datetime.datetime.fromisoformat(text).timestamp()


def started_at(trace: dict) -> float | None:
    """Epoch seconds of a run's start: a trace document records it as text,
    a line record as the `time` of its header."""
    started = trace.get("started")
    return stamp(started) if started else trace.get("time")


def clock_anchors(trace: dict) -> list[tuple[float, float]]:
    """(elapsed, epoch) of each origin of a trace's elapsed clock. The clock
    stops while a run is down, so each resumption sets a new origin."""
    return [(0.0, started_at(trace))] + [
        (entry["elapsed_seconds"], stamp(entry["time"]))
        for entry in trace.get("resumptions") or []
    ]


def epoch_of(trace: dict):
    """Function from a trace's elapsed seconds to epoch seconds."""
    anchors = clock_anchors(trace)

    def epoch(elapsed: float) -> float:
        origin = max(
            (anchor for anchor in anchors if anchor[0] <= elapsed),
            default=anchors[0],
        )
        return origin[1] + elapsed - origin[0]

    return epoch


def outages(trace: dict) -> list[tuple[float, float]]:
    """[down, up) of each period in which a run was down, in epoch seconds."""
    anchors = clock_anchors(trace)
    return [
        (origin[1] + resumed[0] - origin[0], resumed[1])
        for origin, resumed in zip(anchors, anchors[1:])
    ]


def readable(directory: Path) -> bool:
    """Whether a directory exists and may be listed; another account's may
    not."""
    return os.access(directory, os.R_OK | os.X_OK)


def account_dirs(relative: str) -> list[Path]:
    """A directory of the repository and of each user, where readable."""
    directories = [ROOT / relative, *(ROOT / "users").glob(f"*/{relative}")]
    return [directory for directory in directories if readable(directory)]


def job_stores() -> list[Path]:
    return account_dirs("aiprover/work/jobs")


def modified(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def job_sessions(job_dir: Path) -> list[list[float]]:
    """[start, end, 1] of each session of a job directory. A job's time in
    the slot queue is not part of them. A session's clock counts earlier
    time when it resumes, so its start is back-dated by that time and only
    its end is a moment at which it ran."""
    sessions = []
    for session in sorted(job_dir.glob("s[0-9]*")):
        elapsed = read_json(session / "resume.json").get("elapsed")
        start = read_json(session / ".hevo_state.json").get("t0")
        if start is None and elapsed is not None:
            # A session without a start time ends with its job.
            done = read_json(job_dir / "result.json").get("done")
            start = None if done is None else done - elapsed
        if start is None:
            continue
        if elapsed is None:
            # A session killed before it reported ended at its last output.
            last = max(modified(session / name) for name in SESSION_OUTPUT)
            elapsed = last - start
        if elapsed > 0:
            sessions.append([start, start + elapsed, 1])
    return sessions


def running_spans(session: list, down: list) -> list[list[float]]:
    """A session of a resumed run as the spans in which it ran: its elapsed
    time, counted back from its end, skipping the outages in `down`."""
    start, end, count = session
    remaining = end - start
    spans = []
    for stopped, resumed in reversed(down):
        if stopped >= end:
            continue
        if end - remaining >= resumed:
            break
        if resumed < end:
            spans.append([resumed, end, count])
            remaining -= end - resumed
        end = stopped
    return spans + [[end - remaining, end, count]]


def is_solver_call(step: dict) -> bool:
    return step["kind"] == "model_call" and step.get("backend") == "aiprover"


def counted_samples(step: dict) -> list[dict]:
    """The samples of a solver call; sessions cut off by a lost model server
    are not counted."""
    return [
        sample
        for sample in step.get("samples") or []
        if sample.get("status") != "infra"
    ]


def job_starts(steps: list[dict]) -> list[dict]:
    """The first step of each job: its `aiprover_job_started` decision, or
    the job's call in traces without one. A resumed run starts its unfinished
    jobs again under their names."""
    first = {}
    for step in steps:
        if step.get("event") == "aiprover_job_started" or is_solver_call(step):
            first.setdefault(step.get("aiprover_job") or step["index"], step)
    return list(first.values())


def job_names(trace: dict) -> list[str]:
    return sorted(
        {
            step["aiprover_job"]
            for step in trace.get("steps", [])
            if step.get("aiprover_job")
        }
    )


def stored_sessions(name: str, stores: list[Path]) -> list[list[float]]:
    """Sessions of the first store that holds the job."""
    directory = next(
        (store / name for store in stores if name and (store / name).is_dir()),
        None,
    )
    return job_sessions(directory) if directory else []


def call_sessions(call: dict, epoch) -> list[list[float]]:
    """Sessions of a job known only from its call, which they end with."""
    end = epoch(call["elapsed_seconds"])
    return [
        [end - sample["elapsed_sec"], end, 1]
        for sample in counted_samples(call)
        if sample.get("elapsed_sec")
    ]


def traced_span(step: dict, call: dict | None, trace: dict, epoch) -> list:
    """The time in the trace of a job that started at `step`, in the slot
    queue included; if left without a call, it ended at the next step of its
    lemma or, at the latest, at the next resumption or the end of the run."""
    steps = trace["steps"]
    start = step["elapsed_seconds"]
    if call:
        end = call["elapsed_seconds"]
        count = len(counted_samples(call))
    else:
        stops = [
            *sorted(e["elapsed_seconds"] for e in trace.get("resumptions") or []),
            max(later["elapsed_seconds"] for later in steps),
        ]
        end = min(
            (
                later["elapsed_seconds"]
                for later in steps[step["index"] + 1:]
                if later.get("lemma") == step.get("lemma")
            ),
            default=next(stop for stop in stops if stop >= start),
        )
        count = 1
    return [[epoch(start), epoch(end), count]] if count and end > start else []


def open_sessions(trace: dict) -> list[list[float]]:
    """[start, end, sessions] of a run's AIProver sessions, in epoch seconds,
    from their job directories, or from the trace for a job whose directory
    is gone."""
    steps = trace.get("steps", [])
    if not started_at(trace):
        return []
    epoch = epoch_of(trace)
    down = outages(trace)
    calls = {
        step.get("aiprover_job"): step for step in filter(is_solver_call, steps)
    }
    stores = job_stores()
    jobs = []
    for step in job_starts(steps):
        name = step.get("aiprover_job") or ""
        found = stored_sessions(name, stores)
        if found:
            for session in found:
                jobs += running_spans(session, down)
        elif is_solver_call(step):
            jobs += call_sessions(step, epoch)
        else:
            jobs += traced_span(step, calls.get(name), trace, epoch)
    return jobs


def server_samples(metrics_path: Path) -> list[tuple]:
    """(time, running requests, finished requests) of each server sample."""
    return [
        (
            sample["time"],
            sample["server"][RUNNING],
            sample["server"].get(FINISHED, 0),
        )
        for sample in read_jsonl(metrics_path)
        if RUNNING in (sample.get("server") or {})
    ]


def extend_spans(spans: list, start: float, end: float) -> None:
    """Add [start, end] to time-ordered merged spans."""
    if spans and spans[-1][1] >= start:
        spans[-1][1] = end
    else:
        spans.append([start, end])


def sampled_spans(metrics_path: Path) -> tuple[list, list]:
    """Merged spans in which the server was sampled, and those in which it
    was computing."""
    spans: list[list[float]] = []
    sampled: list[list[float]] = []
    samples = server_samples(metrics_path)
    for previous, current in itertools.pairwise(samples):
        if current[0] - previous[0] > MAX_SAMPLE_GAP:
            continue
        extend_spans(sampled, previous[0], current[0])
        if previous[1] > 0 or current[1] > 0 or current[2] > previous[2]:
            extend_spans(spans, previous[0], current[0])
    return sampled, spans


def edges(spans: list, axis: int):
    """The start and end events of [start, end] or [start, end, count]
    spans, as (moment, axis, change)."""
    for start, end, *count in spans:
        size = count[0] if count else 1
        yield start, axis, size
        yield end, axis, -size


def charged_hours(
    mine: list, others: list, active: list
) -> tuple[float, float]:
    """Active hours with sessions of this run open, whole and in proportion
    to this run's share of the open sessions."""
    events = sorted(
        [*edges(mine, 0), *edges(others, 1), *edges(active, 2)]
    )
    levels = [0, 0, 0]
    whole = share = last = 0.0
    for moment, axis, change in events:
        own, other, computing = levels
        if computing > 0 and own > 0:
            whole += moment - last
            share += (moment - last) * own / (own + other)
        levels[axis] += change
        last = moment
    return whole / 3600, share / 3600


def coverage(sessions: list, sampled: list) -> float:
    """Share of the time with open sessions in which the server was
    sampled."""
    whole = charged_hours(sessions, [], [[0.0, float("inf")]])[0]
    seen = charged_hours(sessions, [], sampled)[0]
    return seen / whole if whole else 0.0


def bounds(spans: dict) -> tuple[float, float]:
    sessions = spans["sessions"]
    return (
        min((s[0] for s in sessions), default=0.0),
        max((s[1] for s in sessions), default=0.0),
    )


def peers(result_dir: Path) -> list[Path]:
    """Run directories that may share a model server with this run."""
    roots = {result_dir.parent, *account_dirs("results")}
    return [path for root in roots for path in root.iterdir()]


def is_import(path: Path, spans: dict) -> bool:
    """Whether a run is a copy made by an import, which keeps the run id of
    its original."""
    return spans.get("run_id", path.name) != path.name


def write_record(path: Path, record: dict) -> None:
    """Write a record of a run. A run of another account that cannot be
    written keeps its record."""
    try:
        write_atomic(path, json.dumps(record))
    except PermissionError:
        pass


def write_spans(result_dir: Path, trace: dict) -> dict:
    sampled, active = sampled_spans(result_dir / "metrics.jsonl")
    sessions = open_sessions(trace)
    start = started_at(trace)
    group = solver_group(trace)
    spans = {
        "run_id": trace.get("run_id") or result_dir.name,
        "server": server_of(trace),
        "sharing": sharing_of(trace),
        "started": (
            datetime.date.fromtimestamp(start).isoformat() if start else ""
        ),
        "pooled": bool(group and group.borrows),
        "sessions": sessions,
        "active": active,
        "coverage": round(coverage(sessions, sampled), 3),
        "solver_calls": sum(map(is_solver_call, trace.get("steps", []))),
        "jobs": job_names(trace),
    }
    write_record(result_dir / SPANS_FILE, spans)
    return spans


def write_usage(result_dir: Path, spans: dict, neighbors: list[dict]) -> None:
    others = [job for neighbor in neighbors for job in neighbor["sessions"]]
    whole, share = charged_hours(spans["sessions"], others, spans["active"])
    usage = {
        **{key: value for key, value in spans.items() if key not in EVIDENCE},
        "recorded": bool(spans["active"] and spans["sessions"]),
        "active_hours": round(whole, 3),
        "charged_hours": round(share, 3),
    }
    write_record(result_dir / USAGE_FILE, usage)


def sharing(spans: dict) -> str | None:
    """Sharing key of a record; one written before keys has only its server."""
    return spans.get("sharing", spans.get("server"))


def overlapping(spans: dict, candidates: dict[Path, dict]) -> dict:
    """The candidates sharing a server whose sessions overlap `spans`."""
    start, end = bounds(spans)
    return {
        path: other
        for path, other in candidates.items()
        if other is not spans
        and sharing(other) == sharing(spans)
        and bounds(other)[0] < end
        and bounds(other)[1] > start
    }


def charge(path: Path, spans: dict, pool: dict[Path, dict]) -> None:
    """Charge a run against the others of `pool`. An import does not compete
    with its original."""
    neighbors = overlapping(spans, pool) if spans["server"] else {}
    write_usage(
        path,
        spans,
        [
            other
            for other in neighbors.values()
            if other.get("run_id") != spans.get("run_id", path.name)
        ],
    )


def load_trace(path: Path) -> dict:
    try:
        return Trace.load(path / "trace.json") or {}
    except (OSError, ValueError):
        return {}


def recorded_jobs(path: Path, spans: dict) -> list[str]:
    """The jobs of a run; a run in progress has not recorded its own."""
    return spans["jobs"] if "jobs" in spans else job_names(load_trace(path))


def charging_pool(runs: dict[Path, dict]) -> dict:
    """The sessions that compete for a server: those of every run but the
    imports, and those of jobs that belong to no run, such as experiments."""
    referenced = {
        job for path, spans in runs.items() for job in recorded_jobs(path, spans)
    }
    pool = {
        path: spans for path, spans in runs.items() if not is_import(path, spans)
    }
    pool[ROOT / "untraced"] = {
        "server": UNTRACED_SERVER,
        "sessions": [
            session
            for store in job_stores()
            for job_dir in store.iterdir()
            if job_dir.name not in referenced
            for session in job_sessions(job_dir)
        ],
    }
    return pool


def refresh(result_dir: Path, trace: dict) -> None:
    """Record a run's evidence and charge it and the runs it overlaps."""
    result_dir = Path(result_dir).resolve()
    spans = write_spans(result_dir, trace)
    if not spans["server"]:
        write_usage(result_dir, spans, [])
        return
    runs = {path: read_json(path / SPANS_FILE) for path in peers(result_dir)}
    runs[result_dir] = spans
    pool = charging_pool(runs)
    charge(result_dir, spans, pool)
    for path, other in overlapping(spans, pool).items():
        if path in runs:
            charge(path, other, pool)


def hardware_of(server: str, started: str) -> dict:
    """The hardware table's entry for a server at a run's start date."""
    entries = read_json(HARDWARE).get(server) or []
    dated = [entry for entry in entries if entry["since"] <= started]
    return max(dated, key=lambda entry: entry["since"], default={})


def usage_of(result_dir: Path) -> dict:
    """GPU time and cost of a run: `state` is "none" for a run that made no
    AIProver call, "unrecorded" for one whose server activity was not sampled
    or whose hardware is not listed, "partial" when part of it was sampled
    or its server belongs to a pool, so that the figures are lower bounds,
    "measured" otherwise, and empty before the run's usage is computed."""
    usage = read_json(Path(result_dir) / USAGE_FILE)
    if not usage:
        return {"state": ""}
    if not usage["server"] or not usage["solver_calls"]:
        return {"state": "none"}
    hardware = hardware_of(usage["server"], usage["started"])
    if not usage["recorded"] or not hardware:
        return {"state": "unrecorded"}
    gpu_hours = usage["charged_hours"] * hardware["gpus"]
    exact = usage["coverage"] >= MIN_COVERAGE and not usage.get("pooled")
    return {
        "state": "measured" if exact else "partial",
        "gpu": hardware["gpu"],
        "gpus": hardware["gpus"],
        "active_hours": usage["charged_hours"],
        "gpu_hours": round(gpu_hours, 2),
        "usd_per_gpu_hour": hardware["usd_per_gpu_hour"],
        "gpu_cost_usd": round(gpu_hours * hardware["usd_per_gpu_hour"], 2),
    }


def refresh_all(roots: list[Path]) -> None:
    """Recompute every run under `roots` from its trace and metrics."""
    runs = {
        path: write_spans(path, trace)
        for root in roots
        if readable(root)
        for path in sorted(root.iterdir())
        if (trace := load_trace(path))
    }
    pool = charging_pool(runs)
    for path, spans in runs.items():
        charge(path, spans, pool)


if __name__ == "__main__":
    refresh_all(
        [Path(name) for name in sys.argv[1:]] or account_dirs("results")
    )

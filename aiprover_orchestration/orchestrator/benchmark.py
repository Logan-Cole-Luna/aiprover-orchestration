"""Performance metrics of a run, for benchmarking the system.

During a run, `MetricsSampler` appends a sample every `interval` seconds to
results/<run_id>/metrics.jsonl: the model server's vLLM metrics (gauges,
counters and histogram sums and counts, summed over label sets) and the
load of this machine. After the run, `summarize` combines these samples
with the trace into benchmark.json:

- server: requests, prompt and generated tokens, throughput, running and
  waiting requests, KV-cache use, mean context length, queue time, time to
  first token, request latency, preemptions, prefix-cache hit rate,
  speculative-decoding acceptance (when the server speculates);
- sessions: AIProver sessions (wall time, turns, tool calls, endings) and
  their model turns as the reasoning proxy recorded them (context length,
  latency, requests in flight);
- calls and Lean checks per role and phase, with their durations;
- machine: load average, available memory, Lean processes.

The server counters cover every client of the endpoint; they describe the
run only when it is the endpoint's sole client.

    python -m aiprover_orchestration.orchestrator.benchmark results/<run_id>
rewrites benchmark.json of a finished run.
"""

import json
import math
import os
import re
import statistics
import sys
import threading
import time
import urllib.request
from pathlib import Path

import psutil

SAMPLE_SECONDS = 30


def parse_prometheus(text: str) -> dict[str, float]:
    """vLLM metrics by name, summed over label sets; histogram buckets and
    creation times are left out."""
    values: dict[str, float] = {}
    for line in text.splitlines():
        if not line.startswith("vllm:"):
            continue
        name = line.split("{", 1)[0].split(" ", 1)[0]
        if name.endswith(("_bucket", "_created")):
            continue
        try:
            value = float(line.rsplit(" ", 1)[1])
        except ValueError:
            continue
        values[name] = values.get(name, 0.0) + value
    return values


def machine_load() -> dict:
    """Load average, available memory and Lean processes of this machine."""
    lean_processes = sum(
        process.info["name"] == "lean"
        for process in psutil.process_iter(["name"])
    )
    return {
        "load1": os.getloadavg()[0],
        "mem_available_gb": psutil.virtual_memory().available / 2**30,
        "lean_processes": lean_processes,
    }


class MetricsSampler(threading.Thread):
    """Appends a metrics sample to `path` every `interval` seconds."""

    def __init__(
        self,
        path: Path,
        metrics_url: str | None,
        interval: float = SAMPLE_SECONDS,
    ):
        super().__init__(name="metrics-sampler", daemon=True)
        self.path = Path(path)
        self.metrics_url = metrics_url
        self.interval = interval
        self._stopped = threading.Event()

    def run(self) -> None:
        while True:
            self.sample()
            if self._stopped.wait(self.interval):
                return

    def sample(self) -> None:
        record = {"time": time.time(), "machine": machine_load()}
        if self.metrics_url:
            try:
                with urllib.request.urlopen(
                    self.metrics_url, timeout=10
                ) as reply:
                    record["server"] = parse_prometheus(reply.read().decode())
            except OSError as error:
                record["server_error"] = str(error)[:200]
        with open(self.path, "a") as samples:
            samples.write(json.dumps(record) + "\n")

    def stop(self) -> None:
        self._stopped.set()
        self.join(timeout=15)
        self.sample()


# Summaries -----------------------------------------------------------------


def describe(values: list[float]) -> dict | None:
    """Count, mean, median, 90th percentile and maximum of `values`."""
    values = sorted(value for value in values if value is not None)
    if not values:
        return None
    return {
        "count": len(values),
        "mean": round(statistics.fmean(values), 3),
        "median": round(statistics.median(values), 3),
        # Nearest-rank percentile.
        "p90": round(values[math.ceil(0.9 * len(values)) - 1], 3),
        "max": round(values[-1], 3),
    }


def increase(samples: list[dict], name: str) -> float:
    """Growth of a server counter across samples; a server restart (counter
    reset) adds the counter's new value instead of a negative step."""
    total, previous = 0.0, None
    for sample in samples:
        value = sample["server"].get(name)
        if value is None:
            continue
        if previous is not None:
            total += value - previous if value >= previous else value
        previous = value
    return total


def ratio(numerator: float, denominator: float) -> float | None:
    return round(numerator / denominator, 3) if denominator else None


def server_summary(samples: list[dict]) -> dict | None:
    samples = [sample for sample in samples if "server" in sample]
    if len(samples) < 2:
        return None
    seconds = samples[-1]["time"] - samples[0]["time"]
    grow = {name: increase(samples, name) for name in samples[-1]["server"]}

    def mean_of(histogram: str) -> float | None:
        return ratio(
            grow.get(f"vllm:{histogram}_sum", 0.0),
            grow.get(f"vllm:{histogram}_count", 0.0),
        )

    def gauge(name: str) -> dict | None:
        return describe(
            [sample["server"].get(f"vllm:{name}") for sample in samples]
        )

    generated = grow.get("vllm:generation_tokens_total", 0.0)
    drafts = grow.get("vllm:spec_decode_num_drafts_total", 0.0)
    accepted = grow.get("vllm:spec_decode_num_accepted_tokens_total", 0.0)
    return {
        "sampled_seconds": round(seconds, 1),
        "requests": grow.get("vllm:request_success_total"),
        "prompt_tokens": grow.get("vllm:prompt_tokens_total"),
        "generation_tokens": generated,
        "generation_tokens_per_second": ratio(generated, seconds),
        "requests_running": gauge("num_requests_running"),
        "requests_waiting": gauge("num_requests_waiting"),
        "kv_cache_usage": gauge("kv_cache_usage_perc"),
        "mean_context_tokens": mean_of("request_prompt_tokens"),
        "mean_generation_tokens": mean_of("request_generation_tokens"),
        "mean_queue_seconds": mean_of("request_queue_time_seconds"),
        "mean_time_to_first_token_seconds": mean_of(
            "time_to_first_token_seconds"
        ),
        "mean_inter_token_seconds": mean_of("inter_token_latency_seconds"),
        "mean_request_seconds": mean_of("request_inference_time_seconds"),
        "preemptions": grow.get("vllm:num_preemptions_total"),
        "prefix_cache_hit_rate": ratio(
            grow.get("vllm:prefix_cache_hits_total", 0.0),
            grow.get("vllm:prefix_cache_queries_total", 0.0),
        ),
        # Accepted draft tokens per proposed draft token, and tokens
        # emitted per speculative step (accepted drafts plus the verified
        # token).
        "spec_acceptance_rate": ratio(
            accepted, grow.get("vllm:spec_decode_num_draft_tokens_total", 0.0)
        ),
        "spec_tokens_per_step": (
            round(1 + accepted / drafts, 3) if drafts else None
        ),
    }


# A tool name; other names are text of malformed tool calls in the model's
# output, counted together.
TOOL_NAME = re.compile(r"[A-Za-z][\w.-]{0,63}")


def tool_counts(session: dict) -> dict[str, int]:
    """Tool calls of an AIProver session by tool name."""
    counts = session.get("tool_calls") or {}
    if not isinstance(counts, dict):
        return {"all": counts}
    named: dict[str, int] = {}
    for name, count in counts.items():
        key = name if TOOL_NAME.fullmatch(name) else "(malformed call)"
        named[key] = named.get(key, 0) + count
    return named


def session_summary(steps: list[dict]) -> dict | None:
    sessions = [
        sample for step in steps for sample in step.get("samples") or []
    ]
    if not sessions:
        return None
    turns = [
        turn for session in sessions for turn in session.get("reasoning") or []
    ]
    endings: dict[str, int] = {}
    tool_totals: dict[str, int] = {}
    for session in sessions:
        for name, count in tool_counts(session).items():
            tool_totals[name] = tool_totals.get(name, 0) + count
        ending = session.get("ending") or "unknown"
        endings[ending] = endings.get(ending, 0) + 1
    return {
        "sessions": len(sessions),
        "jobs": sum(bool(step.get("samples")) for step in steps),
        "session_seconds": describe([s.get("elapsed_sec") for s in sessions]),
        "turns_per_session": describe([s.get("turns") for s in sessions]),
        "tool_calls_per_session": describe(
            [sum(tool_counts(s).values()) for s in sessions]
        ),
        "tool_calls_by_name": tool_totals,
        "endings": endings,
        "verified_sessions": sum(
            s.get("status") == "verified" for s in sessions
        ),
        "turn_context_tokens": describe(
            [turn.get("prompt_tokens") for turn in turns]
        ),
        "turn_seconds": describe([turn.get("seconds") for turn in turns]),
        "requests_in_flight": describe(
            [turn.get("in_flight") for turn in turns]
        ),
        "truncated_turns": sum(bool(turn.get("truncated")) for turn in turns),
    }


def call_summary(steps: list[dict]) -> dict:
    """Model calls by role and Lean checks, with their durations."""
    by_role: dict[str, list[dict]] = {}
    for step in steps:
        if step["kind"] == "model_call":
            by_role.setdefault(step["role"], []).append(step)
    checks = [step for step in steps if step["kind"] == "lean_check"]
    return {
        "model_calls": {
            role: {
                "seconds": describe([call.get("seconds") for call in calls]),
                "cost_usd": round(
                    sum(call.get("cost_usd") or 0.0 for call in calls), 4
                ),
            }
            for role, calls in by_role.items()
        },
        "lean_checks": {
            "seconds": describe([c.get("seconds") for c in checks]),
            "passed": sum(bool(c.get("ok")) for c in checks),
        },
    }


def call_purpose(step: dict) -> str:
    """What a call was for: its label without the lemma, sample or repair
    number (`handback/mc_flat` and `report_repair0` give `handback` and
    `report_repair`)."""
    return re.sub(r"\d+$", "", step.get("label", "").split("/")[0])


def run_costs(trace: dict) -> dict:
    """Hosted model costs and the GPU time of a run, from its trace, which
    leaves out refused calls. Input tokens are left out: they are reported
    without the cached prompt.

    GPU time is attributed by session slots: a model server with `gpus`
    GPUs serves `aiprover_session_slots` sessions, so a session-hour counts
    as gpus / slots GPU-hours."""
    from ..server.vista import group_of_solver

    steps = trace.get("steps", [])
    hosted: dict[tuple, dict] = {}
    sessions = []
    jobs = 0
    for step in steps:
        if step["kind"] != "model_call":
            continue
        if step.get("backend") == "aiprover":
            jobs += 1
            # Sessions cut off by a lost model server are not counted.
            sessions += [
                sample
                for sample in step.get("samples") or []
                if sample.get("status") != "infra"
            ]
            continue
        usage = step.get("usage") or {}
        key = (step.get("role"), step.get("model"), call_purpose(step))
        row = hosted.setdefault(
            key,
            {
                "role": key[0],
                "model": key[1],
                "purpose": key[2],
                "calls": 0,
                "output_tokens": 0,
                "cost_usd": 0.0,
            },
        )
        row["calls"] += 1
        row["output_tokens"] += int(usage.get("outputTokens") or 0)
        row["cost_usd"] += float(step.get("cost_usd") or 0.0)
    config = trace.get("config") or {}
    solver = (config.get("agents") or {}).get("solver") or {}
    group = group_of_solver(solver.get("config") or "")
    slots = config.get("aiprover_session_slots") or 0
    session_hours = sum(
        float(session.get("elapsed_sec") or 0) for session in sessions
    ) / 3600
    gpu_hours = (
        session_hours * group.gpus / slots
        if group and group.gpus and slots
        else None
    )
    return {
        "hosted": sorted(hosted.values(), key=lambda row: -row["cost_usd"]),
        "hosted_cost_usd": round(
            sum(row["cost_usd"] for row in hosted.values()), 4
        ),
        "solver": {
            "model": solver.get("model"),
            "jobs": jobs,
            "sessions": len(sessions),
            "session_hours": round(session_hours, 2),
            "gpu": group.gpu if group else "",
            "gpus_per_server": group.gpus if group else 0,
            "sessions_per_server": slots,
            "gpu_hours": None if gpu_hours is None else round(gpu_hours, 2),
        },
    }


def usage_by_model(costs: dict) -> dict:
    """Tokens and cost per hosted model."""
    totals: dict[str, dict] = {}
    for row in costs["hosted"]:
        total = totals.setdefault(
            row["model"],
            {"output_tokens": 0, "cost_usd": 0.0},
        )
        for key in total:
            total[key] += row[key]
    return totals


def machine_summary(samples: list[dict]) -> dict | None:
    if not samples:
        return None
    return {
        key: describe([sample["machine"][key] for sample in samples])
        for key in samples[0]["machine"]
    }


def read_samples(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text().splitlines()
        if line.strip()
    ]


def summarize(result_dir: Path, trace: dict) -> dict:
    """The benchmark summary of a run; written to benchmark.json."""
    samples = read_samples(Path(result_dir) / "metrics.jsonl")
    steps = trace.get("steps", [])
    outcome = trace.get("outcome") or {}
    costs = run_costs(trace)
    summary = {
        "run_id": trace.get("run_id"),
        "status": outcome.get("status"),
        "wall_seconds": outcome.get("wall_seconds"),
        "phase_seconds": outcome.get("phase_seconds"),
        "usage_by_model": usage_by_model(costs),
        "costs": costs,
        "server": server_summary(samples),
        "sessions": session_summary(steps),
        **call_summary(steps),
        "machine": machine_summary(samples),
    }
    (Path(result_dir) / "benchmark.json").write_text(
        json.dumps(summary, indent=1)
    )
    return summary


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit(__doc__.strip().splitlines()[-2].strip())
    result_dir = Path(sys.argv[1])
    trace = json.loads((result_dir / "trace.json").read_text())
    print(json.dumps(summarize(result_dir, trace), indent=1))

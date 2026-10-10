"""Copy run result directories into a repository for sharing.

    python -m aiprover_orchestration.utils.share_results \\
        --target ~/workspace/orchestration-results --match OpenAIMath

Of each problem's runs that match, the latest is copied: to
completed_problems/<run_id>/ once the run has finished, otherwise to
ip_problems/<run_id>/. A copy holds the trace gzipped (trace.json.gz, under
the per-file size limit of git hosts), the run's config as config.json,
trace.html rendered from the copied trace, and the remaining result files;
trace_replay.html is left out, being rendered from the trace. Error texts of
failed model calls are reduced to their HTTP status, since they quote
provider messages verbatim, and the strings of a redaction file
(`--redactions`, if it exists) are replaced everywhere: one per line, either
`text` (replaced by "[redacted]") or `text==>replacement`. The target's
README.md indexes the copies and states how to resume one.
"""

import argparse
import gzip
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from ..orchestrator.trace_view.render import render
from ..paths import RESULTS_DIR
from ..server.jobs import JobStore

COMPLETED = "completed_problems"
IN_PROGRESS = "ip_problems"
EXCLUDED = {"trace.json", "trace.html", "trace_replay.html"}
# The provider's message after a failed call's exit status.
PROVIDER_MESSAGE = re.compile(r"exit -?\d+ \(api_status=(\w+)\): .*", re.S)
DEFAULT_REDACTIONS = Path("~/.config/aiprover/share_redactions.txt")
# (text, replacement) pairs applied to every shared text (set by `main`).
redactions: list[tuple[str, str]] = []

README = """# orchestration-results

Runs of the AIProver orchestration
([aiprover-orchestration](https://github.com/Logan-Cole-Luna/aiprover-orchestration)).
`{completed}/` holds finished runs, `{in_progress}/` runs in progress (synced
every 15 min). Each directory `<run_id>/` is the run's result directory:

| File | Content |
|---|---|
| `trace.json.gz` | every step: model calls, Lean checks, decisions |
| `config.json` | the run's orchestrator config |
| `summary.json` | status, checks, timings, calls and cost |
| `library.lean` | the library the run started from (runs with one) |
| `trace.html` | the trace as a page |
| `*.lean`, `report/` | solution modules and report (finished runs) |

## Resuming a run

The trace records the problem, the config and every completed step, so a run
continues from its last step without the original dataset or files:

```
cp -r {in_progress}/<run_id> <aiprover_orchestration>/results/<run_id>
cd <aiprover_orchestration>
python -m aiprover_orchestration.orchestrator.run --run-id <run_id> --resume \\
    [--config my_config.json]
```

Requirements: the aiprover_orchestration repository with its Lean workspace
(Lean v4.23.0, Mathlib), a Claude API key, and an AIProver model endpoint. The
recorded config names our solver config (`agents.solver.config`); to use
another endpoint, copy `config.json`, point `agents.solver.config` to an
AIProver config of your own and pass it with `--config`. AIProver jobs that
were in progress on our machine are submitted afresh.

## Runs

| Run | Problem | State | Status | Wall time (h) |
|---|---|---|---|---|
{rows}
"""


def latest_runs(match: str) -> dict[str, Path]:
    """The latest result directory of each problem whose run id contains
    `match`; run ids are `srv_<date>_<time>_<problem>`."""
    latest: dict[str, Path] = {}
    for run in sorted(RESULTS_DIR.glob("srv_*")):
        if match in run.name and (run / "trace.json").exists():
            latest[run.name.split("_", 3)[3]] = run
    return latest


def provider_status(match: re.Match) -> str:
    return f"HTTP {match[1]}" if match[1].isdigit() else "request failed"


def clean(value):
    """`value` with the error texts of failed calls reduced to their HTTP
    status and the redactions applied."""
    if isinstance(value, dict):
        return {
            key: (
                clean(PROVIDER_MESSAGE.sub(provider_status, item))
                if key == "error" and isinstance(item, str)
                else clean(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [clean(item) for item in value]
    if isinstance(value, str):
        return redact(value)
    return value


def redact(text: str) -> str:
    for old, new in redactions:
        text = text.replace(old, new)
    return text


def copy_if_newer(source: Path, target: Path) -> None:
    """Copy `source` unless `target` is as new; JSON files are cleaned."""
    if target.exists() and target.stat().st_mtime >= source.stat().st_mtime:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    if source.suffix == ".json":
        document = clean(json.loads(source.read_text()))
        target.write_text(json.dumps(document, indent=1) + "\n")
    elif source.suffix == ".jsonl":
        lines = source.read_text().splitlines()
        target.write_text(
            "".join(json.dumps(clean(json.loads(line))) + "\n" for line in lines)
        )
    else:
        try:
            target.write_text(redact(source.read_text()))
        except UnicodeDecodeError:
            shutil.copy2(source, target)


def share_run(run: Path, target: Path) -> None:
    """Bring the copy of `run` at `target` up to date."""
    for source in run.rglob("*"):
        relative = source.relative_to(run)
        if source.is_file() and str(relative) not in EXCLUDED:
            copy_if_newer(source, target / relative)
    trace = run / "trace.json"
    packed = target / "trace.json.gz"
    if packed.exists() and packed.stat().st_mtime >= trace.stat().st_mtime:
        return
    document = clean(json.loads(trace.read_text()))
    text = json.dumps(document).encode()
    packed.write_bytes(gzip.compress(text, compresslevel=6))
    (target / "config.json").write_text(
        json.dumps(document["config"], indent=1) + "\n"
    )
    with tempfile.TemporaryDirectory() as scratch:
        cleaned = Path(scratch) / "trace.json"
        cleaned.write_bytes(text)
        render(cleaned, Path(scratch))
        shutil.copy(Path(scratch) / "trace.html", target / "trace.html")
    mtime = trace.stat().st_mtime
    os.utime(packed, (mtime, mtime))


def index_row(run: Path, job: dict | None) -> str:
    summary_path = run / "summary.json"
    summary = (
        json.loads(summary_path.read_text()) if summary_path.exists() else {}
    )
    wall = summary.get("wall_seconds")
    return (
        f"| `{run.name}` | {run.name.split('_', 3)[3]} "
        f"| {(job or {}).get('state', '')} "
        f"| {(job or {}).get('status') or ''} "
        f"| {f'{wall / 3600:.1f}' if wall else ''} |"
    )


def share(target: Path, match: str) -> None:
    store = JobStore()
    rows = []
    for run in latest_runs(match).values():
        job = store.get(run.name)
        finished = job is not None and job["state"] == "finished"
        place, other = (
            (COMPLETED, IN_PROGRESS) if finished else (IN_PROGRESS, COMPLETED)
        )
        share_run(run, target / place / run.name)
        shutil.rmtree(target / other / run.name, ignore_errors=True)
        rows.append(index_row(run, job))
    (target / "README.md").write_text(
        README.format(
            completed=COMPLETED, in_progress=IN_PROGRESS, rows="\n".join(rows)
        )
    )
    print(f"{time.strftime('%H:%M:%S')} shared {len(rows)} runs to {target}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--match", default="OpenAIMath")
    parser.add_argument(
        "--redactions", type=Path, default=DEFAULT_REDACTIONS
    )
    args = parser.parse_args()
    path = args.redactions.expanduser()
    if path.exists():
        for line in filter(None, path.read_text().splitlines()):
            old, _, new = line.partition("==>")
            redactions.append((old, new if _ else "[redacted]"))
    share(args.target.expanduser(), args.match)


if __name__ == "__main__":
    main()

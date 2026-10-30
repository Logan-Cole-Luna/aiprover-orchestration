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
(`--redactions`, if it exists) are replaced everywhere, names included: one
per line, either `text` (replaced by "[redacted]") or `text==>replacement`.
Binary files, which cannot be redacted, are left out of a run whose trace
holds such a string. Model calls left
out of traces (`trace.without_untraced_calls`) are removed from the shared
trace. The target's README.md indexes the copies and states how to resume
one.
"""

import argparse
import gzip
import json
import mmap
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

from ..orchestrator.trace import without_untraced_calls
from ..orchestrator.trace_view.render import render
from ..paths import RESULTS_DIR
from ..server.jobs import JobStore

COMPLETED = "completed_problems"
IN_PROGRESS = "ip_problems"
EXCLUDED = {"trace.json", "trace.html", "trace_replay.html"}
# Fields that quote a failed model call: its error, and the diagnosis of a
# hand-back the model refused. A model call's error, or one naming the call
# (`<backend> call failed (<role>/<phase>): ...`), quotes the provider's
# message verbatim; a shared copy keeps the call and the HTTP status.
ERROR_FIELDS = {"error", "diagnosis"}
FAILED_CALL = re.compile(r"^\S+ call failed \([^)]*\)")
HTTP_STATUS = re.compile(r"\b[45]\d\d\b")
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


def run_problem(run_id: str) -> str:
    """The problem of a run id `srv_<date>_<time>_<problem>`; a named or
    imported run stands for itself."""
    return re.sub(r"^srv_\d{8}_\d{6}_", "", run_id)


def latest_runs(match: str) -> dict[str, Path]:
    """The latest result directory of each problem whose run id contains
    `match`."""
    latest: dict[str, Path] = {}
    for run in sorted(RESULTS_DIR.iterdir()):
        if match in run.name and (run / "trace.json").exists():
            latest[run_problem(run.name)] = run
    return latest


def reduced_error(text: str) -> str:
    """A failed call's error as the call and its outcome: the HTTP status,
    a refusal, or a failed request."""
    call = FAILED_CALL.match(text)
    message = text[call.end():] if call else text
    status = HTTP_STATUS.search(message)
    if status:
        outcome = f"HTTP {status[0]}"
    elif "refus" in message.lower() or "safeguards" in message.lower():
        outcome = "refused"
    else:
        outcome = "request failed"
    return f"{call[0]}: {outcome}" if call else outcome


def clean(value):
    """`value` with the error texts of failed calls reduced (`reduced_error`)
    and the redactions applied."""
    if isinstance(value, dict):
        model_call = value.get("kind") == "model_call"
        return {
            redact(key): (
                clean(reduced_error(item))
                if key in ERROR_FIELDS
                and isinstance(item, str)
                and item
                and (model_call or FAILED_CALL.match(item))
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


def is_binary(path: Path) -> bool:
    """True if `path` is not text; JSON files parse as text when copied."""
    if path.suffix in (".json", ".jsonl"):
        return False
    try:
        path.read_text()
    except UnicodeDecodeError:
        return True
    return False


def holds_redaction(path: Path) -> bool:
    if not redactions:
        return False
    with open(path, "rb") as file, mmap.mmap(
        file.fileno(), 0, access=mmap.ACCESS_READ
    ) as content:
        return any(content.find(old.encode()) >= 0 for old, _ in redactions)


def share_run(run: Path, target: Path) -> None:
    """Bring the copy of `run` at `target` up to date."""
    trace = run / "trace.json"
    private = holds_redaction(trace)
    for source in run.rglob("*"):
        relative = source.relative_to(run)
        copy = target / redact(str(relative))
        # A `.tmp` file is a trace being rewritten by a running run.
        if (
            not source.is_file()
            or str(relative) in EXCLUDED
            or source.suffix == ".tmp"
        ):
            continue
        if private and is_binary(source):
            copy.unlink(missing_ok=True)
        else:
            copy_if_newer(source, copy)
    packed = target / "trace.json.gz"
    if packed.exists() and packed.stat().st_mtime >= trace.stat().st_mtime:
        return
    document = clean(without_untraced_calls(json.loads(trace.read_text())))
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
        f"| `{run.name}` | {run_problem(run.name)} "
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
        shared_name = redact(run.name)
        share_run(run, target / place / shared_name)
        shutil.rmtree(target / other / shared_name, ignore_errors=True)
        rows.append(redact(index_row(run, job)))
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

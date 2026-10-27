"""Step-by-step record of an orchestration run, for later replay and display.

The trace is one JSON document: a header (problem, models, configuration,
algorithm description, system prompts) followed by an ordered list of steps.
Each step is a model call, a Lean check, or an orchestration decision. The
file is rewritten atomically, in batches of steps at most WRITE_SECONDS
apart and at once when the run finishes, so it can be inspected while the
run is in progress.

A model call that failed on the account's usage limit, or that the
provider's safety classifier refused, records the account or the provider,
not the run: it is not traced (the call log keeps it), and a continued trace
drops such calls recorded earlier (`without_untraced_calls`).

    python -m aiprover_orchestration.orchestrator.trace results/<run_id> ...

removes them from the traces of runs that are not in progress; `--rewind N`
also drops the steps from index N on (`rewound`), and `--refused-handbacks`
the hand-backs the captain's model refused.
"""

import argparse
import atexit
import gzip
import json
import re
import threading
import time
from pathlib import Path

from ..utils.files import write_atomic

# Interval between writes of a running trace. A write serializes the whole
# document, hundreds of MB for a long run, so it happens in the background
# for the steps added meanwhile; a killed process loses at most one
# interval of steps.
WRITE_SECONDS = 15.0

# Error text of a call refused by the account's session or usage limit.
USAGE_LIMIT = re.compile(r"(?:session|usage|weekly) limit", re.IGNORECASE)


# Error text of a request declined by the model's safety classifier.
REFUSAL_MARKER = "safeguards flagged"


def is_usage_limit(error: str | None) -> bool:
    return bool(error) and bool(USAGE_LIMIT.search(error))


def is_untraced(error: str | None, refused: bool = False) -> bool:
    """A failed call that is left out of the trace: a usage limit or a
    refusal by the provider's safety classifier."""
    return refused or is_usage_limit(error) or REFUSAL_MARKER in (error or "")


def _without_limit_error(outcome: dict | None) -> dict | None:
    if outcome and is_usage_limit(outcome.get("error")):
        return {**outcome, "error": "", "error_kind": ""}
    return outcome


def _without_steps(document: dict, removed: set[int]) -> dict:
    """The trace document without the steps at the given positions: steps
    renumbered and resumption positions shifted."""
    steps = document.get("steps", [])
    kept, removed_before = [], []
    for position, step in enumerate(steps):
        removed_before.append(position - len(kept))
        if position not in removed:
            kept.append({**step, "index": len(kept)})

    def shift(position: int) -> int:
        if position < len(removed_before):
            return position - removed_before[position]
        return position - (len(steps) - len(kept))

    resumptions = [
        {**resumption, "at_step": shift(resumption["at_step"])}
        for resumption in document.get("resumptions", [])
    ]
    return {**document, "steps": kept, "resumptions": resumptions}


def without_untraced_calls(document: dict) -> dict:
    """The trace document without model calls that failed on a usage limit
    or were refused, and with outcomes stripped of usage-limit errors."""
    cleaned = _without_steps(
        document,
        {
            position
            for position, step in enumerate(document.get("steps", []))
            if step["kind"] == "model_call"
            and is_untraced(step.get("error"), bool(step.get("refused")))
        },
    )
    for resumption in cleaned["resumptions"]:
        resumption["previous_outcome"] = _without_limit_error(
            resumption.get("previous_outcome")
        )
    cleaned["outcome"] = _without_limit_error(document.get("outcome"))
    return cleaned


def rewound(document: dict, at_step: int) -> dict:
    """The trace document as it stood before step `at_step`, for a run whose
    later steps an infrastructure failure made worthless. AIProver jobs
    still running at that point are dropped too, so that a resume starts
    their lemmas afresh instead of continuing their sessions."""
    steps = document.get("steps", [])
    finished = {
        step.get("aiprover_job")
        for step in steps[:at_step]
        if step["kind"] == "model_call"
    }
    removed = set(range(at_step, len(steps))) | {
        position
        for position, step in enumerate(steps[:at_step])
        if step.get("event") == "aiprover_job_started"
        and step["aiprover_job"] not in finished
    }
    document = {
        **document,
        "resumptions": [
            resumption
            for resumption in document.get("resumptions", [])
            if resumption["at_step"] <= at_step
        ],
    }
    return _without_steps(document, removed)


def without_refused_handbacks(document: dict) -> dict:
    """The trace document without hand-backs the captain's model refused, so
    that a resume hands those lemmas back again."""
    return _without_steps(
        document,
        {
            position
            for position, step in enumerate(document.get("steps", []))
            if step.get("event") == "lemma_handback"
            and step.get("action") == "refused"
        },
    )


def without_jobs(document: dict, jobs: set[str]) -> dict:
    """The trace document without the given AIProver jobs, for jobs whose
    failure was the infrastructure's: their start, call, end decisions and
    the Lean checks of their samples (which follow the call)."""
    steps = document.get("steps", [])
    removed, open_lemmas = set(), set()
    for position, step in enumerate(steps):
        lemma = step.get("lemma")
        if step.get("aiprover_job") in jobs:
            removed.add(position)
            if step["kind"] == "model_call":
                open_lemmas.add(lemma)
            else:
                open_lemmas.discard(lemma)
        elif step["kind"] == "lean_check" and lemma in open_lemmas:
            removed.add(position)
        elif lemma in open_lemmas:
            open_lemmas.discard(lemma)
    return _without_steps(document, removed)


class Trace:

    def __init__(self, path: Path, header: dict, previous: dict | None = None):
        """Start a trace, or continue `previous` (a loaded trace document).

        A continued trace keeps its steps and its clock: elapsed times resume
        from the last recorded step, and each resumption is listed in
        `resumptions`.
        """
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()  # guards the document
        self._write_lock = threading.Lock()  # one write at a time
        self._pending = threading.Event()  # steps not yet written
        self._closed = False
        self.stage = "setup"  # coarse pipeline phase, set by the pipeline
        if previous is None:
            self._start = time.time()
            self.document = {
                **header,
                "started": time.strftime("%Y-%m-%dT%H:%M:%S"),
                "steps": [],
                "outcome": None,
            }
        else:
            previous = without_untraced_calls(previous)
            steps = previous.get("steps", [])
            elapsed = steps[-1]["elapsed_seconds"] if steps else 0.0
            self._start = time.time() - elapsed
            resumptions = previous.get("resumptions", []) + [
                {
                    "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    "at_step": len(steps),
                    "elapsed_seconds": elapsed,
                    "previous_outcome": previous.get("outcome"),
                }
            ]
            self.document = {
                **previous,
                **header,
                "started": previous.get("started"),
                "steps": steps,
                "outcome": None,
                "resumptions": resumptions,
            }
        self.flush()
        threading.Thread(
            target=self._write_batches, name="trace-writer", daemon=True
        ).start()
        atexit.register(self.flush)

    @staticmethod
    def load(path: Path) -> dict | None:
        """The trace document at `path`, its gzip copy `<path>.gz` (as
        shared result directories hold it), or the line record
        `trace.jsonl` of an interactive run; None if none exists."""
        path = Path(path)
        packed = path.with_name(path.name + ".gz")
        lines = path.with_suffix(".jsonl")
        if path.suffix != ".jsonl" and path.exists():
            return json.loads(path.read_text())
        if packed.exists():
            return json.loads(gzip.decompress(packed.read_bytes()))
        if lines.exists():
            return Trace.from_lines(lines)
        return None

    @staticmethod
    def from_lines(path: Path) -> dict:
        """The trace document of a line record, as written by the
        `aiprover-captain` skill: the header on the first line, then one
        step per line. A line without `kind` updates the header (`outcome`,
        `finished`). Steps are indexed in order; a step's `time` (Unix
        seconds) gives its `elapsed_seconds` from the header's `time`."""
        header, *records = [
            json.loads(line)
            for line in Path(path).read_text().splitlines()
            if line.strip()
        ]
        document = {"steps": [], "outcome": None, **header}
        for record in records:
            if "kind" not in record:
                document.update(record)
                continue
            step = {"stage": "", **record, "index": len(document["steps"])}
            if "elapsed_seconds" not in step:
                elapsed = step.get("time", header["time"]) - header["time"]
                step["elapsed_seconds"] = round(elapsed, 1)
            document["steps"].append(step)
        return document

    @property
    def elapsed_seconds(self) -> float:
        return round(time.time() - self._start, 1)

    def add(self, kind: str, **data) -> int:
        """Append a step, tagged with the current stage, and return its index."""
        with self._lock:
            index = len(self.document["steps"])
            self.document["steps"].append(
                {
                    "index": index,
                    "kind": kind,
                    "stage": self.stage,
                    "elapsed_seconds": round(time.time() - self._start, 1),
                    **data,
                }
            )
        self._pending.set()
        return index

    def finish(self, outcome: dict) -> None:
        """Record the outcome and write the trace; later steps are kept in
        memory only, so that a finished trace is not overwritten (by a
        resumed run's trace of the same path)."""
        with self._lock:
            self.document["outcome"] = outcome
            self.document["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        self.flush()
        self._closed = True

    def flush(self) -> None:
        """Write the document now."""
        with self._write_lock:
            if self._closed:
                return
            self._pending.clear()
            with self._lock:
                document = {
                    **self.document,
                    "steps": list(self.document["steps"]),
                }
            write_atomic(
                self.path, json.dumps(document, indent=1, ensure_ascii=False)
            )

    def _write_batches(self) -> None:
        while not self._closed:
            self._pending.wait()
            time.sleep(WRITE_SECONDS)
            self.flush()


def main() -> None:
    """Repair the traces of runs not in progress (a running run rewrites its
    trace): usage-limit failures and refused calls are always removed."""
    parser = argparse.ArgumentParser(description=main.__doc__)
    parser.add_argument("directories", nargs="+", type=Path)
    parser.add_argument(
        "--rewind", type=int, help="drop the steps from this index on"
    )
    parser.add_argument(
        "--refused-handbacks",
        action="store_true",
        help="drop hand-backs the captain's model refused",
    )
    parser.add_argument(
        "--drop-jobs",
        nargs="+",
        default=[],
        metavar="JOB",
        help="drop AIProver jobs that failed on the infrastructure",
    )
    parser.add_argument(
        "--reinstate",
        action="store_true",
        help="return lemmas the last replan dropped although kept proofs "
        "use them",
    )
    args = parser.parse_args()
    for directory in args.directories:
        path = directory / "trace.json"
        document = Trace.load(path)
        if document is None:
            print(f"{directory}: no trace")
            continue
        repaired = without_untraced_calls(document)
        if args.rewind is not None:
            repaired = rewound(repaired, args.rewind)
        if args.refused_handbacks:
            repaired = without_refused_handbacks(repaired)
        if args.drop_jobs:
            repaired = without_jobs(repaired, set(args.drop_jobs))
        if args.reinstate:
            from .resume import with_reinstated

            repaired = with_reinstated(repaired)
        change = len(repaired["steps"]) - len(document["steps"])
        write_atomic(path, json.dumps(repaired, indent=1, ensure_ascii=False))
        print(f"{directory}: {change:+d} steps")


if __name__ == "__main__":
    main()

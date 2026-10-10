"""Step-by-step record of an orchestration run, for later replay and display.

The trace is one JSON document: a header (problem, models, configuration,
algorithm description, system prompts) followed by an ordered list of steps.
Each step is a model call, a Lean check, or an orchestration decision. The
file is rewritten atomically after every step so it can be inspected while
the run is in progress.

A model call that the provider's safety classifier refused records the
provider, not the run: it is not traced (the call log keeps it), and a
continued trace drops such calls recorded earlier (`without_untraced_calls`).

    python -m aiprover_orchestration.orchestrator.trace results/<run_id> ...

removes them from the traces of runs that are not in progress; `--rewind N`
also drops the steps from index N on (`rewound`), and `--refused-handbacks`
the hand-backs the captain's model refused.
"""

import argparse
import gzip
import json
import os
import threading
import time
from pathlib import Path

# Error text of a request declined by the model's safety classifier.
REFUSAL_MARKER = "safeguards flagged"


def is_untraced(error: str | None, refused: bool = False) -> bool:
    """A failed call that is left out of the trace: a refusal by the
    provider's safety classifier."""
    return refused or REFUSAL_MARKER in (error or "")


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
    """The trace document without refused model calls."""
    return _without_steps(
        document,
        {
            position
            for position, step in enumerate(document.get("steps", []))
            if step["kind"] == "model_call"
            and is_untraced(step.get("error"), bool(step.get("refused")))
        },
    )


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


class Trace:

    def __init__(self, path: Path, header: dict, previous: dict | None = None):
        """Start a trace, or continue `previous` (a loaded trace document).

        A continued trace keeps its steps and its clock: elapsed times resume
        from the last recorded step, and each resumption is listed in
        `resumptions`.
        """
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
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
        self._write()

    @staticmethod
    def load(path: Path) -> dict | None:
        """The trace document at `path`, or its gzip copy `<path>.gz` (as
        shared result directories hold it); None if neither exists."""
        path = Path(path)
        packed = path.with_name(path.name + ".gz")
        if path.exists():
            return json.loads(path.read_text())
        if packed.exists():
            return json.loads(gzip.decompress(packed.read_bytes()))
        return None

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
            self._write()
            return index

    def finish(self, outcome: dict) -> None:
        with self._lock:
            self.document["outcome"] = outcome
            self.document["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            self._write()

    def _write(self) -> None:
        temporary = self.path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(self.document, indent=1, ensure_ascii=False)
        )
        os.replace(temporary, self.path)


def main() -> None:
    """Repair the traces of runs not in progress (a running run rewrites its
    trace): refused calls are always removed."""
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
        removed = len(document["steps"]) - len(repaired["steps"])
        temporary = path.with_suffix(".json.tmp")
        temporary.write_text(
            json.dumps(repaired, indent=1, ensure_ascii=False)
        )
        os.replace(temporary, path)
        print(f"{directory}: {removed} steps removed")


if __name__ == "__main__":
    main()

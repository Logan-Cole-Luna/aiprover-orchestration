"""MathArena ArXivLean (https://matharena.ai/arxivlean/) for the orchestration:
dataset rows for prove mode, and a run's verified result as a MathArena
submission, graded with MathArena's comparator.

    bash benchmark/arxivlean_setup.sh             # once per machine
    python benchmark/arxivlean.py export results/<run_id>
    python benchmark/arxivlean.py grade results/<run_id>
    python benchmark/arxivlean.py rows <matharena_data>/arxivlean-<MMYY>.jsonl

The rows of the September 2026 edition are in benchmark/arxivlean/, beside
MathArena's single-session baseline. A row's formal statement is MathArena's
frozen definitions followed by
`theorem arxivlean : arxivleanConjecture := by sorry`; runs use `mode` prove
with `disproof` on, so that the captain may prove the negation instead. The
arXiv source of a problem is left out of its row.
"""

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL = Path.home() / "workspace" / "external"
MATHARENA = Path(os.environ.get("MATHARENA_DIR", EXTERNAL / "matharena"))
RUNTIME = Path(
    os.environ.get("MATHARENA_LEAN_RUNTIME_ROOT", EXTERNAL / "matharena_runtime")
)
CONJECTURE = "arxivleanConjecture"
THEOREM = f"\n\ntheorem arxivlean : {CONJECTURE} := by sorry\n"
# The run's theorem in its final file (orchestrator/config.py MAIN_NAME).
MAIN = "solution"
GRADE = (
    "import json, sys\n"
    "from matharena.lean_conjecture import check_submission\n"
    "text, formal = json.load(sys.stdin)\n"
    "print(json.dumps(check_submission(text, formal)))\n"
)


def dataset(edition: str) -> Path:
    return ROOT / "benchmark" / "arxivlean" / f"arxivlean_{edition}.jsonl"


def rows(source: Path) -> None:
    edition = re.search(r"\d{4}", source.stem).group()
    with open(source) as lines, open(dataset(edition), "w") as out:
        for line in lines:
            item = json.loads(line)
            index = int(item["problem_idx"])
            row = {
                "uuid": f"ArXivLean{edition}_{index:02d}",
                "source_name": "ArXivLean",
                "source_subset": f"MathArena ArXivLean {edition}, problem {index}",
                "informal_statement": item["problem"],
                "informal_proof": "",
                "formal_statement": item["formal_statement"].rstrip() + THEOREM,
            }
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(dataset(edition))


def submission(result: Path) -> str:
    """The run's verified proof, or its refutation found while sketching, as
    a file that proves or disproves the conjecture."""
    summary = json.loads((result / "summary.json").read_text())
    slug = summary["slug"]
    if summary.get("checks", {}).get("verified"):
        text = (result / f"{slug}_standalone.lean").read_text()
        negated = re.search(rf"^theorem {MAIN}\s*:\s*¬", text, re.M)
        ending = (
            f"theorem arxivlean_disproof : Not {CONJECTURE} := {MAIN}"
            if negated
            else f"theorem arxivlean_proof : {CONJECTURE} := {MAIN}"
        )
        return f"{text.rstrip()}\n\n{ending}\n"
    if summary.get("refutation"):
        # The file states the run's theorem with `sorry` and refutes it.
        text = (result / f"{slug}_refutation.lean").read_text()
        stated = re.search(rf"^theorem {MAIN}\s*:(.*?):= by sorry\n", text, re.M | re.S)
        claim = stated.group(1).strip()
        text = text.replace(stated.group(0), "")
        text = text.replace(f"type_of% @{MAIN}", f"({claim})")
        refuted = f"{MAIN}_refuted"
        ending = (
            f"theorem arxivlean_proof : {CONJECTURE} := "
            f"Classical.not_not.mp {refuted}"
            if claim.startswith("¬")
            else f"theorem arxivlean_disproof : Not {CONJECTURE} := {refuted}"
        )
        return f"{text.rstrip()}\n\n{ending}\n"
    raise SystemExit(f"{result.name}: no verified proof or refutation")


def export(result: Path) -> Path:
    path = result / "Solution.lean"
    path.write_text(submission(result))
    print(path)
    return path


def frozen_statement(uuid: str) -> str:
    edition = re.search(r"ArXivLean(\d{4})_", uuid).group(1)
    with open(dataset(edition)) as lines:
        for line in lines:
            row = json.loads(line)
            if row["uuid"] == uuid:
                return row["formal_statement"].removesuffix(THEOREM)
    raise SystemExit(f"{uuid} not in {dataset(edition)}")


def grade(result: Path) -> dict:
    """MathArena's verdict on the exported submission; the result is kept as
    `grade.json` beside it."""
    uuid = json.loads((result / "summary.json").read_text())["uuid"]
    text = export(result).read_text()
    checked = subprocess.run(
        [str(MATHARENA / ".venv-grade" / "bin" / "python"), "-c", GRADE],
        input=json.dumps([text, frozen_statement(uuid)]),
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PYTHONPATH": str(MATHARENA / "src"),
            "MATHARENA_LEAN_RUNTIME_ROOT": str(RUNTIME),
        },
        check=True,
    )
    verdict = json.loads(checked.stdout.strip().splitlines()[-1])
    (result / "grade.json").write_text(json.dumps(verdict, indent=2) + "\n")
    print(json.dumps(verdict, indent=2))
    return verdict


if __name__ == "__main__":
    action, target = sys.argv[1], Path(sys.argv[2])
    {"rows": rows, "export": export, "grade": grade}[action](target)

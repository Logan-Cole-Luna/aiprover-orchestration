"""Grade a benchmark run outside its session, on the rules of a run.

    python3 benchmark/grade.py RUN_DIR [--no-review]

Solution.lean is elaborated once with the workspace's Lean checker, under
the Lean sandbox. A file that compiles without `sorry`, on the standard
axioms alone, goes to the final review of the orchestration and to the FL-FL
and NL-NL prompts of the dual-judge evaluation, whose Claude stand-in runs
here; Judge2 requires Bedrock.
"""

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from aiprover_orchestration.agents.claude_cli import invoke_once  # noqa: E402
from aiprover_orchestration.lean.checker import LeanChecker  # noqa: E402
from aiprover_orchestration.orchestrator import prompts  # noqa: E402
from aiprover_orchestration.orchestrator.structures import (  # noqa: E402
    extract_tag,
)
from aiprover_orchestration.paths import WORKSPACE  # noqa: E402

EVALUATOR = ROOT.parent / "PartitionAndProve" / "llm_inferAndEval"
REVIEW_MODELS = ("claude-sonnet-5-5", "claude-opus-5-5")
JUDGE_MODEL = "claude-opus-5-5"
STANDARD_AXIOMS = {"propext", "Classical.choice", "Quot.sound"}
LEAN_TIMEOUT = 1800


def compile_check(solution: Path) -> dict:
    checker = LeanChecker(WORKSPACE, 1, LEAN_TIMEOUT)
    result = checker.check_file(solution)
    axioms = sorted({a for names in result.axioms.values() for a in names})
    return {
        "compiles": result.ok,
        "timed_out": result.timed_out,
        "error_count": len(result.errors),
        "first_errors": [error[:400] for error in result.errors[:5]],
        "sorry": result.has_sorry_warning,
        "axioms": axioms,
        "axioms_reported": bool(result.axioms),
        "standard_axioms": (
            bool(result.axioms) and set(axioms) <= STANDARD_AXIOMS
        ),
    }


def ask(prompt: str, system: str, model: str, path: Path) -> tuple[str, float]:
    text, payload, error = invoke_once(prompt, model, system, effort="high")
    path.write_text(text or str(error))
    usage = (payload.get("modelUsage") or {}).values()
    return text, sum(float(u.get("costUSD") or 0) for u in usage)


def review(run: Path, problem: dict, text: str) -> dict:
    """Verdicts of the final review and the Claude judge prompts."""
    sys.path.insert(0, str(EVALUATOR))
    import evaluate

    results, cost = {}, 0.0
    prompt = prompts.FINAL_REVIEW_TEMPLATE.format(
        informal_statement=problem["informal_statement"],
        informal_proof=problem["informal_proof"],
        target_statement=text,
        lean_solution=text,
    )
    for model in REVIEW_MODELS:
        reply, used = ask(
            prompt, prompts.REVIEWER_SYSTEM, model, run / f"review_{model}.txt"
        )
        results[f"review_{model}"] = extract_tag(reply, "verdict").strip()
        cost += used
    spec = evaluate.nl_specification(problem)
    reply, used = ask(
        evaluate.build_semantic_prompt(text, spec, "natural_language"),
        evaluate.JUDGE_SYSTEM_PROMPT,
        JUDGE_MODEL,
        run / "judge_fl_fl.txt",
    )
    results["judge_fl_fl"] = extract_tag(reply, "score").strip()
    cost += used
    reply, used = ask(
        evaluate.build_nl_nl_prompt(text, spec),
        evaluate.JUDGE_SYSTEM_PROMPT,
        JUDGE_MODEL,
        run / "judge_nl_nl.txt",
    )
    results["judge_nl_nl"] = extract_tag(reply, "equivalence").strip()
    cost += used
    return {**results, "review_cost_usd": round(cost, 3)}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("run")
    parser.add_argument("--no-review", action="store_true")
    options = parser.parse_args()
    run = Path(options.run).resolve()
    solution = run / "work" / "Solution.lean"
    grade = {"solution": solution.exists()}
    if solution.exists():
        grade |= compile_check(solution)
        clean = (
            grade["compiles"]
            and not grade["sorry"]
            and grade["standard_axioms"]
        )
        grade["clean"] = clean
        if clean and not options.no_review:
            manifest = json.loads((run / "manifest.json").read_text())
            entries = json.loads((ROOT / "benchmark/problems.json").read_text())
            entry = entries[manifest["problem"]]
            with open(ROOT / entry["dataset"]) as dataset:
                rows = map(json.loads, dataset)
                row = next(r for r in rows if r["uuid"] == entry["uuid"])
            grade |= review(run, row, solution.read_text())
    (run / "grade.json").write_text(json.dumps(grade, indent=1))
    print(json.dumps(grade, indent=1))


if __name__ == "__main__":
    main()

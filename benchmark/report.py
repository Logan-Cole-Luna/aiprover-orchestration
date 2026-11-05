"""Table of every benchmark run: python3 benchmark/report.py"""

import json
from pathlib import Path

RUNS = Path(__file__).resolve().parent / "runs"
HEADER = (
    "| Problem | Condition | Repeat | USD | Minutes | Turns | Compiles "
    "| No sorry | Axioms | Review | Judges |"
)


def read(path: Path) -> dict:
    return json.loads(path.read_text()) if path.exists() else {}


def mark(value) -> str:
    return "?" if value is None else "yes" if value else "no"


print(HEADER)
print("|---" * 11 + "|")
for result in sorted(RUNS.glob("*/*/r*/result.json")):
    run = result.parent
    done, grade = read(result), read(run / "grade.json")
    problem, condition, repeat = run.parts[-3:]
    verdicts = [v for k, v in grade.items() if k.startswith("review_c")]
    judges = [grade.get("judge_fl_fl"), grade.get("judge_nl_nl")]
    print(
        f"| {problem} | {condition} | {repeat[1:]} | {done.get('cost_usd')} "
        f"| {(done.get('wall_seconds') or 0) / 60:.1f} | {done.get('turns')} "
        f"| {mark(grade.get('compiles'))} "
        f"| {mark(None if 'sorry' not in grade else not grade['sorry'])} "
        f"| {mark(grade.get('standard_axioms'))} "
        f"| {', '.join(verdicts) or '-'} "
        f"| {', '.join(filter(None, judges)) or '-'} |"
    )

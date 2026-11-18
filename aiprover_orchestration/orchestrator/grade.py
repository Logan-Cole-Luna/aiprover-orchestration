"""Rubric grading of recorded runs.

Each gradable unit of a run's trace (formalization, prove-mode draft, sketch,
failed or proved lemma, escalation, final result) is graded with its rubric in
`aiprover_orchestration/rubrics/`. What the trace, Lean and the AIProver job
folders settle is computed here: evidence, hindsight labels, and the grade of a
failed job whose sessions were all starved. The rest goes to the grader role.
Grades are appended to `results/<run>/grades.jsonl`, calls to `grade_calls.jsonl`.

    python -m aiprover_orchestration.orchestrator.grade RUN_DIR ... [--kinds KIND ...]
        [--units ID ...] [--limit N] [--dry-run] [--agent JSON] [--compare REFERENCE.jsonl]
"""

import argparse
import json
import re
import sys
from pathlib import Path

from ..agents import AgentCallError, AgentPool, build_agent
from ..paths import AIPROVER_JOBS
from ..utils.files import read_json, read_jsonl
from .stages.gaps import GAP_PREFIX
from .structures import declared_names, extract_tag, parse_lemmas, recorded_lemmas, refers_to
from .trace import Trace, is_usage_limit

RUBRICS = Path(__file__).resolve().parents[1] / "rubrics"
# Axes scored separately, never averaged; a failed lemma gets a category.
AXES = {"faithfulness": ("faithfulness",), "draft": ("correctness", "completeness", "approach"),
        "sketch": ("fidelity", "decomposition", "encoding"), "lemma_failure": ("category",),
        "lemma_proof": ("quality",), "escalation": ("decision",), "final": ("faithfulness", "proof")}
# Failure causes by level: mathematical strategy (the plan) or execution.
LEVELS = {"false_or_misstated": "strategy", "open_step": "strategy", "too_coarse": "strategy",
          "missing_support_or_encoding": "execution", "prover_capability": "execution",
          "budget_or_infrastructure": "execution", "other": "other"}
# A failed session with fewer model turns than a quarter of verified proofs
# need (the 75th percentile over 118 verified sessions) may have lacked budget;
# turn rate does not separate the outcomes. The label is therefore uncertain.
STARVED = 38
STARVED_GRADE = {"category": "budget_or_infrastructure", "level": "execution", "uncertain": True}
STARVED_REASON = "every session ran fewer model turns than a quarter of proofs need, or ended on an infrastructure error"
EXCERPT = 8000  # characters kept of each long evidence field
AUTOMATION = re.compile(r"\b(?:simp|simp_all|aesop|omega|linarith|nlinarith|decide|native_decide"
                        r"|norm_num|positivity|grind|tauto|polyrith)\b")
SYSTEM = ("You grade one output of an automated pipeline that formalizes and proves mathematics in "
          "Lean 4 with Mathlib. Apply the rubric exactly. The evidence marked mechanical was computed "
          "from the run's trace and Lean; it is settled fact, not a claim to re-examine. Judge only "
          "what the rubric leaves to judgment, and answer in the requested tags.")


def clip(text, limit: int = EXCERPT) -> str:
    text = text if isinstance(text, str) else json.dumps(text, ensure_ascii=False, indent=1)
    cut = len(text) - limit
    return text if cut <= 0 else f"{text[: limit // 2]}\n[... {cut} characters ...]\n{text[-limit // 2 :]}"


def name_of(statement: str) -> str:
    return next((lemma.name for lemma in recorded_lemmas([statement or ""])), "")


def job_evidence(job_id: str) -> dict:
    """The AIProver job's sessions (outcome, minutes, model turns, failed
    calls) and final Lean. The job is starved when every session ran fewer
    than `STARVED` model turns or ended on an infrastructure error."""
    folder, sessions = AIPROVER_JOBS / (job_id or "-"), []
    for sample in read_json(folder / "result.json").get("samples", []):
        path = folder / f"s{sample.get('sample')}"
        log = (path / "agent.log").read_text(errors="replace") if (path / "agent.log").exists() else ""
        seconds = read_json(path / "resume.json").get("elapsed") or sample.get("elapsed_sec") or 0
        sessions.append({key: sample.get(key) for key in ("sample", "status", "timed_out")} | {
            "infra": sample.get("infra") or sample.get("status") == "infra",
            "problems": (sample.get("check") or {}).get("problems"), "minutes": round(seconds / 60, 1),
            "model_turns": len(read_jsonl(path / "reasoning.jsonl")),
            "failed_calls": len(re.findall(r"Tool call failed|LLM backend error", log))})
    starved = bool(sessions) and all(s["infra"] or s["model_turns"] < STARVED for s in sessions)
    lean = {path.name: clip(path.read_text(errors="replace"), 3000) for path in sorted(folder.glob("s[0-9]*.lean"))}
    return {"job_folder_found": folder.is_dir(), "starved": starved, "sessions": sessions, "session_lean": lean}


class Run:
    """A recorded run: its trace, and what each lemma became."""

    def __init__(self, directory: Path):
        self.dir = Path(directory)
        self.trace = Trace.load(self.dir / "trace.json")
        self.steps = self.trace["steps"]
        self.id = self.trace.get("run_id") or self.dir.name
        self.prove_mode = self.trace.get("config", {}).get("mode") == "prove"
        row, draft = self.trace["problem"], self.dir / "draft.md"
        self.drafts = sorted(self.dir.glob("approach_*.md")) or [draft] * draft.exists()
        followed = draft.read_text() if self.prove_mode and draft.exists() else row.get("informal_proof", "")
        self.source = {"informal_statement": row.get("informal_statement", ""), "followed_proof": clip(followed)}
        self.by_lemma: dict[str, list[dict]] = {}  # each lemma's decisions, in order
        for step in self.steps:
            if step["kind"] == "decision" and step.get("lemma"):
                self.by_lemma.setdefault(step["lemma"], []).append(step)

    def decisions(self, after: int, lemma: str) -> list[dict]:
        return [s for s in self.by_lemma.get(lemma, []) if s["index"] > after]

    def fates(self, index: int, names: list[str]) -> dict:
        return {name: self.proved_after(index, name) or "never proved" for name in names}

    def proved_after(self, index: int, lemma: str) -> str:
        """Who proved `lemma` after step `index`: prover, orchestrator, or ''."""
        step = next((s for s in self.decisions(index, lemma) if s["event"] == "lemma_proved"), None)
        return "" if step is None else "orchestrator" if step.get("worker") == "orchestrator" else "prover"

    def effective(self, step: dict) -> bool:
        """Whether an escalation worked: Lean accepted its proof or refutation,
        every part of its split was proved, or its lemma was proved later."""
        if step["action"] == "split":
            return all(self.proved_after(step["index"], name_of(text)) for text in step.get("new_lemmas") or [])
        return step["action"] in ("proof", "refute") or bool(self.proved_after(step["index"], step["lemma"]))

    def failure_label(self, index: int, lemma: str):
        """The failure's cause as the rest of the run reveals it, or None."""
        causes = {"refute": "false_or_misstated", "restate": "false_or_misstated", "split": "too_coarse",
                  "proof": "prover_capability"}
        for step in self.decisions(index, lemma):
            if step["event"] == "lemma_proved":  # by the orchestrator, or a later job sufficed
                return "prover_capability" if step.get("worker") == "orchestrator" else "budget_or_infrastructure"
            if step.get("action") in causes:
                return causes[step["action"]] if self.effective(step) else None
        # A step the plan itself declared open (stages/gaps.py).
        return "open_step" if lemma.startswith(GAP_PREFIX) else None


def proof_stats(statement: str, proof: str, helpers: str) -> dict:
    declared = declared_names(helpers)
    return {"statement_chars": len(statement),
            "proof_lines": sum(bool(line.strip()) for line in (helpers + "\n" + proof).splitlines()),
            "automation_calls": len(AUTOMATION.findall(proof + helpers)),
            "unused_helpers": sorted(n for n in declared if not refers_to(proof, n))}


def units(run: Run) -> list[dict]:
    """The gradable units of a run with their mechanical evidence; a unit that
    the evidence settles carries its grade as `decided`."""
    found, form, judged, statements, steps = [], {}, {}, {}, run.steps
    reviews = [{"final_review": s.get("verdict")} for s in steps if s.get("event") == "final_review"]

    def add(kind, key, lemma="", hindsight=None, verdict=None, decided=None, **evidence):
        found.append({"unit": f"{run.id}/{kind}:{key}", "kind": kind, "lemma": lemma, "hindsight": hindsight,
                      "mode": "prove" if run.prove_mode else "autoformalize", "verdict": verdict,
                      "decided": decided, "evidence": run.source | evidence})

    for path in run.drafts if run.prove_mode else []:
        add("draft", path.stem, followed_proof="(none: the draft below is graded)", draft=clip(path.read_text()),
            run_outcome=(run.trace.get("outcome") or {}).get("status"))
    # A run that drafted several approaches sketches the first, then each one
    # it switches to; `draft.md` holds only the last.
    approach = run.dir / "approach_1.md"
    followed = clip(approach.read_text()) if approach.exists() else run.source["followed_proof"]
    for step in steps:
        event, index, lemma = step.get("event"), step["index"], step.get("lemma", "")
        statement = statements.get(lemma, "")  # the statement in force before this step
        if event in ("formalization_compiled", "fixed_statement"):
            form = step
        elif event == "sketch_judged":
            judged = step
        elif event == "approach_switched":
            path = run.dir / f"approach_{step['approach']}.md"
            followed = clip(path.read_text()) if path.exists() else run.source["followed_proof"]
        elif event == "audit_verdict":
            add("faithfulness", index, verdict={"audit": step.get("verdict"), **(reviews or [{}])[-1]},
                formal_statement=clip({k: form.get(k) for k in ("definitions", "statement", "notes", "name")}))
        elif event in ("sketch_accepted", "sketch_rejected"):
            reply = next((s for s in reversed(steps[:index]) if s["kind"] == "model_call"
                          and s.get("label", "").startswith("sketch")), {})
            # An accepted sketch is read from its recorded lemmas and main proof,
            # each statement given an equal share of a fixed budget; a rejected
            # sketch records none, so its reply is read and names them.
            lemmas = step.get("lemmas") or []
            names = ([name_of(text) for text in lemmas]
                     or [parsed.name for parsed in parse_lemmas(reply.get("response", ""))])
            share = max(200, 20000 // max(len(lemmas), 1))
            sketch = ({"lemma_statements": [clip(text, share) for text in lemmas],
                       "main_proof": clip(step.get("main_proof", ""), 8000)} if lemmas
                      else {"sketch_reply": clip(reply.get("response", ""), 16000)})
            add("sketch", index, followed_proof=followed, verdict={k: judged.get(k) for k in ("verdict", "issues")},
                **sketch, lean_rejection=step.get("problems"),
                lemma_fates=run.fates(index, names),
                declared_gaps=[name for name in names if name.startswith(GAP_PREFIX)])
            judged = {}
        elif event == "prover_budget_exhausted":
            job, label = job_evidence(step.get("aiprover_job")), run.failure_label(index, lemma)
            starved = job["starved"] and LEVELS.get(label) != "strategy"
            add("lemma_failure", index, lemma, STARVED_GRADE["category"] if starved else label,
                decided=STARVED_GRADE if starved else None, statement=statement,
                errors=clip(step.get("errors", ""), 3000), last_attempts=clip(step.get("attempts", ""), 4000),
                knowledge=clip(step.get("knowledge", ""), 3000), job=job)
        elif event == "lemma_proved":
            proof, helpers = step.get("proof", ""), step.get("helpers", "")
            add("lemma_proof", index, lemma, proved_by=step.get("worker"), statement=statement,
                helpers=clip(helpers), proof=clip(proof), stats=proof_stats(statement, proof, helpers))
        elif event == "lemma_escalation":
            fates = run.fates(index, [lemma] + [name_of(text) for text in step.get("new_lemmas") or []])
            history = [s.get("errors", "")[:1500] for s in steps[:index]
                       if s.get("event") == "prover_budget_exhausted" and s.get("lemma") == lemma]
            add("escalation", index, lemma, "effective" if run.effective(step) else "ineffective",
                statement=statement, action=step["action"], diagnosis=step.get("diagnosis", ""),
                new_lemmas=step.get("new_lemmas"), restated=step.get("statement"), prover_failures=history[-2:],
                outcome="accepted by Lean" if step["action"] in ("proof", "refute") else fates)
        elif event == "final_review":
            # The evidence stays the same size whatever the proof's length: the
            # target, Lean's checks, and per-lemma statistics with the longest
            # proofs; each lemma's proof is graded on its own (lemma_proof).
            proved = [s for s in steps if s.get("event") == "lemma_proved"]
            stats = {s["lemma"]: proof_stats("", s.get("proof", ""), s.get("helpers", "")) for s in proved}
            longest = sorted(proved, key=lambda s: -stats[s["lemma"]]["proof_lines"])[:2]
            add("final", index, verdict={k: step.get(k) for k in ("verdict", "concerns")},
                checks=(run.trace.get("outcome") or {}).get("checks"),
                target=clip({k: form.get(k) for k in ("definitions", "statement", "name")}),
                lemma_stats=stats, longest_proofs={s["lemma"]: clip(s.get("proof", ""), 6000) for s in longest},
                lemmas_proved_by_orchestrator=sum(s.get("worker") == "orchestrator" for s in proved))
        texts = (step.get("lemmas", []) if event == "sketch_accepted" else []) + (step.get("new_lemmas") or [])
        statements |= {name_of(text): text for text in texts + [step.get("statement") or ""]}
    return found


def prompt(unit: dict) -> str:
    rubric = (RUBRICS / f"{unit['kind']}.md").read_text()
    blocks = "\n".join(f'<evidence name="{k}">\n{clip(v, 24000)}\n</evidence>' for k, v in unit["evidence"].items())
    answer = " ".join(f"<{axis}>score 0 to 5</{axis}>" for axis in AXES[unit["kind"]])
    if unit["kind"] == "lemma_failure":
        answer = f"<category>one of {', '.join(LEVELS)}</category> <uncertain>yes or no</uncertain>"
    return (f"<rubric>\n{rubric}\n</rubric>\n\nUnit {unit['unit']} ({unit['mode']} mode).\n"
            f"Hindsight label (mechanical): {unit['hindsight']}\n{blocks}\n\n"
            f"Reply with {answer}, then <reason>at most three sentences</reason>.")


def parse(kind: str, reply: str) -> dict:
    if kind == "lemma_failure":
        value = extract_tag(reply, "category").strip().lower()
        category = next((c for c in LEVELS if c in value), "unparsed")
        uncertain = extract_tag(reply, "uncertain").strip().lower().startswith("yes")
        return {"category": category, "level": "uncertain" if uncertain else LEVELS.get(category),
                "uncertain": uncertain}
    digits = {axis: re.search(r"[0-5]", extract_tag(reply, axis)) for axis in AXES[kind]}
    return {axis: int(digit.group()) if digit else None for axis, digit in digits.items()}


def grader_spec(run: Run) -> dict:
    """The run's checker or reviewer, the first whose model is not the orchestrator's."""
    agents = run.trace.get("agents", {})
    roles = [agents[r] for r in ("checker", "reviewer") if r in agents]
    if not roles:
        raise SystemExit(f"{run.dir}: the trace names no checker or reviewer; give --agent")
    own = agents.get("orchestrator", {}).get("model")
    spec = next((s for s in roles if s.get("model") != own), roles[0])
    return {k: v for k, v in spec.items() if k not in ("phases", "source_dir")} | {"tools": False}


def compare(grades: list[dict], reference: list[dict]) -> dict:
    """Agreement of grades with reference grades, per axis and category, over
    the reference units that were graded."""
    latest, disagreements = {g["unit"]: g["grades"] for g in grades if g.get("grades")}, []
    stats = dict.fromkeys(("ungraded", "scores", "exact", "within_one", "categories", "category_agree",
                           "level_agree", "hindsight", "grader_hindsight_agree", "reference_hindsight_agree"), 0)
    for ref in reference:
        stats["ungraded"] += ref["unit"] not in latest
        for axis, value in ref["grades"].items() if ref["unit"] in latest else ():
            if axis in ("level", "uncertain"):
                continue  # parts of the category, not scores
            mine, hindsight = latest[ref["unit"]].get(axis), ref.get("hindsight")
            if axis == "category":
                counts = {"categories": True, "category_agree": mine == value,
                          "level_agree": LEVELS.get(mine) == LEVELS.get(value), "hindsight": bool(hindsight),
                          "grader_hindsight_agree": bool(hindsight) and mine == hindsight,
                          "reference_hindsight_agree": bool(hindsight) and value == hindsight}
            else:
                scored = isinstance(mine, int)
                counts = {"scores": scored, "exact": mine == value, "within_one": scored and abs(mine - value) <= 1}
            for key, hit in counts.items():
                stats[key] += hit
            if mine != value:
                disagreements.append(f"{ref['unit']} {axis}: reference {value}, grader {mine}")
    return stats | {"disagreements": disagreements}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("run_dirs", nargs="+", type=Path)
    parser.add_argument("--kinds", nargs="+", choices=sorted(AXES))
    parser.add_argument("--units", nargs="+", help="unit ids, as --dry-run lists them")
    parser.add_argument("--limit", type=int, default=0, help="grader calls at most; 0 = no limit")
    parser.add_argument("--dry-run", action="store_true", help="list the units and their evidence only")
    parser.add_argument("--agent", type=json.loads, help="grader agent specification (JSON)")
    parser.add_argument("--compare", type=Path, help="reference grades (JSON lines)")
    args = parser.parse_args()
    if args.compare:
        grades = [g for d in args.run_dirs for g in read_jsonl(d / "grades.jsonl")]
        print(json.dumps(compare(grades, read_jsonl(args.compare)), indent=1))
        return
    for path in [] if args.dry_run else [d / f for d in args.run_dirs for f in ("grades.jsonl", "grade_calls.jsonl")]:
        try:  # fail before the first call, not after it
            open(path, "a").close()
        except OSError as error:
            raise SystemExit(f"cannot write {path}: {error}")
    calls = 0
    for run in map(Run, args.run_dirs):
        chosen = [u for u in units(run) if (not args.kinds or u["kind"] in args.kinds)
                  and (not args.units or u["unit"] in args.units)]
        if args.dry_run:
            for unit in chosen:
                print(json.dumps(unit | {"prompt_chars": len(prompt(unit))}, ensure_ascii=False))
            continue
        spec = args.agent or grader_spec(run)
        pool = AgentPool({"grader": build_agent(spec)}, run.dir / "grade_calls.jsonl")
        for unit in chosen:
            grader, reply = "mechanical", f"<reason>{STARVED_REASON}</reason>"
            if not unit["decided"]:
                if args.limit and calls >= args.limit:
                    return
                calls, grader = calls + 1, spec
                try:
                    reply = pool.complete(prompt(unit), role="grader", system_prompt=SYSTEM,
                                          phase=f"grade/{unit['kind']}")
                except AgentCallError as error:
                    if is_usage_limit(str(error)):
                        raise SystemExit(f"usage limit reached after {calls} calls: {error}")
                    # No grade is recorded, so that a failed call never replaces an earlier grade.
                    print(f"{unit['unit']}: grader call failed: {error}", file=sys.stderr)
                    continue
            record = {key: unit[key] for key in ("unit", "kind", "lemma", "mode", "hindsight", "verdict")}
            record |= {"grader": grader, "grades": unit["decided"] or parse(unit["kind"], reply),
                       "reason": extract_tag(reply, "reason")}
            with open(run.dir / "grades.jsonl", "a") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()

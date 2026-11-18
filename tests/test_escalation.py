"""Escalation and conflict analysis with scripted orchestrator replies and real
Lean, and the replay of their decisions on resume."""

import json
import sys
import unittest
from unittest import mock

from aiprover_orchestration.agents import Agent, AgentLimitReached, Completion
from aiprover_orchestration.orchestrator import grade
from aiprover_orchestration.orchestrator.config import Config
from aiprover_orchestration.orchestrator.resume import restore_state
from aiprover_orchestration.orchestrator.structures import (
    Lemma,
    Sketch,
    statement_key,
    stub_listing,
)
from tests.support import (
    ScriptedPool,
    aiprover_phase,
    TemporaryDirectoryTest,
    requires_lean,
)

GOAL = "theorem goal : (2 : Nat) + 2 = 4"
SPLIT = (
    "<diagnosis>split</diagnosis>"
    "<split>theorem goal_part : (2 : Nat) + 2 = 4 := by sorry</split>"
    "<proof>exact goal_part</proof>"
)


@requires_lean
class EscalationTest(TemporaryDirectoryTest):

    def escalation(self, replies, lemma, sketch):
        run = self.orchestration(ScriptedPool(replies))
        phase = aiprover_phase(run, sketch)
        run._escalation(phase, lemma)
        return run, phase.pool.lemmas

    def test_split_with_definitions_and_replay(self):
        goal = Lemma("goal", GOAL)
        sketch = Sketch([goal], "trivial")
        split = (
            "<split_definitions>def {name} : Nat := 4</split_definitions>"
            "<split>theorem goal_part : (2 : Nat) + 2 = {name} := by sorry"
            "</split><proof>exact goal_part</proof>"
        )
        run, submitted = self.escalation(
            [split.format(name="four"), split.format(name="goal_four")],
            goal,
            sketch,
        )
        self.assertEqual(run.agents.phases, ["escalation/goal"] * 2)
        self.assertTrue(goal.proved)
        part = sketch.lemmas[0]
        self.assertEqual(part.definitions, ("def goal_four : Nat := 4",))
        self.assertEqual(submitted, [part])
        # The definitions precede the first lemma that uses them, once.
        listing = stub_listing([part, goal], before=part)
        self.assertEqual(listing.count("def goal_four"), 1)
        self.assertLess(
            listing.index("def goal_four"), listing.index("goal_part")
        )
        run.trace.document["steps"].insert(
            0,
            {
                "kind": "decision",
                "event": "sketch_accepted",
                "lemmas": [GOAL],
                "main_proof": "trivial",
            },
        )
        state = restore_state(run.trace.document)
        self.assertEqual(
            [lemma.definitions for lemma in state.sketch.lemmas],
            [part.definitions, ()],
        )

    def test_direct_proof_after_the_limit_is_rejected(self):
        goal = Lemma("goal", GOAL)

        def at_limit():
            raise AgentLimitReached("error_max_turns")

        run, _ = self.escalation(
            [at_limit, "<proof>rfl</proof>"], goal, Sketch([goal], "trivial")
        )
        self.assertFalse(goal.proved)
        self.assertIn(("lemma_escalation", "rejected"), self.decisions(run))

    def test_restate_refused_after_a_proof_found_meanwhile(self):
        other = Lemma("other", "theorem other : (1 : Nat) = 2", attempts=3)
        user = Lemma("user", "theorem user : True")
        sketch = Sketch([other, user], "trivial")

        def reply():
            # `user` is proved from `other` while the orchestrator answers.
            user.proof, user.proved = "exact (fun _ => trivial) other", True
            return "<restate>theorem other : (1 : Nat) = 1</restate>"

        run = self.orchestration(ScriptedPool([reply, "<retry/>"]))
        run._escalation(aiprover_phase(run, sketch), other)
        self.assertEqual(other.statement, "theorem other : (1 : Nat) = 2")
        self.assertIn(("lemma_escalation", "retry"), self.decisions(run))

    def conflict_run(self, replies, config):
        """A split of `goal` whose child is stuck, and a run whose orchestrator
        answers its conflict analysis with `replies`."""
        goal = Lemma("goal", GOAL)
        sketch = Sketch([goal], "trivial")
        first, _ = self.escalation([SPLIT], goal, sketch)
        child = sketch.lemmas[0]
        child.handed_back = True
        run = self.orchestration(ScriptedPool(replies), config)
        run.trace.document["steps"] += first.trace.document["steps"]
        return run, goal, child, sketch

    def test_conflict_backjump_and_replay(self):
        run, goal, child, sketch = self.conflict_run(
            [
                "<diagnosis>bad split</diagnosis>"
                f"<backjump>{GOAL}</backjump>"
                "<clause>splitting does not help</clause>"
            ],
            Config(cdcl=True, cdcl_backjump=True),
        )
        phase = aiprover_phase(run, sketch)
        run._conflict(phase, goal, [child])
        self.assertEqual(sketch.lemmas, [goal])
        self.assertFalse(goal.proved)
        self.assertEqual(phase.pool.lemmas, [goal])
        self.assertEqual(len(run.clauses.clauses()), 2)
        # The trace of the escalation and the backjump replays to the same
        # sketch.
        run.trace.document["steps"].insert(
            0,
            {
                "kind": "decision",
                "event": "sketch_accepted",
                "lemmas": [GOAL],
                "main_proof": "trivial",
            },
        )
        state = restore_state(run.trace.document)
        self.assertEqual(
            [(lemma.name, lemma.proved) for lemma in state.sketch.lemmas],
            [("goal", False)],
        )
        self.assertEqual(
            statement_key(state.sketch.lemmas[0].statement),
            statement_key(GOAL),
        )


class GradeTest(TemporaryDirectoryTest):
    """Rubric grading of a recorded run: hindsight labels from the trace,
    replies of a stub grader, and agreement with reference grades."""

    def test_hindsight_labels_and_grades(self):
        def step(index, kind="decision", **fields):
            return {"index": index, "kind": kind, **fields}

        coarse = "theorem coarse : (2 : Nat) + 2 = 4"
        steps = [
            step(0, event="formalization_compiled", statement="theorem t : True"),
            step(1, event="sketch_accepted", lemmas=[coarse, "theorem hard : True", "theorem gap_open : True"]),
            step(2, event="prover_budget_exhausted", lemma="coarse"),
            step(3, event="prover_budget_exhausted", lemma="hard"),
            step(4, event="prover_budget_exhausted", lemma="gap_open"),
            step(5, event="lemma_escalation", lemma="coarse", action="split",
                 new_lemmas=["theorem coarse_a : True"]),
            step(6, event="lemma_proved", lemma="coarse_a", worker=0, proof="trivial"),
            step(7, event="lemma_escalation", lemma="hard", action="proof"),
            step(8, event="lemma_proved", lemma="hard", worker="orchestrator", proof="trivial"),
            step(9, event="prover_budget_exhausted", lemma="slow", aiprover_job="slow_job"),
        ]
        # One session with 1 model turn: starved (fewer than grade.STARVED).
        job = self.directory / "jobs" / "slow_job"
        (job / "s0").mkdir(parents=True)
        sample = {"sample": 0, "status": "error", "elapsed_sec": 1800}
        (job / "result.json").write_text(json.dumps({"samples": [sample]}))
        (job / "s0" / "reasoning.jsonl").write_text('{"turn": 1}\n')
        self.enterContext(mock.patch.object(grade, "AIPROVER_JOBS", self.directory / "jobs"))
        trace = {"run_id": "r", "problem": {"informal_statement": "s"},
                 "config": {}, "agents": {}, "steps": steps, "outcome": None}
        (self.directory / "trace.json").write_text(json.dumps(trace))
        units = {u["unit"]: u for u in grade.units(grade.Run(self.directory))}
        for unit, label in [
            ("r/lemma_failure:2", "too_coarse"),
            ("r/lemma_failure:3", "prover_capability"),
            ("r/lemma_failure:4", "open_step"),
            ("r/escalation:5", "effective"),
            ("r/lemma_failure:9", "budget_or_infrastructure"),
        ]:
            with self.subTest(unit=unit):
                self.assertEqual(units[unit]["hindsight"], label)
        with self.subTest("starvation and gaps are mechanical"):
            self.assertEqual(units["r/lemma_failure:9"]["decided"], grade.STARVED_GRADE)
            self.assertIsNone(units["r/lemma_failure:3"]["decided"])
            self.assertEqual(units["r/sketch:1"]["evidence"]["declared_gaps"], ["gap_open"])

        prompts = []

        class Stub(Agent):
            def complete_once(self, prompt, system_prompt):
                prompts.append(prompt)
                return Completion("<category>too_coarse</category><quality>4</quality><reason>r</reason>")

        def grade_run(directory):
            argv = ["grade", str(directory), "--kinds", "lemma_failure", "lemma_proof",
                    "--agent", '{"backend": "stub", "model": "m"}']
            with mock.patch.object(grade, "build_agent", lambda spec: Stub(spec["model"])), \
                    mock.patch.object(sys, "argv", argv):
                grade.main()

        grade_run(self.directory)
        grades = [json.loads(line) for line in (self.directory / "grades.jsonl").read_text().splitlines()]
        with self.subTest("grades"):
            self.assertEqual((len(grades), len(prompts)), (6, 5))
            self.assertEqual(grades[0]["grades"], {"category": "too_coarse", "level": "strategy", "uncertain": False})
            self.assertEqual(grades[-2]["grades"], {"quality": 4})
            self.assertEqual((grades[-1]["grader"], grades[-1]["grades"]), ("mechanical", grade.STARVED_GRADE))
        with self.subTest("unwritable run folder fails before any call"):
            locked = self.directory / "locked"
            locked.mkdir()
            (locked / "trace.json").write_text(json.dumps(trace))
            locked.chmod(0o555)
            self.addCleanup(locked.chmod, 0o755)
            with self.assertRaises(SystemExit):
                grade_run(locked)
            self.assertEqual(len(prompts), 5)
        reference = [{"unit": "r/lemma_failure:3", "hindsight": "prover_capability",
                      "grades": {"category": "prover_capability"}},
                     {"unit": "r/lemma_proof:8", "grades": {"quality": 5}}]
        stats = grade.compare(grades, reference)
        with self.subTest("agreement"):
            self.assertEqual((stats["category_agree"], stats["reference_hindsight_agree"]), (0, 1))
            self.assertEqual((stats["exact"], stats["within_one"]), (0, 1))


if __name__ == "__main__":
    unittest.main()

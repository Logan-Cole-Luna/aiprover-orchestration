"""Hand-back and conflict analysis with scripted captain replies and real
Lean, and the replay of their decisions on resume."""

import unittest

from aiprover_orchestration.orchestrator.resume import restore_state
from aiprover_orchestration.orchestrator.structures import (
    Lemma,
    Sketch,
    statement_key,
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
class HandbackTest(TemporaryDirectoryTest):

    def handback(self, replies, lemma, sketch):
        run = self.orchestration(ScriptedPool(replies))
        phase = aiprover_phase(run, sketch)
        run._handback(phase, lemma)
        return run, phase.pool.lemmas

    def test_rejected_reply_then_split(self):
        goal = Lemma("goal", GOAL)
        sketch = Sketch([goal], "trivial")
        run, submitted = self.handback(
            ["<proof>native_decide</proof>", SPLIT], goal, sketch
        )
        self.assertEqual(run.agents.phases, ["handback/goal"] * 2)
        self.assertTrue(goal.proved)
        self.assertEqual(
            [(lemma.name, lemma.parent) for lemma in sketch.lemmas],
            [("goal_part", "goal"), ("goal", "")],
        )
        self.assertEqual([lemma.name for lemma in submitted], ["goal_part"])
        self.assertIn(("lemma_handback", "split"), self.decisions(run))

    def test_proof_with_stop_is_rejected(self):
        goal = Lemma("goal", GOAL)
        run, _ = self.handback(
            ["<proof>stop\n  rfl</proof>", "<retry/>"],
            goal,
            Sketch([goal], "trivial"),
        )
        self.assertFalse(goal.proved)
        self.assertIn(("lemma_handback", "retry"), self.decisions(run))

    def test_restate_takes_the_attempts_of_the_new_statement(self):
        other = Lemma("other", "theorem other : (1 : Nat) = 2", attempts=3)
        sketch = Sketch([other], "trivial")
        run = self.orchestration(
            ScriptedPool(["<restate>theorem other : (1 : Nat) = 1</restate>"])
        )
        restated = Lemma("earlier", "theorem earlier : (1 : Nat) = 1")
        run.state.count_attempt(restated)
        run._handback(aiprover_phase(run, sketch), other)
        self.assertEqual(other.statement, "theorem other : (1 : Nat) = 1")
        self.assertEqual(other.attempts, 1)

    def test_conflict_backjump_and_replay(self):
        goal = Lemma("goal", GOAL)
        sketch = Sketch([goal], "trivial")
        first, _ = self.handback([SPLIT], goal, sketch)
        child = sketch.lemmas[0]
        child.handed_back = True
        run = self.orchestration(
            ScriptedPool(
                [
                    "<diagnosis>bad split</diagnosis>"
                    f"<backjump>{GOAL}</backjump>"
                    "<clause>splitting does not help</clause>"
                ]
            )
        )
        run.trace.document["steps"] += first.trace.document["steps"]
        phase = aiprover_phase(run, sketch)
        run._conflict(phase, goal, [child])
        self.assertEqual(sketch.lemmas, [goal])
        self.assertFalse(goal.proved)
        self.assertEqual(phase.pool.lemmas, [goal])
        self.assertEqual(len(run.clauses.clauses()), 2)
        # The trace of the hand-back and the backjump replays to the same
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


if __name__ == "__main__":
    unittest.main()

"""Hand-back and conflict analysis with scripted captain replies and real
Lean, and the replay of their decisions on resume."""

import unittest

from aiprover_orchestration.agents import Agent, AgentLimitReached, Completion
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
class HandbackTest(TemporaryDirectoryTest):

    def handback(self, replies, lemma, sketch):
        run = self.orchestration(ScriptedPool(replies))
        phase = aiprover_phase(run, sketch)
        run._handback(phase, lemma)
        return run, phase.pool.lemmas

    def test_split_with_definitions_and_replay(self):
        goal = Lemma("goal", GOAL)
        sketch = Sketch([goal], "trivial")
        split = (
            "<split_definitions>def {name} : Nat := 4</split_definitions>"
            "<split>theorem goal_part : (2 : Nat) + 2 = {name} := by sorry"
            "</split><proof>exact goal_part</proof>"
        )
        run, submitted = self.handback(
            [split.format(name="four"), split.format(name="goal_four")],
            goal,
            sketch,
        )
        self.assertEqual(run.agents.phases, ["handback/goal"] * 2)
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

        run, _ = self.handback(
            [at_limit, "<proof>rfl</proof>"], goal, Sketch([goal], "trivial")
        )
        self.assertFalse(goal.proved)
        self.assertIn(("lemma_handback", "rejected"), self.decisions(run))

    def test_restate_refused_after_a_proof_found_meanwhile(self):
        other = Lemma("other", "theorem other : (1 : Nat) = 2", attempts=3)
        user = Lemma("user", "theorem user : True")
        sketch = Sketch([other, user], "trivial")

        def reply():
            # `user` is proved from `other` while the captain answers.
            user.proof, user.proved = "exact (fun _ => trivial) other", True
            return "<restate>theorem other : (1 : Nat) = 1</restate>"

        run = self.orchestration(ScriptedPool([reply, "<retry/>"]))
        run._handback(aiprover_phase(run, sketch), other)
        self.assertEqual(other.statement, "theorem other : (1 : Nat) = 2")
        self.assertIn(("lemma_handback", "retry"), self.decisions(run))

    def conflict_run(self, replies, config):
        """A split of `goal` whose child is stuck, and a run whose captain
        answers its conflict analysis with `replies`."""
        goal = Lemma("goal", GOAL)
        sketch = Sketch([goal], "trivial")
        first, _ = self.handback([SPLIT], goal, sketch)
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

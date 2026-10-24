"""Hand-back and conflict analysis with scripted captain replies and real
Lean, and the replay of their decisions on resume."""

import unittest

from aiprover_orchestration.agents import (
    Agent,
    AgentLimitReached,
    AgentPool,
    Completion,
)
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

    def test_proof_with_stop_is_rejected(self):
        goal = Lemma("goal", GOAL)
        run, _ = self.handback(
            ["<proof>stop\n  rfl</proof>", "<retry/>"],
            goal,
            Sketch([goal], "trivial"),
        )
        self.assertFalse(goal.proved)
        self.assertIn(("lemma_handback", "retry"), self.decisions(run))

    def test_reply_at_its_limit_is_followed_by_a_split(self):
        goal = Lemma("goal", GOAL)

        def at_limit():
            raise AgentLimitReached("error_max_budget_usd")

        run, submitted = self.handback(
            [at_limit, SPLIT], goal, Sketch([goal], "trivial")
        )
        self.assertNotIn("(action 1) is not available", run.agents.prompts[0])
        self.assertIn("(action 1) is not available", run.agents.prompts[1])
        self.assertEqual([lemma.name for lemma in submitted], ["goal_part"])

    def test_direct_proof_after_the_limit_is_rejected(self):
        goal = Lemma("goal", GOAL)

        def at_limit():
            raise AgentLimitReached("error_max_turns")

        run, _ = self.handback(
            [at_limit, "<proof>rfl</proof>"], goal, Sketch([goal], "trivial")
        )
        self.assertFalse(goal.proved)
        self.assertIn(("lemma_handback", "rejected"), self.decisions(run))

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

    def test_retry_refused_past_the_limit(self):
        goal = Lemma("goal", GOAL, attempts=6, reviewed_at=6)
        sketch = Sketch([goal], "trivial")
        run = self.orchestration(
            ScriptedPool(["<retry/>", SPLIT]), Config(aiprover_handback_after=2)
        )
        phase = aiprover_phase(run, sketch)
        phase.retries["goal"] = 3
        # Retried more often than allowed: handed back before another job.
        run._maybe_handback(phase, goal)
        self.assertIn("Retry is not available", run.agents.prompts[0])
        self.assertEqual(run.agents.phases, ["handback/goal"] * 2)
        self.assertTrue(goal.proved)

    def test_early_revision_once_per_statement(self):
        goal = Lemma("goal", GOAL, attempts=1, handed_back=True)
        sketch = Sketch([goal], "trivial")
        run = self.orchestration(
            ScriptedPool(["<retry/>"]), Config(aiprover_handback_after=2)
        )
        phase = aiprover_phase(run, sketch)
        # A retry leaves attempts, so jobs continue ...
        self.assertTrue(run._revise(phase, goal))
        self.assertIn(("early_revision", None), self.decisions(run))
        # ... and the statement is not revised again once they are used.
        goal.attempts = 5
        self.assertFalse(run._revise(phase, goal))
        self.assertEqual(run.agents.phases, ["handback/goal"])
        self.assertEqual([name for name, _ in phase.revised], ["goal"])

    def test_early_revision_split(self):
        goal = Lemma("goal", GOAL, attempts=1, handed_back=True)
        sketch = Sketch([goal], "trivial")
        run = self.orchestration(ScriptedPool([SPLIT]))
        phase = aiprover_phase(run, sketch)
        self.assertFalse(run._revise(phase, goal))
        self.assertTrue(goal.proved)
        self.assertEqual(
            [lemma.name for lemma in phase.pool.lemmas], ["goal_part"]
        )

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

    def test_conflict_proves_leaf_without_backjump(self):
        run, goal, child, sketch = self.conflict_run(
            [
                f"<diagnosis>bad split</diagnosis><backjump>{GOAL}</backjump>",
                "<diagnosis>local</diagnosis><local/>"
                "<clause>norm_num closes it</clause>"
                '<leaf_proof name="goal_part">norm_num</leaf_proof>',
            ],
            Config(cdcl=True),
        )
        run._conflict(aiprover_phase(run, sketch), goal, [child])
        self.assertEqual(run.agents.phases, ["conflict/goal"] * 2)
        self.assertEqual(sketch.lemmas, [child, goal])
        self.assertTrue(child.proved)
        self.assertEqual(len(run.clauses.clauses()), 1)

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


class LimitedAgent(Agent):
    """An endpoint whose every call stops at its spend limit."""

    backend = "limited"

    def __init__(self):
        super().__init__("limited", max_retries=3)
        self.calls = 0

    def complete_once(self, prompt, system_prompt):
        self.calls += 1
        return Completion(error="error_max_budget_usd", limited=True)


class LimitTest(TemporaryDirectoryTest):

    def test_call_at_its_limit_is_not_retried(self):
        agent = LimitedAgent()
        pool = AgentPool({"captain": agent}, self.directory / "calls.jsonl")
        with self.assertRaises(AgentLimitReached):
            pool.complete("p", role="captain", system_prompt="s", phase="x")
        self.assertEqual(agent.calls, 1)


if __name__ == "__main__":
    unittest.main()

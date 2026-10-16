"""The run-wide attempt budget per lemma statement, and the replan rule that
a statement whose budget is spent is not stated again."""

import unittest

from aiprover_orchestration.orchestrator import cdcl
from aiprover_orchestration.orchestrator.config import Config
from aiprover_orchestration.orchestrator.structures import (
    Lemma,
    Sketch,
    statement_key,
)
from tests.support import (
    FORM,
    ScriptedPool,
    TemporaryDirectoryTest,
    requires_lean,
)

SPENT = "theorem a (n : Nat) : n = n"


class BudgetTest(TemporaryDirectoryTest):

    def test_retry_extends_the_budget(self):
        run = self.orchestration(
            ScriptedPool(),
            Config(aiprover_attempts_per_lemma=2, aiprover_handback_after=3),
        )
        self.assertEqual(run._attempts_allowed(0), 2)
        self.assertEqual(run._attempts_allowed(2), 5)
        lemma = Lemma("a", SPENT, attempts=2)
        self.assertTrue(run._exhausted(lemma))
        lemma.reviewed_at = 2
        self.assertFalse(run._exhausted(lemma))

    def test_excluded_statements(self):
        run = self.orchestration(ScriptedPool())
        proved = "theorem p : True"
        run.state.attempts = {
            statement_key(SPENT): 1,
            statement_key(proved): 1,
            statement_key("theorem old : 2 = 2"): 1,
        }
        run.clauses.add(
            cdcl.LearnedClause("refuted", "theorem r : 1 = 2", "", "r")
        )
        sketch = Sketch(
            [Lemma("a", SPENT), Lemma("p", proved, proved=True)], "trivial"
        )
        excluded = run._excluded_statements(sketch)
        self.assertEqual(
            set(excluded),
            {
                statement_key(SPENT),
                statement_key("theorem old : 2 = 2"),
                statement_key("theorem r : 1 = 2"),
            },
        )
        self.assertIn("refuted", excluded[statement_key("theorem r : 1 = 2")])

    def test_chat_solvers_count_one_attempt_per_phase(self):
        # The solver's only reply uses `sorry`, which the text filter
        # rejects before Lean.
        pool = ScriptedPool(["<proof>sorry</proof>"], solver=object())
        run = self.orchestration(pool, Config(workers=1, max_repairs=0))
        lemma = Lemma("a", SPENT)
        sketch = Sketch([lemma], "trivial")
        run.prove(FORM, sketch)
        self.assertEqual(lemma.attempts, 1)
        self.assertIn(("lemma_failed", None), self.decisions(run))
        run.prove(FORM, sketch)  # the budget of one attempt is spent
        self.assertEqual(len(pool.phases), 1)


@requires_lean
class ReplanTest(TemporaryDirectoryTest):

    def test_spent_statement_is_rejected_under_a_new_name(self):
        pool = ScriptedPool(
            [
                "<lemmas>theorem c (n : Nat) : n = n := by sorry</lemmas>"
                "<main_proof>trivial</main_proof>",
                "<lemmas>theorem d (n : Nat) : n + 0 = n := by sorry</lemmas>"
                "<main_proof>trivial</main_proof>",
            ]
        )
        run = self.orchestration(
            pool, Config(sketch_judge=False, max_repairs=1)
        )
        lemma = Lemma("a", SPENT)
        run.state.count_attempt(lemma)
        sketch = run.replan(FORM, Sketch([lemma], "trivial"))
        self.assertIn("BUDGET SPENT: 1 failed attempts", pool.prompts[0])
        self.assertEqual([other.name for other in sketch.lemmas], ["d"])
        rejected = [
            step
            for step in run.trace.document["steps"]
            if step.get("event") == "sketch_rejected"
        ]
        self.assertEqual(len(rejected), 1)
        self.assertIn("budget", rejected[0]["problems"][0])


if __name__ == "__main__":
    unittest.main()

"""The run-wide attempt budget per lemma statement, and the replan rule that
a statement whose budget is spent is not stated again."""

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

from aiprover_orchestration.agents.base import BudgetExhausted
from aiprover_orchestration.agents.pool import AgentPool, role_spec
from aiprover_orchestration.orchestrator import cdcl
from aiprover_orchestration.orchestrator.config import Config
from aiprover_orchestration.orchestrator.stages.sketch import SketchStage
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


class SpendLimitTest(unittest.TestCase):
    def pool(self, spent: float) -> AgentPool:
        with TemporaryDirectory() as directory:
            pool = AgentPool(
                {"captain": SimpleNamespace(hosted=True)},
                Path(directory) / "calls.jsonl",
                max_claude_usd=50,
            )
        pool.usage_by_model = {"model": {"cost_usd": spent}}
        return pool

    def test_a_run_under_its_limit_may_call(self):
        pool = self.pool(49.99)
        self.assertGreater(pool.claude_budget_left(), 0)
        with pool._budget_reservation(pool.agents["captain"]):
            pass

    def test_a_run_at_its_limit_stops(self):
        pool = self.pool(50)
        self.assertEqual(pool.claude_budget_left(), 0)
        with self.assertRaises(BudgetExhausted):
            with pool._budget_reservation(pool.agents["captain"]):
                pass

    def test_a_limit_of_zero_is_unlimited(self):
        pool = self.pool(1000)
        pool.max_claude_usd = 0
        self.assertGreater(pool.claude_budget_left(), 0)


class SketchLimitTest(unittest.TestCase):
    def spent(self, steps, limit=3):
        stage = SketchStage.__new__(SketchStage)
        stage.config = SimpleNamespace(max_sketch_calls=limit)
        stage.trace = SimpleNamespace(document={"steps": steps})
        return bool(stage._sketch_calls_spent())

    def test_billed_sketch_calls_count(self):
        call = {"kind": "model_call", "label": "sketch0"}
        self.assertFalse(self.spent([call, call]))
        self.assertTrue(self.spent([call, call, call]))

    def test_a_call_stopped_at_its_limit_is_billed(self):
        call = {"kind": "model_call", "label": "sketch1"}
        stopped = call | {"error": "limit", "limited": True}
        self.assertTrue(self.spent([call, call, stopped]))

    def test_other_calls_do_not_count(self):
        steps = [
            {"kind": "model_call", "label": "judge_sketch0"},
            {"kind": "model_call", "label": "sketch0", "error": "outage"},
            {"kind": "lean_check", "label": "sketch0"},
        ]
        self.assertFalse(self.spent(steps * 3))

    def test_a_limit_of_zero_is_unlimited(self):
        call = {"kind": "model_call", "label": "sketch0"}
        self.assertFalse(self.spent([call] * 9, limit=0))


class SketchCallBudgetTest(unittest.TestCase):
    def test_a_sketch_call_has_a_spend_limit(self):
        spec = role_spec("captain", {"backend": "claude"})
        self.assertEqual(spec["phases"]["sketch"]["max_budget_usd"], 6.0)

    def test_a_specification_may_set_its_own_limit(self):
        phases = {"sketch": {"max_budget_usd": 20}}
        spec = role_spec("captain", {"backend": "claude", "phases": phases})
        self.assertEqual(spec["phases"]["sketch"]["max_budget_usd"], 20)


class ReportSwitchTest(TemporaryDirectoryTest):
    def test_reports_are_off_by_default(self):
        run = self.orchestration(ScriptedPool())
        self.assertFalse(run.config.write_reports)
        self.assertEqual(run.report(None, None, "", {}, {}), {})
        self.assertEqual(run.informalize(None, None, None), {})


class BudgetTest(TemporaryDirectoryTest):

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

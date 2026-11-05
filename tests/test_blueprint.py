"""The blueprint of a proof plan, and the review of a plan before proving."""

import json
import unittest

from aiprover_orchestration.orchestrator.config import Config
from aiprover_orchestration.orchestrator.reports import blueprint
from aiprover_orchestration.orchestrator.structures import (
    Lemma,
    Sketch,
    statement_key,
)
from tests.support import FORM, ScriptedPool, TemporaryDirectoryTest

REPLY = """<lemma name="a"><statement>$1=1$.</statement><idea>Reflexivity.</idea>
<uses></uses></lemma>
<lemma name="b"><statement>Truth holds.</statement><idea>Use $a$.</idea>
<uses>a, unknown</uses></lemma>
<lemma name="solution"><statement>The theorem.</statement><idea>Use $b$.</idea>
<uses>b</uses></lemma>"""


def sketch() -> Sketch:
    return Sketch(
        [
            Lemma("a", "theorem a : 1 = 1", proof="rfl", proved=True),
            Lemma("b", "theorem b : True"),
        ],
        "exact b",
    )


class PlanTest(unittest.TestCase):
    def check_dependencies_combine_the_writer_and_the_lean_text(self):
        plan = sketch()
        keys = {lemma.name: statement_key(lemma.statement) for lemma in plan.lemmas}
        keys["solution"] = statement_key(FORM.statement)
        nodes = blueprint.plan_nodes(FORM, plan, blueprint.parse_reply(REPLY, keys))
        self.assertEqual(nodes[1]["uses"], ["a"])
        self.assertEqual(nodes[2]["uses"], ["b"])

    def check_only_a_verified_assembly_proves_the_theorem(self):
        plan = sketch()
        plan.lemmas[1].proved = True
        self.assertEqual(
            blueprint.plan_nodes(FORM, plan, {})[-1]["status"], "planned"
        )
        nodes = blueprint.plan_nodes(FORM, plan, {}, verified=True)
        self.assertEqual(nodes[-1]["status"], "proved")
        self.assertEqual(blueprint.latex(nodes).count("{proof}\\leanok"), 3)

    def test_checks(self):
        for index, name in enumerate([
            "check_dependencies_combine_the_writer_and_the_lean_text",
            "check_only_a_verified_assembly_proves_the_theorem",
        ]):
            if index:
                self.tearDown()
                self.setUp()
            with self.subTest(name):
                getattr(self, name)()


class PlanReviewTest(TemporaryDirectoryTest):
    def run_with(self, **settings):
        return self.orchestration(ScriptedPool(), Config(**settings))

    def plan(self):
        path = self.directory / "blueprint" / "blueprint.json"
        return json.loads(path.read_text())

    def check_a_failed_description_leaves_the_lean_statements(self):
        run = self.run_with()

        def failing(prompt, role, phase):
            raise RuntimeError("no writer")

        run._writer_or_reviewer = failing
        run.blueprint(FORM, sketch())
        self.assertEqual(self.plan()["problems"], ["no writer"])

    def check_a_proved_statement_is_marked_in_the_blueprint(self):
        folder = self.directory / "blueprint"
        nodes = blueprint.plan_nodes(FORM, sketch(), {})
        blueprint.write(folder, 1, nodes)
        blueprint.record_proved(folder, "theorem b : True")
        self.assertEqual(self.plan()["nodes"][1]["status"], "proved")

    def check_guidance_reaches_the_guided_phases_only(self):
        run = self.run_with(sketch_guidance="prefer induction")
        run.agents.replies = ["", ""]
        run._captain("prompt", "direction/a")
        run._captain("prompt", "judge0")
        self.assertIn("prefer induction", run.agents.prompts[0])
        self.assertEqual(run.agents.prompts[1], "prompt")

    def check_a_replan_receives_the_findings(self):
        run = self.run_with(sketch_feedback="a bound is known", plan_revision=1)
        self.assertTrue(run._revision_requested())
        run._decision("plan_revised", revision=1)
        self.assertFalse(run._revision_requested())

    def check_a_revision_answers_its_request_once(self):
        run = self.run_with(sketch_feedback="split b", plan_revision=1)
        revised = sketch()
        run.sketch = lambda form, feedback, earlier: (
            self.assertIn("split b", feedback) or revised
        )
        self.assertIs(run._revised_plan(FORM, sketch()), revised)
        self.assertIsNot(run._revised_plan(FORM, sketch()), revised)

    def test_checks(self):
        for index, name in enumerate([
            "check_a_failed_description_leaves_the_lean_statements",
            "check_a_proved_statement_is_marked_in_the_blueprint",
            "check_guidance_reaches_the_guided_phases_only",
            "check_a_replan_receives_the_findings",
            "check_a_revision_answers_its_request_once",
        ]):
            if index:
                self.tearDown()
                self.setUp()
            with self.subTest(name):
                getattr(self, name)()



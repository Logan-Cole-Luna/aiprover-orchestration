"""Failed assemblies: the lemmas responsible and the withdrawal of proofs."""

import unittest
from types import SimpleNamespace

from aiprover_orchestration.orchestrator.stages.assemble import AssembleStage
from aiprover_orchestration.orchestrator.structures import Lemma, Sketch


class Stage(AssembleStage):
    def __init__(self, axioms: dict):
        self.axioms = axioms
        self.decisions = []

    def _check(self, label, text, **fields):
        return SimpleNamespace(axioms=self.axioms, timed_out=False)

    def _decision(self, event, **fields):
        self.decisions.append((event, fields))


BAD = ["propext", "Lean.ofReduceBool"]


def sketch() -> Sketch:
    return Sketch(
        [
            Lemma("root", "theorem root : True", proof="bv_decide", proved=True),
            Lemma("user", "theorem user : True", proof="exact root", proved=True),
            Lemma("clean", "theorem clean : True", proof="trivial", proved=True),
        ],
        "exact user",
    )


class ResponsibleLemmasTest(unittest.TestCase):
    def test_source_of_an_axiom_not_its_users(self):
        problems = Stage(
            {"root": BAD, "user": BAD, "clean": ["propext"]}
        ).responsible_lemmas(sketch(), "")
        self.assertEqual(list(problems), ["root"])
        self.assertIn("Lean.ofReduceBool", problems["root"])

    def test_declaration_missing_from_the_file(self):
        problems = Stage(
            {"user": [], "clean": []}
        ).responsible_lemmas(sketch(), "")
        self.assertEqual(list(problems), ["root"])

    def test_no_lemma_responsible(self):
        problems = Stage(
            {"root": [], "user": [], "clean": []}
        ).responsible_lemmas(sketch(), "")
        self.assertEqual(problems, {})


class RejectAssemblyTest(unittest.TestCase):
    def test_only_the_lemma_responsible_is_reopened(self):
        stage, plan = Stage({}), sketch()
        stage.reject_assembly(plan, {"errors": []}, {"root": "the problem"})
        root, user, clean = plan.lemmas
        self.assertFalse(root.proved)
        self.assertEqual(root.last_attempts, "bv_decide")
        self.assertEqual(root.assembly_failure, "the problem")
        self.assertTrue(user.proved and clean.proved)
        self.assertEqual(stage.decisions[0][0], "assembly_rejected")


if __name__ == "__main__":
    unittest.main()

"""Failed assemblies: the lemmas responsible and the withdrawal of proofs."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from aiprover_orchestration.agents.base import AgentCallError
from aiprover_orchestration.orchestrator.resume import ResumeState
from aiprover_orchestration.orchestrator.stages.assemble import (
    AssembleStage,
    checker,
)
from aiprover_orchestration.orchestrator.structures import Lemma, Sketch


class Stage(AssembleStage):
    config = SimpleNamespace(lean_timeout=300)
    state = ResumeState()

    def __init__(self, axioms: dict, timed_out: bool = False):
        self.axioms = axioms
        self.timed_out = timed_out
        self.timeouts = []
        self.decisions = []

    def _check(self, label, text, timeout=0, **fields):
        self.timeouts.append(timeout)
        return SimpleNamespace(axioms=self.axioms, timed_out=self.timed_out)

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


class AssemblyTimeoutTest(unittest.TestCase):

    def test_timeout_blames_no_lemma(self):
        with self.assertRaises(AgentCallError):
            Stage({}, timed_out=True).responsible_lemmas(sketch(), "text")

    def test_module_build_timeout_stops_the_run(self):
        stage = Stage({})
        stage.slug = "slug"
        stage.workspace = "workspace"
        built = SimpleNamespace(ok=False, timed_out=True, output="")
        with tempfile.TemporaryDirectory() as directory:
            stage.result_dir = Path(directory)
            with mock.patch.object(checker, "build", return_value=built):
                with self.assertRaises(AgentCallError):
                    stage._verify_modules(
                        SimpleNamespace(
                            definitions="",
                            preamble="",
                            statement="",
                            theorem_name="target",
                        ),
                        "body",
                    )
        self.assertEqual(stage.decisions[-1][0], "lean_timed_out")


class ResponsibleLemmasTest(unittest.TestCase):
    def test_source_of_an_axiom_not_its_users(self):
        problems = Stage(
            {"root": BAD, "user": BAD, "clean": ["propext"]}
        ).responsible_lemmas(sketch(), "")
        self.assertEqual(list(problems), ["root"])
        self.assertIn("Lean.ofReduceBool", problems["root"])

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

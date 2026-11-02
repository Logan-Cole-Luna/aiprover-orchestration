"""A replan keeps the proofs that survive a changed lemma."""

import unittest
from pathlib import Path
from types import SimpleNamespace

from aiprover_orchestration.orchestrator.resume import ResumeState
from aiprover_orchestration.orchestrator.stages.sketch import SketchStage
from aiprover_orchestration.orchestrator.structures import (
    Formalization,
    Lemma,
    Sketch,
)


class Stage(SketchStage):
    config = SimpleNamespace(lean_parallel=1, lean_timeout=300)
    state = ResumeState(lean_timeouts=1)
    result_dir = Path("/nonexistent")

    def __init__(self, new_sketch: Sketch, failing: str = "", timeout=False):
        self.new_sketch = new_sketch
        self.failing = failing
        self.timeout = timeout
        self.decisions = []

    def _excluded_statements(self, sketch):
        return {}

    def _clauses_text(self, focus, lemmas):
        return ""

    def _decision(self, event, **fields):
        self.decisions.append((event, fields))

    def sketch(self, *args):
        return self.new_sketch

    def _check_proof(
        self, form, context, lemma, helpers, proof, label, timeout
    ):
        self.context = context
        self.timeout_used = timeout
        result = SimpleNamespace(timed_out=self.timeout)
        return result, "fails" if lemma.name == self.failing else ""

    def reused(self):
        return [
            fields["lemma"]
            for event, fields in self.decisions
            if event == "lemma_reused"
        ]


def old_sketch() -> Sketch:
    return Sketch(
        [
            Lemma("base", "theorem base : 1 = 1", proof="rfl", proved=True),
            Lemma(
                "user",
                "theorem user : True",
                proof="have := base; trivial",
                proved=True,
            ),
            Lemma("open", "theorem open : True"),
        ],
        "exact user",
    )


def changed_sketch() -> Sketch:
    return Sketch(
        [
            Lemma("base", "theorem base : 2 = 2"),
            Lemma("user", "theorem user : True"),
            Lemma("open", "theorem open : True"),
        ],
        "exact user",
    )


class ChangedLemmaTest(unittest.TestCase):
    def test_a_proof_that_still_compiles_is_kept(self):
        stage = Stage(changed_sketch())
        sketch = stage.replan(None, old_sketch())
        user = sketch.lemmas[1]
        self.assertTrue(user.proved)
        self.assertEqual(stage.reused(), ["user"])
        self.assertIn("theorem base : 2 = 2", stage.context)
        self.assertNotIn(
            "lemma_proved", [event for event, _ in stage.decisions]
        )

    def test_a_proof_that_fails_is_reproved(self):
        stage = Stage(changed_sketch(), failing="user")
        sketch = stage.replan(None, old_sketch())
        self.assertFalse(sketch.lemmas[1].proved)
        self.assertEqual(stage.reused(), [])

    def survives_without_a_recheck(self, before, after):
        """Whether a proof that would fail a recheck stays proved."""
        statement = "theorem a : 1 = 1"
        old = Sketch(
            [
                Lemma(
                    "a", statement, proof="rfl", proved=True, definitions=before
                )
            ],
            "exact a",
        )
        new = Sketch([Lemma("a", statement, definitions=after)], "exact a")
        stage = Stage(new, failing="a")
        return stage.replan(None, old).lemmas[0].proved

    def test_a_changed_definition_rechecks_every_proof(self):
        self.assertFalse(
            self.survives_without_a_recheck(("def f := 1",), ("def f := 2",))
        )

class SketchReplyTest(unittest.TestCase):
    def rejected(self, reply, excluded=None, earlier=()):
        stage = Stage(None)
        stage.config = SimpleNamespace(max_repairs=0, sketch_judge=False)
        stage.row = {"informal_proof": ""}
        stage._captain = lambda *args, **fields: reply
        stage._standalone = lambda form, body: body
        stage._sketch_body = lambda form, sketch: ""
        stage._check = lambda *args, **fields: SimpleNamespace(
            ok=False, error_report=lambda: "compile error"
        )
        form = Formalization("", "", "target", " : True")
        with self.assertRaises(RuntimeError):
            SketchStage.sketch(stage, form, "", excluded, earlier)
        return stage.decisions[-1][1]["problems"]

    def test_a_definition_may_not_be_sorry(self):
        problems = self.rejected(
            "<lemmas>def f : Nat := sorry\n\n"
            "theorem a : f = f := by sorry</lemmas>"
            "<main_proof>trivial</main_proof>"
        )
        self.assertIn("forbidden construct in definitions: sorry", problems)

if __name__ == "__main__":
    unittest.main()

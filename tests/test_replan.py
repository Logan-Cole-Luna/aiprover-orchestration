"""A replan keeps the proofs that survive a changed lemma."""

import unittest
from types import SimpleNamespace

from aiprover_orchestration.orchestrator.stages.sketch import SketchStage
from aiprover_orchestration.orchestrator.structures import (
    Lemma,
    Sketch,
    carry_definitions,
    parse_lemmas,
    sketch_definitions,
)


class Stage(SketchStage):
    state = SimpleNamespace(load_attempts=lambda lemma: None)

    def __init__(self, new_sketch: Sketch, failing: str = ""):
        self.new_sketch = new_sketch
        self.failing = failing
        self.decisions = []
        self.recorded = []

    def _excluded_statements(self, sketch):
        return {}

    def _clauses_text(self, focus, lemmas):
        return ""

    def _decision(self, event, **fields):
        self.decisions.append((event, fields))

    def _record_proof(self, lemma, worker):
        self.recorded.append((lemma.name, worker))

    def sketch(self, *args):
        return self.new_sketch

    def _check_proof(self, form, context, lemma, helpers, proof, label):
        self.context = context
        return None, "fails" if lemma.name == self.failing else ""


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
        self.assertEqual(stage.recorded, [("user", "recheck")])
        self.assertIn("theorem base : 2 = 2", stage.context)

    def test_a_proof_that_fails_is_reproved(self):
        stage = Stage(changed_sketch(), failing="user")
        sketch = stage.replan(None, old_sketch())
        self.assertFalse(sketch.lemmas[1].proved)
        self.assertEqual(stage.recorded, [])


class SketchDefinitionTest(unittest.TestCase):
    def test_a_definition_stays_with_the_lemmas_that_use_it(self):
        block = "theorem a : f = 1 := by sorry"
        definition = "def f : Nat := 1"
        lemmas = parse_lemmas(block)
        new = sketch_definitions(definition + "\n\n" + block)
        carry_definitions(lemmas, [], new)
        self.assertEqual(lemmas[0].definitions, (definition,))

    def test_a_new_definition_replaces_an_earlier_one(self):
        earlier = Lemma("old", "theorem old : True", definitions=("def f := 1",))
        lemmas = parse_lemmas("theorem a : f = 2 := by sorry")
        carry_definitions(lemmas, [earlier], ("def f := 2",))
        self.assertEqual(lemmas[0].definitions, ("def f := 2",))


if __name__ == "__main__":
    unittest.main()

"""A replan keeps the proofs that survive a changed lemma."""

import unittest
from types import SimpleNamespace

from aiprover_orchestration.agents.base import AgentCallError
from aiprover_orchestration.orchestrator.resume import ResumeState
from aiprover_orchestration.orchestrator.stages.sketch import SketchStage
from aiprover_orchestration.orchestrator.structures import (
    Formalization,
    Lemma,
    Sketch,
    carry_definitions,
    parse_lemmas,
    reinstated,
    sketch_definitions,
    statement_key,
)


class Stage(SketchStage):
    config = SimpleNamespace(lean_parallel=1, lean_timeout=300)
    state = ResumeState(lean_timeouts=1)

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

    def test_a_recheck_timeout_stops_the_run(self):
        stage = Stage(changed_sketch(), timeout=True)
        with self.assertRaises(AgentCallError):
            stage.replan(None, old_sketch())
        self.assertEqual(stage.timeout_used, 600)
        self.assertEqual(stage.decisions[-1][0], "lean_timed_out")

    def test_a_kept_proof_may_use_a_lemma_placed_after_it(self):
        changed = changed_sketch()
        changed.lemmas.reverse()
        stage = Stage(changed)
        stage.replan(None, old_sketch())
        self.assertIn("theorem base : 2 = 2", stage.context)

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

    def test_an_added_instance_rechecks_every_proof(self):
        self.assertFalse(
            self.survives_without_a_recheck(
                (), ("instance : Inhabited Foo := ⟨0⟩",)
            )
        )

    def test_an_added_definition_keeps_proofs_without_a_recheck(self):
        self.assertTrue(
            self.survives_without_a_recheck((), ("def f : Nat := 1",))
        )


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

    def test_a_replacement_precedes_the_blocks_that_use_it(self):
        earlier = Lemma(
            "old",
            "theorem old : True",
            definitions=("def f := 1", "def g := f"),
        )
        lemmas = parse_lemmas("theorem a : g = 2 := by sorry")
        carry_definitions(lemmas, [earlier], ("def f := 2",))
        self.assertEqual(lemmas[0].definitions, ("def f := 2", "def g := f"))

    def test_an_instance_reaches_every_lemma(self):
        block = "instance : Inhabited Foo := ⟨.mk⟩"
        foo = "structure Foo"
        lemmas = parse_lemmas("theorem a : True := by sorry")
        carry_definitions(lemmas, [], sketch_definitions(f"{foo}\n\n{block}"))
        self.assertEqual(lemmas[0].definitions, (foo, block))

    def test_a_reinstated_lemma_takes_the_new_definition(self):
        earlier = [
            Lemma(
                "d",
                "theorem d : f = 1",
                proved=True,
                definitions=("def f := 1",),
            ),
            Lemma("a", "theorem a : True", proof="exact d", proved=True),
        ]
        lemmas = [
            Lemma("b", "theorem b : f = 2", definitions=("def f := 2",)),
            Lemma("a", "theorem a : True"),
        ]
        reinstated(earlier, lemmas)
        self.assertEqual(
            [lemma.definitions for lemma in lemmas],
            [("def f := 2",), ("def f := 2",), ()],
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

    def test_a_new_definition_lifts_an_exclusion(self):
        reply = (
            "<lemmas>def f : Nat := 2\n\n"
            "theorem a : f = 1 := by sorry</lemmas>"
            "<main_proof>trivial</main_proof>"
        )
        excluded = {statement_key("theorem a : f = 1"): "was refuted"}
        kept = Lemma(
            "a", "theorem a : f = 1", definitions=("def f : Nat := 2",)
        )
        self.assertIn("was refuted", self.rejected(reply, excluded, [kept])[0])
        replaced = Lemma(
            "a", "theorem a : f = 1", definitions=("def f : Nat := 1",)
        )
        self.assertEqual(
            self.rejected(reply, excluded, [replaced]), ["compile error"]
        )


if __name__ == "__main__":
    unittest.main()

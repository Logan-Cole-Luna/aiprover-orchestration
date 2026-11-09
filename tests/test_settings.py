"""Validation of the orchestrator settings of a submission
(server/settings.py), and the theorem-proving mode they select
(orchestrator/stages/draft.py)."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from aiprover_orchestration.orchestrator.stages.draft import (
    FIXED_VERDICT,
    WITHHELD_PROOF,
    DraftStage,
    negation,
)
from aiprover_orchestration.orchestrator.stages.sketch import SketchStage
from aiprover_orchestration.orchestrator.structures import Lemma, Sketch
from aiprover_orchestration.server import settings
from aiprover_orchestration.utils.vocabulary import modernize


class SettingsTest(unittest.TestCase):

    def test_values_are_checked(self):
        accepted = {"max_escalations": 5, "sketch_judge": False,
                    "aiprover_guidance": ""}
        self.assertEqual(settings.validate(accepted), accepted)
        for rejected in (
            {"workers": 0},
            {"workers": 2.5},
            {"workers": True},
            {"sketch_judge": 1},
            {"aiprover_guidance": "other"},
            {"lean_parallel": 8},
        ):
            with self.assertRaises(ValueError):
                settings.validate(rejected)
        self.assertEqual(settings.validate({"mode": "prove"}), {"mode": "prove"})
        # Settings renamed to be model-neutral keep their former names.
        self.assertEqual(settings.validate({"max_claude_usd": 5}), {"max_hosted_usd": 5})
        with self.subTest("records and settings under former names are read"):
            self.assertEqual(
                settings.validate({"max_handbacks": 3, "aiprover_direction": "captain"}),
                {"max_escalations": 3, "aiprover_guidance": "orchestrator"})
            recorded = {"config": {"agents": {"captain": {}, "auditor": {}}},
                        "steps": [{"event": "lemma_handback", "role": "solver",
                                   "label": "handback/lz_rank_of_directions",
                                   "lemma": "lz_rank_of_directions",
                                   "prompt": "the captain's direction"},
                                  {"label": "lz_rank_captain", "HANDBACK_TEMPLATE": "x"}]}
            self.assertEqual(modernize(recorded), {
                "config": {"agents": {"orchestrator": {}, "checker": {}}},
                "steps": [{"event": "lemma_escalation", "role": "prover",
                           "label": "escalation/lz_rank_of_directions",
                           "lemma": "lz_rank_of_directions",
                           "prompt": "the captain's direction"},
                          {"label": "lz_rank_orchestrator", "ESCALATION_TEMPLATE": "x"}]})
        checked = []
        stage = DraftStage()
        stage.row = {
            "informal_proof": "the source proof",
            "formal_statement": "import Mathlib\ndef double (n : ℕ) := 2 * n\n\n"
            "theorem given (n : ℕ) : double n = n + n := by sorry",
        }
        stage.library, stage.slug = "", "slug"
        stage._check = lambda label, text: checked.append(text) or SimpleNamespace(ok=True)
        stage._decision = lambda *args, **fields: None
        with self.subTest("the source proof is withheld"):
            stage.withhold_proof()
            self.assertEqual(stage.row["informal_proof"], WITHHELD_PROOF)
            self.assertIn("theorem given", stage.row["informal_statement"])
        with self.subTest("the formal statement is kept, renamed to the slug"):
            form = stage.fixed_formalization()
            self.assertEqual(form.verdict, FIXED_VERDICT)
            self.assertIn("def double", form.definitions)
            self.assertEqual(form.statement,
                             "theorem slug (n : ℕ) : double n = n + n := by sorry")
            self.assertIn(form.statement, checked[0])
        with self.subTest("one that does not compile ends the run"):
            stage._check = lambda label, text: SimpleNamespace(ok=False)
            self.assertIsNone(stage.fixed_formalization())
        with self.subTest("the judge and the reviewer know the proof is a draft"):
            stage.config = SimpleNamespace(mode="prove", open_subgoals=False)
            self.assertIn("own draft", stage.proof_for("judge"))
            self.assertIn("no reason for UNFAITHFUL", stage.proof_for("review"))
            stage.config.mode = "autoformalize"
            self.assertEqual(stage.proof_for("review"), stage.row["informal_proof"])
        with self.subTest("a counterexample rejects the sketch"):
            outputs = {
                "false_one": "error:\nFound a counter-example!\nn := 7\n-----\n",
                "untestable": "error: Failed to create a `testable` instance",
            }
            judge = SimpleNamespace(
                config=SimpleNamespace(lemma_counterexamples=True, lean_parallel=2),
                _standalone=lambda form, body: body,
                _decision=lambda *args, **fields: None,
                _check=lambda label, text, **fields: SimpleNamespace(
                    output=outputs[label.removeprefix("counterexample_")]
                ),
            )
            lemmas = [Lemma(name, f"theorem {name} : True") for name in outputs]
            found = SketchStage._counterexamples(judge, None, Sketch(lemmas, "trivial"))
            self.assertIn("`false_one` is false", found)
            self.assertIn("n := 7", found)
            self.assertNotIn("untestable", found)
        with self.subTest("the judge blocks on blocking issues only and recalls them"):
            seen = []
            replies = iter([
                "<verdict>REVISE</verdict><issues>B1 misses a case</issues>"
                "<notes>split A</notes><suspect>none</suspect>",
                "<verdict>ACCEPT</verdict><issues>none</issues>"
                "<notes>split A</notes><suspect>none</suspect>",
            ])
            judge = SimpleNamespace(
                config=SimpleNamespace(sketch_judge=True),
                _counterexamples=lambda form, sketch: "",
                _test_suspects=lambda *args: "",
                _decision=lambda *args, **fields: None,
                proof_for=lambda reader: "draft",
                _orchestrator=lambda prompt, phase: seen.append(prompt) or next(replies),
            )
            sketch = Sketch([Lemma("a", "theorem a : True")], "trivial")
            self.assertIn("misses a case", SketchStage._judge_sketch(judge, form, sketch, 0))
            self.assertEqual(SketchStage._judge_sketch(judge, form, sketch, 1), "")
            self.assertIn("misses a case", seen[1])
        with self.subTest("the draft is reused on resume"):
            with tempfile.TemporaryDirectory() as folder:
                stage.result_dir = Path(folder)
                (stage.result_dir / "draft.md").write_text("kept\n")
                self.assertEqual(stage.draft_proof(form), "kept\n")
        with self.subTest("a statement judged false becomes its negation"):
            self.assertEqual(negation(" : P "), " : ¬ (P) ")
            self.assertEqual(negation(" {n : ℕ} (h : 0 < n) : f n = (1 : ℕ) "),
                             " : ¬ (∀ {n : ℕ} (h : 0 < n), f n = (1 : ℕ)) ")
            self.assertEqual(settings.validate({"disproof": True}), {"disproof": True})
            replies = []
            stage.agents = SimpleNamespace(
                complete=lambda *args, **fields: replies.append(1)
                or "<reason>n = 0 fails</reason><side>disprove</side>")
            stage._own_definitions = lambda form: form.definitions
            stage._check = lambda label, text: checked.append(text) or SimpleNamespace(ok=True)
            with tempfile.TemporaryDirectory() as folder:
                stage.result_dir = Path(folder)
                negated = stage.choose_side(stage.fixed_formalization())
                self.assertEqual(negated.statement, "theorem slug : ¬ "
                                 "(∀ (n : ℕ), double n = n + n) := by sorry")
                self.assertIn(negated.statement, checked[-1])
                self.assertEqual(stage.row["side"], "disprove")
                # A resumed run keeps the choice without asking again.
                stage.choose_side(stage.fixed_formalization())
                self.assertEqual(len(replies), 1)

if __name__ == "__main__":
    unittest.main()

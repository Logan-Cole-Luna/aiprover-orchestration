"""The AIProver phase with a fake solver and real Lean: the gate, helper
renaming, and assembly of the proved lemmas."""

import threading
import unittest

from aiprover_orchestration.orchestrator.config import Config
from aiprover_orchestration.orchestrator.structures import (
    Lemma,
    Sketch,
    statement_key,
)
from tests.support import (
    FORM,
    FakeSolver,
    ScriptedPool,
    TemporaryDirectoryTest,
    requires_lean,
)

# `lem_a` and `lem_b` each come with a helper named `aux`; `lem_c` hides
# `sorry` behind `stop`, which Lean accepts with a warning only.
ANSWERS = {
    "lem_a": "theorem aux (n : Nat) : n + 0 = n := rfl\n\n"
    "theorem lem_a (n : Nat) : n + 0 = n := by\n  exact aux n\n",
    "lem_b": "theorem aux : (2 : Nat) = 2 := rfl\n\n"
    "theorem lem_b : (2 : Nat) = 2 := by\n  exact aux\n",
    "lem_c": "theorem lem_c : (3 : Nat) = 3 := by\n  stop\n  rfl\n",
}


@requires_lean
class AIProverPhaseTest(TemporaryDirectoryTest):

    def test_gate(self):
        solver = FakeSolver(ANSWERS)
        run = self.orchestration(
            ScriptedPool(solver=solver, budget=0),
            Config(aiprover_handback_after=0),
        )
        lemmas = [
            Lemma("lem_a", "theorem lem_a (n : Nat) : n + 0 = n"),
            Lemma("lem_b", "theorem lem_b : (2 : Nat) = 2"),
            Lemma("lem_c", "theorem lem_c : (3 : Nat) = 3"),
        ]
        sketch = Sketch(lemmas, "trivial")
        run._prove_with_aiprover(FORM, sketch, list(enumerate(lemmas)))
        a, b, c = lemmas
        self.assertTrue(a.proved and b.proved)
        self.assertIn("theorem lem_a_aux", a.helpers)
        self.assertIn("theorem lem_b_aux", b.helpers)
        self.assertFalse(c.proved)
        self.assertIn("sorry", c.last_errors)
        self.assertEqual(c.attempts, 1)
        self.assertEqual(run.state.attempts[statement_key(c.statement)], 1)
        # The phase leaves no thread behind.
        self.assertEqual(
            [
                thread.name
                for thread in threading.enumerate()
                if thread.name.startswith("ThreadPool")
            ],
            [],
        )
        sketch.lemmas = [a, b]
        body = run._sketch_body(FORM, sketch, use_proofs=True)
        result = run.lean.check_text(
            run._standalone(FORM, body), self.directory / "assembled.lean"
        )
        self.assertTrue(result.ok, result.errors[:1])
        self.assertFalse(result.has_sorry_warning)

    def test_exhausted_lemma_starts_no_job(self):
        solver = FakeSolver(ANSWERS)
        run = self.orchestration(ScriptedPool(solver=solver, budget=0))
        lemma = Lemma("lem_a", "theorem lem_a (n : Nat) : n + 0 = n")
        run.state.count_attempt(lemma)
        run._prove_with_aiprover(
            FORM, Sketch([lemma], "trivial"), [(0, lemma)]
        )
        self.assertFalse(lemma.proved)
        self.assertNotIn(
            "aiprover_job_started",
            [event for event, _ in self.decisions(run)],
        )


if __name__ == "__main__":
    unittest.main()

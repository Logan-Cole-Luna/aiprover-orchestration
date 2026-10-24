"""The AIProver phase with a fake solver and real Lean: the gate, helper
renaming, and assembly of the proved lemmas."""

import threading
import unittest

from aiprover_orchestration.agents.aiprover import AIProverAgent
from aiprover_orchestration.lean.text import normalize
from aiprover_orchestration.orchestrator.config import Config
from aiprover_orchestration.orchestrator.stages.aiprover import named_lemmas
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
    aiprover_phase,
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


class DirectionTest(TemporaryDirectoryTest):

    def test_direction_once_per_statement(self):
        lemma = Lemma("lem_a", "theorem lem_a (n : Nat) : n + 0 = n")
        sketch = Sketch([lemma], "trivial")
        run = self.orchestration(ScriptedPool(["1. Apply `add_zero`."]))
        phase = aiprover_phase(run, sketch)
        key = (lemma.name, normalize(lemma.statement))
        proof = run._directed_proof(phase, lemma, "context", key)
        self.assertIn("1. Apply `add_zero`.", proof)
        self.assertTrue(proof.endswith("p"))
        # Later jobs and a resumed phase reuse the direction.
        self.assertEqual(run._directed_proof(phase, lemma, "", key), proof)
        self.assertEqual(run.agents.phases, ["direction/lem_a"])
        resumed = aiprover_phase(run, sketch)
        self.assertEqual(resumed.directions, phase.directions)

    def test_source_proof_without_budget(self):
        lemma = Lemma("lem_a", "theorem lem_a (n : Nat) : n + 0 = n")
        run = self.orchestration(ScriptedPool(budget=0))
        phase = aiprover_phase(run, Sketch([lemma], "trivial"))
        key = (lemma.name, normalize(lemma.statement))
        self.assertEqual(run._directed_proof(phase, lemma, "", key), "p")
        self.assertEqual(run.agents.phases, [])

    def test_direction_from_sketch_comments(self):
        lemma = Lemma("lem_a", "theorem lem_a (n : Nat) : n + 0 = n")
        run = self.orchestration(
            ScriptedPool(), Config(aiprover_direction="sketch")
        )
        run.trace.add(
            "model_call",
            role="captain",
            response="-- Use `add_zero`.\n-- Then close by rfl.\n"
            "theorem lem_a (n : Nat) : n + 0 = n := by sorry\n",
        )
        phase = aiprover_phase(run, Sketch([lemma], "trivial"))
        key = (lemma.name, normalize(lemma.statement))
        proof = run._directed_proof(phase, lemma, "", key)
        self.assertIn("Use `add_zero`.\nThen close by rfl.", proof)
        self.assertEqual(run.agents.phases, [])

    def test_cited_lemmas_from_direction(self):
        earlier = [
            Lemma("lem_a", "theorem lem_a (n : Nat) : n + 0 = n"),
            Lemma("lem_ab", "theorem lem_ab : True"),
        ]
        run = self.orchestration(ScriptedPool())
        block = run._cited_lemmas(earlier, "1. Rewrite with `lem_a`.")
        self.assertIn("theorem lem_a (n : Nat) : n + 0 = n", block)
        self.assertNotIn("lem_ab", block)
        self.assertEqual(run._cited_lemmas(earlier, "1. By `simp`."), "")
        off = self.orchestration(
            ScriptedPool(), Config(aiprover_cite_lemmas=False)
        )
        self.assertEqual(off._cited_lemmas(earlier, "by lem_a"), "")


    def test_condensed_prompt(self):
        lemma = Lemma("lem_b", "theorem lem_b (n : Nat) : n + 0 = n")
        earlier = [
            Lemma("lem_a", "theorem lem_a (n : Nat) : n + 0 = n"),
            Lemma("lem_c", "theorem lem_c : True"),
        ]
        run = self.orchestration(
            ScriptedPool(["1. Apply `lem_a`."]),
            Config(aiprover_condensed_prompt=True),
        )
        phase = aiprover_phase(run, Sketch([*earlier, lemma], "trivial"))
        key = (lemma.name, normalize(lemma.statement))
        proof = run._directed_proof(phase, lemma, "context", key)
        self.assertIn("1. Apply `lem_a`.", proof)
        self.assertNotIn("source's proof", proof)
        cited = named_lemmas(earlier, proof)
        self.assertEqual([other.name for other in cited], ["lem_a"])
        stubs, context = run._job_context(phase, cited, lemma)
        self.assertIn("theorem lem_a", context)
        self.assertNotIn("lem_c", context)


class ProbeTest(TemporaryDirectoryTest):

    def test_changed_files_of_running_sessions(self):
        agent = object.__new__(AIProverAgent)
        agent.work_root = self.directory
        job = self.directory / "jobs" / "job1"
        for session in ("s0", "s1"):
            (job / session / "proj").mkdir(parents=True)
            (job / session / "proj" / "Work.lean").write_text("theorem a")
        # A session that has ended is gated through its summary instead.
        (job / "s1" / "summary.json").write_text("{}")
        probed = {}
        first = agent._changed("job1", probed)
        self.assertEqual([(s.index, s.status) for s in first], [(0, "running")])
        self.assertEqual(agent._changed("job1", probed), [])
        (job / "s0" / "proj" / "Work.lean").write_text("theorem a := rfl")
        self.assertEqual(len(agent._changed("job1", probed)), 1)

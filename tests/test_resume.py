"""Reconstruction of a run's state from its trace (`restore_state`)."""

import unittest

from aiprover_orchestration.orchestrator.resume import restore_state
from aiprover_orchestration.orchestrator.structures import (
    Lemma,
    statement_key,
)
from aiprover_orchestration.orchestrator.trace import Trace
from tests.support import RESULTS


def decision(event: str, **fields) -> dict:
    return {"kind": "decision", "event": event, **fields}


def job(lemma: str, job_id: str, *statuses: str) -> list[dict]:
    """An AIProver job on `lemma` that ended with samples of `statuses`
    without a proof."""
    return [
        {
            "kind": "model_call",
            "backend": "aiprover",
            "lemma": lemma,
            "aiprover_job": job_id,
            "samples": [{"status": status} for status in statuses],
        },
        decision("solver_budget_exhausted", lemma=lemma, aiprover_job=job_id),
    ]


SKETCH = decision(
    "sketch_accepted",
    lemmas=["theorem a (n : Nat) : n = n", "theorem b : True"],
    main_proof="trivial",
)


class RecordedTracesTest(unittest.TestCase):
    """Every trace in results/ replays to a consistent state."""

    def test_replay(self):
        paths = sorted(RESULTS.glob("*/trace.json"))
        if not paths:
            self.skipTest("no recorded traces")
        for path in paths:
            with self.subTest(run=path.parent.name):
                document = Trace.load(path)
                state = restore_state(document)
                self.assertEqual(state, restore_state(document))
                if state.sketch is None:
                    continue
                lemmas = state.sketch.lemmas
                names = {lemma.name for lemma in lemmas}
                self.assertEqual(len(names), len(lemmas))
                for lemma in lemmas:
                    if lemma.proved:
                        self.assertTrue(lemma.proof.strip(), lemma.name)
                    self.assertEqual(
                        lemma.attempts,
                        state.attempts.get(statement_key(lemma.statement), 0),
                    )


class AttemptBudgetTest(unittest.TestCase):

    def test_attempts_follow_the_statement_across_sketches(self):
        renamed = decision(
            "sketch_accepted",
            lemmas=["theorem c (n : Nat) :  n = n", "theorem b : True"],
            main_proof="trivial",
        )
        steps = [
            SKETCH,
            *job("a", "j1", "rejected"),
            *job("a", "j2", "rejected"),
            decision("replan"),
            renamed,
        ]
        state = restore_state({"steps": steps})
        c, b = state.sketch.lemmas
        self.assertEqual((c.name, c.attempts), ("c", 2))
        self.assertEqual(b.attempts, 0)

    def test_cut_off_jobs_are_not_attempts(self):
        steps = [
            SKETCH,
            *job("a", "j1", "cancelled", "infra"),
            *job("a", "j2", "rejected", "cancelled"),
        ]
        state = restore_state({"steps": steps})
        self.assertEqual(state.sketch.lemmas[0].attempts, 1)

    def test_chat_solver_phases_are_attempts(self):
        steps = [SKETCH, decision("lemma_failed", lemma="b", attempts=1)]
        state = restore_state({"steps": steps})
        self.assertEqual(state.sketch.lemmas[1].attempts, 1)

    def test_retry_records_the_review(self):
        steps = [
            SKETCH,
            *job("a", "j1", "rejected"),
            decision(
                "lemma_handback", lemma="a", action="retry", attempts=1
            ),
        ]
        a = restore_state({"steps": steps}).sketch.lemmas[0]
        self.assertEqual((a.attempts, a.reviewed_at), (1, 1))
        self.assertFalse(a.handed_back)

    def test_restated_lemma_starts_its_own_count(self):
        steps = [
            SKETCH,
            *job("a", "j1", "rejected"),
            decision(
                "lemma_handback",
                lemma="a",
                action="restate",
                statement="theorem a (n : Nat) : n + 0 = n",
            ),
        ]
        a = restore_state({"steps": steps}).sketch.lemmas[0]
        self.assertEqual(a.attempts, 0)

    def test_state_methods(self):
        state = restore_state({"steps": [SKETCH]})
        a = state.sketch.lemmas[0]
        state.count_attempt(a)
        state.count_attempt(a)
        state.record_review(a)
        copy = Lemma("copy", "theorem copy (n : Nat) : n = n")
        state.load_attempts(copy)
        self.assertEqual((copy.attempts, copy.reviewed_at), (2, 2))


class SketchChangesTest(unittest.TestCase):

    def test_split_and_backjump(self):
        steps = [
            SKETCH,
            decision(
                "lemma_handback",
                lemma="b",
                action="split",
                new_lemmas=["theorem b1 : True"],
            ),
            decision("lemma_proved", lemma="b", helpers="", proof="exact b1"),
        ]
        lemmas = restore_state({"steps": steps}).sketch.lemmas
        self.assertEqual([lemma.name for lemma in lemmas], ["a", "b1", "b"])
        self.assertEqual(lemmas[1].parent, "b")
        self.assertTrue(lemmas[2].proved)
        steps.append(
            decision("backjump", lemma="b", statement="theorem b : 1 = 1")
        )
        lemmas = restore_state({"steps": steps}).sketch.lemmas
        self.assertEqual([lemma.name for lemma in lemmas], ["a", "b"])
        self.assertFalse(lemmas[1].proved)
        self.assertEqual(lemmas[1].statement, "theorem b : 1 = 1")

    def test_proof_kept_across_a_replan_needs_its_lemmas(self):
        steps = [
            SKETCH,
            decision("lemma_proved", lemma="b", helpers="", proof="exact a 0"),
            decision("replan"),
            decision(
                "sketch_accepted",
                lemmas=["theorem b : True"],
                main_proof="trivial",
            ),
        ]
        b = restore_state({"steps": steps}).sketch.lemmas[0]
        self.assertFalse(b.proved)  # its proof used `a`, which is gone

    def test_interrupted_job_is_resumable(self):
        steps = [
            SKETCH,
            decision(
                "aiprover_job_started",
                lemma="a",
                statement="theorem a (n : Nat) : n = n",
                aiprover_job="j1",
            ),
        ]
        state = restore_state({"steps": steps})
        self.assertEqual(
            state.resumable_jobs,
            {("a", "theorem a (n : Nat) : n = n"): "j1"},
        )
        steps += job("a", "j1", "rejected")
        self.assertEqual(restore_state({"steps": steps}).resumable_jobs, {})


if __name__ == "__main__":
    unittest.main()

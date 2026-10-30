"""Reconstruction of a run's state from its trace (`restore_state`)."""

import unittest

from aiprover_orchestration.orchestrator.resume import restore_state
from aiprover_orchestration.orchestrator.structures import (
    Lemma,
    reinstated,
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

    def test_dropped_lemma_reinstated_for_a_kept_proof(self):
        a = Lemma("a", "theorem a (n : Nat) : n = n")
        b = Lemma("b", "theorem b : True", proof="exact a 0", proved=True)
        lemmas = [Lemma("c", "theorem c : 1 = 1"), Lemma("b", b.statement)]
        added = reinstated([a, b], lemmas)
        self.assertEqual([lemma.name for lemma in lemmas], ["c", "a", "b"])
        self.assertEqual([(lemma.name, before) for lemma, before in added],
                         [("a", "b")])
        a.refutation = "intro h; exact absurd h id"
        lemmas = [Lemma("b", b.statement)]
        self.assertEqual(reinstated([a, b], lemmas), [])  # `a` is false
        steps = [
            SKETCH,
            decision("lemma_proved", lemma="b", helpers="", proof="exact a 0"),
            decision("replan"),
            decision(
                "sketch_accepted",
                lemmas=["theorem c : 1 = 1", "theorem b : True"],
                main_proof="trivial",
            ),
            decision(
                "lemma_reinstated",
                lemma="a",
                statement=a.statement,
                before="b",
                attempts=5,
            ),
        ]
        state = restore_state({"steps": steps})
        restored = {lemma.name: lemma for lemma in state.sketch.lemmas}
        self.assertEqual(list(restored), ["c", "a", "b"])
        self.assertTrue(restored["b"].proved)
        self.assertEqual(restored["a"].reviewed_at, 5)
        # The trace records no reuse of `b`'s proof in the new sketch.
        self.assertEqual(state.unrecorded_proofs, ["b"])
        steps.append(decision("lemma_reused", lemma="b"))
        self.assertEqual(restore_state({"steps": steps}).unrecorded_proofs, [])

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

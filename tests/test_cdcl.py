"""The lemma tree and learned clauses of conflict-driven learning."""

import unittest

from aiprover_orchestration.orchestrator import cdcl
from aiprover_orchestration.orchestrator.structures import Lemma


def tree() -> list[Lemma]:
    """`root` split into `a` and `b`, and `a` split into `a1`; `root` and
    `a` are proved from their children."""
    lemmas = [Lemma("root", "theorem root : True", proved=True)]
    cdcl.insert_split(
        lemmas,
        lemmas[0],
        [Lemma("a", "theorem a : True"), Lemma("b", "theorem b : True")],
    )
    cdcl.insert_split(lemmas, lemmas[0], [Lemma("a1", "theorem a1 : True")])
    lemmas[1].proved = True  # a
    return lemmas


class TreeTest(unittest.TestCase):

    def test_split_places_children_before_parent(self):
        lemmas = tree()
        self.assertEqual(
            [lemma.name for lemma in lemmas], ["a1", "a", "b", "root"]
        )
        self.assertEqual(
            [lemma.name for lemma in cdcl.subtree("root", lemmas)],
            ["a", "a1", "b"],
        )
        a1 = lemmas[0]
        self.assertEqual(
            [lemma.name for lemma in cdcl.ancestors(a1, lemmas)],
            ["a", "root"],
        )

    def test_retract_reopens_the_decision(self):
        lemmas = tree()
        root = lemmas[-1]
        root.attempts = 3
        removed = cdcl.retract(root, lemmas)
        self.assertEqual(
            sorted(lemma.name for lemma in removed), ["a", "a1", "b"]
        )
        self.assertEqual(lemmas, [root])
        self.assertFalse(root.proved)
        self.assertEqual(root.attempts, 0)

    def test_conflict_needs_enough_stuck_leaves(self):
        lemmas = tree()
        a1, b = lemmas[0], lemmas[2]
        a1.handed_back = True
        self.assertIsNone(cdcl.conflict(a1, lemmas, 2, 2, set()))
        b.attempts = 4  # two hand-back intervals
        decision, stuck = cdcl.conflict(a1, lemmas, 2, 2, set())
        self.assertEqual(decision.name, "root")
        self.assertEqual({lemma.name for lemma in stuck}, {"a1", "b"})
        analysed = {cdcl.conflict_key(decision, stuck)}
        self.assertIsNone(cdcl.conflict(a1, lemmas, 2, 2, analysed))


class SketchConflictTest(unittest.TestCase):
    """Flat sketches: no lemma has a parent, so the tree finds no conflict."""

    def flat(self, stuck: int, other: int = 0) -> list[Lemma]:
        lemmas = [
            Lemma(f"s{i}", f"theorem s{i} : Enc = 1", attempts=2)
            for i in range(stuck)
        ]
        return lemmas + [
            Lemma(f"o{i}", f"theorem o{i} : True") for i in range(other)
        ]

    def test_stuck_lemmas_sharing_a_declaration_conflict_at_it(self):
        lemmas = self.flat(3, 2)
        decision, leaves = cdcl.sketch_conflict(lemmas, {"Enc"}, 3, set())
        self.assertEqual(decision.name, "Enc")
        self.assertNotIn(decision, lemmas)
        self.assertEqual(len(leaves), 3)

    def test_the_narrowest_shared_declaration_is_chosen(self):
        lemmas = self.flat(3, 4)
        lemmas[0].statement = "theorem s0 : Enc = Sub"
        lemmas[1].statement = "theorem s1 : Enc = Sub"
        lemmas[2].statement = "theorem s2 : Enc = Sub"
        lemmas[3].statement = "theorem o0 : Enc = 1"
        decision, _ = cdcl.sketch_conflict(lemmas, {"Enc", "Sub"}, 3, set())
        self.assertEqual(decision.name, "Sub")

    def test_the_setting_of_the_theorem_is_not_a_decision(self):
        self.assertIsNone(cdcl.sketch_conflict(self.flat(3), {"Enc"}, 3, set()))

    def test_many_stuck_lemmas_conflict_with_the_sketch(self):
        lemmas = self.flat(4)
        decision, leaves = cdcl.sketch_conflict(lemmas, set(), 3, set())
        self.assertEqual(decision.name, "sketch")
        analysed = {cdcl.conflict_key(decision, leaves)}
        self.assertIsNone(cdcl.sketch_conflict(lemmas, set(), 3, analysed))

    def test_one_failed_job_is_not_stuck(self):
        lemmas = self.flat(4)
        for lemma in lemmas:
            lemma.attempts = 1
        self.assertIsNone(cdcl.sketch_conflict(lemmas, set(), 3, set()))


class ClauseStoreTest(unittest.TestCase):

    def test_from_steps(self):
        steps = [
            {
                "kind": "decision",
                "event": "sketch_accepted",
                "lemmas": ["theorem a : 1 = 2"],
            },
            {
                "kind": "decision",
                "event": "lemma_handback",
                "lemma": "a",
                "action": "refute",
            },
            {
                "kind": "decision",
                "event": "clause_learned",
                **cdcl.clause_fields(
                    cdcl.LearnedClause("failed_route", "theorem r : x", "y")
                ),
            },
        ]
        store = cdcl.ClauseStore.from_steps(steps)
        self.assertEqual(
            store.refuted_statements(), {"theorem a : 1 = 2": "a"}
        )
        self.assertEqual(len(store.clauses()), 2)


if __name__ == "__main__":
    unittest.main()

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

    def test_context_leaves_out_other_branches(self):
        lemmas = [Lemma(name, f"theorem {name} : True") for name in "xy"]
        cdcl.insert_split(
            lemmas, lemmas[0], [Lemma(n, f"theorem {n} : True") for n in "pq"]
        )
        cdcl.insert_split(
            lemmas, lemmas[3], [Lemma(n, f"theorem {n} : True") for n in "rs"]
        )
        self.assertEqual(
            [lemma.name for lemma in lemmas], ["p", "q", "x", "r", "s", "y"]
        )
        self.assertEqual(
            [lemma.name for lemma in cdcl.context_lemmas(lemmas[4], lemmas)],
            ["x", "r"],
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

    def test_refuted_leaf_is_a_conflict_alone(self):
        lemmas = tree()
        lemmas[2].refutation = "proof of the negation"
        decision, stuck = cdcl.conflict(lemmas[2], lemmas, 3, 2, set())
        self.assertEqual(decision.name, "root")
        self.assertEqual([lemma.name for lemma in stuck], ["b"])

    def test_decision_used_outside_its_subtree_is_skipped(self):
        lemmas = tree()
        lemmas[0].handed_back = True
        lemmas[2].handed_back = True
        user = Lemma("user", "theorem user : True", proof="exact a", proved=True)
        lemmas.append(user)
        # `a` is used by `user`, so a conflict under `a` goes to `root`.
        decision, _ = cdcl.conflict(lemmas[0], lemmas, 1, 2, set())
        self.assertEqual(decision.name, "root")


class ClauseStoreTest(unittest.TestCase):

    def test_duplicates_are_learned_once(self):
        store = cdcl.ClauseStore()
        clause = cdcl.LearnedClause("fact", "", "use Nat.rec", "a")
        self.assertTrue(store.add(clause))
        self.assertFalse(
            store.add(cdcl.LearnedClause("fact", "", " use  Nat.rec ", "b"))
        )
        self.assertIn("use Nat.rec", store.text())

    def test_text_stays_within_its_budget(self):
        store = cdcl.ClauseStore()
        for index in range(100):
            store.add(cdcl.LearnedClause("fact", "", f"fact {index} " * 20))
        text = store.text()
        self.assertLessEqual(len(text), cdcl.MAX_CLAUSES_CHARS + 200)
        self.assertIn("fact 99", text)  # newest first

    def test_text_ranks_the_branch_before_newer_clauses(self):
        lemmas = tree()  # a1, a, b, root
        store = cdcl.ClauseStore()
        store.add(cdcl.LearnedClause("fact", "", "route for a1 " * 30, "a1"))
        for index in range(30):
            store.add(
                cdcl.LearnedClause("fact", "", f"other {index} " * 30, "x")
            )
        store.add(cdcl.LearnedClause("refuted", "theorem z : False", "neg", "z"))
        text = store.text([lemmas[2]], lemmas)  # b, a sibling of a
        lines = text.splitlines()[1:]
        self.assertTrue(lines[0].startswith("- [refuted]"))
        # a1 is not on b's branch, so newer clauses crowd it out there ...
        self.assertNotIn("route for a1", text)
        # ... while on a1's own branch it outranks every newer clause.
        own = store.text([lemmas[0]], lemmas)
        self.assertIn("route for a1", own.splitlines()[2])

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

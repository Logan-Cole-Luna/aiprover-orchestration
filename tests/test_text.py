"""Lean text handling: comments, statement keys, filters."""

import unittest

from aiprover_orchestration.lean.checker import forbidden_problems
from aiprover_orchestration.lean.text import statements_only


class ForbiddenTest(unittest.TestCase):

    def test_constructs(self):
        self.assertEqual(
            forbidden_problems("exact sorryAx _"),
            ["forbidden construct: sorryAx"],
        )
        self.assertTrue(forbidden_problems("by native_decide"))
        self.assertTrue(forbidden_problems("#eval IO.println 1"))
        self.assertTrue(forbidden_problems("axiom cheat : False"))

class FilterEvasionTest(unittest.TestCase):

    def test_commands_are_found_wherever_they_start(self):
        for text in (
            'open Lean in elab "x" : tactic => pure ()',
            "open Nat in axiom bad : False",
            "example : True := trivial axiom bad : False",
        ):
            self.assertTrue(forbidden_problems(text), text)
        self.assertFalse(forbidden_problems("theorem h_axiom : Nat.axiom := x"))


class StatementsOnlyTest(unittest.TestCase):

    def test_statements_keep_what_the_definitions_need(self):
        self.assertIn(
            "let n := 3; n + 0 = n := by sorry",
            statements_only("theorem t : let n := 3; n + 0 = n := by simp"),
        )
        text = statements_only(
            "theorem pos : 0 < 1 := by decide\n"
            "instance : Fact (0 < 1) := ⟨pos⟩\n"
            "theorem other : True := trivial\n"
            "notation \"T\" => True\n"
            "attribute [simp] other\n",
            cited="",
        )
        self.assertIn("theorem pos : 0 < 1 := by sorry", text)
        self.assertIn('notation "T" => True', text)
        self.assertNotIn("other", text)

if __name__ == "__main__":
    unittest.main()

"""Lean text handling: comments, statement keys, filters."""

import unittest

from aiprover_orchestration.lean.checker import forbidden_problems


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

    def test_attributes_do_not_hide_declarations(self):
        self.assertTrue(forbidden_problems("@[simp] axiom a : False"))
        self.assertTrue(forbidden_problems("attribute [command_elab x] y"))

    def test_commands_are_found_wherever_they_start(self):
        for text in (
            'open Lean in elab "x" : tactic => pure ()',
            "open Nat in axiom bad : False",
            "example : True := trivial axiom bad : False",
        ):
            self.assertTrue(forbidden_problems(text), text)
        self.assertFalse(forbidden_problems("theorem h_axiom : Nat.axiom := x"))


if __name__ == "__main__":
    unittest.main()

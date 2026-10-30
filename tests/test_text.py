"""Lean text handling: comments, statement keys, filters."""

import unittest

from aiprover_orchestration.lean.checker import forbidden_problems
from aiprover_orchestration.lean.text import declarations, strip_comments
from aiprover_orchestration.orchestrator.reports.report import (
    ReportText,
    escape,
    forbidden_latex,
)
from aiprover_orchestration.orchestrator.structures import statement_key

SOURCE = """theorem a : True := by -- a sorry in a comment
  /- block /- nested -/ sorry -/
  trivial
def s := "/- not a comment"
theorem b (h' : 1 = 1) : True := trivial
"""


class CommentsTest(unittest.TestCase):

    def test_comments_removed_and_strings_kept(self):
        text = strip_comments(SOURCE)
        self.assertNotIn("sorry", text)
        self.assertIn('"/- not a comment"', text)
        self.assertIn("theorem b (h' : 1 = 1)", text)


class StatementKeyTest(unittest.TestCase):

    def test_name_and_layout_do_not_matter(self):
        self.assertEqual(
            statement_key("theorem foo (n : Nat) :\n    n = n"),
            statement_key("lemma   bar (n : Nat) : n = n"),
        )
        self.assertNotEqual(
            statement_key("theorem foo (n : Nat) : n = n"),
            statement_key("theorem foo (n : Nat) : n = n + 0"),
        )


class ForbiddenTest(unittest.TestCase):

    def test_constructs(self):
        self.assertEqual(
            forbidden_problems("exact sorryAx _"),
            ["forbidden construct: sorryAx"],
        )
        self.assertTrue(forbidden_problems("by native_decide"))
        self.assertTrue(forbidden_problems("#eval IO.println 1"))
        self.assertTrue(forbidden_problems("axiom cheat : False"))

    def test_statements_may_be_sorry(self):
        stub = "theorem x : True := by sorry"
        self.assertTrue(forbidden_problems(stub))
        self.assertFalse(forbidden_problems(stub, statements=True))


class FilterEvasionTest(unittest.TestCase):

    def test_guillemet_identifiers_do_not_open_comments(self):
        text = "def «/-» := 1\n#eval 1\ndef «-/» := 2\n"
        self.assertTrue(forbidden_problems(text))

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


class LatexTest(unittest.TestCase):

    def test_escape_leaves_no_command(self):
        escaped = escape(r"\input{/etc/passwd} ^^5c a_b 50% {x}")
        self.assertNotRegex(escaped, r"\\input")
        self.assertNotIn("^^", escaped)
        self.assertFalse(
            forbidden_latex(ReportText("t", "a", "", escaped)),
        )

    def test_forbidden_latex(self):
        for text in (r"\input{x}", r"\write18{ls}", "^^5cinput", r"\csname"):
            self.assertTrue(
                forbidden_latex(ReportText("t", "a", "", text)), text
            )


if __name__ == "__main__":
    unittest.main()

"""Lean text handling: comments, declarations, statement keys, filters."""

import unittest

from aiprover_orchestration.lean.checker import forbidden_problems
from aiprover_orchestration.lean.text import (
    declaration_names,
    declarations,
    strip_comments,
)
from aiprover_orchestration.orchestrator.extraction import prefixed_helpers
from aiprover_orchestration.orchestrator.reports.report import (
    ReportText,
    escape,
    forbidden_latex,
)
from aiprover_orchestration.orchestrator.structures import (
    parse_lemmas,
    recorded_lemmas,
    statement_key,
)

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

    def test_declarations(self):
        self.assertEqual(
            declarations(SOURCE),
            [("theorem", "a", 1), ("def", "s", 4), ("theorem", "b", 5)],
        )
        self.assertEqual(declaration_names(SOURCE), ["a", "s", "b"])


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

    def test_parsed_and_recorded_lemmas(self):
        lemmas = parse_lemmas(
            "theorem p (n : Nat) : n = n := by sorry\n\n"
            "@[simp] lemma q : True := sorry\n"
        )
        self.assertEqual([lemma.name for lemma in lemmas], ["p", "q"])
        recorded = recorded_lemmas([lemma.statement for lemma in lemmas])
        self.assertEqual(
            [statement_key(lemma.statement) for lemma in recorded],
            [statement_key(lemma.statement) for lemma in lemmas],
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

    def test_comments_are_not_constructs(self):
        self.assertFalse(forbidden_problems("trivial -- not sorry"))


class HelperRenamingTest(unittest.TestCase):

    def test_plain_names_get_the_prefix(self):
        helpers, proof, renamed = prefixed_helpers(
            "theorem aux : True := trivial\n"
            "theorem Foo.bar : True := trivial",
            "exact aux",
            "lem_",
        )
        self.assertIn("theorem lem_aux", helpers)
        self.assertIn("theorem Foo.bar", helpers)
        self.assertEqual(proof, "exact lem_aux")
        self.assertEqual(renamed, ["lem_aux"])


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

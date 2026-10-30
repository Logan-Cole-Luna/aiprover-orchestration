"""The paper contract of a project (project.py)."""

import unittest

from aiprover_orchestration.orchestrator.project import PaperBuilder
from aiprover_orchestration.orchestrator.reports.report import ReportText

RESULTS = [
    {"slug": "base", "role": "background", "theorem_name": "base",
     "lemmas": ["b1"], "run": "r1"},
    {"slug": "top", "role": "main", "theorem_name": "top",
     "lemmas": ["t1", "t2"], "run": "r2"},
]


def text(body: str) -> ReportText:
    return ReportText("Title", "Abstract.", "", body)


class PaperContractTest(unittest.TestCase):

    def setUp(self):
        self.builder = PaperBuilder({"name": "demo"}, RESULTS)

    def test_missing_and_repeated_labels(self):
        body = (r"\begin{theorem}\label{thm:top}D\end{theorem}"
                r"\begin{lemma}\label{lem:top:t1}B\end{lemma}"
                r"\begin{lemma}\label{lem:top:t1}B\end{lemma}")
        problems = self.builder.problems(text(body))
        self.assertIn("no theorem labelled thm:base", problems)
        self.assertTrue(any("t2" in problem for problem in problems))
        self.assertTrue(any("more than once" in problem for problem in problems))


if __name__ == "__main__":
    unittest.main()

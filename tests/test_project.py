"""The paper contract of a project (project.py)."""

import unittest

from aiprover_orchestration.orchestrator.project import PaperBuilder
from aiprover_orchestration.orchestrator.project_tasks import absorb
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


class ReportAbsorptionTest(unittest.TestCase):

    def test_a_report_adds_each_finding_once(self):
        report = """<claim>Every word is finite.</claim>
        <open name="bound">No proof is known.</open>
        <milestone name="base"><role>background</role><uses>a, b</uses>
        <statement>S</statement><proof>P</proof></milestone>"""
        project = {"open": [], "reviews": [], "members": []}
        absorb(project, report)
        absorb(project, report)
        self.assertEqual(project["reviews"], [{"claim": "Every word is finite."}])
        self.assertEqual(
            project["open"], [{"lemma": "bound", "reason": "No proof is known."}]
        )
        self.assertEqual(project["members"], [{
            "uuid": "base", "role": "background", "statement": "S", "proof": "P",
            "uses": ["a", "b"],
        }])
        # A revision replaces only unstarted milestones.
        project = {"open": [], "reviews": [], "members": [
            {"uuid": "done", "role": "main", "statement": "D", "run": "r1"},
            {"uuid": "dropped", "role": "main", "statement": "X"},
        ]}
        absorb(project, """<milestone name="done"><statement>D2</statement></milestone>
        <milestone name="new"><role>main</role><statement>N</statement></milestone>""", True)
        self.assertEqual([m["uuid"] for m in project["members"]], ["done", "new"])
        self.assertEqual(project["members"][0]["statement"], "D")
        absorb(project, "<claim>No milestones here.</claim>", True)
        self.assertEqual([m["uuid"] for m in project["members"]], ["done", "new"])
        project["members"][1]["proof"] = "edited by hand"
        absorb(project, '<milestone name="new"><statement>N</statement><proof>other</proof></milestone>', True)
        self.assertEqual(project["members"][1]["proof"], "edited by hand")

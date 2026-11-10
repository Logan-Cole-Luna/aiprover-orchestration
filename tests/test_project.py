"""The paper contract of a project (project.py)."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from aiprover_orchestration.orchestrator import project as project_file

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
        # Reports before the rename tag a theorem `<milestone>`.
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
        # A revision replaces only unstarted theorems.
        project = {"open": [], "reviews": [], "members": [
            {"uuid": "done", "role": "main", "statement": "D", "run": "r1"},
            {"uuid": "dropped", "role": "main", "statement": "X"},
        ]}
        absorb(project, """<theorem name="done"><statement>D2</statement></theorem>
        <theorem name="new"><role>main</role><statement>N</statement></theorem>""", True)
        self.assertEqual([m["uuid"] for m in project["members"]], ["done", "new"])
        self.assertEqual(project["members"][0]["statement"], "D")
        absorb(project, "<claim>No theorems here.</claim>", True)
        self.assertEqual([m["uuid"] for m in project["members"]], ["done", "new"])
        project["members"][1]["proof"] = "edited by hand"
        absorb(project, '<theorem name="new"><statement>N</statement><proof>other</proof></theorem>', True)
        self.assertEqual(project["members"][1]["proof"], "edited by hand")
        with self.subTest("a project runs from the command line, member by member"):
            with tempfile.TemporaryDirectory() as folder, \
                    mock.patch.object(project_file, "PROJECTS_DIR", Path(folder)), \
                    mock.patch.object(project_file.subprocess, "run") as run:
                config = Path(folder) / "config.json"
                config.write_text(json.dumps({"agents": {"orchestrator": {"tools": True}}}))
                project = {"name": "demo", "library": "demo.lean", "source": "/src",
                           "settings": {"aiprover_attempts_per_lemma": 3},
                           "tasks": [], "open": [], "reviews": [{"claim": "C"}],
                           "members": [{"uuid": "done", "run": "r0"},
                                       {"uuid": "row_1"},
                                       {"uuid": "new", "statement": "S", "proof": "P"}]}
                project_file.save(project)
                project_file.launch(project, lambda body: project_file.run_member(
                    body, project, config, Path("data.jsonl")))
                commands = [call.args[0] for call in run.call_args_list]
                self.assertEqual(len(commands), 2)
                self.assertIn("--problem-uuid", commands[0])
                self.assertIn("libraries/demo.lean", commands[0])
                self.assertIn("--aiprover-attempts-per-lemma", commands[0])
                self.assertIn("--statement", commands[1])
                member = Path(folder) / "demo" / "members" / "new"
                self.assertEqual((member / "proof.txt").read_text(), "P")
                self.assertEqual(json.loads((member / "config.json").read_text())
                                 ["agents"]["orchestrator"]["source_dir"], "/src")
                self.assertEqual([m.get("run") for m in project_file.load("demo")["members"]],
                                 ["r0", "demo_row_1", "demo_new"])
                project_file.decide("demo", 0, "confirmed", "checked")
                self.assertEqual(project_file.load("demo")["reviews"][0]["decision"], "confirmed")

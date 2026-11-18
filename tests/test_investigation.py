"""The investigation before the draft: its session, its record, and the draft
that receives its report."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from aiprover_orchestration.orchestrator import project_tasks
from aiprover_orchestration.orchestrator.stages.draft import DraftStage
from aiprover_orchestration.orchestrator.structures import Formalization


class Stage(DraftStage):
    library = ""

    def __init__(self, folder: Path, source: str):
        self.result_dir = folder
        self.config = SimpleNamespace(
            investigation_usd=20.0,
            agents={"orchestrator": {"source_dir": source}},
        )
        self.row = {"informal_statement": "Every n satisfies P."}
        self.decisions = []
        self.prompts = []
        self.agents = SimpleNamespace(complete=self.complete)

    def _own_definitions(self, form):
        return "def P (n : ℕ) : Prop := True"

    def _decision(self, event, **fields):
        self._add("decision", event=event, **fields)

    def _add(self, kind, **data):
        # The signature of Trace.add, so that a field named like its
        # parameter fails here as in a run.
        self.decisions.append((data.pop("event"), data))

    def complete(self, prompt, **options):
        self.prompts.append(prompt)
        return "<proof>1. Trivial.</proof>"


FORM = Formalization("", "", "main", " : ∀ n, P n ", verdict="FAITHFUL")


class InvestigationTest(unittest.TestCase):
    def test_the_report_reaches_the_draft_and_is_kept(self):
        calls = []

        def session(project, folder, prompt, limit, spent):
            calls.append((project, folder, prompt, limit))
            spent.append(3.5)
            return "Excluded: induction, since P 0 fails to help."

        with tempfile.TemporaryDirectory() as folder, mock.patch.object(
            project_tasks, "session", session
        ):
            stage = Stage(Path(folder), "/material")
            stage.investigate(FORM)
            stage.investigate(FORM)  # a resumed run keeps the report
            stage.draft_proof(FORM)
        self.assertEqual(len(calls), 1)
        project, _, prompt, limit = calls[0]
        self.assertEqual(project, {"source": "/material"})
        self.assertEqual(limit, 20.0)
        self.assertIn("/material", prompt)
        self.assertIn("∀ n, P n", prompt)
        self.assertEqual(stage.decisions[0], ("investigated", {"usd": 3.5, "chars": 45}))
        self.assertIn("Excluded: induction", stage.prompts[0])

    def test_a_complete_proof_is_checked_and_ends_the_run(self):
        with tempfile.TemporaryDirectory() as folder:
            stage = Stage(Path(folder), "")
            self.assertIsNone(stage.investigation_proof(FORM))
            (Path(folder) / "investigation").mkdir()
            proof = Path(folder) / "investigation" / "proof.lean"
            checked = []

            def verify(form, body):
                checked.append(body)
                return "file", {"verified": body.endswith("trivial")}

            stage.verify = verify
            proof.write_text("import Mathlib\ntheorem solution : ∀ n, P n := fun _ => trivial")
            self.assertEqual(stage.investigation_proof(FORM), ("file", {"verified": True}))
            self.assertFalse(checked[0].startswith("import"))
            proof.write_text("theorem solution : ∀ n, P n := by sorry")
            self.assertIsNone(stage.investigation_proof(FORM))
            proof.write_text("theorem solution : ∀ n, P n := by native_decide")
            self.assertIsNone(stage.investigation_proof(FORM))
            self.assertEqual(len(checked), 1)  # forbidden text is never compiled
        self.assertEqual(
            [fields["verified"] for event, fields in stage.decisions], [True, False, False]
        )

    def test_a_refutation_turns_the_run_to_the_negation(self):
        with tempfile.TemporaryDirectory() as folder:
            stage = Stage(Path(folder), "")
            form = Formalization("", "", "main", " : ∀ n, P n ", verdict="FAITHFUL")
            (Path(folder) / "investigation").mkdir()
            (Path(folder) / "investigation" / "refutation.lean").write_text(
                "theorem solution : ¬ (∀ n, P n) := fun h => absurd"
            )
            seen = []
            stage.verify = lambda f, body: (seen.append(f.signature), ("file", {"verified": True}))[1]
            self.assertEqual(stage.investigation_proof(form), ("file", {"verified": True}))
        self.assertEqual(seen, [" : ¬ (∀ n, P n) "])
        self.assertEqual(form.signature, " : ¬ (∀ n, P n) ")
        self.assertEqual(stage.row["side"], "disprove")

    def test_the_prompt_offers_a_refutation_and_the_literature(self):
        prompts_seen = []

        def session(project, folder, prompt, limit, spent):
            prompts_seen.append((project, prompt))
            return "report"

        with tempfile.TemporaryDirectory() as folder, mock.patch.object(
            project_tasks, "session", session
        ), mock.patch.dict("os.environ", {"PAPER_SEARCH_LOG": "/log"}):
            Stage(Path(folder), "").investigate(FORM)
        project, prompt = prompts_seen[0]
        self.assertEqual(project, {"paper_search": True})
        self.assertIn("theorem solution : ¬ (∀ n, P n)", prompt)

    def test_an_open_report_is_read_from_its_first_line(self):
        with tempfile.TemporaryDirectory() as folder:
            stage = Stage(Path(folder), "")
            self.assertFalse(stage.investigation_open())
            report = Path(folder) / "investigation.md"
            report.write_text("**Outcome: open**\n\nNo argument; tried induction.")
            self.assertTrue(stage.investigation_open())
            report.write_text("Outcome: argument\n\n1. Count. The outcome is open no longer.")
            self.assertFalse(stage.investigation_open())

    def test_a_session_at_its_time_limit_keeps_its_report(self):
        def timeout(command, **options):
            (Path(options["cwd"]) / "report.md").write_text("Found so far.")
            raise subprocess.TimeoutExpired(command, 1)

        spent = []
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(
            project_tasks, "run_group", timeout
        ), mock.patch.object(project_tasks, "make_lean_project"):
            report = project_tasks.session({}, Path(folder), "prompt", 5.0, spent)
        self.assertEqual((report, spent), ("Found so far.", [5.0]))

    def test_a_research_task_has_every_tool_and_records_its_outcome(self):
        calls, prompts_seen = [], []

        def session(project, folder, prompt, limit, spent):
            calls.append(project)
            prompts_seen.append(prompt)
            return "Outcome: open\n\nNo argument.\n<open name=\"step_two\">unproved</open>"

        project = {"name": "p", "title": "T", "goal": "G", "members": [],
                   "open": [], "reviews": [], "library": "lib",
                   "tasks": [{"id": "t1", "question": "Q", "max_usd": 4.0}]}  # fmt: skip
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(
            project_tasks, "session", session
        ):
            absorbed = project_tasks.research(project, project["tasks"][0], Path(folder), [])
        absorbed(project)
        self.assertTrue(all(c["paper_search"] and c["sat_search"] for c in calls))
        self.assertEqual(project["tasks"][0]["outcome"], "open")
        # The referee is told where the draft's files and the earlier reports lie.
        self.assertIn("`draft/`", prompts_seen[1])
        self.assertIn(str(Path(folder).parent), prompts_seen[1])
        self.assertEqual(project["open"], [{"lemma": "step_two", "reason": "unproved", "task": "t1"}])

    def test_optional_tools_follow_the_project_and_the_site(self):
        names = lambda project: [name for name, *_ in project_tasks.optional_tools(project)]
        with mock.patch.object(project_tasks.singular, "executable", lambda: ""):
            self.assertEqual(names({}), [])
            self.assertEqual(names({"sat_search": True}), ["sat-solve"])
        with mock.patch.object(project_tasks.singular, "executable", lambda: "/bin/Singular"):
            self.assertEqual(names({"paper_search": True}), ["paper-search", "singular"])

    def test_a_session_without_a_project_reads_only_its_source(self):
        self.assertEqual(project_tasks.readable_files({}), [])
        self.assertEqual(
            project_tasks.readable_files({"source": "/material"}), [Path("/material")]
        )


if __name__ == "__main__":
    unittest.main()

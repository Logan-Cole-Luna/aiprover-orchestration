"""SQLite stores of the query server and the theorem library, and the
trace writer."""

import json
import subprocess
import threading
import time
import unittest
from unittest import mock

from aiprover_orchestration.agents.aiprover import AIProverAgent
from aiprover_orchestration.agents.base import AgentCallError
from aiprover_orchestration.lean.checker import CheckResult
from aiprover_orchestration.library import verifier
from aiprover_orchestration.library.store import Store
from aiprover_orchestration.library.verifier import Library, Rejected
from aiprover_orchestration.orchestrator.trace import Trace
from aiprover_orchestration.server import models
from aiprover_orchestration.server.jobs import CONFIG, JobStore, Worker
from aiprover_orchestration.utils import share_results
from tests.support import TemporaryDirectoryTest, job


class JobStoreTest(TemporaryDirectoryTest):

    def setUp(self) -> None:
        super().setUp()
        self.store = JobStore(self.directory / "jobs.db")

    def test_transition_succeeds_once(self):
        self.store.add(job("r1", "queued"))
        outcomes = []
        threads = [
            threading.Thread(
                target=lambda: outcomes.append(
                    self.store.transition("r1", "queued", state="running")
                )
            )
            for _ in range(8)
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(sorted(outcomes), [False] * 7 + [True])
        self.assertEqual(self.store.get("r1")["state"], "running")

    def test_cancel_by_state(self):
        worker = Worker(self.store)
        for run_id, state in [
            ("waiting", "awaiting_approval"),
            ("queued", "queued"),
            ("running", "running"),
            ("done", "finished"),
        ]:
            self.store.add(job(run_id, state))
        self.assertTrue(worker.cancel("waiting"))
        self.assertTrue(worker.cancel("queued"))
        self.assertTrue(worker.cancel("running"))  # no pid yet
        self.assertFalse(worker.cancel("done"))
        self.assertEqual(
            [
                self.store.get(run_id)["state"]
                for run_id in ("waiting", "queued", "running", "done")
            ],
            ["cancelled", "cancelled", "cancelling", "finished"],
        )

    def test_approve_only_awaiting(self):
        worker = Worker(self.store)
        self.store.add(job("r1", "awaiting_approval"))
        self.assertTrue(worker.approve("r1", "admin"))
        self.assertFalse(worker.reject("r1", "admin"))
        self.assertEqual(self.store.get("r1")["state"], "queued")

    def test_resume_only_ended(self):
        worker = Worker(self.store)
        for run_id, state in [("stopped", "cancelled"), ("running", "running")]:
            self.store.add(job(run_id, state))
        self.store.update("stopped", status="failed", ended=1.0)
        self.assertTrue(worker.resume("stopped"))
        self.assertFalse(worker.resume("running"))
        stopped = self.store.get("stopped")
        self.assertEqual(
            (stopped["state"], stopped["status"], stopped["ended"]),
            ("queued", None, None),
        )


class LibraryStoreTest(TemporaryDirectoryTest):

    def test_items_and_status(self):
        store = Store(self.directory / "library.db")
        item = store.add_item(
            kind="theorem",
            name="T",
            module="Lib.T",
            lean="theorem T : True := by sorry",
            status="Open",
            tags=["a"],
            created=time.time(),
        )
        self.assertEqual(store.item("T", by="name")["tags"], ["a"])
        store.set_status(item, "Proved")
        self.assertEqual(store.proved(), {item})


class PublishTest(TemporaryDirectoryTest):

    def setUp(self) -> None:
        super().setUp()
        self.library = Library(
            Store(self.directory / "library.db"), self.directory
        )
        built = CheckResult(ok=True, output="")
        patcher = mock.patch.object(verifier, "build", return_value=built)
        self.build = patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_last_theorem_is_published_as_an_axiom(self):
        with self.assertRaises(Rejected):
            self.library.publish(
                "theorem",
                "c",
                "theorem c : True := trivial\n\n"
                "theorem h : False := by sorry",
            )
        self.library.publish(
            "theorem", "c", "-- note\n@[simp] theorem c : True := by sorry"
        )
        (source,) = self.build.call_args.args[1].values()
        self.assertIn("@[simp] axiom c : True", source)
        self.assertNotIn("sorry", source)

    def test_a_sorry_beyond_the_statement_is_rejected(self):
        self.build.return_value = CheckResult(
            ok=True, output="", has_sorry_warning=True
        )
        with self.assertRaises(Rejected):
            self.library.publish("theorem", "c", "theorem c : True := by sorry")

    def test_names_of_one_module_collide(self):
        self.library.publish("theorem", "a_b", "theorem a_b : True := by sorry")
        with self.assertRaises(Rejected):
            self.library.publish(
                "theorem", "a.b", "theorem a.b : True := by sorry"
            )
        self.assertEqual(self.build.call_count, 1)


class ShareTest(unittest.TestCase):

    def test_redaction_reaches_keys(self):
        with mock.patch.object(share_results, "redactions", [("private", "x")]):
            self.assertEqual(
                share_results.clean({"private_lemma": ["private"]}),
                {"x_lemma": ["x"]},
            )


class CliTimeoutTest(unittest.TestCase):

    def test_timeout_is_an_infrastructure_failure(self):
        agent = mock.Mock(cli="cli", env={})
        timeout = subprocess.TimeoutExpired(["cli", "wait"], 1)
        with mock.patch.object(subprocess, "run", side_effect=timeout):
            with self.assertRaises(AgentCallError):
                AIProverAgent._cli(agent, "wait", "job", timeout=1)


class TraceTest(TemporaryDirectoryTest):

    def test_flush_finish_and_resume(self):
        path = self.directory / "trace.json"
        trace = Trace(path, {"run_id": "r"})
        trace.add("decision", event="a")
        trace.flush()
        self.assertEqual(len(json.loads(path.read_text())["steps"]), 1)
        trace.add("decision", event="b")
        trace.finish({"status": "x"})
        trace.add("decision", event="after")  # kept in memory only
        trace.flush()
        written = Trace.load(path)
        self.assertEqual(
            [step["event"] for step in written["steps"]], ["a", "b"]
        )
        self.assertEqual(written["outcome"], {"status": "x"})
        resumed = Trace(path, {"run_id": "r"}, previous=written)
        self.addCleanup(resumed.finish, {})
        self.assertEqual(len(resumed.document["resumptions"]), 1)
        self.assertIsNone(resumed.document["outcome"])
        self.assertEqual(resumed.add("decision", event="c"), 2)


if __name__ == "__main__":
    unittest.main()


class HandbackRoleTest(unittest.TestCase):

    def test_handback_model_sets_the_captain_phase(self):
        selection = models.validate({
            "subagent": {"provider": "aiprover", "model": "dgx"},
            "handback": {"provider": "claude", "model": "claude-sonnet-5-5",
                         "effort": "", "tools": True},
        })
        captain = models.build_config(CONFIG, selection)["agents"]["captain"]
        self.assertEqual(captain["model"], "claude-opus-5-5")
        self.assertEqual(captain["phases"]["handback"],
                         {"model": "claude-sonnet-5-5", "tools": True})
        self.assertIn("judge", captain["phases"])

    def test_handback_requires_claude(self):
        with self.assertRaises(ValueError):
            models.validate({"handback": {"provider": "aiprover", "model": "dgx"}})

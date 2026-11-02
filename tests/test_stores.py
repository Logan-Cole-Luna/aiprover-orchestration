"""SQLite stores of the query server and the theorem library, and the
trace writer."""

import json
import threading
import unittest
from unittest import mock

from aiprover_orchestration.library import verifier
from aiprover_orchestration.orchestrator.trace import Trace
from aiprover_orchestration.server.jobs import JobStore
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

class ShareTest(unittest.TestCase):

    def test_redaction_reaches_keys(self):
        with mock.patch.object(share_results, "redactions", [("private", "x")]):
            self.assertEqual(
                share_results.clean({"private_lemma": ["private"]}),
                {"x_lemma": ["x"]},
            )


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



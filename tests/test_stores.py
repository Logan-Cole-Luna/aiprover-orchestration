"""SQLite stores of the query server and the theorem library, and the
trace writer."""

import json
import threading
import time
import unittest

from aiprover_orchestration.library.store import Store
from aiprover_orchestration.orchestrator.trace import Trace
from aiprover_orchestration.server.jobs import JobStore, Worker
from tests.support import TemporaryDirectoryTest


def job(run_id: str, state: str) -> dict:
    return {
        "run_id": run_id,
        "owner": "tester",
        "uuid": "u",
        "title": "t",
        "dataset": "d",
        "state": state,
        "submitted": time.time(),
    }


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

"""The explore task: its shared ledger, the `findings` command, and the agents
and referee of the task."""

import hashlib
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from aiprover_orchestration.agents.common import FINDINGS_BIN
from aiprover_orchestration.findings import findings
from aiprover_orchestration.findings.findings import Refused, digest, entries, post

COMMAND = FINDINGS_BIN / "findings"


def run(arguments, shared=None, cwd=None, **variables):
    environment = {**os.environ, "SESSION_PYTHON": sys.executable}
    environment.pop("FINDINGS_DIR", None)
    if shared:
        environment |= {"FINDINGS_DIR": str(shared), "FINDINGS_AUTHOR": "agent_1",
                        "SESSION_WRITE": str(cwd), **variables}  # fmt: skip
    return subprocess.run([str(COMMAND), *arguments], cwd=cwd, env=environment,
                          capture_output=True, text=True)  # fmt: skip


class LedgerTest(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.shared = Path(self.folder.name) / "shared"

    def tearDown(self):
        self.folder.cleanup()

    def test_entries_are_validated_posted_and_read(self):
        self.assertEqual(entries(self.shared), [])
        post(self.shared, "agent_1", "sketch", "parity", "Induction on n", "Plan.")
        post(self.shared, "agent_2", "negative", "parity/odd", "Fails", "n = 3.",
             status="refuted", refutes=1)  # fmt: skip
        ledger = entries(self.shared)
        self.assertEqual([e["id"] for e in ledger], [1, 2])
        self.assertEqual((ledger[1]["agent"], ledger[1]["refutes"]), ("agent_2", 1))
        for wrong in (
            dict(kind="positive", topic="parity", status="refuted"),  # status of a positive
            dict(kind="positive", topic="parity"),  # a result needs a status
            dict(kind="note", topic="Bad Topic"),
            dict(kind="note", topic="parity", supersedes=9),  # no such entry
            dict(kind="positive", topic="parity", status="proved", refutes=1),
            dict(kind="guess", topic="parity"),
        ):
            with self.subTest(wrong), self.assertRaises(Refused):
                post(self.shared, "agent_1", title="T", body="B", **wrong)
        self.assertEqual(len(entries(self.shared)), 2)
        with mock.patch.dict("os.environ", {"FINDINGS_DIR": str(self.shared)}):
            listed = findings.main(["list", "--kind", "negative"])
            self.assertIn("#2 [negative refuted] parity/odd agent_2 refutes #1", listed)
            self.assertNotIn("Induction", listed)
            self.assertIn("n = 3.", findings.main(["read", "2"]))

    def test_concurrent_posts_neither_interleave_nor_get_lost(self):
        body = "x" * 15_000  # longer than an atomic write to a pipe

        def poster(k):
            for i in range(10):
                post(self.shared, f"agent_{k}", "note", "load", f"{k}.{i}", body)

        threads = [threading.Thread(target=poster, args=(k,)) for k in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        ledger = entries(self.shared)
        self.assertEqual([e["id"] for e in ledger], list(range(1, 61)))
        self.assertEqual(len({e["title"] for e in ledger}), 60)
        self.assertTrue(all(e["body"] == body for e in ledger))

    def test_the_digest_stays_within_its_limit(self):
        ledger, now = [], time.time()
        for i in range(1, 5001):
            kind = ("sketch", "claim", "positive", "negative", "counterexample", "note")[i % 6]
            status = {"positive": "proved", "negative": "pruned", "counterexample": "tested"}
            entry = {"id": i, "agent": f"agent_{i % 40}", "time": now, "kind": kind,
                     "topic": f"topic-{i % 700}/case-{i % 13}", "status": status.get(kind, ""),
                     "title": "A long title " * 20, "body": "B", "files": [],
                     "expires": now + 3600}  # fmt: skip
            if kind == "counterexample":
                entry |= {"refutes": i - 1, "alert": [f"agent_{(i - 1) % 40}"]}
            ledger.append(entry)
        for limit in (500, 4000, 20_000):
            text = digest(ledger, now, limit)
            self.assertLessEqual(len(text), limit)
            self.assertIn("Ledger: 5000 entries", text)
            for part in ("Open alerts", "Active leases", "Latest results"):
                self.assertIn(part, text)
        self.assertIn("ALERT to agent_", digest(ledger, now))

    def test_cursors_miss_and_repeat_no_entry_across_concurrent_posts(self):
        seen, done = [], threading.Event()

        def poster(k):
            for i in range(20):
                post(self.shared, f"agent_{k}", "note", "load", f"{k}.{i}", "B")

        def reader():
            cursor = 0
            while True:
                finished = done.is_set()
                ledger = entries(self.shared)
                text, cursor_after = findings.since(self.shared, ledger, cursor, "agent_9")
                seen.extend(e["id"] for e in ledger[cursor:cursor_after])
                self.assertTrue(text.endswith(f"Cursor: {cursor_after}"))
                cursor = cursor_after
                if finished:
                    return

        threads = [threading.Thread(target=poster, args=(k,)) for k in range(4)]
        watcher = threading.Thread(target=reader)
        watcher.start()
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        done.set()
        watcher.join()
        self.assertEqual(seen, list(range(1, 81)))

    def test_a_long_reply_stops_its_cursor_at_the_last_entry_shown(self):
        for i in range(60):
            post(self.shared, "agent_1", "note", "load", f"n{i}", "x" * 2000)
        ledger, cursor, shown = entries(self.shared), 0, []
        while cursor < 60:
            text, after = findings.since(self.shared, ledger, cursor, "agent_2")
            self.assertLessEqual(len(text), findings.MAX_REPLY + 100)
            shown += [i for i in range(cursor + 1, after + 1) if f"#{i} [" in text]
            cursor = after
        self.assertEqual(shown, list(range(1, 61)))

    def test_leases_expire_and_end_with_a_result(self):
        post(self.shared, "agent_1", "claim", "gap-a", "Claim", "B", hours=1)
        post(self.shared, "agent_2", "sketch", "gap-b", "Plan", "B", hours=2)
        post(self.shared, "agent_3", "claim", "gap-c", "Claim", "B")
        post(self.shared, "agent_3", "positive", "gap-c", "Done", "B", status="proved")
        now = time.time()
        held = lambda at: sorted(topic for _, topic in findings.leases(entries(self.shared), at))
        self.assertEqual(held(now), ["gap-a", "gap-b"])
        self.assertEqual(held(now + 1.5 * 3600), ["gap-b"])
        self.assertEqual(held(now + 3 * 3600), [])
        # A later sketch replaces its holder's earlier one.
        post(self.shared, "agent_2", "sketch", "gap-d", "New plan", "B")
        self.assertEqual(held(now), ["gap-a", "gap-d"])
        self.assertIn("gap-a agent_1", digest(entries(self.shared), now))

    def test_a_refutation_alerts_the_holders_and_the_authors_it_concerns(self):
        post(self.shared, "agent_1", "positive", "lemma-a", "A holds", "B", status="proved")
        post(self.shared, "agent_2", "positive", "lemma-a/ext", "A extended", "B",
             status="proved", supersedes=1)  # fmt: skip
        post(self.shared, "agent_3", "claim", "lemma-a/use", "Using A", "B")
        post(self.shared, "agent_5", "claim", "other", "Elsewhere", "B")
        post(self.shared, "agent_4", "counterexample", "lemma-a", "A fails at 7", "B",
             status="tested", refutes=1)  # fmt: skip
        post(self.shared, "agent_4", "negative", "unheld", "Pruned", "B", status="pruned")
        ledger = entries(self.shared)
        self.assertEqual(ledger[4]["alert"], ["agent_1", "agent_2", "agent_3"])
        self.assertNotIn("alert", ledger[5])
        text, _ = findings.since(self.shared, ledger, 0, "agent_3")
        self.assertTrue(text.startswith("ALERT to agent_1, agent_2, agent_3: #5"))
        self.assertFalse(findings.since(self.shared, ledger, 0, "agent_5")[0].startswith("ALERT"))
        self.assertIn("#1 superseded by #2, refuted by #5", digest(ledger, time.time()))


class CommandTest(unittest.TestCase):
    def test_files_are_shared_under_the_hash_of_their_content(self):
        with tempfile.TemporaryDirectory() as folder:
            shared, own = Path(folder) / "shared", Path(folder) / "agent_1"
            (own / "work").mkdir(parents=True)
            (own / "work" / "search.py").write_text("print(1)")
            (own / "copy.py").write_text("print(1)")
            (own / "note.md").write_text("Case n = 4 closed by search.")
            posted = run(["post", "--kind", "positive", "--topic", "case-4", "--status",
                          "tested", "--title", "Case 4", "--body-file", "note.md",
                          "--file", "work/search.py", "--file", "copy.py"], shared, own)  # fmt: skip
            self.assertEqual(posted.returncode, 0, posted.stderr)
            (entry,) = entries(shared)
            name = "files/" + hashlib.sha256(b"print(1)").hexdigest()[:32] + ".py"
            self.assertEqual(entry["files"], [{"path": "work/search.py", "ref": name},
                                              {"path": "copy.py", "ref": name}])  # fmt: skip
            self.assertEqual((shared / name).read_text(), "print(1)")
            self.assertEqual(len(list((shared / "files").iterdir())), 1)
            self.assertNotIn("print(1)", (shared / "ledger.jsonl").read_text())
            outside = run(["post", "--kind", "note", "--topic", "t", "--title", "T",
                           "--body", "B", "--file", str(shared / name)], shared, own)  # fmt: skip
            self.assertIn("not a file of your own folder", outside.stderr)
            with mock.patch.object(findings, "MAX_FILE_BYTES", 4):
                with mock.patch.dict("os.environ", {"SESSION_WRITE": str(own)}), \
                        self.assertRaises(Refused):  # fmt: skip
                    findings.cited_files([str(own / "copy.py")])
            # The cursor is kept in the agent's folder.
            self.assertIn("Cursor: 1", run(["since"], shared, own).stdout)
            self.assertEqual((own / "findings_cursor").read_text(), "1")
            self.assertIn("No new entries", run(["since"], shared, own).stdout)
            self.assertEqual(run(["claim", "case-5"], shared, own).returncode, 0)
            self.assertIn("case-5 agent_1", run(["digest"], shared, own).stdout)
            refused = run(["claim", "case-6"], shared, own, FINDINGS_READ_ONLY="1")
            self.assertIn("reads the ledger only", refused.stderr)
            self.assertEqual(len(entries(shared)), 2)

    def test_without_a_ledger_the_command_fails_cleanly(self):
        reply = run(["digest"])
        self.assertEqual(reply.returncode, 1)
        self.assertIn("FINDINGS_DIR is not set", reply.stderr)
        self.assertNotIn("Traceback", reply.stderr)


class ExploreTest(unittest.TestCase):
    def test_agents_share_a_ledger_and_a_referee_reads_its_digest(self):
        from aiprover_orchestration.orchestrator import project_tasks

        calls, together = [], threading.Barrier(3, timeout=10)

        def session(project, folder, prompt, limit, spent):
            calls.append((project, folder, prompt, limit))
            spent.append(1.0)
            if not folder.name.startswith("agent_"):
                return "Outcome: open\n\nNo argument.\n<open name=\"step\">unproved</open>"
            together.wait()  # the agents run at once
            if "This short session" in prompt:  # the reconcile round
                if folder.name == "agent_2":
                    raise RuntimeError("Stopped again")
                return f"{folder.name} reconciled."
            shared = Path(project["findings"])
            if folder.name == "agent_1":
                post(shared, "agent_1", "sketch", "induction", "Induction", "Plan.")
                post(shared, "agent_1", "positive", "induction/base", "Base case",
                     "n = 0.", status="proved")  # fmt: skip
                return "Report one."
            if folder.name == "agent_2":
                post(shared, "agent_2", "sketch", "counting", "Counting", "Plan.")
                folder.mkdir(parents=True)
                (folder / "report.md").write_text("Partial two.")
                raise RuntimeError("Stopped at its spending limit")
            post(shared, "agent_3", "negative", "parity", "Parity fails", "At 3.",
                 status="pruned")  # fmt: skip
            return "Report three."

        project = {"name": "p", "title": "T", "goal": "G", "members": [],
                   "open": [], "reviews": [], "library": "lib",
                   "tasks": [{"id": "t1", "question": "Q", "max_usd": 8.0,
                              "agents": 3}]}  # fmt: skip
        spent = []
        with tempfile.TemporaryDirectory() as folder, mock.patch.object(
            project_tasks, "session", session
        ):
            task = Path(folder) / "t1"
            absorbed = project_tasks.explore(project, project["tasks"][0], task, spent)
            agents, again = calls[:3], calls[3:6]
            reader, referee_folder, referee_prompt, referee_limit = calls[6]
            self.assertEqual(sorted(f.name for _, f, _, _ in agents),
                             ["agent_1", "agent_2", "agent_3"])  # fmt: skip
            for agent_project, agent_folder, prompt, limit in agents:
                self.assertEqual(agent_project["findings"], str(task / "shared"))
                self.assertIn(agent_folder.name, prompt)
                self.assertIn("memory.md", prompt)
                self.assertEqual(limit, 1.6)
                environment = project_tasks.session_environment(agent_project, agent_folder)
                self.assertEqual(environment["FINDINGS_DIR"], str(task / "shared"))
                self.assertEqual(environment["FINDINGS_AUTHOR"], agent_folder.name)
                self.assertNotIn("FINDINGS_READ_ONLY", environment)
                self.assertIn(str(FINDINGS_BIN), environment["PATH"])
                self.assertIn("Bash(findings:*)", project_tasks.claude_command(agent_project, limit))
            # Each agent then reconciles its report with the late entries.
            self.assertEqual(sorted(f.name for _, f, _, _ in again),
                             ["agent_1", "agent_2", "agent_3"])  # fmt: skip
            for _, _, prompt, limit in again:
                self.assertIn("findings since", prompt)
                self.assertAlmostEqual(limit, 0.4)
            # The referee reads the ledger through the command, which it may not post to.
            environment = project_tasks.session_environment(reader, referee_folder)
            self.assertEqual(environment["FINDINGS_READ_ONLY"], "1")
        self.assertEqual((referee_folder, referee_limit), (task, 2.0))
        for text in ("agent_1 reconciled.", "Partial two.", "agent_3 reconciled.", "Base case",
                     "Parity fails", "Ledger: 4 entries", "`agent_2/`",
                     "spending limit", "findings read"):  # fmt: skip
            self.assertIn(text, referee_prompt)
        self.assertNotIn("n = 0.", referee_prompt)  # bodies stay in the ledger
        self.assertEqual(sum(spent), 7.0)
        absorbed(project)
        record = project["tasks"][0]
        self.assertEqual(record["outcome"], "open")
        self.assertEqual(record["missing_sketch"], ["agent_3"])
        self.assertEqual(record["agent_errors"], {"agent_2": "Stopped at its spending limit"})
        self.assertEqual(project["open"], [{"lemma": "step", "reason": "unproved", "task": "t1"}])


if __name__ == "__main__":
    unittest.main()

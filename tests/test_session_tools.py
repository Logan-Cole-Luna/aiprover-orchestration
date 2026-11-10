"""The restrictions of a Codex session and of a project session's Python, and
what makes the backends interchangeable: limits, usage, failures, and roles."""

import json
import io
import os
import subprocess
import zipfile
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import numpy as np

from aiprover_orchestration.agents import common, openai_compatible, session_tools
from aiprover_orchestration.agents.base import BudgetExhausted, billed_tokens
from aiprover_orchestration.agents.codex_cli import command, token_usage
from aiprover_orchestration.agents.common import failed_call
from aiprover_orchestration.agents.pool import AgentPool
from aiprover_orchestration.lean import tool_check
from aiprover_orchestration.lean.sandbox import abi_version
from aiprover_orchestration.orchestrator import libraries, project_tasks
from aiprover_orchestration.search import arxiv_index, paper_search, papers
from aiprover_orchestration.server import models, settings


def tool_call(name: str, **arguments) -> dict:
    function = {"name": name, "arguments": json.dumps(arguments)}
    return {"id": name, "type": "function", "function": function}


def scripted_endpoint(replies: list[dict], received: list[dict]) -> HTTPServer:
    """A chat endpoint on a free port that answers with `replies` in order."""

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            size = int(self.headers["Content-Length"])
            received.append(json.loads(self.rfile.read(size)))
            body = json.dumps(replies.pop(0)).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *arguments):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def chat_reply(tokens: int = 10, **message) -> dict:
    reason = "tool_calls" if message.get("tool_calls") else "stop"
    return {
        "choices": [{"message": message, "finish_reason": reason}],
        "usage": {"prompt_tokens": tokens, "completion_tokens": 1},
    }


class SessionRestrictionsTest(unittest.TestCase):
    def test_checks(self):
        self.check_backend_parity()
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            home, shared, secret = root / "home", root / "shared", root / "secret"
            for tree in (home, shared, secret):
                tree.mkdir()
            (shared / "note").write_text("shared text")
            (secret / "key").write_text("secret text")
            (home / "link").symlink_to(secret / "key")
            tools = session_tools.Session(str(home), [str(shared)], ["lean-check"])
            with self.subTest("reads the session's trees only"):
                self.assertEqual(tools.read_file(str(shared / "note")), "shared text")
                for path in (secret / "key", "link", "../secret/key"):
                    with self.assertRaises(PermissionError):
                        tools.read_file(str(path))
            with self.subTest("search skips what lies outside"):
                self.assertEqual(tools.grep("secret", "."), "")
                self.assertNotIn("link", tools.list_files("*"))
            with self.subTest("writes in the working directory only"):
                tools.write_file("sub/a.lean", "one two")
                tools.edit_file("sub/a.lean", "one", "three")
                self.assertEqual(tools.read_file("sub/a.lean"), "three two")
                with self.assertRaises(PermissionError):
                    tools.write_file(str(shared / "b"), "x")
            with self.subTest("the protocol lists and calls tools"):
                names = [t["name"] for t in tools.answer("tools/list", {})["tools"]]
                self.assertIn("lean_check", names)
                self.assertNotIn("run", names)
                reply = tools.answer(
                    "tools/call",
                    {"name": "run", "arguments": {"command": "curl", "arguments": ""}},
                )
                self.assertTrue(reply["isError"])
            with self.subTest("lean-check leaves reference material alone"):
                for tree in ("library", "source"):
                    (home / tree).mkdir()
                    (home / tree / "Evil.lean").write_text("#eval 1")
                checked = {p.name for p in tool_check.project_files(home)}
                self.assertNotIn("Evil.lean", checked)
                cwd = os.getcwd()
                os.chdir(home)
                try:
                    with self.assertRaises(SystemExit) as refusal:
                        tool_check.main(["library/Evil.lean"])
                    self.assertIn("reference material", str(refusal.exception))
                finally:
                    os.chdir(cwd)
            with self.subTest("a package library has views and a table"):
                package = root / "package"
                for name, text in {
                    "lakefile.lean": "import Lake",
                    "Lib.lean": "import Lib.A",
                    "Lib/A.lean": "theorem t : True := by\n  trivial\n",
                    "Solutions/Run.lean": "theorem run : True := trivial",
                }.items():
                    (package / name).parent.mkdir(parents=True, exist_ok=True)
                    (package / name).write_text(text)
                self.assertEqual(libraries.root_modules(str(package)), ["Lib"])
                views = libraries.write_views(str(package))
                self.assertIn("sorry", (views / "Lib" / "A.lean").read_text())
                table = (views / "toc.md").read_text()
                self.assertIn("| Lib.A | theorem | t |", table)
                self.assertNotIn("run", table)
                self.assertIn("Lib", libraries.load(str(package)))
            with self.subTest("an uploaded package is checked before a build"):
                def archive(files: dict[str, str]) -> bytes:
                    data = io.BytesIO()
                    with zipfile.ZipFile(data, "w") as out:
                        for name, text in files.items():
                            out.writestr(name, text)
                    return data.getvalue()

                files = libraries.package_files(
                    archive({"top/Lib.lean": "x", "top/Lib/A.lean": "y"})
                )
                self.assertEqual(sorted(map(str, files)), ["Lib.lean", "Lib/A.lean"])
                for bad in ({"../Lib.lean": "x"}, {"Lib/A.lean": "y", "B/C.lean": "z"}):
                    with self.assertRaises(ValueError):
                        libraries.package_files(archive(bad))
                for unsound in ("by native_decide", "axiom bad : False"):
                    self.assertTrue(libraries.UNSOUND.search(unsound))
                with self.assertRaises(ValueError):
                    libraries.create_package("../x", "v4.23.0", b"")
            with self.subTest("a task without a report says why"):
                quiet = SimpleNamespace(stdout="", stderr="")
                self.assertIn(
                    "turn limit",
                    project_tasks.no_report(
                        "", SimpleNamespace(stdout=session_tools.TURN_LIMIT, stderr=""), 1, 0.4
                    ),
                )
                self.assertIn(
                    "spending limit of $1", project_tasks.no_report("", quiet, 1, 1.2)
                )
                self.assertIn("report.md", project_tasks.no_report("", quiet, 10, 0.5))
                events = "\n".join(
                    json.dumps({"item": {"type": "mcp_tool_call", "tool": name}})
                    for name in ("list_files", "read_file", "grep")
                )
                said = project_tasks.activity(events, {}, "Here is my summary")
                self.assertIn("3 tool calls, the last list_files, read_file, grep", said)
                self.assertIn("Here is my summary", said)
            with self.subTest("Codex has no shell and no web"):
                arguments = command("m", "", home, True, (str(shared),), ("lean-check",))
                self.assertIn('web_search="disabled"', arguments)
                for feature in ("shell_tool", "unified_exec", "apps", "plugins"):
                    self.assertIn(feature, arguments)
                self.assertEqual(arguments[arguments.index("-s") + 1], "read-only")
            with self.subTest("a long file reads by pages"):
                tools.write_file("long", "".join(f"{n}\n" for n in range(1, 11)))
                self.assertEqual(
                    tools.read_file("long", offset=3, limit=2),
                    "3\n4\n\n[Lines 3 to 4 of 10. Continue with offset 5.]",
                )
                self.assertTrue(tools.read_file("long").endswith("10\n"))
            with self.subTest("a reply says where it was cut"):
                tools.write_file("wide", "x" * (session_tools.MAX_REPLY + 1))
                reply = tools.answer(
                    "tools/call",
                    {"name": "read_file", "arguments": {"path": "wide"}},
                )
                self.assertIn("[Cut at", reply["content"][0]["text"])
            with self.subTest("a search skips builds and filters by name"):
                tools.write_file("pkg/.lake/build/a.lean", "needle")
                tools.write_file("pkg/a.lean", "needle")
                tools.write_file("pkg/a.txt", "needle")
                found = tools.grep("needle", "pkg", "*.lean")
                self.assertEqual(len(found.splitlines()), 1)
                self.assertIn("pkg/a.lean:1:", found)
            with self.subTest("tool arguments are quoted as in a shell"):
                run = mock.Mock(
                    return_value=SimpleNamespace(stdout="", stderr="", returncode=0)
                )
                quoted = session_tools.Session(str(home), [], ["python3"], 0, run)
                quoted.call("python3", {"arguments": """-c "print('a b')" """})
                self.assertEqual(
                    run.call_args.args[0], ["python3", "-c", "print('a b')"]
                )
            with self.subTest("the tool server enforces the turn limit"):
                limited = session_tools.Session(str(home), [], [], max_turns=1)
                limited.call("list_files", {"pattern": "*"})
                with self.assertRaisesRegex(RuntimeError, "turn limit"):
                    limited.call("list_files", {"pattern": "*"})
                arguments = command("m", "", home, True, max_turns=7)
                self.assertIn("7", json.loads(next(
                    a.split("=", 1)[1] for a in arguments
                    if a.startswith("mcp_servers.session.args=")
                )))
            with self.subTest("a chat model works through tools to a reply"):
                received: list[dict] = []
                server = scripted_endpoint(
                    [
                        chat_reply(
                            reasoning_content="kept",
                            tool_calls=[tool_call("write_file", path="a", content="b")],
                        ),
                        chat_reply(content="done"),
                    ],
                    received,
                )
                work = root / "work"
                work.mkdir()
                with mock.patch.object(
                    openai_compatible, "tool_project",
                    return_value=(work, " note", "", ("lean-check",)),
                ):
                    agent = openai_compatible.OpenAICompatibleAgent(
                        "m",
                        base_url=f"http://127.0.0.1:{server.server_port}",
                        tools=True,
                        hosted=True,
                    )
                    completion = agent.complete_once("p", "s")
                server.shutdown()
                server.server_close()
                self.assertEqual(completion.text, "done")
                self.assertEqual(completion.usage["m"]["inputTokens"], 20)
                self.assertEqual(received[0]["messages"][0]["content"], "s note")
                self.assertEqual(
                    received[1]["messages"][2]["reasoning_content"], "kept"
                )
                self.assertEqual(received[1]["messages"][3]["content"], "wrote a")
            with self.subTest("a chat model stops at its limits"):
                calls = [tool_call("list_files", pattern="*")]
                for options, tokens, expected in (
                    ({"max_turns": 1}, 10, "turn limit"),
                    ({"max_budget_usd": 0.001, "hosted": True}, 200, "token limit"),
                ):
                    server = scripted_endpoint(
                        [chat_reply(tokens, tool_calls=calls)] * 2, []
                    )
                    work = root / f"work{tokens}"
                    work.mkdir()
                    with mock.patch.object(
                        openai_compatible, "tool_project",
                        return_value=(work, "", "", ("lean-check",)),
                    ):
                        completion = openai_compatible.OpenAICompatibleAgent(
                            "m",
                            base_url=f"http://127.0.0.1:{server.server_port}",
                            tools=True,
                            **options,
                        ).complete_once("p", "s")
                    server.shutdown()
                    server.server_close()
                    self.assertTrue(completion.limited)
                    self.assertIn(expected, completion.error)
            if abi_version() >= 4:
                with self.subTest("the session Python is confined"):
                    project = {"name": "absent", "source": str(shared)}
                    script = (
                        "import socket\n"
                        f"print(open({str(shared / 'note')!r}).read())\n"
                        "open('out', 'w').write('x')\n"
                        "for act in (lambda: socket.create_connection(('1.1.1.1', 443), 3),\n"
                        f"            lambda: open({str(secret / 'key')!r}),\n"
                        f"            lambda: open({str(shared / 'new')!r}, 'w')):\n"
                        "    try:\n"
                        "        act(); print('ALLOWED')\n"
                        "    except OSError:\n"
                        "        print('denied')\n"
                    )
                    reply = subprocess.run(
                        ["python3", "-c", script],
                        cwd=home,
                        env=project_tasks.session_environment(project, home),
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        reply.stdout.split(),
                        ["shared", "text", "denied", "denied", "denied"],
                        reply.stderr,
                    )
                    self.assertTrue((home / "out").exists())

    def check_backend_parity(self):
        with self.subTest("tokens share one naming and weight"):
            usage = token_usage(
                {"input_tokens": 100, "cached_input_tokens": 80, "output_tokens": 5}
            )
            self.assertEqual(usage["inputTokens"], 20)
            self.assertEqual(billed_tokens(usage), 33)
        with self.subTest("a USD limit converts tokens without a price"):
            with tempfile.TemporaryDirectory() as directory:
                pool = AgentPool(
                    {"orchestrator": SimpleNamespace(hosted=True)},
                    Path(directory) / "calls.jsonl",
                    max_hosted_usd=1,
                )
                pool._accumulate("orchestrator", True, {"m": {"outputTokens": 99_999}})
                self.assertGreater(pool.hosted_budget_left(), 0)
                pool._accumulate("orchestrator", False, {"m": {"outputTokens": 9}})
                self.assertGreater(pool.hosted_budget_left(), 0)
                pool._accumulate("orchestrator", True, {"m": {"outputTokens": 1}})
                with self.assertRaises(BudgetExhausted):
                    pool.check_spend_limit()
        with self.subTest("a usage limit waits for its reset"):
            failure = failed_call(
                "You've hit your usage limit. Try again at 11:59 PM.", {}
            )
            self.assertTrue(failure.retryable)
            self.assertIsNotNone(failure.retry_after_s)
            dated = failed_call(
                "You've hit your usage limit. Try again at Jan 2nd, 2020 1:30 PM.", {}
            )
            self.assertEqual(dated.retry_after_s, 0.0)
        with self.subTest("a refusal is not retried"):
            failure = failed_call(
                "stream disconnected before completion: Incomplete response "
                "returned, reason: content_filter",
                {},
            )
            self.assertTrue(failure.refused)
            self.assertFalse(failure.retryable)
        with self.subTest("the escalation needs no particular service"):
            codex = {"provider": "codex", "model": "gpt-6.1-sol", "effort": ""}
            selection = models.validate({"orchestrator": codex})
            self.assertEqual(selection["escalation"]["provider"], "codex")
            self.assertTrue(selection["escalation"]["tools"])
            config = models.build_config(
                models.Path("configs/orchestrator/default.json"),
                selection | {"orchestrator": selection["orchestrator"]},
            )
            phase = config["agents"]["orchestrator"]["phases"]["escalation"]
            self.assertEqual(phase["backend"], "codex")
        with self.subTest("a provider's API can serve any role with tools"):
            servers = {
                "kimi": {"url": "http://x/v1", "model": "kimi-k3", "kind": "hosted"}
            }
            with mock.patch.object(models, "custom_servers", lambda: servers):
                choice = {"provider": "custom", "model": "kimi", "tools": True}
                spec = models.chat_spec("orchestrator", models.validate(
                    {"orchestrator": choice}
                )["orchestrator"], {})
            self.assertEqual(spec["backend"], "openai_compatible")
            self.assertTrue(spec["hosted"] and spec["tools"])
        with self.subTest("paper search is local, delimited, logged, and off by default"):
            self.check_paper_search()

    def check_paper_search(self):
        page = b"""<OAI-PMH xmlns="http://www.openarchives.org/OAI/2.0/"><ListRecords>
<record><metadata><arXiv xmlns="http://arxiv.org/OAI/arXiv/"><id>2604.00001</id>
<created>2026-04-01</created><authors><author><keyname>Noether</keyname>
<forenames>Emmy</forenames></author></authors><title>Rings  and
ideals</title><categories>math.AC</categories><abstract> On ideals. </abstract>
</arXiv></metadata></record><resumptionToken cursor="0"></resumptionToken>
</ListRecords></OAI-PMH>"""
        found, token = arxiv_index.records(page)
        self.assertEqual((found[0]["title"], found[0]["authors"], token),
                         ("Rings and ideals", "Emmy Noether", ""))
        for good in ("2604.26311", "2604.26311v2", "math/0601001", "math.AG/0601001v1"):
            self.assertTrue(papers.ARXIV_ID.fullmatch(good), good)
        for bad in ("https://evil.org/x", "../etc/passwd", "2604.26311 x", ""):
            self.assertFalse(papers.ARXIV_ID.fullmatch(bad), bad)
        index = object.__new__(papers.Papers)
        index.documents = [{"id": "a", "date": "2019-05-01"}, {"id": "b", "date": "2021-01-01"}]
        index.dates = np.array([d["date"] for d in index.documents])
        index.vectors = np.eye(2, dtype=np.float32)
        index.embedder = lambda texts: np.array([[0.0, 1.0]], dtype=np.float32)
        index.lock = threading.Lock()
        # "b" ranks first by embedding; "a", first by keywords, also ranks second
        # by embedding, so the fusion puts it first.
        index.keywords = SimpleNamespace(scores=lambda query: np.array([2.0, 0.0]))
        self.assertEqual([d["id"] for d in index.search("q", 5, "")], ["a", "b"])
        self.assertEqual([d["id"] for d in index.search("q", 5, "2020-01-01")], ["a"])
        reply = {"results": [{"id": "2604.00001", "date": "2026-04-01",
                              "title": "Rings", "authors": "E", "abstract": "On ideals."}]}
        server = scripted_endpoint([reply], received := [])
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "log.jsonl"
            environment = {"PAPER_SEARCH_LOG": str(log), "PAPER_SEARCH_BEFORE": "2027-01-01"}
            with mock.patch.dict(os.environ, environment), mock.patch.object(
                paper_search, "SERVICE", f"http://127.0.0.1:{server.server_port}"
            ):
                shown = paper_search.main(["ideals", "-n", "3"])
                self.assertIn("paper-search", common.tool_commands()[0])
            self.assertTrue(shown.startswith(paper_search.HEADER))
            self.assertIn('<paper id="2604.00001" date="2026-04-01">', shown)
            self.assertEqual(received[0], {"query": "ideals", "top_k": 3, "before": "2027-01-01"})
            self.assertEqual(json.loads(log.read_text())["ids"], ["2604.00001"])
        server.shutdown()
        server.server_close()
        with mock.patch.dict(os.environ):
            os.environ.pop("PAPER_SEARCH_LOG", None)
            self.assertEqual(common.tool_commands()[0], ("lean-check",))
        self.assertEqual(settings.validate({"paper_search": True}), {"paper_search": True})

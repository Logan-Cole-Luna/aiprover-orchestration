"""Sign-in to Claude and Codex from the page (server/accounts.py), and the
Codex agent's reading of its events (agents/codex_cli.py)."""

import json
import stat
import unittest

from aiprover_orchestration.agents.codex_cli import parse_events
from tests.support import ServerProcessTest

PROGRAM = """#!/bin/sh
if [ "$2" = status ]; then
  [ -f "$HOME/in" ] && echo '{"loggedIn": true, "email": "a@b.c"}' && exit 0
  echo '{"loggedIn": false}'; exit 0
fi
echo "visit: https://claude.com/cai/oauth/authorize?code=true&state=s"
printf 'Paste code here if prompted > '
read -r code
[ "$code" = good ] || exit 1
touch "$HOME/in"
echo 'Login successful'
read -r _
"""
REFUSED = """
import json
from fastapi.testclient import TestClient
from aiprover_orchestration.server import accounts
from aiprover_orchestration.server.app import app
accounts.read_account = lambda service: None
choice = {"provider": "codex", "model": "gpt-6.1-sol"}
reply = TestClient(app).post("/api/runs", json={
    "statement": "S", "page": "user", "gpu_group": "cpu",
    "orchestrator": choice, "subagent": choice, "auditor": choice,
    "reviewer": choice, "writer": choice,
})
print(json.dumps([reply.status_code, reply.json()["detail"]]))
"""
FLOW = """
import json
from fastapi.testclient import TestClient
from aiprover_orchestration.server.app import app
client = TestClient(app, client=("127.0.0.1", 5))
url = client.post("/api/accounts/claude/sign-in").json()["url"]
code = "/api/accounts/claude/code"
refused = client.post(code, json={"value": "bad"}).status_code
client.post("/api/accounts/claude/sign-in")
finished = client.post(code, json={"value": "good"}).json()
print(json.dumps([url, refused, finished]))
"""


class SignInTest(ServerProcessTest):
    def test_the_code_reaches_the_program_and_the_account_shows(self):
        tools = self.directory / "tools"
        tools.mkdir()
        program = tools / "claude"
        program.write_text(PROGRAM)
        program.chmod(program.stat().st_mode | stat.S_IEXEC)
        url, refused, finished = self.run_python(
            FLOW,
            HOME=str(self.directory),
            PATH=f"{tools}:/usr/bin:/bin",
            QUERY_SERVER_PER_USER="on",
        )
        self.assertIn("/oauth/authorize", url)
        self.assertEqual(refused, 400)
        self.assertEqual(finished["account"], "a@b.c")

    def test_a_run_on_a_service_without_a_login_asks_for_it(self):
        status, detail = self.run_python(REFUSED, QUERY_SERVER_PER_USER="on")
        self.assertEqual(status, 409)
        self.assertEqual(detail["sign_in"], "codex")


class CodexEventTest(unittest.TestCase):
    def test_the_last_message_usage_and_failure_are_read(self):
        events = [
            {"type": "item.completed",
             "item": {"type": "agent_message", "text": "first"}},
            {"type": "item.completed",
             "item": {"type": "agent_message", "text": "done"}},
            {"type": "turn.completed", "usage": {"input_tokens": 3}},
        ]
        output = "\n".join(map(json.dumps, events)) + "\nnot json"
        self.assertEqual(
            parse_events(output), ("done", {"input_tokens": 3}, None)
        )
        failed = json.dumps(
            {"type": "turn.failed", "error": {"message": "limit"}}
        )
        self.assertEqual(parse_events(failed)[2], "limit")


if __name__ == "__main__":
    unittest.main()

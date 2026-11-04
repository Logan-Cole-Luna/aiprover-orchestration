"""The benchmark's confinement: benchmark/confine.py under Landlock."""

import json
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFINE = ROOT / "benchmark" / "confine.py"
sys.path.insert(0, str(ROOT / "benchmark"))
import run as benchmark_run  # noqa: E402

PYTHON = str(Path("/usr/bin/python3").resolve())


def confined(code: str, run: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            sys.executable, "-I", str(CONFINE), "--run", str(run),
            "--execute", PYTHON, "--", PYTHON, "-I", "-c", code,
        ],  # fmt: skip
        capture_output=True,
        text=True,
        timeout=60,
    )


class ConfinementTest(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.run_dir = Path(directory.name)

    def assert_denied(self, code: str):
        done = confined(code, self.run_dir)
        self.assertNotEqual(done.returncode, 0, done.stdout)
        self.assertIn("PermissionError", done.stderr)

    def test_repository_is_unreadable(self):
        self.assert_denied(f"open({str(ROOT / 'README.md')!r}).read()")

    def test_home_is_unreadable(self):
        self.assert_denied(f"import os; os.listdir({str(Path.home())!r})")

    def test_lean_cannot_run(self):
        lean = Path.home() / ".elan" / "bin" / "lean"
        self.assert_denied(
            f"import subprocess; subprocess.run([{str(lean)!r}])"
        )

    def test_shell_cannot_run(self):
        self.assert_denied("import subprocess; subprocess.run(['/bin/sh'])")

    def test_writes_stay_in_the_run(self):
        self.assert_denied(f"open({str(ROOT / 'x')!r}, 'w')")
        path = self.run_dir / "ok"
        done = confined(f"open({str(path)!r}, 'w').write('x')", self.run_dir)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_only_the_api_port_connects(self):
        server = socket.create_server(("127.0.0.1", 0))
        self.addCleanup(server.close)
        port = server.getsockname()[1]
        self.assert_denied(
            "import socket; "
            f"socket.create_connection(('127.0.0.1', {port}), 5)"
        )


class LeanConditionTest(unittest.TestCase):
    def test_session_commands_are_limited_to_lean_and_filters(self):
        condition = {"model": "m", "effort": "high", "lean": True,
                     "tools": ["Bash", "Read"], "max_budget_usd": 1}
        command = benchmark_run.claude_command(condition, "claude")
        rules = command[command.index("--allowedTools") + 1 :]
        shell = [rule for rule in rules if rule.startswith("Bash(")]
        self.assertEqual(tuple(shell), benchmark_run.LEAN_COMMANDS)
        self.assertNotIn("Bash(./**)", rules)


class RecordTest(unittest.TestCase):
    def test_tool_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "transcript.jsonl"
            events = [
                {"message": {"content": [{"type": "tool_use", "name": "Write"}]}},
                {"message": {"content": [{"type": "tool_result", "is_error": True}]}},
                {"message": "a status line"},
                {"type": "result", "total_cost_usd": 1.5, "num_turns": 2},
            ]
            path.write_text("\n".join(map(json.dumps, events)))
            done = benchmark_run.measured(path, Path(directory))
        self.assertEqual(done["tool_calls"], {"Write": 1})
        self.assertEqual((done["tool_errors"], done["cost_usd"]), (1, 1.5))

    def test_every_tool_is_scoped_to_the_session(self):
        condition = {"model": "m", "effort": "high", "tools": ["Read", "Write"],
                     "max_budget_usd": 1}
        command = benchmark_run.claude_command(condition, "claude")
        self.assertEqual(command[command.index("--tools") + 1], "Read,Write")
        self.assertNotIn("Bash", " ".join(command))


if __name__ == "__main__":
    unittest.main()

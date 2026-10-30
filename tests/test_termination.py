"""Termination of a run: model calls, Lean checks and AIProver jobs end
promptly once the run is stopped (`agents.base.STOPPING`)."""

import os
import signal
import subprocess
import sys
import threading
import time
import unittest
from pathlib import Path

from aiprover_orchestration.agents import (
    STOPPING,
    Agent,
    AgentPool,
    Completion,
    Terminated,
)
from aiprover_orchestration.lean.checker import communicate
from aiprover_orchestration.utils.processes import run_group
from aiprover_orchestration.paths import ROOT
from tests.support import TESTS, TemporaryDirectoryTest


class BusyAgent(Agent):
    """An endpoint that is always overloaded and asks for a long wait."""

    backend = "busy"

    def __init__(self):
        super().__init__("busy", max_retries=3)
        self.calls = 0

    def complete_once(self, prompt, system_prompt):
        self.calls += 1
        return Completion(error="overloaded", retryable=True, retry_after_s=60)


def stop_after(seconds: float) -> None:
    threading.Timer(seconds, STOPPING.set).start()


def alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


class StopFlagTest(TemporaryDirectoryTest):

    def pool(self, agent: Agent) -> AgentPool:
        return AgentPool({"solver": agent}, self.directory / "calls.jsonl")

    def complete(self, pool: AgentPool) -> None:
        pool.complete("p", role="solver", system_prompt="s", phase="x")

    def test_no_call_starts_once_stopping(self):
        agent = BusyAgent()
        STOPPING.set()
        with self.assertRaises(Terminated):
            self.complete(self.pool(agent))
        self.assertEqual(agent.calls, 0)

    def test_retry_wait_ends(self):
        agent = BusyAgent()
        stop_after(0.5)
        start = time.monotonic()
        with self.assertRaises(Terminated):
            self.complete(self.pool(agent))
        self.assertLess(time.monotonic() - start, 5)
        self.assertEqual(agent.calls, 1)


class LeanProcessTest(TemporaryDirectoryTest):
    """A check runs in a process group of its own, which is killed as a
    whole (`lake env lean` runs `lean` as a child)."""

    def run_tree(self, timeout: int, stop) -> tuple[str, int]:
        pid_file = self.directory / "child.pid"
        ran = communicate(
            ["sh", "-c", f"sleep 60 & echo $! > {pid_file}; wait"],
            self.directory,
            timeout,
            stop,
        )
        return ran, int(pid_file.read_text())

    def assert_dies(self, pid: int) -> None:
        deadline = time.monotonic() + 5
        while alive(pid) and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertFalse(alive(pid), "the check's child outlived it")

    def test_stop_kills_the_group(self):
        stop_after(0.5)
        start = time.monotonic()
        ran, child = self.run_tree(60, STOPPING)
        self.assertLess(time.monotonic() - start, 5)
        self.assertIn("stopped", ran)
        self.assert_dies(child)

    def test_timeout_kills_the_group(self):
        ran, child = self.run_tree(1, None)
        self.assertIn("timed out", ran)
        self.assert_dies(child)

    def test_grace_ends_the_group_as_well(self):
        marker = self.directory / "child.pid"
        script = f"sleep 300 & echo $! > {marker}; wait"
        with self.assertRaises(subprocess.TimeoutExpired):
            run_group(
                ["sh", "-c", script], cwd=self.directory, timeout=1, grace=1
            )
        self.assert_dies(int(marker.read_text()))

    def test_output_and_exit_code(self):
        self.assertEqual(
            communicate(["sh", "-c", "echo out; echo err >&2; exit 2"],
                         self.directory, 10, None),
            (2, "out\nerr\n"),
        )


class SignalTest(TemporaryDirectoryTest):
    """A signal to a process in the AIProver phase cancels its running
    jobs, and the process exits within seconds."""

    def setUp(self) -> None:
        super().setUp()
        cli = self.directory / "bin" / "aiprover"
        cli.parent.mkdir()
        cli.write_text(
            f'#!/bin/sh\nexec {sys.executable} {TESTS / "fake_aiprover.py"} '
            '"$@"\n'
        )
        cli.chmod(0o755)
        (self.directory / "aiprover.toml").write_text(
            f'[runtime]\nwork_root = "{self.directory / "work"}"\n'
            '[endpoint]\napi_base = "http://127.0.0.1:9/v1"\n'
        )

    def commands(self) -> list[str]:
        log = self.directory / "commands.log"
        return log.read_text().splitlines() if log.exists() else []

    def interrupt(self, signum: int) -> int:
        child = subprocess.Popen(
            [sys.executable, "-m", "tests.terminate_child", self.directory],
            cwd=ROOT,
            env={**os.environ, "FAKE_AIPROVER_DIR": str(self.directory)},
            stderr=subprocess.DEVNULL,
        )
        self.addCleanup(child.kill)
        deadline = time.monotonic() + 60
        while (
            sum(line.startswith("submit") for line in self.commands()) < 2
            and time.monotonic() < deadline
        ):
            time.sleep(0.1)
        self.assertEqual(
            sorted(self.commands()), ["submit job1", "submit job2"]
        )
        child.send_signal(signum)
        start = time.monotonic()
        returncode = child.wait(timeout=30)
        self.assertLess(time.monotonic() - start, 10)
        self.assertTrue((self.directory / "unwound").exists())
        cancelled = {
            line.split()[1]
            for line in self.commands()
            if line.startswith("cancel")
        }
        self.assertEqual(cancelled, {"job1", "job2"})
        return returncode

    def test_sigterm(self):
        self.assertEqual(self.interrupt(signal.SIGTERM), 143)

    def test_sigint(self):
        self.assertNotEqual(self.interrupt(signal.SIGINT), 0)


if __name__ == "__main__":
    unittest.main()

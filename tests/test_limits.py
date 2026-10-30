"""Limits of the machine (limits.py): slots shared across threads and
processes, the budget under concurrent calls, and jobs that wait for
session slots."""

import threading
import time
import unittest
from unittest import mock

from aiprover_orchestration import limits
from aiprover_orchestration.agents.base import (
    Agent,
    BudgetExhausted,
    Completion,
)
from aiprover_orchestration.agents.pool import AgentPool
from aiprover_orchestration.orchestrator.config import Config
from aiprover_orchestration.orchestrator.structures import Lemma, Sketch
from aiprover_orchestration.server import jobs
from tests.support import (
    FakeSolver,
    ScriptedPool,
    TemporaryDirectoryTest,
    aiprover_phase,
)


class MachineSlotsTest(unittest.TestCase):

    def test_at_most_the_limit_run_at_once(self):
        lock, running, peak = threading.Lock(), [0], [0]

        def work() -> None:
            with limits.machine_slots("test_peak", 2, 1):
                with lock:
                    running[0] += 1
                    peak[0] = max(peak[0], running[0])
                time.sleep(0.1)
                with lock:
                    running[0] -= 1

        threads = [threading.Thread(target=work) for _ in range(6)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        self.assertEqual(peak[0], 2)

    def test_a_waiter_takes_what_is_free_and_stop_ends_the_wait(self):
        with limits.machine_slots("test_grant", 3, 2) as first:
            self.assertEqual(len(first), 2)
            with limits.machine_slots("test_grant", 3, 2) as second:
                self.assertEqual(len(second), 1)
                stop = threading.Event()
                stop.set()
                with limits.machine_slots("test_grant", 3, 1, None, stop) as third:
                    self.assertEqual(len(third), 0)
                with limits.machine_slots(
                    "test_grant", 3, 1, wait=0.01
                ) as timed_out:
                    self.assertEqual(len(timed_out), 0)
            self.assertEqual(first.take(5), 1)
            first.release(2)
            self.assertEqual(len(first), 1)


class HostedAgent(Agent):
    """A hosted agent whose calls wait until `release` is set."""

    backend = "test"
    hosted = True

    def __init__(self):
        super().__init__("test", max_retries=0)
        self.started = threading.Semaphore(0)
        self.release = threading.Event()

    def complete_once(self, prompt, system_prompt):
        self.started.release()
        self.release.wait(10)
        return Completion(text="ok")


class ConcurrentCallsTest(TemporaryDirectoryTest):

    def calls(self, count: int, **pool_options) -> tuple:
        """`count` concurrent calls; the agent, the threads and outcomes."""
        agent = HostedAgent()
        pool = AgentPool(
            {"solver": agent}, self.directory / "calls.jsonl", **pool_options
        )
        outcomes: list = []

        def call() -> None:
            try:
                pool.complete(
                    "p", role="solver", system_prompt="s", phase="x"
                )
                outcomes.append("called")
            except BudgetExhausted:
                outcomes.append("refused")

        threads = [threading.Thread(target=call) for _ in range(count)]
        for thread in threads:
            thread.start()
        return agent, threads, outcomes

    def test_the_budget_holds_for_a_burst(self):
        agent, threads, outcomes = self.calls(12, max_claude_calls=3)
        deadline = time.monotonic() + 5
        while outcomes.count("refused") < 9 and time.monotonic() < deadline:
            time.sleep(0.01)
        agent.release.set()
        for thread in threads:
            thread.join()
        self.assertEqual(outcomes.count("called"), 3)
        self.assertEqual(outcomes.count("refused"), 9)

    def test_hosted_calls_wait_for_a_machine_slot(self):
        with mock.patch.dict(
            limits.os.environ, {"AIPROVER_MAX_HOSTED": "2"}
        ):
            agent, threads, outcomes = self.calls(5)
            self.assertTrue(agent.started.acquire(timeout=5))
            self.assertTrue(agent.started.acquire(timeout=5))
            self.assertFalse(agent.started.acquire(timeout=3))
            agent.release.set()
            for thread in threads:
                thread.join()
        self.assertEqual(outcomes, ["called"] * 5)


class SessionSlotsTest(TemporaryDirectoryTest):

    def test_a_lemma_waits_for_a_free_slot(self):
        run = self.orchestration(
            ScriptedPool(solver=FakeSolver({})),
            Config(aiprover_session_slots=1),
        )
        lemma = Lemma("a", "theorem a : True")
        phase = aiprover_phase(run, Sketch([lemma], "trivial"))
        phase.sessions_in_flight["b"] = 1
        claimed: list[int] = []
        waiter = threading.Thread(
            target=lambda: claimed.append(run._claim_sessions(phase, lemma))
        )
        waiter.start()
        time.sleep(0.3)
        self.assertEqual(claimed, [])
        with phase.slot_freed:
            phase.sessions_in_flight.clear()
            phase.slot_freed.notify()
        waiter.join(5)
        self.assertEqual(claimed, [1])


if __name__ == "__main__":
    unittest.main()

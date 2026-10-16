"""Fixtures shared by the tests: an orchestration without model servers,
scripted agents, and a stand-in for the AIProver CLI."""

import shutil
import tempfile
import threading
import unittest
from concurrent.futures import Future
from pathlib import Path

from aiprover_orchestration.agents import STOPPING
from aiprover_orchestration.agents.aiprover import (
    AIProverAgent,
    AIProverJob,
    AIProverSample,
)
from aiprover_orchestration.lean.checker import LeanChecker
from aiprover_orchestration.orchestrator import cdcl
from aiprover_orchestration.orchestrator.config import Config
from aiprover_orchestration.orchestrator.pipeline import Orchestration
from aiprover_orchestration.orchestrator.resume import ResumeState
from aiprover_orchestration.orchestrator.structures import Formalization
from aiprover_orchestration.orchestrator.trace import Trace
from aiprover_orchestration.paths import ROOT, WORKSPACE

TESTS = Path(__file__).parent
RESULTS = ROOT / "results"
LEAN_AVAILABLE = bool(shutil.which("lake")) and (WORKSPACE / ".lake").is_dir()
requires_lean = unittest.skipUnless(
    LEAN_AVAILABLE, "needs Lean and the built orchestration workspace"
)
# A formalization without definitions; lemmas are stated over `Nat`.
FORM = Formalization("", "", "t", " : True ")


class Captain:
    """The captain's agent as the stages inspect it."""

    hosted = True


class ScriptedPool:
    """An agent pool whose captain gives `replies` in order."""

    def __init__(self, replies=(), solver=None, budget: int = 100):
        self.replies = list(replies)
        self.phases: list[str] = []
        self.prompts: list[str] = []
        self.budget = budget
        self.agents = {"captain": Captain(), "solver": solver}

    def complete(self, prompt, *, role, phase, system_prompt, **_):
        self.phases.append(phase)
        self.prompts.append(prompt)
        return self.replies.pop(0)

    def claude_budget_left(self) -> int:
        return self.budget

    def record(self, *_, **__) -> None:
        pass


class FakeSolver(AIProverAgent):
    """AIProver solver answering each lemma with a fixed Lean file."""

    def __init__(self, answers: dict[str, str]):
        self.model = "fake"
        self.metrics_url = None
        self.phase_agents = {}
        self.answers = answers
        self.cancelled: list[str] = []

    def servers(self) -> int:
        return 1

    def endpoint_up(self) -> bool:
        return True

    def cancel(self, job: str) -> None:
        self.cancelled.append(job)

    def solve(self, *, statement, accept, on_start, **_):
        lemma = statement.split()[1]
        job = f"job_{lemma}"
        on_start(job)
        sample = AIProverSample(
            index=0,
            status="verified",
            lean=self.answers[lemma],
            ending="finished",
        )
        accept(sample)
        return AIProverJob(job, "problem", [sample], 0.1)


class RecordingPool:
    """A thread pool that records the lemmas submitted to it."""

    def __init__(self):
        self.lemmas = []

    def submit(self, _function, _phase, lemma):
        self.lemmas.append(lemma)
        future = Future()
        future.set_result(None)
        return future


def aiprover_phase(run: Orchestration, sketch):
    """An AIProver phase of `run` over `sketch` whose submitted lemmas are
    recorded (`phase.pool.lemmas`) rather than proved."""
    phase = run._aiprover_phase(FORM, sketch)
    phase.pool = RecordingPool()
    return phase


def orchestration(
    directory: Path, pool, config: Config | None = None
) -> Orchestration:
    """An Orchestration over `pool` that checks Lean in the orchestration
    workspace and writes its trace and attempt files to `directory`."""
    run = object.__new__(Orchestration)
    run.config = config or Config()
    run.row = {"informal_statement": "s", "informal_proof": "p"}
    run.run_id = "test"
    run.temp_dir = run.result_dir = run.log_dir = directory
    run.workspace = WORKSPACE
    run.lean = LeanChecker(WORKSPACE, 3, 300, stop=STOPPING)
    run.trace = Trace(directory / "trace.json", {})
    run.agents = pool
    run.state = ResumeState()
    run.clauses = cdcl.ClauseStore()
    run._file_counter = 0
    run._counter_lock = threading.Lock()
    run.library = ""
    run.phase_times = {}
    return run


class TemporaryDirectoryTest(unittest.TestCase):
    """A test with a fresh directory `self.directory`, and the run's stop
    flag cleared before and after."""

    def setUp(self) -> None:
        STOPPING.clear()
        self.addCleanup(STOPPING.clear)
        directory = tempfile.TemporaryDirectory(prefix="test_")
        self.addCleanup(directory.cleanup)
        self.directory = Path(directory.name)

    def orchestration(self, pool, config: Config | None = None):
        """`orchestration` in this test's directory; its trace is closed
        before the directory is removed."""
        run = orchestration(self.directory, pool, config)
        self.addCleanup(run.trace.finish, {})
        return run

    def decisions(self, run: Orchestration) -> list[tuple]:
        return [
            (step.get("event"), step.get("action"))
            for step in run.trace.document["steps"]
            if step["kind"] == "decision"
        ]

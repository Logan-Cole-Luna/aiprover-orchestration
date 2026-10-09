"""AIProver harness as a solver backend.

An AIProver call is a complete agentic Lean session: the evolved hevo harness
(`AIProver/AIProver_plugin`) drives a model through lean-lsp tools for
up to `max_turns` turns and writes a Lean file. The model is whatever
OpenAI-compatible endpoint the AIProver configuration names (`[endpoint]` of
`aiprover.toml`), so the same backend serves a small local model now and a
large model later.

The pipeline gives each lemma to one AIProver job with `samples` independent
rollouts; this module submits the job through the plugin's CLI, waits for it,
and extracts the lemma's proof and any helper declarations from each sample.
"""

import json
import logging
import os
import subprocess
import threading
import time
import tomllib
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Callable

from ..paths import ROOT
from .base import Agent

logger = logging.getLogger(__name__)


@dataclass
class AIProverSample:
    index: int
    status: str  # verified | sorry | rejected | error | empty | infra | ...
    lean: str = ""
    turns: int | None = None
    tool_calls: int | None = None
    elapsed_sec: float | None = None
    problems: list[str] = field(default_factory=list)
    ending: str = ""  # why the session stopped; see session_ending()
    check: dict = field(
        default_factory=dict
    )  # harness's Lean check of the final file
    session: list = field(
        default_factory=list
    )  # transcript; see read_session()
    reasoning: list = field(
        default_factory=list
    )  # model reasoning per turn; see read_reasoning()


def session_ending(sample: dict) -> str:
    """Why an AIProver session stopped, from its entry in `aiprover result --json`.

    "server lost" and "server error" are infrastructure failures; "timeout" is
    the job's wall clock, "turn limit" its max_turns; "finished" means the agent
    stopped on its own.
    """
    infra = sample.get("infra") or ""
    if infra:
        return "server lost" if "unreachable" in infra else "server error"
    if sample.get("timed_out"):
        return "timeout"
    if "turn limit" in (sample.get("stop_reason") or "").lower():
        return "turn limit"
    if sample.get("agent_error"):
        return "agent error"
    return "finished"


# Diagnostics beyond this length are cut from the trace record.
MAX_DIAGNOSTICS = 20000
# Transcript entries are cut to these lengths in the trace record.
MAX_MESSAGE_TEXT = 8000
MAX_TOOL_TEXT = 6000


def sample_dir(lean_file: str | None, index) -> Path | None:
    """The harness's directory of sample `index`, next to its `s<k>.lean`."""
    if not lean_file or index is None:
        return None
    return Path(lean_file).parent / f"s{index}"


def read_session(directory: Path | None) -> list[dict]:
    """The agent's transcript from vibe's session log: each message, tool call
    and tool result, in order, shortened for the trace."""
    logs = (
        sorted(
            (directory / ".vibe" / "logs" / "session").glob(
                "session_*/messages.jsonl"
            )
        )
        if directory
        else []
    )
    transcript = []
    for log in logs:
        for line in log.read_text(errors="replace").splitlines():
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            role = message.get("role")
            if role == "tool":
                transcript.append(
                    {
                        "role": "tool",
                        "name": message.get("name"),
                        "text": str(message.get("content") or "")[
                            :MAX_TOOL_TEXT
                        ],
                    }
                )
                continue
            entry = {
                "role": role,
                "text": str(message.get("content") or "")[:MAX_MESSAGE_TEXT],
            }
            calls = [
                {
                    "name": (call.get("function") or {}).get("name"),
                    "arguments": str(
                        (call.get("function") or {}).get("arguments") or ""
                    )[:MAX_TOOL_TEXT],
                }
                for call in message.get("tool_calls") or []
            ]
            if calls:
                entry["tool_calls"] = calls
            if message.get("injected"):
                entry["injected"] = True
            transcript.append(entry)
    return transcript


def read_reasoning(directory: Path | None) -> list[dict]:
    """The model's reasoning per turn, as recorded by server/reasoning_proxy.py
    (`reasoning.jsonl` in the sample directory); empty if it was not recorded.
    """
    path = directory / "reasoning.jsonl" if directory else None
    if not path or not path.exists():
        return []
    records = []
    for line in path.read_text(errors="replace").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def lean_check_summary(check: dict | None) -> dict:
    """The harness's Lean check of a sample's final file, as kept in the trace."""
    check = check or {}
    return {
        "verdict": check.get("verdict"),
        "compiles": check.get("compiles"),
        "complete": check.get("complete"),
        "problems": check.get("problems") or [],
        "warnings": check.get("warnings") or [],
        "axioms_used": check.get("axioms_used"),
        "diagnostics": (check.get("diagnostics") or "")[:MAX_DIAGNOSTICS],
    }


class Terminated(BaseException):
    """The run was terminated while an AIProver job was pending."""


# Set when the run is terminated (`stop_jobs`): pending jobs are cancelled
# within one poll interval and no new job starts. Solver threads would
# otherwise keep waiting on their jobs and hold the process open.
STOPPING = threading.Event()


def stop_jobs() -> None:
    STOPPING.set()


@dataclass
class AIProverJob:
    job: str
    problem: str
    samples: list[AIProverSample]
    seconds: float
    error: str | None = None


class AIProverAgent(Agent):
    """Solver backed by AIProver jobs.

    Options: `config` (path of the aiprover.toml, relative to the
    orchestration root), `cli` (path of the plugin's `bin/aiprover`),
    `timeout` (per-rollout seconds), `max_turns`, `poll_seconds`, and `env`
    (extra environment for the harness, e.g. `AGENT_THINKING`).
    """

    backend = "aiprover"

    def __init__(
        self,
        model: str,
        config: str,
        cli: str,
        timeout: int = 1800,
        max_turns: int = 100,
        poll_seconds: int = 60,
        env: dict | None = None,
        **options,
    ):
        super().__init__(
            model,
            max_retries=0,
            config=config,
            cli=cli,
            timeout=timeout,
            max_turns=max_turns,
            env=env or {},
            **options,
        )
        self.config_path = (ROOT / config).resolve()
        self.cli = (ROOT / cli).resolve()
        with open(self.config_path, "rb") as config_file:
            harness = tomllib.load(config_file)
        self.work_root = Path(harness["runtime"]["work_root"]).expanduser()
        # The model server's Prometheus metrics, beside its OpenAI API.
        api_base = harness["endpoint"]["api_base"].rstrip("/")
        self.metrics_url = api_base.removesuffix("/v1") + "/metrics"
        self.timeout = timeout
        self.max_turns = max_turns
        self.poll_seconds = poll_seconds
        self.env = {
            **os.environ,
            "AIPROVER_CONFIG": str(self.config_path),
            **{key: str(value) for key, value in (env or {}).items()},
        }

    def complete_once(self, prompt: str, system_prompt: str):
        raise NotImplementedError(
            "AIProverAgent serves the solver role through solve()"
        )

    def _cli(
        self, *args: str, timeout: float | None = None
    ) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(self.cli), *args],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    def endpoint_up(self) -> bool:
        """True if the model endpoint answers (through the tunnel)."""
        try:
            return self._cli("tunnel", "status", timeout=60).returncode == 0
        except subprocess.TimeoutExpired:
            return False

    @cached_property
    def supports_resume(self) -> bool:
        """True if the installed AIProver CLI provides `aiprover resume`."""
        try:
            return self._cli("resume", "--help", timeout=60).returncode == 0
        except subprocess.TimeoutExpired:
            return False

    def solve(
        self,
        *,
        name: str,
        theorem_text: str,
        proof_text: str,
        context: str,
        statement: str,
        samples: int,
        work_dir: Path,
        resume_job: str | None = None,
        on_start: Callable[[str], None] | None = None,
    ) -> AIProverJob:
        """Run one AIProver job and return its samples (blocks until done).

        With `resume_job`, the sessions of that earlier job that were cut off (a
        lost model server, an interrupted run) continue where they stopped,
        under this agent's timeout and turn budget, instead of a new job being
        submitted. `on_start` receives the job id once the job runs, so that an
        interruption can still find it. If the installed CLI cannot resume, a
        new job is submitted instead."""
        if STOPPING.is_set():
            raise Terminated
        work_dir.mkdir(parents=True, exist_ok=True)
        start = time.time()
        if resume_job and not self.supports_resume:
            logger.warning(
                f"Cannot resume AIProver job {resume_job}: the installed "
                "AIProver CLI has no `resume` command. Submitting a new job."
            )
            resume_job = None
        if resume_job:
            submitted = self._cli(
                "resume",
                resume_job,
                "-q",
                "--timeout",
                str(self.timeout),
                "--max-turns",
                str(self.max_turns),
                timeout=120,
            )
            rendered = ""
            if submitted.returncode == 0:
                problem = self.work_root / "jobs" / resume_job / "problem.txt"
                rendered = problem.read_text() if problem.exists() else ""
        else:
            context_path = work_dir / f"{name}_context.lean"
            statement_path = work_dir / f"{name}_statement.lean"
            context_path.write_text(context)
            statement_path.write_text(statement)
            args = [
                "--theorem-text",
                theorem_text,
                "--proof-text",
                proof_text,
                "--context",
                str(context_path),
                "--lean-statement",
                str(statement_path),
            ]
            rendered = self._cli("render", *args, timeout=60).stdout
            submitted = self._cli(
                "submit",
                "-q",
                "-k",
                str(samples),
                "--name",
                name,
                "--timeout",
                str(self.timeout),
                "--max-turns",
                str(self.max_turns),
                *args,
                timeout=120,
            )
        job = (
            submitted.stdout.strip().splitlines()[-1]
            if submitted.stdout.strip()
            else ""
        )
        if submitted.returncode != 0 or not job:
            return AIProverJob(
                job,
                rendered,
                [],
                time.time() - start,
                error=(submitted.stderr or submitted.stdout)[-1000:],
            )
        if on_start:
            on_start(job)
        try:
            while (
                not STOPPING.is_set()
                and self._cli(
                    "wait",
                    job,
                    "--timeout",
                    str(self.poll_seconds),
                    timeout=self.poll_seconds + 60,
                ).returncode
                == 3
            ):
                pass
            if STOPPING.is_set():
                raise Terminated
        except BaseException:
            self._cli("cancel", job, timeout=60)
            raise
        result = self._cli("result", job, "--json", timeout=120)
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            return AIProverJob(
                job,
                rendered,
                [],
                time.time() - start,
                error=(result.stderr or result.stdout)[-1000:],
            )
        job_samples = []
        for sample in payload.get("samples", []):
            lean_file = sample.get("lean_file")
            lean = (
                Path(lean_file).read_text(errors="replace") if lean_file else ""
            )
            job_samples.append(
                AIProverSample(
                    index=sample.get("sample", len(job_samples)),
                    status=sample.get("status", "unknown"),
                    lean=lean,
                    turns=sample.get("turns"),
                    tool_calls=sample.get("tool_calls"),
                    elapsed_sec=sample.get("elapsed_sec"),
                    ending=session_ending(sample),
                    check=lean_check_summary(sample.get("check")),
                    session=read_session(
                        sample_dir(lean_file, sample.get("sample"))
                    ),
                    reasoning=read_reasoning(
                        sample_dir(lean_file, sample.get("sample"))
                    ),
                    problems=list(
                        (sample.get("check") or {}).get("problems") or []
                    ),
                )
            )
        return AIProverJob(job, rendered, job_samples, time.time() - start)

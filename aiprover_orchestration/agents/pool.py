"""Pluggable model backends for the orchestration roles.

Each role (captain, auditor, solver, reviewer, writer) is served by an agent
built from a specification in the run configuration:

    {"backend": "claude", "model": "claude-sonnet-5"}
    {"backend": "openai_compatible", "model": "qwen3-4b-instruct",
     "base_url": "http://localhost:8000/v1"}
    {"backend": "aiprover", "model": "aiprover",
     "config": "configs/aiprover/local.toml",
     "cli": "AIProver/AIProver_plugin/bin/aiprover"}
    {"backend": "python", "class": "my_package.my_module:MyAgent", ...}
    {"backend": "claude", "model": "claude-opus-5-5",
     "phases": {"judge": {"model": "claude-sonnet-5-5"}}}
    {"backend": "claude", "model": "claude-opus-5-5", "tools": true}

`claude` calls Claude through the Anthropic API when `ANTHROPIC_API_KEY` is
set, otherwise through the logged-in `claude` CLI (Claude subscription);
`CLAUDE_AUTH=api` or `CLAUDE_AUTH=subscription` selects one explicitly.
`openai_compatible` covers any server that implements the OpenAI chat
completions schema: a local vLLM server, or a hosted provider named by
`provider` (`openai`, `huggingface`, `openrouter`). `aiprover` runs the AIProver
harness (a full agentic Lean session per call; solver role only) with the
model named in its own configuration. `python` loads a user-defined
subclass of `Agent`, so a custom agent can replace any role without changes
to the pipeline.

`tools` gives a Claude agent on the CLI Lean tools (agents/claude_cli.py);
`role_spec` sets the defaults: on for the captain except its judge phases,
off for the other roles.

`AgentPool` routes each call to the agent of its role, retries transient
failures, and records every call in `calls.jsonl` and in the run trace.

    python -m aiprover_orchestration.agents.pool --config <run config>
probes every role's agent.
"""

import importlib
import json
import logging
import os
import threading
import time
from pathlib import Path

from ..orchestrator.trace import REFUSAL_MARKER, is_untraced
from .base import (
    STOPPING,
    Agent,
    AgentCallError,
    AgentRefusal,
    BudgetExhausted,
    Completion,
    Terminated,
    add_usage,
    raise_if_stopping,
)
from .claude import ClaudeAgent
from .claude_cli import ClaudeCLIAgent
from .openai_compatible import OpenAICompatibleAgent

logger = logging.getLogger(__name__)


CLAUDE_AGENTS = {"api": ClaudeAgent, "subscription": ClaudeCLIAgent}

def claude_agent(**options) -> Agent:
    """A Claude agent on the API (a key is set) or on the subscription of
    the logged-in `claude` CLI; `CLAUDE_AUTH` overrides the choice."""
    auth = os.environ.get("CLAUDE_AUTH") or (
        "api" if os.environ.get("ANTHROPIC_API_KEY") else "subscription"
    )
    if auth not in CLAUDE_AGENTS:
        raise ValueError(
            f"CLAUDE_AUTH={auth!r}; expected one of {sorted(CLAUDE_AGENTS)}"
        )
    return CLAUDE_AGENTS[auth](**options)


BACKENDS = {
    "claude": claude_agent,
    "openai_compatible": OpenAICompatibleAgent,
}


# Roles whose Claude agent has Lean tools unless its specification says
# otherwise, and the captain phases that have none by default: the judges
# assess text, and tools would let them consult more than they are shown;
# a direction is written from the job's inputs, as the prover sees them.
TOOL_ROLES = ("captain",)
TOOLLESS_PHASES = ("judge", "direction")


def role_spec(role: str, spec: dict) -> dict:
    """`spec` with the default Lean tools of `role` for a Claude backend."""
    if spec.get("backend") != "claude":
        return spec
    spec = {"tools": role in TOOL_ROLES, **spec}
    if spec["tools"]:
        phases = dict(spec.get("phases") or {})
        for prefix in TOOLLESS_PHASES:
            phases[prefix] = {"tools": False, **phases.get(prefix, {})}
        spec["phases"] = phases
    return spec


def build_agent(spec: dict) -> Agent:
    """Instantiate the agent described by one role specification. Its
    `phases` maps phase-label prefixes to option overrides, each served by a
    variant agent (e.g. a smaller model for the captain's judge phase)."""
    phases = spec.get("phases") or {}
    base = {key: value for key, value in spec.items() if key != "phases"}
    agent = _build(base)
    agent.phase_agents = {
        prefix: _build({**base, **overrides})
        for prefix, overrides in phases.items()
    }
    return agent


def _build(spec: dict) -> Agent:
    options = dict(spec)
    backend = options.pop("backend")
    if backend == "aiprover":
        from .aiprover import AIProverAgent

        return AIProverAgent(**options)
    if backend == "python":
        module_name, class_name = options.pop("class").split(":")
        agent_class = getattr(importlib.import_module(module_name), class_name)
        if not issubclass(agent_class, Agent):
            raise TypeError(
                f"{agent_class} is not a subclass of agents.base.Agent"
            )
        return agent_class(**options)
    if backend not in BACKENDS:
        raise ValueError(
            f"unknown backend {backend!r}; expected one of "
            f"{sorted(BACKENDS) + ['aiprover', 'python']}"
        )
    return BACKENDS[backend](**options)


class AgentPool:
    """Routes calls by role, retries transient failures, and records calls.

    Each call is appended to `calls_path` as one JSON line and, if a trace is
    given, added to it as a `model_call` step.
    """

    BACKOFF_S = [10, 30, 60, 180, 300]

    def __init__(
        self,
        agents: dict[str, Agent],
        calls_path: Path,
        trace=None,
        system_prompt_ids: dict[str, str] | None = None,
        max_claude_calls: int = 0,
    ):
        self.agents = agents
        self.max_claude_calls = max_claude_calls
        self.calls_path = Path(calls_path)
        self.calls_path.parent.mkdir(parents=True, exist_ok=True)
        self.trace = trace
        # Maps a system prompt's text to its key in the trace header, so the
        # trace stores each system prompt once.
        self.system_prompt_ids = system_prompt_ids or {}
        self._lock = threading.Lock()
        self.num_calls: dict[str, int] = {}
        self.claude_calls = 0
        self.usage_by_model: dict[str, dict] = {}
        if self.calls_path.exists():
            self._load_history()
        elif trace is not None:
            self._load_trace_history()

    def _load_history(self) -> None:
        """Seed the call and usage totals from an existing calls file (resume)."""
        for line in self.calls_path.read_text().splitlines():
            try:
                record = json.loads(line)
            except json.JSONDecodeError:
                # The last line of a process killed while writing it.
                logger.warning(f"{self.calls_path}: unreadable line skipped")
                continue
            self._accumulate(
                record["role"],
                _hosted(record),
                record.get("model_usage") or {},
                error=record.get("error"),
                refused=_refused(record),
            )

    def _load_trace_history(self) -> None:
        """Seed the call totals from a resumed trace's model calls, for a run
        whose call log is absent (a result directory copied elsewhere)."""
        for step in self.trace.document["steps"]:
            if step["kind"] == "model_call":
                usage = step.get("usage") or {}
                self._accumulate(
                    step["role"],
                    _hosted(step),
                    {step.get("model") or "": usage} if usage else {},
                    error=step.get("error"),
                    refused=_refused(step),
                )

    def _accumulate(
        self,
        role: str,
        hosted: bool,
        model_usage: dict,
        error: str | None = None,
        refused: bool = False,
    ) -> None:
        # The budget counts the calls to hosted models that are billed: a
        # refusal is billed, a failure on a rate limit or outage is not.
        self.claude_calls += hosted and (error is None or refused)
        # The reported totals cover the calls the trace keeps.
        if is_untraced(error, refused):
            return
        self.num_calls[role] = self.num_calls.get(role, 0) + 1
        add_usage(self.usage_by_model, model_usage)

    def complete(
        self,
        prompt: str,
        *,
        role: str,
        system_prompt: str,
        phase: str,
        **trace_fields,
    ) -> str:
        """Return the reply of the role's agent.

        Raises RuntimeError when the call fails permanently, so that the
        pipeline never proceeds on an empty reply, and AgentRefusal when the
        provider's safety classifier declines the prompt. Raises Terminated
        once the run is terminating: no call starts and no retry waits.
        """
        agent = self.agents[role].for_phase(phase)
        if agent.hosted and self.max_claude_calls:
            self._check_claude_budget()
        completion = Completion()
        for attempt in range(agent.max_retries + 1):
            raise_if_stopping()
            start = time.time()
            completion = agent.complete_once(prompt, system_prompt)
            self._record(
                role,
                phase,
                agent,
                system_prompt,
                prompt,
                completion,
                time.time() - start,
                trace_fields,
            )
            if completion.error is None:
                return completion.text
            if not completion.retryable or attempt == agent.max_retries:
                break
            wait = completion.retry_after_s
            if wait is None:
                wait = self.BACKOFF_S[min(attempt, len(self.BACKOFF_S) - 1)]
            logger.warning(
                f"[{role}/{phase}] transient failure, retrying in "
                f"{wait:.0f}s: {completion.error[:200]}"
            )
            # A terminated run stops waiting, so the process can exit.
            if STOPPING.wait(max(wait, 1.0)):
                raise Terminated
        error = AgentRefusal if completion.refused else AgentCallError
        raise error(
            f"{agent.backend} call failed ({role}/{phase}): "
            f"{completion.error}"
        )

    def claude_budget_left(self) -> int:
        """Billed calls to hosted models left in the run's budget (large if
        unlimited)."""
        return (
            self.max_claude_calls - self.claude_calls
            if self.max_claude_calls
            else 1 << 30
        )

    def _check_claude_budget(self) -> None:
        # Not an AgentCallError: the pipeline reports it as a budget stop,
        # which is not restarted automatically.
        if self.claude_calls >= self.max_claude_calls:
            raise BudgetExhausted(
                f"hosted model call budget of {self.max_claude_calls} "
                f"exhausted"
            )

    def record(
        self,
        role: str,
        phase: str,
        prompt: str,
        completion: Completion,
        seconds: float,
        **trace_fields,
    ) -> None:
        """Record a call made outside `complete` (e.g. an AIProver job)."""
        self._record(
            role,
            phase,
            self.agents[role],
            "",
            prompt,
            completion,
            seconds,
            trace_fields,
        )

    def _record(
        self,
        role,
        phase,
        agent,
        system_prompt,
        prompt,
        completion,
        seconds,
        trace_fields,
    ):
        cost = sum(u.get("costUSD", 0.0) for u in completion.usage.values())
        record = {
            "time": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "role": role,
            "phase": phase,
            "backend": agent.backend,
            "hosted": agent.hosted,
            "requested_model": agent.model,
            "model_usage": completion.usage,
            "cost_usd": cost,
            "seconds": round(seconds, 1),
            "error": completion.error,
            "refused": completion.refused,
            "system_prompt": system_prompt,
            "prompt": prompt,
            "response": completion.text,
        }
        with self._lock:
            self._accumulate(
                role,
                agent.hosted,
                completion.usage,
                error=completion.error,
                refused=completion.refused,
            )
            with open(self.calls_path, "a") as f:
                f.write(json.dumps(record) + "\n")
        # A usage-limit failure or a refusal concerns the account or the
        # provider, not the run: it stays in the call log and is left out of
        # the trace.
        if self.trace is not None and not is_untraced(
            completion.error, completion.refused
        ):
            primary_usage = completion.usage.get(agent.model) or next(
                iter(completion.usage.values()), {}
            )
            self.trace.add(
                "model_call",
                label=phase,
                role=role,
                backend=agent.backend,
                hosted=agent.hosted,
                model=agent.model,
                resolved_models=list(completion.usage),
                system_prompt=self.system_prompt_ids.get(
                    system_prompt, system_prompt
                ),
                prompt=prompt,
                response=completion.text,
                error=completion.error,
                refused=completion.refused,
                seconds=record["seconds"],
                cost_usd=cost,
                usage=primary_usage,
                **trace_fields,
            )


def _hosted(record: dict) -> bool:
    """Whether a recorded call went to a hosted model; records written
    before the field existed count the `claude` backend as hosted."""
    return record.get("hosted", record.get("backend") == "claude")


def _refused(record: dict) -> bool:
    return record.get(
        "refused", REFUSAL_MARKER in (record.get("error") or "")
    )


def probe(agents: dict[str, Agent]) -> None:
    """Send one short prompt to each role's agent and print the result."""
    for role, agent in agents.items():
        completion = agent.complete_once(
            "Reply with the single word OK.", "You are terse."
        )
        print(
            f"{role}: {agent.backend}/{agent.model} reply={completion.text!r} "
            f"error={completion.error} served_by={list(completion.usage)}"
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Probe every role's agent.")
    parser.add_argument("--config", type=Path, required=True)
    config = json.loads(parser.parse_args().config.read_text())
    probe(
        {
            role: build_agent(role_spec(role, spec))
            for role, spec in config["agents"].items()
        }
    )

"""Codex models through the local `codex exec`, on the user's ChatGPT plan or
API key. With Lean tools, a call runs in a scratch Lake project as a Claude
call does (agents/claude_cli.py), in Codex's workspace sandbox.

Codex reports tokens, not prices, so a call records no cost.
"""

import json
import os
import shutil
import subprocess
from pathlib import Path

from ..utils.processes import ProcessStopped, run_group
from .base import STOPPING, Agent, Completion, Terminated
from .claude_cli import (
    CLI_WORKDIR,
    LEAN_CHECK_BIN,
    LEAN_TOOLS_NOTE,
    MAX_RETRIES,
    REQUEST_TIMEOUT_S,
    RETRYABLE_ERROR,
    SOURCE_NOTE,
    STOP_GRACE_SECONDS,
    add_source,
    lean_scratch_project,
)


DEFAULT_CODEX_MODEL = "gpt-6.1-sol"


def command(model: str, effort: str, workdir: Path, write: bool) -> list[str]:
    """`codex exec` on a prompt from standard input."""
    sandbox = "workspace-write" if write else "read-only"
    arguments = ["codex", "exec", "--json", "--ephemeral", "-m", model]
    arguments += ["--skip-git-repo-check", "-s", sandbox, "-C", str(workdir)]
    if effort:
        arguments += ["-c", f'model_reasoning_effort="{effort}"']
    return [*arguments, "-"]


def parse_events(output: str) -> tuple[str, dict, str | None]:
    """The final message, the token usage, and the error of a JSON event
    stream."""
    text, usage, error = "", {}, None
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        item = event.get("item") or {}
        if kind == "item.completed" and item.get("type") == "agent_message":
            text = item.get("text", "")
        elif kind == "turn.completed":
            usage, error = event.get("usage") or {}, None
        elif kind in ("turn.failed", "error"):
            failure = event.get("error") or event
            error = str(failure.get("message", failure))
    return text, usage, error


class CodexCLIAgent(Agent):
    """Codex model through the local `codex` CLI."""

    backend = "codex"
    hosted = True

    def __init__(
        self,
        model: str,
        effort: str = "",
        timeout: int = REQUEST_TIMEOUT_S,
        tools: bool = False,
        source_dir: str = "",
        **options,
    ):
        super().__init__(
            model,
            max_retries=MAX_RETRIES,
            effort=effort,
            timeout=timeout,
            tools=tools,
            **options,
        )
        self.effort = effort
        self.timeout = timeout
        self.tools = tools
        self.source_dir = source_dir

    def complete_once(self, prompt: str, system_prompt: str) -> Completion:
        env = dict(os.environ)
        workdir = CLI_WORKDIR
        if self.tools:
            workdir = lean_scratch_project()
            env["PATH"] = f"{LEAN_CHECK_BIN}:{env.get('PATH', '')}"
            system_prompt += LEAN_TOOLS_NOTE
            if self.source_dir:
                add_source(workdir, self.source_dir)
                system_prompt += SOURCE_NOTE
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            reply = run_group(
                command(self.model, self.effort, workdir, self.tools),
                cwd=workdir,
                timeout=self.timeout,
                stop=STOPPING,
                stdin=f"{system_prompt}\n\n{prompt}",
                env=env,
                grace=STOP_GRACE_SECONDS,
            )
        except ProcessStopped:
            raise Terminated from None
        except subprocess.TimeoutExpired:
            return Completion(error=f"codex timed out after {self.timeout}s")
        except FileNotFoundError:
            return Completion(error="`codex` CLI not found on PATH")
        finally:
            if self.tools:
                shutil.rmtree(workdir, ignore_errors=True)
        text, tokens, error = parse_events(reply.stdout)
        usage = {
            self.model: {
                "inputTokens": int(tokens.get("input_tokens") or 0),
                "outputTokens": int(tokens.get("output_tokens") or 0),
                "cacheReadInputTokens": int(
                    tokens.get("cached_input_tokens") or 0
                ),
                "costUSD": 0.0,
            }
        }
        if error or not text:
            detail = error or reply.stderr.strip()[-500:] or "no reply"
            return Completion(
                usage=usage,
                error=detail,
                retryable=bool(RETRYABLE_ERROR.search(detail)),
            )
        return Completion(text=text, usage=usage)

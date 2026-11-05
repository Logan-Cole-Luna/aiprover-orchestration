"""Codex models through the local `codex exec`, on the user's ChatGPT plan or
API key. Every built-in tool that can act is turned off, so that a call has the
restrictions of a Claude call (agents/claude_cli.py): none without tools; with
Lean tools, the file tools and `lean-check` of session_tools.py in a scratch
Lake project. No call has a shell, web search, connected apps, or sub-agents.

Codex reports tokens, not prices, and only when a call ends, and it has no
setting for the length of a reply. A call's spend limit therefore becomes a turn
limit, which the tool server enforces, and the pool counts the tokens against
the run's limit once the call ends. Session files would report tokens per
response, but they keep every prompt on disk, so calls stay ephemeral.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..utils.processes import ProcessStopped, run_group
from .base import STOPPING, TURNS_PER_USD, Agent, Completion, Terminated
from .common import (
    CLI_WORKDIR,
    LEAN_PACKAGES_DIR,
    LEAN_TOOLS_BUDGET_USD,
    MAX_RETRIES,
    REQUEST_TIMEOUT_S,
    STOP_GRACE_SECONDS,
    failed_call,
    tool_project,
)
from .session_tools import COMMAND_SECONDS, TURN_LIMIT


DEFAULT_CODEX_MODEL = "gpt-6.1-sol"


# The features of Codex that give the model a tool. Its code-mode host stays on:
# the model reaches every tool through it, and it has no file or network access
# of its own. The patch tool stays only in name: the read-only sandbox rejects it.
DISABLED_FEATURES = (
    "shell_tool", "unified_exec", "apps", "plugins", "plugin_discovery_prefetch",
    "remote_plugin", "multi_agent", "collaboration_modes", "goals", "image_generation", "view_image", "hooks",
    "skill_search", "skill_mcp_dependency_install", "sleep_tool", "tool_suggest",
    "browser_use", "browser_use_external", "browser_use_full_cdp_access",
    "computer_use", "in_app_browser",
)  # fmt: skip
SESSION_TOOLS = Path(__file__).resolve().with_name("session_tools.py")
# What the server passes to `lean-check` and the session Python.
SERVER_ENVIRONMENT = [
    "PATH", "HOME", "ELAN_HOME", "AIPROVER_ORCHESTRATION_HOME",
    "AIPROVER_LIMITS_DIR", "SESSION_PYTHON", "SESSION_PREFIX", "SESSION_SANDBOX",
    "SESSION_READ", "SESSION_WRITE",
]  # fmt: skip
def token_usage(reported: dict) -> dict:
    """Codex's usage in the naming of `Completion.usage`. Its input tokens
    include the cached ones."""
    cached = int(reported.get("cached_input_tokens") or 0)
    return {
        "inputTokens": int(reported.get("input_tokens") or 0) - cached,
        "outputTokens": int(reported.get("output_tokens") or 0),
        "cacheReadInputTokens": cached,
        "costUSD": 0.0,
    }


def command(
    model: str,
    effort: str,
    workdir: Path,
    write: bool,
    read: tuple[str, ...] = (),
    tools: tuple[str, ...] = (),
    max_turns: int = 0,
) -> list[str]:
    """`codex exec` on a prompt from standard input. With `write`, the model
    has session_tools.py over `workdir`, the `read` trees, and the programs
    `tools`, for at most `max_turns` tool calls (0: unlimited)."""
    arguments = ["codex", "exec", "--json", "--ephemeral", "-m", model]
    arguments += ["--skip-git-repo-check", "-s", "read-only", "-C", str(workdir)]
    arguments += ["-c", 'web_search="disabled"', "-c", 'approval_policy="never"']
    for feature in DISABLED_FEATURES:
        arguments += ["--disable", feature]
    if effort:
        arguments += ["-c", f'model_reasoning_effort="{effort}"']
    if write:
        server = ["-I", str(SESSION_TOOLS), "--write", str(workdir)]
        server += ["--max-turns", str(max_turns)]
        for path in read:
            server += ["--read", str(path)]
        for program in tools:
            server += ["--command", program]
        arguments += [
            "-c", f'mcp_servers.session.command="{sys.executable}"',
            "-c", f"mcp_servers.session.args={json.dumps(server)}",
            "-c", 'mcp_servers.session.default_tools_approval_mode="approve"',
            "-c", f"mcp_servers.session.tool_timeout_sec={COMMAND_SECONDS}",
            "-c", f"mcp_servers.session.env_vars={json.dumps(SERVER_ENVIRONMENT)}",
        ]  # fmt: skip
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
        max_budget_usd: float = LEAN_TOOLS_BUDGET_USD,
        max_turns: int = 0,
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
        self.max_turns = max_turns or round(max_budget_usd * TURNS_PER_USD)

    def complete_once(self, prompt: str, system_prompt: str) -> Completion:
        env = dict(os.environ)
        workdir = CLI_WORKDIR
        if self.tools:
            workdir, note, env["PATH"] = tool_project(self.source_dir)
            system_prompt += note
        workdir.mkdir(parents=True, exist_ok=True)
        try:
            reply = run_group(
                command(
                    self.model,
                    self.effort,
                    workdir,
                    self.tools,
                    read=(str(LEAN_PACKAGES_DIR),),
                    tools=("lean-check",),
                    max_turns=self.max_turns,
                ),
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
        usage = {self.model: token_usage(tokens)}
        if TURN_LIMIT in reply.stdout:
            return failed_call(
                f"turn limit of {self.max_turns} reached", usage, limited=True
            )
        if error or not text:
            detail = error or reply.stderr.strip()[-500:] or "no reply"
            return failed_call(detail, usage)
        return Completion(text=text, usage=usage)

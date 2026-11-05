"""Claude models through the local `claude` CLI (subscription auth).

Calls are billed against the Claude account session held by the CLI
(`claude login` / `claude setup-token`), not a per-token API key. Adapted from
MixtureOfMathExperts/scripts/utils/generators.py (`_invoke_claude_cli_once`,
`_run_claude_cli`), with the system prompt passed per call so that each
orchestration role carries its own instructions.

Every call runs with setting sources and MCP servers cleared, so that no
CLAUDE.md or project configuration leaks into the model context. Without
tools, a call runs in a working directory outside the workspace. With Lean
tools (`tools=True`), it runs in a scratch Lake project of its own, where the
model may read and write files, search the Mathlib sources and check files
with `lean-check` (lean/tool_check.py: code-running constructs rejected,
elaboration under lean/sandbox.py); other commands and files outside the
project are denied.
"""

import argparse
import json
import logging
import os
import shutil
import subprocess

from ..limits import LEAN_CHECK_TIMEOUT_MS
from ..utils.processes import ProcessStopped, run_group
from .base import STOPPING, Agent, Completion, Terminated
from .common import (
    CLI_WORKDIR,
    LEAN_CHECK_BIN,
    LEAN_PACKAGES_DIR,
    LEAN_TOOLS_BUDGET_USD,
    MAX_RETRIES,
    REQUEST_TIMEOUT_S,
    SOURCE_NOTE,
    STOP_GRACE_SECONDS,
    add_source,
    failed_call,
    lean_scratch_project,
)

logger = logging.getLogger(__name__)

# Lean tools: the built-in tools offered, the permissions granted (all
# others are denied without a prompt), and the scratch projects, one per call.
LEAN_TOOLS = "Bash,Read,Write,Edit,Glob,Grep"
# Unscoped Glob and Grep rules reach any path, so every read rule names its
# tree. The packages are a symlink out of the project, matched by their real
# path ("//" marks an absolute rule); they are readable, not writable.
LEAN_PACKAGES = f"/{LEAN_PACKAGES_DIR}/**"
LEAN_TOOL_PERMISSIONS = (
    "Read(./**)",
    "Glob(./**)",
    "Grep(./**)",
    f"Read({LEAN_PACKAGES})",
    f"Glob({LEAN_PACKAGES})",
    f"Grep({LEAN_PACKAGES})",
    "Write(./**)",
    "Edit(./**)",
    "Bash(lean-check:*)",
)
LEAN_TOOLS_NOTE = """

Tools. The working directory is a Lean 4 project with Mathlib (the versions of
the run). Create and change files in it with the Write and Edit tools, and
check a file with the shell command `lean-check <file>`, run on its own with
a relative path; it is the only shell command permitted. Files may not use
constructs that run code (`#eval`, `IO`, `unsafe`, `elab`, `run_cmd` and
similar); `lean-check` rejects them. Mathlib's
sources are under `.lake/packages/mathlib/Mathlib`, for Grep and Read. Files
outside the working directory are unavailable. Check the Lean you return
before replying. Your final message is the reply, in the format the task
requires."""
# Result subtypes and error text of a call stopped at its spend, turn or
# reply-length limit.
LIMIT_SUBTYPES = ("error_max_budget_usd", "error_max_turns")
OUTPUT_LIMIT_MARKER = "output token maximum"


def get_account_status(timeout: int = 30) -> dict | None:
    """Return `claude auth status --json` if logged in, otherwise None."""
    try:
        proc = subprocess.run(
            ["claude", "auth", "status", "--json"],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        status = json.loads(proc.stdout)
    except (
        subprocess.TimeoutExpired,
        FileNotFoundError,
        json.JSONDecodeError,
    ) as e:
        logger.warning(f"Could not read `claude auth status`: {e}")
        return None
    return status if status.get("loggedIn") else None


def invoke_once(
    prompt: str,
    model: str,
    system_prompt: str,
    timeout: int = REQUEST_TIMEOUT_S,
    effort: str = "",
    max_output_tokens: int = 0,
    tools: bool = False,
    max_budget_usd: float = LEAN_TOOLS_BUDGET_USD,
    max_turns: int = 0,
    source_dir: str = "",
) -> tuple[str, dict, str | None]:
    """One `claude -p` call. Returns (text, payload, error); error is None on success.

    The CLI reports errors as JSON on stdout while exiting non-zero, so stdout
    is parsed regardless of the return code.
    """
    if tools:
        system_prompt += LEAN_TOOLS_NOTE
    cmd = [
        "claude",
        "-p",
        "--model",
        model,
        "--output-format",
        "json",
        "--tools",
        LEAN_TOOLS if tools else "",
        "--no-session-persistence",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--system-prompt",
        system_prompt,
    ]
    if effort and effort != "default":
        cmd += ["--effort", effort]
    if tools:
        cmd += ["--permission-mode", "dontAsk", "--max-budget-usd"]
        cmd += [str(max_budget_usd), "--allowedTools", *LEAN_TOOL_PERMISSIONS]
        if max_turns:
            cmd += ["--max-turns", str(max_turns)]
    env = dict(os.environ)
    if tools:
        env["PATH"] = f"{LEAN_CHECK_BIN}:{env.get('PATH', '')}"
    # The CLI writes every prompt to the one-hour cache at twice the input
    # price. A single-turn call reads it back only when the whole prompt
    # repeats, which orchestration prompts do not, so caching is disabled;
    # the turns of a call with tools share their prefix, so it is kept.
    if not tools:
        env["DISABLE_PROMPT_CACHING"] = "1"
    # The CLI's default reply limit is replaced when the agent sets one.
    if max_output_tokens:
        env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] = str(max_output_tokens)
    workdir = lean_scratch_project() if tools else CLI_WORKDIR
    if tools and source_dir:
        add_source(workdir, source_dir)
        cmd[cmd.index("--system-prompt") + 1] += SOURCE_NOTE
    workdir.mkdir(parents=True, exist_ok=True)
    if tools:
        # `lean-check` may wait for a Lean slot of the machine.
        env["BASH_DEFAULT_TIMEOUT_MS"] = env["BASH_MAX_TIMEOUT_MS"] = str(
            LEAN_CHECK_TIMEOUT_MS
        )
    try:
        proc = run_group(
            cmd,
            cwd=workdir,
            timeout=timeout,
            stop=STOPPING,
            stdin=prompt,
            env=env,
            grace=STOP_GRACE_SECONDS,
        )
    except ProcessStopped:
        raise Terminated from None
    except subprocess.TimeoutExpired:
        return "", {}, f"claude CLI timed out after {timeout}s"
    except FileNotFoundError:
        return "", {}, "__fatal__ `claude` CLI not found on PATH"
    finally:
        if tools:
            shutil.rmtree(workdir, ignore_errors=True)

    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        payload = None
    if payload is None:
        detail = (proc.stderr.strip() or proc.stdout.strip() or "no output")[
            :500
        ]
        return "", {}, f"exit {proc.returncode}, unparseable output: {detail}"
    if proc.returncode != 0 or payload.get("is_error"):
        detail = str(
            payload.get("result")
            or payload.get("error")
            or payload.get("subtype")
            or proc.stderr.strip()
            or "unknown"
        )[:500]
        status = payload.get("api_error_status")
        return (
            "",
            payload,
            f"exit {proc.returncode} (api_status={status}): {detail}",
        )
    return (payload.get("result") or "").strip(), payload, None


class ClaudeCLIAgent(Agent):
    """Claude model through the local `claude` CLI (subscription auth)."""

    backend = "claude"
    hosted = True

    def __init__(
        self,
        model: str,
        effort: str = "",
        timeout: int = REQUEST_TIMEOUT_S,
        max_output_tokens: int = 0,
        tools: bool = False,
        max_budget_usd: float = LEAN_TOOLS_BUDGET_USD,
        max_turns: int = 0,
        source_dir: str = "",
        **options,
    ):
        if get_account_status() is None:
            raise RuntimeError("`claude` CLI is not logged in")
        super().__init__(
            model,
            max_retries=MAX_RETRIES,
            effort=effort,
            timeout=timeout,
            max_output_tokens=max_output_tokens,
            tools=tools,
            **options,
        )
        self.effort = effort
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens
        self.tools = tools
        self.max_budget_usd = max_budget_usd
        self.max_turns = max_turns
        self.source_dir = source_dir

    def complete_once(self, prompt: str, system_prompt: str) -> Completion:
        text, payload, error = invoke_once(
            prompt,
            self.model,
            system_prompt,
            self.timeout,
            self.effort,
            self.max_output_tokens,
            self.tools,
            self.max_budget_usd,
            self.max_turns,
            self.source_dir,
        )
        usage = {
            model_id: {
                "inputTokens": int(u.get("inputTokens") or 0),
                "outputTokens": int(u.get("outputTokens") or 0),
                "cacheReadInputTokens": int(
                    u.get("cacheReadInputTokens") or 0
                ),
                "cacheCreationInputTokens": int(
                    u.get("cacheCreationInputTokens") or 0
                ),
                "costUSD": float(u.get("costUSD") or 0.0),
            }
            for model_id, u in (payload.get("modelUsage") or {}).items()
        }
        if error is None:
            return Completion(text=text, usage=usage)
        return failed_call(
            error,
            usage,
            limited=payload.get("subtype") in LIMIT_SUBTYPES
            or OUTPUT_LIMIT_MARKER in error,
        )


def _probe(models: list[str]) -> None:
    """Send one short prompt to each model and print the resolved model ids."""
    status = get_account_status()
    if status is None:
        raise SystemExit("`claude` CLI is not logged in.")
    print(f"account: {status.get('email')} ({status.get('subscriptionType')})")
    for model in models:
        text, payload, error = invoke_once(
            "Reply with the single word OK.",
            model,
            "You are terse.",
            timeout=120,
        )
        resolved = list((payload or {}).get("modelUsage", {}))
        print(f"{model}: reply={text!r} error={error} resolved={resolved}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--probe",
        nargs="+",
        default=["claude-sonnet-5", "claude-haiku-4-5-20251001"],
    )
    _probe(parser.parse_args().probe)

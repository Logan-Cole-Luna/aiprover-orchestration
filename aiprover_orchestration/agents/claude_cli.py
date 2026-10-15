"""Claude models through the local `claude` CLI (subscription auth).

Calls are billed against the Claude account session held by the CLI
(`claude login` / `claude setup-token`), not a per-token API key. Adapted from
MixtureOfMathExperts/scripts/utils/generators.py (`_invoke_claude_cli_once`,
`_run_claude_cli`), with the system prompt passed per call so that each
orchestration role carries its own instructions.

Every call runs with tools disabled and with setting sources and MCP servers
cleared, in a working directory outside the workspace, so that no CLAUDE.md or
project configuration leaks into the model context.
"""

import argparse
import json
import logging
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..orchestrator.trace import REFUSAL_MARKER
from .base import Agent, Completion

logger = logging.getLogger(__name__)

REQUEST_TIMEOUT_S = 900
MAX_RETRIES = 5
RETRY_BACKOFF_S = [30, 60, 180, 300, 600]
# A usage limit (as opposed to a short rate limit) reports a reset time that
# can be hours away, so a single call sleeps at most this long.
MAX_USAGE_LIMIT_WAIT_S = 3600
RETRYABLE_MARKERS = (
    "rate limit",
    "rate_limit",
    "usage limit",
    "session limit",
    "429",
    "overloaded",
    "server error",
    "500",
    "502",
    "503",
    "529",
    "timeout",
    "timed out",
    "connection error",
    "econnreset",
    "network",
    "temporarily unavailable",
)


# Neutral working directory for CLI subprocesses (no CLAUDE.md above it).
CLI_WORKDIR = Path(tempfile.gettempdir()) / "aiprover_orchestration_cli_cwd"


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


def _clock_reset_epoch(text: str) -> float | None:
    """The next occurrence of a clock time such as `resets 1:40pm
    (America/Chicago)`, as an epoch."""
    match = re.search(
        r"resets (\d{1,2})(?::(\d{2}))?\s*([ap]m)\s*\(([^)]+)\)",
        text,
        flags=re.IGNORECASE,
    )
    if not match:
        return None
    hour, minute, meridiem, zone = match.groups()
    try:
        now = datetime.now(ZoneInfo(zone))
    except (ZoneInfoNotFoundError, ValueError):
        return None
    hour = int(hour) % 12 + (12 if meridiem.lower() == "pm" else 0)
    reset = now.replace(
        hour=hour, minute=int(minute or 0), second=0, microsecond=0
    )
    if reset < now - timedelta(minutes=1):
        reset += timedelta(days=1)
    return reset.timestamp()


def _usage_limit_wait_seconds(text: str) -> float | None:
    """Seconds until the reset time named in a usage-limit message, if any."""
    match = re.search(
        r"usage limit reached\|(\d{10,13})", text, flags=re.IGNORECASE
    )
    if match:
        raw = int(match.group(1))
        reset_epoch = raw / 1000.0 if raw > 10_000_000_000 else float(raw)
    else:
        reset_epoch = _clock_reset_epoch(text)
        if reset_epoch is None:
            return None
        # A margin past the reset, whose clock time has minute resolution.
        reset_epoch += 60
    return max(0.0, min(reset_epoch - time.time(), MAX_USAGE_LIMIT_WAIT_S))


def _is_retryable(error_text: str) -> bool:
    low = (error_text or "").lower()
    return any(marker in low for marker in RETRYABLE_MARKERS)


def invoke_once(
    prompt: str,
    model: str,
    system_prompt: str,
    timeout: int = REQUEST_TIMEOUT_S,
    effort: str = "",
    max_output_tokens: int = 0,
) -> tuple[str, dict, str | None]:
    """One `claude -p` call. Returns (text, payload, error); error is None on success.

    The CLI reports errors as JSON on stdout while exiting non-zero, so stdout
    is parsed regardless of the return code.
    """
    cmd = [
        "claude",
        "-p",
        "--model",
        model,
        "--output-format",
        "json",
        "--tools",
        "",
        "--no-session-persistence",
        "--setting-sources",
        "",
        "--strict-mcp-config",
        "--system-prompt",
        system_prompt,
    ]
    if effort and effort != "default":
        cmd += ["--effort", effort]
    # The CLI's default reply limit is replaced when the agent sets one.
    env = (
        {**os.environ, "CLAUDE_CODE_MAX_OUTPUT_TOKENS": str(max_output_tokens)}
        if max_output_tokens
        else None
    )
    CLI_WORKDIR.mkdir(parents=True, exist_ok=True)
    try:
        proc = subprocess.run(
            cmd,
            input=prompt,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=CLI_WORKDIR,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return "", {}, f"claude CLI timed out after {timeout}s"
    except FileNotFoundError:
        return "", {}, "__fatal__ `claude` CLI not found on PATH"

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
            **options,
        )
        self.effort = effort
        self.timeout = timeout
        self.max_output_tokens = max_output_tokens

    def complete_once(self, prompt: str, system_prompt: str) -> Completion:
        text, payload, error = invoke_once(
            prompt,
            self.model,
            system_prompt,
            self.timeout,
            self.effort,
            self.max_output_tokens,
        )
        usage = {
            model_id: {
                "inputTokens": int(u.get("inputTokens") or 0),
                "outputTokens": int(u.get("outputTokens") or 0),
                "costUSD": float(u.get("costUSD") or 0.0),
            }
            for model_id, u in (payload.get("modelUsage") or {}).items()
        }
        if error is None:
            return Completion(text=text, usage=usage)
        refused = REFUSAL_MARKER in error
        retryable = (
            not refused
            and not error.startswith("__fatal__")
            and _is_retryable(error)
        )
        return Completion(
            usage=usage,
            error=error,
            retryable=retryable,
            retry_after_s=_usage_limit_wait_seconds(error),
            refused=refused,
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

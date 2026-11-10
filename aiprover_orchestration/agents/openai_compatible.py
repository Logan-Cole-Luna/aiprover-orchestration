"""Models behind an OpenAI-compatible chat completions endpoint.

With `tools`, a call runs in a scratch Lake project with the tools of
agents/session_tools.py, as a Codex call does, and answers each tool call of the
model until it replies. Any such endpoint can therefore serve a role with Lean
tools, whether the model is self-hosted or a provider's.
"""

import json
import math
import os
import shutil
import time
import urllib.error
import urllib.request
from functools import partial
from pathlib import Path

from ..utils.processes import run_group
from .base import (
    RETRYABLE_STATUS,
    STOPPING,
    TOKENS_PER_USD,
    TURNS_PER_USD,
    Agent,
    Completion,
    billed_tokens,
    raise_if_stopping,
    retry_after_seconds,
)
from .common import (
    LEAN_PACKAGES_DIR,
    LEAN_TOOLS_BUDGET_USD,
    REFUSAL_TEXT,
    STOP_GRACE_SECONDS,
    tool_project,
)
from .session_tools import TURN_LIMIT, Session


def token_usage(reported: dict) -> dict:
    """A chat completion's usage in the naming of `Completion.usage`. Its
    prompt tokens include the cached ones."""
    details = reported.get("prompt_tokens_details") or {}
    cached = int(details.get("cached_tokens") or reported.get("cached_tokens") or 0)
    return {
        "inputTokens": int(reported.get("prompt_tokens") or 0) - cached,
        "outputTokens": int(reported.get("completion_tokens") or 0),
        "cacheReadInputTokens": cached,
        "costUSD": 0.0,
    }


# Share of the context above which earlier tool results are dropped, and how
# many of the latest results stay.
COMPACT_SHARE = 0.6
KEPT_TOOL_REPLIES = 4
ELIDED_REPLY = "(The result of this earlier call was removed to save context.)"


class OpenAICompatibleAgent(Agent):
    """Model behind an OpenAI-compatible `/chat/completions` endpoint.

    Options: `base_url`, `api_key_file` (the file of the key, which keeps it
    out of the trace; omitted for a server without one), `temperature`,
    `top_p`, `max_tokens`, `max_output_tokens` (replaces `max_tokens`),
    `timeout` (of a whole call), `extra_body` (merged into the request, for
    server-specific parameters), `hosted` (billed by a provider), `tools`,
    `source_dir`, `max_turns`, and `max_budget_usd`, which a hosted model's
    tokens are held to at `TOKENS_PER_USD`. With `max_context`, the context
    length of the model, the results of earlier tool calls are replaced by a
    note once a prompt exceeds `COMPACT_SHARE` of it.
    """

    backend = "openai_compatible"

    def __init__(
        self,
        model: str,
        base_url: str = "",
        api_key_file: str = "",
        temperature: float = 0.6,
        top_p: float = 0.95,
        max_tokens: int = 4096,
        timeout: int = 900,
        extra_body: dict | None = None,
        hosted: bool = False,
        tools: bool = False,
        source_dir: str = "",
        max_output_tokens: int = 0,
        max_budget_usd: float = LEAN_TOOLS_BUDGET_USD,
        max_turns: int = 0,
        max_context: int = 0,
        **options,
    ):
        max_tokens = max_output_tokens or max_tokens
        super().__init__(
            model,
            base_url=base_url,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            timeout=timeout,
            extra_body=extra_body or {},
            hosted=hosted,
            tools=tools,
            **options,
        )
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = Path(api_key_file).read_text() if api_key_file else ""
        self.request_defaults = {
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            **(extra_body or {}),
        }
        self.timeout = timeout
        self.hosted = hosted
        self.tools = tools
        self.source_dir = source_dir
        self.max_context = max_context
        self.max_turns = max_turns or round(max_budget_usd * TURNS_PER_USD)
        self.max_call_tokens = (
            max_budget_usd * TOKENS_PER_USD if hosted else math.inf
        )

    def _post(self, messages: list, tools: list, deadline: float) -> dict | Completion:
        """The endpoint's reply, or the completion of a failed request."""
        body = {"model": self.model, "messages": messages, **self.request_defaults}
        if tools:
            body["tools"] = tools
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )
        seconds = deadline - time.monotonic()
        if seconds <= 0:
            return Completion(
                error=f"connection error: timed out after {self.timeout}s",
                retryable=True,
            )
        try:
            with urllib.request.urlopen(request, timeout=seconds) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            refused = bool(REFUSAL_TEXT.search(detail))
            return Completion(
                error=f"HTTP {e.code}: {detail}",
                retryable=e.code in RETRYABLE_STATUS and not refused,
                retry_after_s=retry_after_seconds(e.headers.get("retry-after")),
                refused=refused,
            )
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            return Completion(error=f"connection error: {e}", retryable=True)

    def _converse(self, messages: list, session: Session | None) -> Completion:
        """Request replies, answering tool calls, until the model replies or
        reaches a limit."""
        tools = session and [
            {
                "type": "function",
                "function": {
                    "name": tool["name"],
                    "description": tool["description"],
                    "parameters": tool["inputSchema"],
                },
            }
            for tool in session.tools()
        ]
        usage: dict[str, dict] = {}
        deadline = time.monotonic() + self.timeout
        while True:
            raise_if_stopping()
            payload = self._post(messages, tools, deadline)
            if isinstance(payload, Completion):
                payload.usage = usage
                return payload
            served = usage.setdefault(payload.get("model") or self.model, {})
            for key, count in token_usage(payload.get("usage") or {}).items():
                served[key] = served.get(key, 0) + count
            choices = payload.get("choices") or []
            message = (choices[0].get("message") or {}) if choices else {}
            reason = choices[0].get("finish_reason") if choices else "no choices"
            calls = message.get("tool_calls") or []
            if not calls:
                return self._reply(message, reason, usage)
            if sum(map(billed_tokens, usage.values())) > self.max_call_tokens:
                return Completion(
                    usage=usage,
                    error=f"token limit of {self.max_call_tokens:.0f} reached",
                    limited=True,
                )
            # The whole message stays in the history: some providers require
            # its reasoning back in the next request.
            messages.append({k: v for k, v in message.items() if v is not None})
            prompt_tokens = int((payload.get("usage") or {}).get("prompt_tokens") or 0)
            if self.max_context and prompt_tokens > self.max_context * COMPACT_SHARE:
                self._elide(messages)
            for call in calls:
                text = self._tool_reply(session, call)
                if text == TURN_LIMIT:
                    return Completion(
                        usage=usage,
                        error=f"turn limit of {self.max_turns} reached",
                        limited=True,
                    )
                messages.append(
                    {"role": "tool", "tool_call_id": call["id"], "content": text}
                )

    @staticmethod
    def _elide(messages: list) -> None:
        """Replace all but the latest tool results by a note."""
        replies = [m for m in messages if m["role"] == "tool"]
        for reply in replies[:-KEPT_TOOL_REPLIES]:
            reply["content"] = ELIDED_REPLY

    @staticmethod
    def _tool_reply(session: Session, call: dict) -> str:
        function = call["function"]
        try:
            arguments = json.loads(function.get("arguments") or "{}")
        except json.JSONDecodeError as error:
            return f"The arguments are not JSON: {error}"
        reply = session.answer(
            "tools/call", {"name": function["name"], "arguments": arguments}
        )
        return reply["content"][0]["text"]

    @staticmethod
    def _reply(message: dict, reason: str, usage: dict) -> Completion:
        text = (message.get("content") or "").strip()
        refusal = message.get("refusal") or (
            reason if reason == "content_filter" else ""
        )
        if refusal:
            return Completion(
                usage=usage,
                error=f"request declined (refusal): {refusal}"[:500],
                refused=True,
            )
        if not text:
            return Completion(
                usage=usage,
                error=f"empty reply ({reason})",
                limited=reason == "length",
            )
        return Completion(text=text, usage=usage)

    def complete_once(self, prompt: str, system_prompt: str) -> Completion:
        if not self.tools:
            return self._converse(
                [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt},
                ],
                None,
            )
        workdir, note, search_path, commands = tool_project(self.source_dir)
        try:
            session = Session(
                str(workdir),
                [str(LEAN_PACKAGES_DIR)],
                list(commands),
                self.max_turns,
                partial(
                    run_group,
                    stop=STOPPING,
                    env={**os.environ, "PATH": search_path},
                    grace=STOP_GRACE_SECONDS,
                ),
            )
            return self._converse(
                [
                    {"role": "system", "content": system_prompt + note},
                    {"role": "user", "content": prompt},
                ],
                session,
            )
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

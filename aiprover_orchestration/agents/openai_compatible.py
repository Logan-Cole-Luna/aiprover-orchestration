"""Models behind an OpenAI-compatible chat completions endpoint."""

import json
import urllib.error
import urllib.request
from pathlib import Path

from .base import (
    RETRYABLE_STATUS,
    Agent,
    Completion,
    retry_after_seconds,
)

class OpenAICompatibleAgent(Agent):
    """Model behind an OpenAI-compatible `/chat/completions` endpoint.

    Options: `base_url`, `api_key_file` (the file of the key, which keeps it
    out of the trace; omitted for a server without one), `temperature`,
    `top_p`, `max_tokens`, `timeout`, and `extra_body` (merged into the
    request, for server-specific parameters).
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
        **options,
    ):
        super().__init__(
            model,
            base_url=base_url,
            temperature=temperature,
            top_p=top_p,
            max_tokens=max_tokens,
            timeout=timeout,
            extra_body=extra_body or {},
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

    def complete_once(self, prompt: str, system_prompt: str) -> Completion:
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": prompt},
            ],
            **self.request_defaults,
        }
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        request = urllib.request.Request(
            self.url,
            data=json.dumps(body).encode(),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(
                request, timeout=self.timeout
            ) as response:
                payload = json.loads(response.read())
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:500]
            return Completion(
                error=f"HTTP {e.code}: {detail}",
                retryable=e.code in RETRYABLE_STATUS,
                retry_after_s=retry_after_seconds(
                    e.headers.get("retry-after")
                ),
            )
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            return Completion(error=f"connection error: {e}", retryable=True)

        choices = payload.get("choices") or []
        message = (choices[0].get("message") or {}) if choices else {}
        usage = payload.get("usage") or {}
        served_model = payload.get("model") or self.model
        completion_usage = {
            served_model: {
                "inputTokens": int(usage.get("prompt_tokens") or 0),
                "outputTokens": int(usage.get("completion_tokens") or 0),
                "costUSD": 0.0,
            }
        }
        text = (message.get("content") or "").strip()
        reason = choices[0].get("finish_reason") if choices else "no choices"
        refusal = message.get("refusal") or (
            reason if reason == "content_filter" else ""
        )
        if refusal:
            return Completion(
                usage=completion_usage,
                error=f"request declined (refusal): {refusal}"[:500],
                refused=True,
            )
        if not text:
            return Completion(
                usage=completion_usage, error=f"empty reply ({reason})"
            )
        return Completion(text=text, usage=completion_usage)

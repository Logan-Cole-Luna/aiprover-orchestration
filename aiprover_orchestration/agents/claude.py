"""Claude models through the Anthropic API.

The key is read from the environment variable named by `api_key_env`
(default `ANTHROPIC_API_KEY`). Each call streams one message with adaptive
thinking, so that long replies do not hit HTTP timeouts; retries are left to
`AgentPool`, which records every attempt.
"""

import os

import anthropic

from .base import Agent, Completion

# USD per million input / output tokens.
PRICES = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
    "claude-haiku-4-5-20251001": (1.0, 5.0),
}
# Models that accept the server-side refusal fallback (`fallbacks: "default"`):
# a request their safety classifiers decline is re-run on a fallback model.
FALLBACK_MODELS = {
    "claude-fable-5-1",
    "claude-opus-5-5",
    "claude-opus-5",
    "claude-sonnet-5-5",
}
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that predate adaptive thinking.
NO_ADAPTIVE_THINKING = ("claude-haiku-4-5",)


def cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    input_price, output_price = PRICES.get(model, (0.0, 0.0))
    return (input_tokens * input_price + output_tokens * output_price) / 1e6


class ClaudeAgent(Agent):
    """Claude model through the Anthropic Messages API.

    Options: `effort` (low | medium | high | xhigh | max; empty = the model's
    default), `max_output_tokens` (0 = 64000), `timeout`, and `api_key_env`.
    """

    backend = "claude"
    hosted = True

    def __init__(
        self,
        model: str,
        effort: str = "",
        timeout: int = 900,
        max_output_tokens: int = 0,
        api_key_env: str = "ANTHROPIC_API_KEY",
        **options,
    ):
        super().__init__(
            model,
            effort=effort,
            timeout=timeout,
            max_output_tokens=max_output_tokens,
            api_key_env=api_key_env,
            **options,
        )
        self.effort = "" if effort == "default" else effort
        self.max_output_tokens = max_output_tokens or 64000
        api_key = os.environ.get(api_key_env)
        if not api_key:
            raise ValueError(f"{api_key_env} is not set")
        self.client = anthropic.Anthropic(
            api_key=api_key, timeout=float(timeout), max_retries=0
        )

    def _request(self, prompt: str, system_prompt: str) -> dict:
        request = {
            "model": self.model,
            "max_tokens": self.max_output_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": prompt}],
        }
        if not self.model.startswith(NO_ADAPTIVE_THINKING):
            request["thinking"] = {"type": "adaptive"}
        if self.effort:
            request["output_config"] = {"effort": self.effort}
        return request

    def complete_once(self, prompt: str, system_prompt: str) -> Completion:
        request = self._request(prompt, system_prompt)
        try:
            if self.model in FALLBACK_MODELS:
                with self.client.beta.messages.stream(
                    **request, betas=[FALLBACK_BETA], fallbacks="default"
                ) as stream:
                    message = stream.get_final_message()
            else:
                with self.client.messages.stream(**request) as stream:
                    message = stream.get_final_message()
        except anthropic.RateLimitError as e:
            retry_after = e.response.headers.get("retry-after")
            return Completion(
                error=f"rate limited: {e.message}"[:500],
                retryable=True,
                retry_after_s=float(retry_after) if retry_after else None,
            )
        except anthropic.APIStatusError as e:
            return Completion(
                error=f"HTTP {e.status_code}: {e.message}"[:500],
                retryable=e.status_code in (408, 409) or e.status_code >= 500,
            )
        except anthropic.APIConnectionError as e:
            return Completion(error=f"connection error: {e}", retryable=True)

        input_tokens = message.usage.input_tokens
        output_tokens = message.usage.output_tokens
        usage = {
            message.model: {
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "costUSD": cost_usd(message.model, input_tokens, output_tokens),
            }
        }
        if message.stop_reason == "refusal":
            return Completion(usage=usage, error="request declined (refusal)")
        text = "".join(
            block.text for block in message.content if block.type == "text"
        ).strip()
        if not text:
            return Completion(
                usage=usage, error=f"empty reply ({message.stop_reason})"
            )
        return Completion(text=text, usage=usage)

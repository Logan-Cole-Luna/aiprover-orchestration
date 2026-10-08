"""Models behind an OpenAI-compatible chat completions endpoint."""

import json
import os
import urllib.error
import urllib.request

from .base import Agent, Completion

# Hosted providers: base URL and the environment variable holding the key.
PROVIDERS = {
    "openai": ("https://api.openai.com/v1", "OPENAI_API_KEY"),
    "huggingface": ("https://router.huggingface.co/v1", "HF_TOKEN"),
    "openrouter": ("https://openrouter.ai/api/v1", "OPENROUTER_API_KEY"),
}
# Sampling defaults for a local server; hosted providers keep their own,
# since some of their models reject explicit sampling parameters.
LOCAL_SAMPLING = {"temperature": 0.6, "top_p": 0.95}


class OpenAICompatibleAgent(Agent):
    """Model behind an OpenAI-compatible `/chat/completions` endpoint.

    Options: `provider` (a key of `PROVIDERS`, which sets `base_url` and
    `api_key_env`), `base_url`, `api_key_env` (name of the environment
    variable that holds the key; omitted for a local server), `temperature`,
    `top_p`, `max_tokens`, `timeout`, and `extra_body` (merged into the
    request, for server-specific parameters).
    """

    backend = "openai_compatible"
    RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}

    def __init__(
        self,
        model: str,
        base_url: str = "",
        api_key_env: str = "",
        provider: str = "",
        temperature: float | None = None,
        top_p: float | None = None,
        max_tokens: int = 4096,
        timeout: int = 900,
        extra_body: dict | None = None,
        **options,
    ):
        if provider:
            default_url, default_key_env = PROVIDERS[provider]
            base_url = base_url or default_url
            api_key_env = api_key_env or default_key_env
        sampling = {"temperature": temperature, "top_p": top_p}
        if not provider:
            sampling = {
                key: LOCAL_SAMPLING[key] if value is None else value
                for key, value in sampling.items()
            }
        super().__init__(
            model,
            base_url=base_url,
            api_key_env=api_key_env,
            provider=provider,
            **sampling,
            max_tokens=max_tokens,
            timeout=timeout,
            extra_body=extra_body or {},
            **options,
        )
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.api_key = os.environ.get(api_key_env, "") if api_key_env else ""
        # OpenAI's reasoning models accept only `max_completion_tokens`.
        token_limit = (
            "max_completion_tokens" if provider == "openai" else "max_tokens"
        )
        self.request_defaults = {
            **{k: v for k, v in sampling.items() if v is not None},
            token_limit: max_tokens,
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
                retryable=e.code in self.RETRYABLE_STATUS,
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
        if not text:
            reason = (
                choices[0].get("finish_reason") if choices else "no choices"
            )
            return Completion(
                usage=completion_usage, error=f"empty reply ({reason})"
            )
        return Completion(text=text, usage=completion_usage)

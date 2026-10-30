"""Interface shared by all model backends."""

import threading
import time
from dataclasses import dataclass, field
from email.utils import parsedate_to_datetime

# HTTP statuses of transient failures, retried by every backend.
RETRYABLE_STATUS = frozenset({408, 409, 429, 500, 502, 503, 504, 529})
# Cache fields of a call's usage and the totals they add to.
CACHE_USAGE_KEYS = {
    "cacheReadInputTokens": "cache_read_tokens",
    "cacheCreationInputTokens": "cache_creation_tokens",
}


def retry_after_seconds(value: str | None) -> float | None:
    """The wait a `Retry-After` header asks for: seconds or an HTTP date."""
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def add_usage(totals: dict[str, dict], model_usage: dict[str, dict]) -> None:
    """Add a call's `Completion.usage` to per-model totals of input and
    output tokens, cache tokens (where reported) and cost."""
    for model_id, usage in model_usage.items():
        total = totals.setdefault(
            model_id,
            {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
        )
        total["input_tokens"] += int(usage.get("inputTokens") or 0)
        total["output_tokens"] += int(usage.get("outputTokens") or 0)
        for key, total_key in CACHE_USAGE_KEYS.items():
            if usage.get(key):
                total[total_key] = total.get(total_key, 0) + int(usage[key])
        total["cost_usd"] += float(usage.get("costUSD") or 0.0)


@dataclass
class Completion:
    """Result of one model call.

    `usage` maps each model id that served the call to a dict with the keys
    `inputTokens`, `outputTokens` and `costUSD` (one naming for all
    backends, so that traces are uniform). Backends with prompt caching add
    `cacheReadInputTokens` and `cacheCreationInputTokens`.
    """

    text: str = ""
    usage: dict[str, dict] = field(default_factory=dict)
    error: str | None = None
    retryable: bool = False
    retry_after_s: float | None = None
    # Declined by the provider's safety classifier. A refused call is billed
    # and the same prompt is refused again, so it is never retried.
    refused: bool = False
    # Stopped at the call's spend, turn or reply-length limit. It is billed
    # and the same prompt reaches the limit again, so it is never retried.
    limited: bool = False


class AgentCallError(RuntimeError):
    """A model call that failed after all retries (an infrastructure failure)."""


class AgentRefusal(AgentCallError):
    """A model call declined by the provider's safety classifier."""


class AgentLimitReached(AgentCallError):
    """A model call stopped at its spend, turn or reply-length limit."""


class BudgetExhausted(RuntimeError):
    """The run's budget of billed hosted-model calls is spent."""


class Terminated(BaseException):
    """The run was terminated (`stop_run`) while work was pending."""


# Set when the run is terminated: model calls, Lean checks and AIProver jobs
# in flight end or are cancelled, and none starts. Worker threads would
# otherwise finish their work and hold the process open.
STOPPING = threading.Event()


def stop_run() -> None:
    STOPPING.set()


def raise_if_stopping() -> None:
    if STOPPING.is_set():
        raise Terminated


class Agent:
    """A model endpoint that answers one prompt under one system prompt.

    Subclasses implement `complete_once`; retrying and recording are handled
    by `AgentPool`. Backend-specific options arrive as keyword arguments from
    the role's specification.
    """

    backend = "abstract"
    # A model served by its provider (Claude, OpenAI, ...), not by us. Such
    # models receive no other model's reasoning: their safeguards refuse
    # prompts that carry it, and refused calls are billed.
    hosted = False
    # Lean tools, with which a call also elaborates Lean on this machine.
    tools = False
    # Prometheus metrics of the model server, where the backend has one.
    metrics_url: str | None = None

    def __init__(self, model: str, max_retries: int = 5, **options):
        self.model = model
        self.max_retries = max_retries
        self.options = options
        # Variant agents by phase-label prefix (the spec's `phases`).
        self.phase_agents: dict[str, "Agent"] = {}

    def complete_once(self, prompt: str, system_prompt: str) -> Completion:
        raise NotImplementedError

    def for_phase(self, phase: str) -> "Agent":
        """The variant whose prefix is the longest match of `phase`, or this
        agent."""
        matches = [
            prefix for prefix in self.phase_agents if phase.startswith(prefix)
        ]
        return self.phase_agents[max(matches, key=len)] if matches else self

    def describe(self) -> dict:
        description = {
            "backend": self.backend,
            "model": self.model,
            **self.options,
        }
        if self.phase_agents:
            description["phases"] = {
                prefix: agent.describe()
                for prefix, agent in self.phase_agents.items()
            }
        return description

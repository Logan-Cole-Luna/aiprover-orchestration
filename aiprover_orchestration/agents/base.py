"""Interface shared by all model backends."""

from dataclasses import dataclass, field


@dataclass
class Completion:
    """Result of one model call.

    `usage` maps each model id that served the call to a dict with the keys
    `inputTokens`, `outputTokens` and `costUSD` (one naming for all
    backends, so that traces are uniform).
    """

    text: str = ""
    usage: dict[str, dict] = field(default_factory=dict)
    error: str | None = None
    retryable: bool = False
    retry_after_s: float | None = None


class AgentCallError(RuntimeError):
    """A model call that failed after all retries (an infrastructure failure)."""


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
    # Prometheus metrics of the model server, where the backend has one.
    metrics_url: str | None = None

    def __init__(self, model: str, max_retries: int = 5, **options):
        self.model = model
        self.max_retries = max_retries
        self.options = options

    def complete_once(self, prompt: str, system_prompt: str) -> Completion:
        raise NotImplementedError

    def describe(self) -> dict:
        return {"backend": self.backend, "model": self.model, **self.options}

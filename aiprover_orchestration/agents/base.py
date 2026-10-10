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
    # Declined by the provider's safety classifier. A refused call is billed
    # and the same prompt is refused again, so it is never retried.
    refused: bool = False


class AgentCallError(RuntimeError):
    """A model call that failed after all retries (an infrastructure failure)."""


class AgentRefusal(AgentCallError):
    """A model call declined by the provider's safety classifier."""


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

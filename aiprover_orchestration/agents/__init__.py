"""Model backends and the pool that routes role calls to them."""

from .base import (
    STOPPING,
    Agent,
    AgentCallError,
    AgentLimitReached,
    AgentRefusal,
    BudgetExhausted,
    Completion,
    Terminated,
    raise_if_stopping,
    stop_run,
)
from .pool import AgentPool, build_agent, role_spec

__all__ = [
    "STOPPING",
    "Agent",
    "AgentCallError",
    "AgentPool",
    "AgentLimitReached",
    "AgentRefusal",
    "BudgetExhausted",
    "Completion",
    "Terminated",
    "build_agent",
    "raise_if_stopping",
    "role_spec",
    "stop_run",
]

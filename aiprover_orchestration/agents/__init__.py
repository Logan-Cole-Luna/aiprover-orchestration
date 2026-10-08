"""Model backends and the pool that routes role calls to them."""

from .base import Agent, AgentCallError, Completion
from .pool import AgentPool, build_agent

__all__ = ["Agent", "AgentCallError", "AgentPool", "Completion", "build_agent"]

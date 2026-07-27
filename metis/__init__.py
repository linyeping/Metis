"""Public Python SDK for the Metis agent runtime."""

from backend.version import __version__

from .agent import Agent, AgentResult, AgentRunError
from .events import AgentEvent, AgentEventKind

__all__ = [
    "Agent",
    "AgentEvent",
    "AgentEventKind",
    "AgentResult",
    "AgentRunError",
    "__version__",
]

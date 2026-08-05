"""Public Python SDK for the Metis agent runtime."""

from backend.version import __version__

from .agent import Agent, AgentResult, AgentRunError
from .events import AgentEvent, AgentEventKind

SDK_API_VERSION = "1"

__all__ = [
    "Agent",
    "AgentEvent",
    "AgentEventKind",
    "AgentResult",
    "AgentRunError",
    "SDK_API_VERSION",
    "__version__",
]

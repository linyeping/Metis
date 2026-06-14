"""Tool boundary for future Hermes-style registry migration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, NewType, Optional, Protocol, Sequence


ToolName = NewType("ToolName", str)
ToolApproval = Literal["never", "mode", "always"]


@dataclass(frozen=True)
class ToolCallRequest:
    name: ToolName
    arguments: Mapping[str, Any] = field(default_factory=dict)
    call_id: Optional[str] = None
    requires_approval: bool = False


@dataclass(frozen=True)
class ToolCallResult:
    name: ToolName
    ok: bool
    content: str
    call_id: Optional[str] = None
    error_code: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolProfile:
    name: ToolName
    canonical_name: ToolName
    description: str
    source: str
    toolset: str
    available: bool = True
    approval: ToolApproval = "mode"
    destructive: bool = False


class ToolAvailabilityError(RuntimeError):
    """Raised when a tool is known but unavailable in the current environment."""


class ToolRegistryProtocol(Protocol):
    def list_tools(self) -> Sequence[ToolName]:
        """Return registered tool names."""

    def is_available(self, name: ToolName) -> bool:
        """Return whether a tool can run in the current environment."""

    def run_tool(self, request: ToolCallRequest) -> ToolCallResult:
        """Run a tool request."""

"""Stream event boundary shared by providers, tools, and the desktop UI."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, Literal, Mapping, Optional, Tuple, Union


EVENT_SCHEMA = "metis.agent_event.v1"

EventKind = Literal["text_delta", "content_delta", "tool_call", "tool_result", "error", "done"]
AgentEventKind = Literal[
    "text_delta",
    "content_delta",
    "content",
    "thinking",
    "tool_call",
    "tool_result",
    "permission_request",
    "error",
    "done",
    "compact",
    "runtime_status",
    "todo_update",
    "memory_nudge",
    "subagent_start",
    "subagent_progress",
    "subagent_done",
]

KNOWN_AGENT_EVENT_KINDS: Tuple[str, ...] = (
    "text_delta",
    "content_delta",
    "content",
    "thinking",
    "tool_call",
    "tool_result",
    "permission_request",
    "error",
    "done",
    "compact",
    "runtime_status",
    "todo_update",
    "memory_nudge",
    "subagent_start",
    "subagent_progress",
    "subagent_done",
)

LEGACY_COMPAT_FIELDS: Dict[str, Tuple[str, ...]] = {
    "text_delta": ("text",),
    "content_delta": ("text",),
    "content": ("text",),
    "thinking": ("text",),
    "tool_call": ("tool", "toolName", "args", "arguments", "call_id", "callId"),
    "tool_result": ("tool", "toolName", "result", "call_id", "callId"),
    "permission_request": (
        "tool",
        "toolName",
        "args",
        "arguments",
        "call_id",
        "callId",
        "request_id",
        "requestId",
    ),
    "error": ("code", "title", "message", "hint", "recoverable", "status", "details"),
    "done": ("turns", "tool_calls", "usage"),
    "compact": ("before_count", "after_count", "summary_preview"),
    "runtime_status": (
        "phase",
        "message",
        "turn",
        "tool_calls",
        "tool",
        "toolName",
        "call_id",
        "callId",
        "recoverable",
    ),
    "todo_update": ("todos", "summary", "call_id", "callId"),
    "memory_nudge": ("message", "memory_count", "skill_count", "memory_path", "skill_path"),
    "subagent_start": ("task_id", "taskId", "name", "progress", "status"),
    "subagent_progress": ("task_id", "taskId", "name", "progress", "status"),
    "subagent_done": ("task_id", "taskId", "name", "progress", "status", "result"),
}


def agent_event_contract_payload() -> Dict[str, Any]:
    """Return the desktop-readable stream event contract."""
    return {
        "schema": EVENT_SCHEMA,
        "version": 1,
        "transport": "sse",
        "event_kinds": list(KNOWN_AGENT_EVENT_KINDS),
        "envelope_required": ["schema", "kind", "type", "event_id", "timestamp", "payload"],
        "legacy_compat_fields": {
            kind: list(fields)
            for kind, fields in LEGACY_COMPAT_FIELDS.items()
        },
    }


@dataclass(frozen=True)
class AgentEventEnvelope:
    schema: str
    kind: AgentEventKind
    event_id: str
    timestamp: float
    payload: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TextDeltaEvent:
    kind: Literal["text_delta"]
    text: str
    message_id: Optional[str] = None


@dataclass(frozen=True)
class ContentDeltaEvent:
    kind: Literal["content_delta"]
    text: str
    message_id: Optional[str] = None


@dataclass(frozen=True)
class ToolCallEvent:
    kind: Literal["tool_call"]
    name: str
    arguments: Mapping[str, Any] = field(default_factory=dict)
    call_id: Optional[str] = None


@dataclass(frozen=True)
class ToolResultEvent:
    kind: Literal["tool_result"]
    name: str
    content: str
    ok: bool = True
    call_id: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ErrorEvent:
    kind: Literal["error"]
    code: str
    message: str
    retryable: bool = False
    suggestion: Optional[str] = None


@dataclass(frozen=True)
class DoneEvent:
    kind: Literal["done"]
    message_id: Optional[str] = None
    usage: Mapping[str, int] = field(default_factory=dict)


MetisEvent = Union[
    TextDeltaEvent,
    ContentDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
    ErrorEvent,
    DoneEvent,
]

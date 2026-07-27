from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Iterator, Literal, Mapping, cast

from backend.bridges.event_contract import EVENT_SCHEMA, KNOWN_AGENT_EVENT_KINDS

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


@dataclass(frozen=True)
class AgentEvent(Mapping[str, Any]):
    """Immutable mapping view of one ``metis.agent_event.v1`` event."""

    _data: Mapping[str, Any]

    def __init__(self, data: Mapping[str, Any]) -> None:
        copied = deepcopy(dict(data))
        if copied.get("schema") != EVENT_SCHEMA:
            raise ValueError(f"unsupported agent event schema: {copied.get('schema')!r}")
        kind = str(copied.get("kind") or copied.get("type") or "")
        if not kind:
            raise ValueError("agent event kind is required")
        object.__setattr__(self, "_data", _freeze_mapping(copied))

    @property
    def schema(self) -> str:
        return str(self._data["schema"])

    @property
    def kind(self) -> AgentEventKind:
        return cast(AgentEventKind, str(self._data.get("kind") or self._data.get("type") or ""))

    @property
    def event_id(self) -> str:
        return str(self._data.get("event_id") or "")

    @property
    def timestamp(self) -> float:
        return float(self._data.get("timestamp") or 0.0)

    @property
    def payload(self) -> Mapping[str, Any]:
        value = self._data.get("payload")
        return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else MappingProxyType({})

    @property
    def text(self) -> str:
        return str(self._data.get("text") or self.payload.get("text") or "")

    @property
    def tool(self) -> str:
        return str(self._data.get("tool") or self._data.get("toolName") or self.payload.get("tool") or "")

    @property
    def arguments(self) -> Mapping[str, Any]:
        value = self._data.get("arguments", self._data.get("args", self.payload.get("arguments", self.payload.get("args", {}))))
        return cast(Mapping[str, Any], value) if isinstance(value, Mapping) else MappingProxyType({})

    def as_dict(self) -> dict[str, Any]:
        return cast(dict[str, Any], _thaw_value(self._data))

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def __iter__(self) -> Iterator[str]:
        return iter(self._data)

    def __len__(self) -> int:
        return len(self._data)


def _freeze_mapping(value: Mapping[str, Any]) -> Mapping[str, Any]:
    return MappingProxyType({key: _freeze_value(item) for key, item in value.items()})


def _freeze_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _freeze_mapping(value)
    if isinstance(value, (list, tuple)):
        return tuple(_freeze_value(item) for item in value)
    if isinstance(value, (set, frozenset)):
        return frozenset(_freeze_value(item) for item in value)
    return value


def _thaw_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {key: _thaw_value(item) for key, item in value.items()}
    if isinstance(value, (tuple, frozenset)):
        return [_thaw_value(item) for item in value]
    return deepcopy(value)


KNOWN_EVENT_KINDS: tuple[str, ...] = tuple(KNOWN_AGENT_EVENT_KINDS)

__all__ = ["AgentEvent", "AgentEventKind", "KNOWN_EVENT_KINDS"]

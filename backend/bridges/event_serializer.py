"""Serialize runtime agent events to a stable SSE-compatible payload."""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import asdict, is_dataclass
from typing import Any, Dict, Mapping

from .event_contract import EVENT_SCHEMA


_DETAIL_LIMIT = 1000
_TOOL_RESULT_LIMIT = 2000


def agent_event_payload(event: Any) -> Dict[str, Any]:
    if isinstance(event, Mapping):
        return normalize_agent_event_payload(event)

    kind = str(getattr(event, "type", "") or getattr(event, "kind", "") or "event")
    timestamp = _event_timestamp(event)
    payload = _payload_from_runtime_event(kind, event)
    return _with_legacy_fields(kind, payload, timestamp=timestamp)


def normalize_agent_event_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    raw = dict(payload)
    kind = str(raw.get("kind") or raw.get("type") or "event")
    timestamp = _to_float(raw.get("timestamp"), time.time())
    inner = raw.get("payload")
    if isinstance(inner, Mapping):
        normalized_payload = dict(inner)
    else:
        normalized_payload = _payload_from_mapping(kind, raw)
    return _with_legacy_fields(kind, normalized_payload, timestamp=timestamp, existing=raw)


def sse_data(payload: Any) -> str:
    if payload == "[DONE]":
        return "data: [DONE]\n\n"
    event = agent_event_payload(payload)
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


def _with_legacy_fields(
    kind: str,
    payload: Mapping[str, Any],
    *,
    timestamp: float,
    existing: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    legacy = dict(existing or {})
    legacy.pop("payload", None)
    legacy["schema"] = EVENT_SCHEMA
    legacy["kind"] = kind
    legacy["type"] = kind
    legacy["event_id"] = str(legacy.get("event_id") or _event_id(kind))
    legacy["timestamp"] = timestamp
    legacy["payload"] = dict(payload)

    if kind in {"text_delta", "content_delta", "content", "thinking"}:
        legacy["text"] = str(payload.get("text") or "")
    elif kind == "tool_call":
        legacy["tool"] = str(payload.get("tool") or payload.get("name") or "")
        legacy["toolName"] = legacy["tool"]
        legacy["args"] = payload.get("args", payload.get("arguments", {}))
        legacy["arguments"] = legacy["args"]
        legacy["call_id"] = str(payload.get("call_id") or payload.get("callId") or "")
        legacy["callId"] = legacy["call_id"]
    elif kind == "tool_result":
        legacy["tool"] = str(payload.get("tool") or payload.get("name") or "")
        legacy["toolName"] = legacy["tool"]
        legacy["result"] = _truncate(str(payload.get("result") or payload.get("content") or ""), _TOOL_RESULT_LIMIT)
        legacy["call_id"] = str(payload.get("call_id") or payload.get("callId") or "")
        legacy["callId"] = legacy["call_id"]
        legacy["payload"]["result"] = legacy["result"]
    elif kind == "permission_request":
        legacy["tool"] = str(payload.get("tool") or payload.get("name") or "")
        legacy["toolName"] = legacy["tool"]
        legacy["args"] = payload.get("args", payload.get("arguments", {}))
        legacy["arguments"] = legacy["args"]
        legacy["call_id"] = str(payload.get("call_id") or payload.get("callId") or "")
        legacy["callId"] = legacy["call_id"]
        legacy["request_id"] = str(payload.get("request_id") or payload.get("requestId") or "")
        legacy["requestId"] = legacy["request_id"]
    elif kind == "error":
        legacy["code"] = str(payload.get("code") or "RUNTIME_ERROR")
        legacy["title"] = str(payload.get("title") or "")
        legacy["message"] = str(payload.get("message") or "")
        legacy["hint"] = str(payload.get("hint") or payload.get("suggestion") or "")
        legacy["recoverable"] = bool(payload.get("recoverable", payload.get("retryable", False)))
        legacy["status"] = int(_to_float(payload.get("status"), 0))
        legacy["details"] = _truncate(str(payload.get("details") or ""), _DETAIL_LIMIT)
        legacy["payload"]["details"] = legacy["details"]
        legacy["error_info"] = {
            "code": legacy["code"],
            "title": legacy["title"],
            "description": legacy["message"],
            "action": legacy["hint"],
            "retry": legacy["recoverable"],
        }
        legacy["payload"]["error_info"] = legacy["error_info"]
    elif kind == "done":
        usage = _usage_payload(payload)
        legacy["turns"] = int(_to_float(payload.get("turns", payload.get("total_turns")), 0))
        legacy["tool_calls"] = int(_to_float(payload.get("tool_calls", payload.get("total_tool_calls")), 0))
        legacy["usage"] = usage
        legacy["payload"]["usage"] = usage
    elif kind == "compact":
        legacy["before_count"] = int(_to_float(payload.get("before_count"), 0))
        legacy["after_count"] = int(_to_float(payload.get("after_count"), 0))
        legacy["summary_preview"] = str(payload.get("summary_preview") or "")
    elif kind == "runtime_status":
        legacy["phase"] = str(payload.get("phase") or "")
        legacy["message"] = str(payload.get("message") or "")
        legacy["turn"] = int(_to_float(payload.get("turn"), 0))
        legacy["tool_calls"] = int(_to_float(payload.get("tool_calls"), 0))
        legacy["tool"] = str(payload.get("tool") or payload.get("tool_name") or "")
        legacy["toolName"] = legacy["tool"]
        legacy["call_id"] = str(payload.get("call_id") or payload.get("callId") or "")
        legacy["callId"] = legacy["call_id"]
        legacy["recoverable"] = bool(payload.get("recoverable", True))
    elif kind == "todo_update":
        todos = payload.get("todos") if isinstance(payload.get("todos"), list) else []
        legacy["todos"] = todos
        legacy["summary"] = str(payload.get("summary") or "")
        legacy["call_id"] = str(payload.get("call_id") or payload.get("callId") or "")
        legacy["callId"] = legacy["call_id"]
    elif kind == "memory_nudge":
        for key in ("message", "memory_count", "skill_count", "memory_path", "skill_path"):
            if key in payload:
                legacy[key] = payload[key]
    elif kind.startswith("subagent_"):
        legacy["task_id"] = str(payload.get("task_id") or payload.get("taskId") or payload.get("call_id") or "")
        legacy["taskId"] = legacy["task_id"]
        legacy["name"] = str(payload.get("name") or payload.get("tool") or "subagent")
        legacy["progress"] = int(_to_float(payload.get("progress"), 0))
        legacy["status"] = str(payload.get("status") or ("done" if kind == "subagent_done" else "running"))
        if "result" in payload:
            legacy["result"] = payload["result"]

    return _json_safe(legacy)


def _payload_from_runtime_event(kind: str, event: Any) -> Dict[str, Any]:
    if kind == "tool_call":
        return {
            "tool": getattr(event, "tool_name", ""),
            "args": getattr(event, "arguments", {}) or {},
            "call_id": getattr(event, "call_id", ""),
        }
    if kind == "tool_result":
        return {
            "tool": getattr(event, "tool_name", ""),
            "result": getattr(event, "result", ""),
            "call_id": getattr(event, "call_id", ""),
        }
    if kind == "permission_request":
        return {
            "tool": getattr(event, "tool_name", ""),
            "args": getattr(event, "arguments", {}) or {},
            "call_id": getattr(event, "call_id", ""),
            "request_id": getattr(event, "request_id", ""),
        }
    if kind in {"text_delta", "content_delta", "content", "thinking"}:
        return {"text": getattr(event, "text", "")}
    if kind == "error":
        return {
            "code": getattr(event, "code", "RUNTIME_ERROR"),
            "title": getattr(event, "title", ""),
            "message": getattr(event, "message", ""),
            "hint": getattr(event, "hint", ""),
            "recoverable": getattr(event, "recoverable", False),
            "status": getattr(event, "status", 0),
            "details": getattr(event, "details", ""),
        }
    if kind == "done":
        return {
            "turns": getattr(event, "total_turns", 0),
            "tool_calls": getattr(event, "total_tool_calls", 0),
            "usage": {
                "prompt_tokens": getattr(event, "prompt_tokens", 0),
                "completion_tokens": getattr(event, "completion_tokens", 0),
                "total_tokens": getattr(event, "total_tokens", 0),
                "prompt_cache_hit_tokens": getattr(event, "prompt_cache_hit_tokens", 0),
                "prompt_cache_miss_tokens": getattr(event, "prompt_cache_miss_tokens", 0),
            },
        }
    if kind == "compact":
        return {
            "before_count": getattr(event, "before_count", 0),
            "after_count": getattr(event, "after_count", 0),
            "summary_preview": getattr(event, "summary_preview", ""),
        }
    if kind == "runtime_status":
        return {
            "phase": getattr(event, "phase", ""),
            "message": getattr(event, "message", ""),
            "turn": getattr(event, "turn", 0),
            "tool_calls": getattr(event, "tool_calls", 0),
            "tool": getattr(event, "tool_name", ""),
            "call_id": getattr(event, "call_id", ""),
            "recoverable": getattr(event, "recoverable", True),
        }
    if kind == "todo_update":
        return {
            "todos": getattr(event, "todos", []),
            "summary": getattr(event, "summary", ""),
            "call_id": getattr(event, "call_id", ""),
        }
    if is_dataclass(event):
        return asdict(event)
    return {"data": getattr(event, "data", None)}


def _payload_from_mapping(kind: str, raw: Mapping[str, Any]) -> Dict[str, Any]:
    if kind == "tool_call":
        return {
            "tool": raw.get("tool") or raw.get("toolName") or raw.get("name") or "",
            "args": raw.get("args", raw.get("arguments", {})),
            "call_id": raw.get("call_id") or raw.get("callId") or "",
        }
    if kind == "tool_result":
        return {
            "tool": raw.get("tool") or raw.get("toolName") or raw.get("name") or "",
            "result": raw.get("result", raw.get("content", "")),
            "call_id": raw.get("call_id") or raw.get("callId") or "",
        }
    if kind == "permission_request":
        return {
            "tool": raw.get("tool") or raw.get("toolName") or raw.get("name") or "",
            "args": raw.get("args", raw.get("arguments", {})),
            "call_id": raw.get("call_id") or raw.get("callId") or "",
            "request_id": raw.get("request_id") or raw.get("requestId") or "",
        }
    if kind in {"text_delta", "content_delta", "content", "thinking"}:
        return {"text": raw.get("text", "")}
    return {k: v for k, v in raw.items() if k not in {"schema", "kind", "type", "event_id", "timestamp"}}


def _usage_payload(payload: Mapping[str, Any]) -> Dict[str, int]:
    usage = payload.get("usage")
    if not isinstance(usage, Mapping):
        usage = payload
    return {
        "prompt_tokens": int(_to_float(usage.get("prompt_tokens", usage.get("promptTokens")), 0)),
        "completion_tokens": int(_to_float(usage.get("completion_tokens", usage.get("completionTokens")), 0)),
        "total_tokens": int(_to_float(usage.get("total_tokens", usage.get("totalTokens")), 0)),
        "prompt_cache_hit_tokens": int(_to_float(usage.get("prompt_cache_hit_tokens", usage.get("promptCacheHitTokens")), 0)),
        "prompt_cache_miss_tokens": int(_to_float(usage.get("prompt_cache_miss_tokens", usage.get("promptCacheMissTokens")), 0)),
    }


def _event_timestamp(event: Any) -> float:
    return _to_float(getattr(event, "timestamp", None), time.time())


def _event_id(kind: str) -> str:
    return f"evt_{kind}_{uuid.uuid4().hex[:12]}"


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def _to_float(value: Any, fallback: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _json_safe(value: Any) -> Any:
    try:
        json.dumps(value, ensure_ascii=False)
        return value
    except TypeError:
        if isinstance(value, dict):
            return {str(k): _json_safe(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [_json_safe(item) for item in value]
        return str(value)

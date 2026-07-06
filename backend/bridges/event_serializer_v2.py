"""Serialize run-scoped events to metis.agent_event.v2 envelopes."""

from __future__ import annotations

import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Dict, Mapping

from .event_contract_v2 import EVENT_SCHEMA_V2, EVENT_VERSION_V2


_ENVELOPE_KEYS = {
    "kind",
    "type",
    "event_id",
    "eventId",
    "timestamp",
    "run_id",
    "runId",
    "session_id",
    "sessionId",
    "turn_id",
    "turnId",
    "assistant_id",
    "assistantId",
    "message_id",
    "messageId",
    "surface_mode",
    "surfaceMode",
    "seq",
}


def agent_event_v2_payload(
    *,
    run_id: str,
    session_id: str,
    turn_id: str,
    message_id: str,
    seq: int,
    kind: str,
    payload: Mapping[str, Any] | None = None,
    timestamp: Any = None,
    source_event_id: str = "",
) -> Dict[str, Any]:
    """Build a v2 envelope with backend-owned identity fields."""
    clean_payload = _business_payload(payload or {})
    if source_event_id:
        clean_payload.setdefault("source_event_id", source_event_id)
    return _json_safe(
        {
            "schema": EVENT_SCHEMA_V2,
            "version": EVENT_VERSION_V2,
            "run_id": str(run_id or ""),
            "session_id": str(session_id or ""),
            "turn_id": str(turn_id or ""),
            "message_id": str(message_id or ""),
            "seq": int(seq),
            "event_id": _event_id(str(run_id or ""), int(seq)),
            "timestamp": _iso_timestamp(timestamp),
            "kind": str(kind or "runtime_status"),
            "payload": clean_payload,
        }
    )


def legacy_business_payload(event: Mapping[str, Any]) -> Dict[str, Any]:
    """Return a payload-only view of a legacy/v1 event."""
    inner = event.get("payload")
    if isinstance(inner, Mapping):
        return _business_payload(inner)
    return _business_payload(event)


def _business_payload(payload: Mapping[str, Any]) -> Dict[str, Any]:
    return {str(key): _json_safe(value) for key, value in payload.items() if str(key) not in _ENVELOPE_KEYS}


def _event_id(run_id: str, seq: int) -> str:
    safe_run_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", run_id or "run")
    return f"evt_{safe_run_id}_{seq:06d}"


def _iso_timestamp(value: Any) -> str:
    if isinstance(value, str) and value:
        parsed = _parse_timestamp(value)
        if parsed is not None:
            return parsed
    seconds = _to_float(value, time.time())
    # Existing v1 streams use seconds. Treat very large values as milliseconds.
    if seconds > 10_000_000_000:
        seconds = seconds / 1000
    return datetime.fromtimestamp(seconds, tz=timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_timestamp(value: str) -> str | None:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


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

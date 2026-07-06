"""Agent event v2 contract for replayable run streams."""

from __future__ import annotations

from typing import Any, Dict, Tuple


EVENT_SCHEMA_V2 = "metis.agent_event.v2"
EVENT_VERSION_V2 = 2

KNOWN_AGENT_EVENT_KINDS_V2: Tuple[str, ...] = (
    "message_delta",
    "message_completed",
    "thinking_delta",
    "tool_requested",
    "permission_required",
    "permission_answered",
    "permission_applied",
    "permission_rejected",
    "permission_expired",
    "permission_audited",
    "tool_running",
    "tool_succeeded",
    "tool_failed",
    "tool_canceled",
    "tool_timed_out",
    "artifact_created",
    "runtime_status",
    "subrun_planned",
    "subrun_running",
    "subrun_waiting_permission",
    "subrun_succeeded",
    "subrun_failed",
    "subrun_canceled",
    "subrun_promoted",
    "run_completed",
    "run_failed",
    "run_canceled",
)

PASSTHROUGH_AGENT_EVENT_KINDS_V2: Tuple[str, ...] = (
    "compact",
    "todo_update",
    "memory_nudge",
)

ENVELOPE_REQUIRED_V2: Tuple[str, ...] = (
    "schema",
    "version",
    "run_id",
    "session_id",
    "turn_id",
    "message_id",
    "seq",
    "event_id",
    "timestamp",
    "kind",
    "payload",
)


def agent_event_contract_payload_v2() -> Dict[str, Any]:
    """Return the desktop-readable v2 run event contract."""
    return {
        "schema": EVENT_SCHEMA_V2,
        "version": EVENT_VERSION_V2,
        "transport": "sse",
        "event_kinds": list(KNOWN_AGENT_EVENT_KINDS_V2),
        "passthrough_event_kinds": list(PASSTHROUGH_AGENT_EVENT_KINDS_V2),
        "envelope_required": list(ENVELOPE_REQUIRED_V2),
        "identity_authority": "backend",
        "tool_identity_key": "payload.call_id",
        "subrun_identity_key": "payload.subrun_id",
    }

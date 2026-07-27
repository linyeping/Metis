"""In-memory permission request protocol state."""

from __future__ import annotations

import json
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Mapping, Optional

PERMISSION_REQUEST_SCHEMA = "metis.permission_request.v1"
PERMISSION_REQUEST_VERSION = 1
DEFAULT_PERMISSION_TIMEOUT_SECONDS = 300


class PermissionRequestStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._requests: Dict[str, Dict[str, Any]] = {}

    def clear(self) -> None:
        with self._lock:
            self._requests.clear()

    def create(
        self,
        *,
        request_id: str,
        call_id: str,
        run_id: str = "",
        session_id: str = "",
        turn_id: str = "",
        tool_name: str,
        arguments_preview: Any = None,
        decision: Mapping[str, Any] | None = None,
        path_safety: Mapping[str, Any] | None = None,
        choices: List[Mapping[str, Any]] | None = None,
        metadata: Mapping[str, Any] | None = None,
        workspace_root: str = "",
        timeout_seconds: int = DEFAULT_PERMISSION_TIMEOUT_SECONDS,
        created_at: float | None = None,
    ) -> Dict[str, Any]:
        now = float(created_at or time.time())
        expires_at = now + max(1, int(timeout_seconds or DEFAULT_PERMISSION_TIMEOUT_SECONDS))
        with self._lock:
            existing = self._requests.get(request_id)
            if existing:
                return dict(existing)
            payload = {
                "schema": PERMISSION_REQUEST_SCHEMA,
                "version": PERMISSION_REQUEST_VERSION,
                "request_id": str(request_id or ""),
                "call_id": str(call_id or ""),
                "run_id": str(run_id or ""),
                "session_id": str(session_id or ""),
                "turn_id": str(turn_id or ""),
                "tool_name": str(tool_name or ""),
                "status": "requested",
                "created_at": _iso_timestamp(now),
                "expires_at": _iso_timestamp(expires_at),
                "arguments_preview": _json_safe(arguments_preview if arguments_preview is not None else {}),
                "decision": dict(decision or {}),
                "path_safety": dict(path_safety or {}),
                "choices": [dict(choice) for choice in choices or []],
                "workspace_root": str(workspace_root or ""),
                "audit_id": "",
                "history": [{"status": "requested", "at": _iso_timestamp(now)}],
            }
            extra = dict(metadata or {})
            for key in (
                "explainer",
                "permission_explainer",
                "autoguard",
                "tool_contract",
                "suggested_writable_root",
                "suggested_writable_roots",
                "can_grant_writable_root",
                "can_grant_full_access",
                "default_choice",
            ):
                if key in extra:
                    payload[key] = _json_safe(extra[key])
            self._requests[str(request_id)] = payload
            return dict(payload)

    def get(self, request_id: str) -> Optional[Dict[str, Any]]:
        with self._lock:
            request = self._requests.get(str(request_id or ""))
            return dict(request) if request else None

    def list_pending(self) -> List[Dict[str, Any]]:
        with self._lock:
            requests = [
                dict(request)
                for request in self._requests.values()
                if str(request.get("status") or "") in {"requested", "displayed"}
            ]
        requests.sort(key=lambda item: str(item.get("created_at") or ""))
        return requests

    def mark_displayed(self, request_id: str, *, surface: str = "desktop", displayed_at: Any = None) -> Optional[Dict[str, Any]]:
        return self._update(
            request_id,
            "displayed",
            {
                "surface": str(surface or "desktop"),
                "displayed_at": _iso_timestamp(displayed_at),
            },
            allowed_previous={"requested", "displayed"},
        )

    def answer(
        self,
        request_id: str,
        *,
        approved: bool,
        choice: str = "",
        remember: str = "",
        grant: str = "",
        root_path: str = "",
        answered_at: Any = None,
    ) -> Optional[Dict[str, Any]]:
        return self._update(
            request_id,
            "answered",
            {
                "approved": bool(approved),
                "choice": str(choice or ""),
                "remember": str(remember or ""),
                "grant": str(grant or ""),
                "root_path": str(root_path or ""),
                "answered_at": _iso_timestamp(answered_at),
            },
        )

    def mark_applied(self, request_id: str, **updates: Any) -> Optional[Dict[str, Any]]:
        return self._update(request_id, "applied", updates)

    def mark_rejected(self, request_id: str, **updates: Any) -> Optional[Dict[str, Any]]:
        return self._update(request_id, "rejected", updates)

    def mark_expired(self, request_id: str, **updates: Any) -> Optional[Dict[str, Any]]:
        return self._update(request_id, "expired", updates)

    def mark_audited(self, request_id: str, *, audit_id: str = "", **updates: Any) -> Optional[Dict[str, Any]]:
        return self._update(request_id, "audited", {"audit_id": str(audit_id or ""), **updates})

    def mark_tool_resumed(self, request_id: str) -> Optional[Dict[str, Any]]:
        return self._update(request_id, "tool_resumed", {})

    def mark_tool_denied(self, request_id: str) -> Optional[Dict[str, Any]]:
        return self._update(request_id, "tool_denied", {})

    def expire_due(self, now: float | None = None) -> List[Dict[str, Any]]:
        current = float(now or time.time())
        expired: List[Dict[str, Any]] = []
        with self._lock:
            for request in self._requests.values():
                if str(request.get("status") or "") in {"applied", "rejected", "expired", "audited", "tool_resumed", "tool_denied"}:
                    continue
                expires_at = _parse_iso_seconds(str(request.get("expires_at") or ""))
                if expires_at and expires_at <= current:
                    request["status"] = "expired"
                    request["expired_at"] = _iso_timestamp(current)
                    request.setdefault("history", []).append({"status": "expired", "at": request["expired_at"]})
                    expired.append(dict(request))
        return expired

    def _update(
        self,
        request_id: str,
        status: str,
        updates: Mapping[str, Any],
        *,
        allowed_previous: set[str] | None = None,
    ) -> Optional[Dict[str, Any]]:
        with self._lock:
            request = self._requests.get(str(request_id or ""))
            if request is None:
                return None
            previous = str(request.get("status") or "")
            if allowed_previous is not None and previous not in allowed_previous:
                return dict(request)
            request.update(
                {
                    str(key): _iso_timestamp(value) if str(key).endswith("_at") else _json_safe(value)
                    for key, value in updates.items()
                }
            )
            request["status"] = status
            request.setdefault("history", []).append({"status": status, "at": _iso_timestamp(time.time())})
            return dict(request)


def _iso_timestamp(value: Any = None) -> str:
    if isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            parsed = datetime.now(timezone.utc)
    else:
        try:
            seconds = float(value if value is not None else time.time())
        except (TypeError, ValueError):
            seconds = time.time()
        if seconds > 10_000_000_000:
            seconds = seconds / 1000
        parsed = datetime.fromtimestamp(seconds, tz=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _parse_iso_seconds(value: str) -> float:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.timestamp()


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

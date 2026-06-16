"""Local bridge between backend tools and the Electron Preview WebContentsView.

The Python agent loop cannot directly touch Electron's in-process
``WebContentsView``.  Instead, backend tools enqueue a command here; the
Electron main process long-polls the queue, executes the command against the
right-rail Preview, and posts the result back.
"""
from __future__ import annotations

import threading
import time
import uuid
from collections import deque
from typing import Any, Deque, Dict

from flask import Blueprint, jsonify, request

from backend.web.helpers import request_client_is_loopback

preview_bridge_bp = Blueprint("preview_bridge", __name__)

_MAX_PENDING = 32
_DEFAULT_TIMEOUT_SECONDS = 12.0
_PENDING: Deque[Dict[str, Any]] = deque()
_WAITING: Dict[str, Dict[str, Any]] = {}
_LOCK = threading.Condition()
_LAST_POLL_AT = 0.0
_LAST_RESULT_AT = 0.0


def _reset_preview_bridge_for_tests() -> None:
    global _LAST_POLL_AT, _LAST_RESULT_AT
    with _LOCK:
        _PENDING.clear()
        _WAITING.clear()
        _LAST_POLL_AT = 0.0
        _LAST_RESULT_AT = 0.0
        _LOCK.notify_all()


def preview_bridge_status() -> Dict[str, Any]:
    with _LOCK:
        return {
            "ok": True,
            "pending": len(_PENDING),
            "waiting": len(_WAITING),
            "connected": time.time() - _LAST_POLL_AT < 45 if _LAST_POLL_AT else False,
            "last_poll_at": _LAST_POLL_AT,
            "last_result_at": _LAST_RESULT_AT,
        }


def request_preview_command(kind: str, payload: Dict[str, Any] | None = None, timeout: float | None = None) -> Dict[str, Any]:
    command_kind = str(kind or "").strip()
    if not command_kind:
        return {"ok": False, "error": "missing preview command kind"}

    request_id = f"preview-{uuid.uuid4().hex}"
    event = threading.Event()
    record = {
        "id": request_id,
        "kind": command_kind,
        "payload": dict(payload or {}),
        "created_at": time.time(),
        "event": event,
        "result": None,
    }
    wait_seconds = max(0.25, min(float(timeout or _DEFAULT_TIMEOUT_SECONDS), 60.0))

    with _LOCK:
        if len(_PENDING) >= _MAX_PENDING:
            return {"ok": False, "error": "preview bridge queue full"}
        _WAITING[request_id] = record
        _PENDING.append({
            "id": request_id,
            "kind": command_kind,
            "payload": record["payload"],
            "created_at": record["created_at"],
        })
        _LOCK.notify_all()

    if not event.wait(wait_seconds):
        with _LOCK:
            _WAITING.pop(request_id, None)
            _remove_pending_locked(request_id)
        return {
            "ok": False,
            "error": "preview bridge timeout",
            "request_id": request_id,
            "connected": preview_bridge_status().get("connected", False),
        }

    result = record.get("result")
    return result if isinstance(result, dict) else {"ok": False, "error": "invalid preview bridge result"}


def _remove_pending_locked(request_id: str) -> None:
    if not _PENDING:
        return
    kept = [item for item in _PENDING if item.get("id") != request_id]
    _PENDING.clear()
    _PENDING.extend(kept)


def _loopback_required():
    if request_client_is_loopback():
        return None
    return jsonify({"ok": False, "error": "loopback only"}), 403


@preview_bridge_bp.route("/api/preview-browser/status", methods=["GET"])
def preview_browser_status_route():
    denied = _loopback_required()
    if denied:
        return denied
    return jsonify(preview_bridge_status())


@preview_bridge_bp.route("/api/preview-browser/next", methods=["GET"])
def preview_browser_next_route():
    denied = _loopback_required()
    if denied:
        return denied

    timeout = max(0.0, min(float(request.args.get("timeout", "25") or 25), 30.0))
    deadline = time.time() + timeout
    global _LAST_POLL_AT

    with _LOCK:
        _LAST_POLL_AT = time.time()
        while not _PENDING:
            remaining = deadline - time.time()
            if remaining <= 0:
                return jsonify({"ok": True, "idle": True})
            _LOCK.wait(timeout=remaining)
            _LAST_POLL_AT = time.time()
        item = _PENDING.popleft()
        return jsonify({"ok": True, "request": item})


@preview_bridge_bp.route("/api/preview-browser/result", methods=["POST"])
def preview_browser_result_route():
    denied = _loopback_required()
    if denied:
        return denied

    data = request.get_json(silent=True) or {}
    request_id = str(data.get("id") or "")
    result = data.get("result")
    if not request_id:
        return jsonify({"ok": False, "error": "missing request id"}), 400
    if not isinstance(result, dict):
        result = {"ok": False, "error": "invalid result payload"}

    global _LAST_RESULT_AT
    with _LOCK:
        record = _WAITING.pop(request_id, None)
        _LAST_RESULT_AT = time.time()
        if not record:
            return jsonify({"ok": False, "error": "unknown or expired request"}), 404
        record["result"] = result
        event = record.get("event")
        if isinstance(event, threading.Event):
            event.set()
        return jsonify({"ok": True})

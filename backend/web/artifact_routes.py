from __future__ import annotations

from typing import Any

from flask import Blueprint, jsonify, request

from backend.runtime.artifact_registry import (
    ArtifactFilters,
    ArtifactRegistryError,
    get_artifact,
    list_artifacts,
    register_artifact,
    reindex_artifacts,
)
from backend.web.helpers import active_workspace_root, request_client_is_loopback

artifact_bp = Blueprint("artifacts", __name__)


@artifact_bp.route("/artifacts", methods=["GET"])
def artifacts_list() -> Any:
    filters = ArtifactFilters(
        session_id=str(request.args.get("session_id") or ""),
        run_id=str(request.args.get("run_id") or ""),
        kind=str(request.args.get("kind") or ""),
        limit=_int_arg("limit", 100),
    )
    return jsonify({"ok": True, "artifacts": list_artifacts(filters)})


@artifact_bp.route("/artifacts/<artifact_id>", methods=["GET"])
def artifacts_get(artifact_id: str) -> Any:
    artifact = get_artifact(artifact_id)
    if not artifact:
        return jsonify({"ok": False, "error": "artifact not found"}), 404
    return jsonify({"ok": True, "artifact": artifact})


@artifact_bp.route("/artifacts", methods=["POST"])
def artifacts_register() -> Any:
    if not request_client_is_loopback():
        return jsonify({"ok": False, "error": "forbidden", "detail": "only loopback"}), 403
    body = request.get_json(force=False, silent=True)
    if not isinstance(body, dict):
        return jsonify({"ok": False, "error": "invalid_json"}), 400
    try:
        artifact = register_artifact(
            kind=str(body.get("kind") or ""),
            title=str(body.get("title") or ""),
            path=str(body.get("path") or ""),
            url=str(body.get("url") or ""),
            mime=str(body.get("mime") or ""),
            run_id=str(body.get("run_id") or body.get("runId") or ""),
            session_id=str(body.get("session_id") or body.get("sessionId") or ""),
            turn_id=str(body.get("turn_id") or body.get("turnId") or ""),
            source_event_id=str(body.get("source_event_id") or body.get("sourceEventId") or ""),
            source_tool_call_id=str(body.get("source_tool_call_id") or body.get("sourceToolCallId") or ""),
            metadata=body.get("metadata") if isinstance(body.get("metadata"), dict) else {},
            workspace_root=active_workspace_root(),
        )
    except ArtifactRegistryError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "artifact": artifact})


@artifact_bp.route("/artifacts/reindex", methods=["POST"])
def artifacts_reindex() -> Any:
    if not request_client_is_loopback():
        return jsonify({"ok": False, "error": "forbidden", "detail": "only loopback"}), 403
    return jsonify(reindex_artifacts(workspace_root=active_workspace_root()))


def _int_arg(name: str, default: int) -> int:
    try:
        return int(request.args.get(name) or default)
    except (TypeError, ValueError):
        return default

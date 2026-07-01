"""Session management Blueprint — list, create, switch, delete, export, search."""
from __future__ import annotations

import json
import os
import subprocess
import time
from typing import Any, Dict, List, Optional

from flask import Blueprint, Response, jsonify, request

from backend.runtime.agent_services import (
    build_agent_runtime_profile,
    build_verification_agent_report,
    generate_away_summary,
    generate_prompt_suggestions,
    generate_session_title,
    should_auto_title,
    verification_command_policy,
)
from backend.web.helpers import get_state
from backend.web.sessions import get_session_manager
from backend.web.workspaces import get_workspace_manager

session_bp = Blueprint("sessions", __name__)


# ---------------------------------------------------------------------------
# Internal helpers (session-specific, not shared with other Blueprints)
# ---------------------------------------------------------------------------

def _export_content(content: Any) -> str:
    """Convert message content to readable Markdown-safe text for export."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(str(block.get("text") or ""))
                elif block.get("type") == "image_url":
                    parts.append("[image]")
        return "\n".join(parts) if parts else ""
    return str(content or "")


def _generate_export(session: Any, fmt: str = "markdown") -> tuple[str, str, str]:
    fmt = (fmt or "markdown").strip().lower()
    safe_title = "".join(
        char if char.isalnum() or char in " _-" else "_"
        for char in (session.title or "chat")
    ).strip()[:40] or "chat"
    created_at = session.created_at or time.time()
    date_str = time.strftime("%Y%m%d", time.localtime(created_at))

    if fmt == "json":
        return (
            json.dumps(
                {
                    "id": session.id,
                    "title": session.title,
                    "created_at": session.created_at,
                    "updated_at": session.updated_at,
                    "mode": session.mode,
                    "workspace_id": session.workspace_id,
                    "compact_state": session.compact_state,
                    "history": session.history,
                },
                ensure_ascii=False,
                indent=2,
            ),
            f"metis-{safe_title}-{date_str}.json",
            "application/json",
        )

    lines = [f"# {session.title or 'Metis Chat'}\n"]
    lines.append(f"*Exported on {time.strftime('%Y-%m-%d %H:%M')}*\n")
    lines.append("---\n")
    for message in session.history:
        role = message.get("role", "unknown")
        content_value = message.get("content", "")
        if role == "system":
            lines.append(f"**System:**\n\n{_export_content(content_value)}\n\n---\n")
        elif role == "user":
            lines.append(f"## You\n\n{_export_content(content_value)}\n")
        elif role == "assistant":
            lines.append(f"## Metis\n\n{_export_content(content_value)}\n")
        elif role == "tool":
            tool_name = message.get("name", "tool")
            result = _export_content(content_value)
            if len(result) > 500:
                result = result[:500] + "..."
            lines.append(f"**Tool Result ({tool_name}):**\n\n```\n{result}\n```\n")
    return "\n".join(lines), f"metis-{safe_title}-{date_str}.md", "text/markdown"


# ---------------------------------------------------------------------------
# Shared session helpers (used by other modules via import)
# ---------------------------------------------------------------------------

def save_active_session() -> None:
    """Persist the current in-memory chat history to the active session."""
    state = get_state()
    if not state.active_session_id:
        return
    save_session_history(
        state.active_session_id,
        history=state.chat_history,
        compact_state=state.compact_state,
        mode=state.execution_mode,
    )


def save_session_history(
    session_id: Optional[str],
    *,
    history: List[Dict[str, Any]],
    compact_state: Optional[Dict[str, Any]] = None,
    mode: str = "auto",
) -> bool:
    """Persist chat history to a specific session without relying on global active state."""
    if not session_id:
        return True
    import logging
    try:
        get_session_manager().update_session(
            session_id,
            history=history,
            compact_state=compact_state,
            mode=mode or "auto",
        )
        return True
    except OSError as exc:
        logging.getLogger(__name__).error("session history save failed: %s", exc)
        return False


def clear_active_session_state() -> None:
    get_state().clear_session()


def load_latest_session_for_workspace(workspace_id: str) -> Optional[str]:
    state = get_state()
    session_manager = get_session_manager()
    for item in session_manager.list_sessions(workspace_id=workspace_id):
        session = session_manager.get_session(item.id)
        if session is None:
            continue
        state.activate_session(
            session.id,
            history=list(session.history),
            compact_state=dict(session.compact_state),
            mode=session.mode,
        )
        return session.id
    clear_active_session_state()
    return None


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@session_bp.route("/sessions", methods=["GET"])
def list_sessions() -> Any:
    """List all sessions for the sidebar."""
    state = get_state()
    manager = get_session_manager()
    sessions = manager.list_sessions()
    payload = []
    for session in sessions:
        full_session = manager.get_session(session.id)
        payload.append(
            {
                "id": session.id,
                "title": session.title,
                "created_at": session.created_at,
                "updated_at": session.updated_at,
                "workspace_id": session.workspace_id,
                "mode": getattr(session, "mode", "chat"),
                "message_count": len(full_session.history) if full_session is not None else 0,
            }
        )
    return jsonify(
        {
            "sessions": payload,
            "active_id": state.active_session_id,
            "active_workspace_id": state.active_workspace_id,
        }
    )


@session_bp.route("/search", methods=["GET"])
def search() -> Any:
    query = str(request.args.get("q") or "").strip()
    if not query:
        return jsonify({"results": []})
    try:
        from backend.web.session_search import search_sessions
    except ImportError:
        from session_search import search_sessions  # type: ignore[import-not-found]
    return jsonify({"results": search_sessions(query)})


@session_bp.route("/sessions", methods=["POST"])
def create_session() -> Any:
    """Create a new session and switch to it."""
    from backend.core.memory.workspace_state import clear_read_tracking

    state = get_state()
    save_active_session()
    data = request.get_json(silent=True) or {}
    mode = data.get("mode", "chat")
    session = get_session_manager().create_session(workspace_id=state.active_workspace_id or "", mode=mode)
    state.activate_session(session.id, history=[], compact_state=dict(session.compact_state), mode=session.mode)
    clear_read_tracking()
    return jsonify(
        {
            "id": session.id,
            "title": session.title,
            "mode": session.mode,
            "workspace_id": session.workspace_id,
            "compact_state": session.compact_state,
        }
    )


@session_bp.route("/sessions/<session_id>", methods=["GET"])
def get_session(session_id: str) -> Any:
    """Get full session data."""
    session = get_session_manager().get_session(session_id)
    if session is None:
        return jsonify({"error": "session not found"}), 404
    return jsonify(
        {
            "id": session.id,
            "title": session.title,
            "history": session.history,
            "compact_state": session.compact_state,
            "mode": session.mode,
            "created_at": session.created_at,
            "updated_at": session.updated_at,
            "workspace_id": session.workspace_id,
        }
    )


@session_bp.route("/sessions/<session_id>/export", methods=["GET"])
def export_session(session_id: str) -> Any:
    """Export a session as Markdown or JSON."""
    fmt = request.args.get("format", "markdown").lower()
    session = get_session_manager().get_session(session_id)
    if session is None:
        return jsonify({"error": "session not found"}), 404
    content, filename, mimetype = _generate_export(session, fmt)

    return Response(
        content,
        mimetype=mimetype,
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Content-Type": f"{mimetype}; charset=utf-8",
        },
    )


@session_bp.route("/sessions/<session_id>/export-save", methods=["POST"])
def export_session_save(session_id: str) -> Any:
    """Export a session through the native desktop save dialog."""
    try:
        from backend.runtime.desktop import get_webview_window

        window = get_webview_window()
    except Exception:
        window = None
    if window is None:
        return jsonify({"error": "save dialog is only available in desktop mode"}), 400

    session = get_session_manager().get_session(session_id)
    if session is None:
        return jsonify({"error": "session not found"}), 404

    import webview

    fmt = request.args.get("format", "markdown").lower()
    content, filename, _mimetype = _generate_export(session, fmt)
    file_type = "JSON Files (*.json)" if fmt == "json" else "Markdown Files (*.md)"
    result = window.create_file_dialog(
        webview.SAVE_DIALOG,
        save_filename=filename,
        file_types=(file_type,),
    )
    if not result:
        return jsonify({"cancelled": True})

    save_path = result[0] if isinstance(result, (list, tuple)) else str(result)
    with open(save_path, "w", encoding="utf-8") as handle:
        handle.write(content)
    return jsonify({"saved": True, "path": save_path})


@session_bp.route("/sessions/<session_id>/switch", methods=["POST"])
def switch_session(session_id: str) -> Any:
    """Switch the active session."""
    from backend.core.memory.workspace_state import clear_read_tracking

    state = get_state()
    session = get_session_manager().get_session(session_id)
    if session is None:
        return jsonify({"error": "session not found"}), 404
    save_active_session()
    session_workspace_id = session.workspace_id or state.active_workspace_id or ""
    workspace_manager = get_workspace_manager()
    workspace = workspace_manager.get_workspace(session_workspace_id) if session_workspace_id else None
    if workspace is not None:
        if os.path.isdir(workspace.path):
            os.chdir(workspace.path)
        state.active_workspace_id = workspace.id
    elif not session.workspace_id:
        state.active_workspace_id = state.active_workspace_id or ""
    state.activate_session(
        session.id,
        history=list(session.history),
        compact_state=dict(session.compact_state),
        mode=session.mode,
    )
    clear_read_tracking()
    return jsonify(
        {
            "id": session.id,
            "title": session.title,
            "mode": session.mode,
            "workspace_id": session.workspace_id,
            "compact_state": session.compact_state,
        }
    )


@session_bp.route("/sessions/<session_id>", methods=["DELETE"])
def delete_session(session_id: str) -> Any:
    """Delete a session and keep the workspace in an empty state if needed."""
    state = get_state()
    manager = get_session_manager()
    active_was_deleted = state.active_session_id == session_id
    if active_was_deleted:
        save_active_session()
    deleted = manager.delete_session(session_id)
    if not deleted:
        return jsonify({"error": "session not found"}), 404

    if state.active_session_id == session_id:
        load_latest_session_for_workspace(state.active_workspace_id or "")
    return jsonify(
        {
            "ok": True,
            "deleted_id": session_id,
            "active_id": state.active_session_id,
            "active_workspace_id": state.active_workspace_id,
            "replacement": None,
        }
    )


@session_bp.route("/workspaces/<workspace_id>/sessions", methods=["DELETE"])
def delete_workspace_sessions(workspace_id: str) -> Any:
    """Delete all sessions in a workspace without deleting the workspace itself."""
    state = get_state()
    workspace = get_workspace_manager().get_workspace(workspace_id)
    if workspace is None:
        return jsonify({"error": "workspace not found"}), 404

    manager = get_session_manager()
    sessions = manager.list_sessions(workspace_id=workspace_id)
    active_was_deleted = any(session.id == state.active_session_id for session in sessions)
    if active_was_deleted:
        save_active_session()

    deleted = manager.delete_sessions_for_workspace(workspace_id)
    if active_was_deleted or workspace_id == state.active_workspace_id:
        if os.path.isdir(workspace.path):
            os.chdir(workspace.path)
        state.active_workspace_id = workspace_id
        clear_active_session_state()

    return jsonify(
        {
            "ok": True,
            "deleted": deleted,
            "active_id": state.active_session_id,
            "active_workspace_id": state.active_workspace_id,
            "replacement": None,
        }
    )


@session_bp.route("/sessions/<session_id>/title", methods=["POST"])
def rename_session(session_id: str) -> Any:
    """Rename a session."""
    data = request.get_json(silent=True) or {}
    title = str(data.get("title", "")).strip()
    if not title:
        return jsonify({"error": "title required"}), 400
    if not get_session_manager().update_session(session_id, title=title):
        return jsonify({"error": "not found"}), 404
    return jsonify({"ok": True, "title": title})


@session_bp.route("/sessions/<session_id>/title/auto", methods=["POST"])
def auto_title_session(session_id: str) -> Any:
    """Generate a concise title without overwriting user-renamed sessions."""
    data = request.get_json(silent=True) or {}
    force = bool(data.get("force", False))
    manager = get_session_manager()
    session = manager.get_session(session_id)
    if session is None:
        return jsonify({"error": "session not found"}), 404
    if not force and not should_auto_title(session.title):
        return jsonify({"ok": True, "updated": False, "title": session.title})
    title = generate_session_title(session.history)
    if not title:
        return jsonify({"ok": False, "updated": False, "title": session.title, "error": "not enough user context"})
    manager.update_session(session_id, title=title)
    return jsonify({"ok": True, "updated": True, "title": title})


@session_bp.route("/sessions/<session_id>/away-summary", methods=["GET"])
def away_summary(session_id: str) -> Any:
    """Return a short while-you-were-away recap for the session."""
    session = get_session_manager().get_session(session_id)
    if session is None:
        return jsonify({"error": "session not found"}), 404
    summary = generate_away_summary(session.history, session.compact_state)
    return jsonify({"ok": bool(summary), "summary": summary})


@session_bp.route("/sessions/<session_id>/suggestions", methods=["GET"])
def prompt_suggestions(session_id: str) -> Any:
    """Return short next-prompt suggestions for the active composer."""
    session = get_session_manager().get_session(session_id)
    if session is None:
        return jsonify({"error": "session not found"}), 404
    return jsonify({"ok": True, "suggestions": generate_prompt_suggestions(session.history)})


@session_bp.route("/sessions/<session_id>/verification-agent", methods=["POST"])
def verification_agent(session_id: str) -> Any:
    """Build a verification-only report shell from supplied checks."""
    session = get_session_manager().get_session(session_id)
    if session is None:
        return jsonify({"error": "session not found"}), 404
    data = request.get_json(silent=True) or {}
    changed_files = data.get("changed_files", data.get("changedFiles", []))
    checks = data.get("checks", [])
    normalized_checks = checks if isinstance(checks, list) else []
    commands = data.get("commands", [])
    if isinstance(commands, list) and commands:
        normalized_checks = [
            *normalized_checks,
            *_run_verification_commands(session.workspace_id, commands),
        ]
    report = build_verification_agent_report(
        task=str(data.get("task") or session.title or ""),
        changed_files=changed_files if isinstance(changed_files, list) else [],
        checks=normalized_checks,
    )
    return jsonify({"ok": True, "report": report})


@session_bp.route("/sessions/<session_id>/agent-runtime-profile", methods=["GET"])
def agent_runtime_profile(session_id: str) -> Any:
    """Return the visible runtime contract profile for the Plan/Activity UI."""
    session = get_session_manager().get_session(session_id)
    if session is None:
        return jsonify({"error": "session not found"}), 404
    workspace_root = _session_workspace_root(session.workspace_id)
    model = ""
    try:
        from backend.web.llm_state import get_runtime_settings

        settings = get_runtime_settings()
        model = str(settings.get("model") or "")
    except Exception:
        model = ""
    payload = build_agent_runtime_profile(
        history=session.history,
        workspace_root=workspace_root,
        session_id=session.id,
        mode=session.mode,
        model=model,
        compact_state=session.compact_state,
        active_run=None,
    )
    return jsonify(payload)


def _run_verification_commands(workspace_id: str, commands: List[Any]) -> List[Dict[str, Any]]:
    workspace = get_workspace_manager().get_workspace(workspace_id) if workspace_id else None
    cwd = workspace.path if workspace is not None and os.path.isdir(workspace.path) else os.getcwd()
    results: List[Dict[str, Any]] = []
    for raw_command in commands[:5]:
        command = str(raw_command or "").strip()
        policy = verification_command_policy(command)
        if not policy.get("allowed"):
            results.append(
                {
                    "name": command or "empty command",
                    "command": command,
                    "result": "blocked",
                    "status": "blocked",
                    "output": "",
                    "error": str(policy.get("reason") or "blocked by verifier policy"),
                }
            )
            continue
        try:
            completed = subprocess.run(
                command,
                cwd=cwd,
                shell=True,
                capture_output=True,
                text=True,
                timeout=90,
            )
            output = "\n".join(part for part in (completed.stdout, completed.stderr) if part).strip()
            results.append(
                {
                    "name": command,
                    "command": command,
                    "result": "pass" if completed.returncode == 0 else "fail",
                    "status": "pass" if completed.returncode == 0 else "fail",
                    "exit_code": completed.returncode,
                    "output": output[:4000],
                }
            )
        except subprocess.TimeoutExpired as exc:
            results.append(
                {
                    "name": command,
                    "command": command,
                    "result": "fail",
                    "status": "timeout",
                    "exit_code": None,
                    "output": str(exc)[:1200],
                }
            )
    return results


def _session_workspace_root(workspace_id: str) -> str:
    workspace = get_workspace_manager().get_workspace(workspace_id) if workspace_id else None
    if workspace is not None and os.path.isdir(workspace.path):
        return workspace.path
    return os.getcwd()

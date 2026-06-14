"""Workspace management Blueprint — CRUD, tree, git status, file preview, file changes."""
from __future__ import annotations

import hashlib
import json
import mimetypes
import os
import subprocess
import tempfile
import time
import uuid
from typing import Any, Dict, List, Optional
from urllib.parse import quote

from flask import Blueprint, Response, jsonify, request, send_file

from backend.runtime.path_safety import validate_path_access
from backend.web.helpers import active_workspace_root, get_state, request_client_is_loopback
from backend.web.sessions import get_session_manager
from backend.web.workspaces import get_workspace_manager

workspace_bp = Blueprint("workspaces", __name__)
_FILE_PREVIEW_TOKEN_TTL_SECONDS = 30 * 60
_FILE_PREVIEW_ROOTS: Dict[str, Dict[str, Any]] = {}
_FILE_PREVIEW_SAFE_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg", ".ico",
    ".txt", ".log", ".csv", ".json", ".xml", ".yaml", ".yml",
    ".py", ".js", ".mjs", ".ts", ".jsx", ".tsx", ".html", ".htm", ".xhtml",
    ".css", ".scss", ".less", ".md", ".sh", ".bat", ".ps1",
    ".java", ".c", ".cpp", ".h", ".rs", ".go", ".rb", ".php",
    ".toml", ".ini", ".cfg", ".conf",
    ".map", ".wasm", ".woff", ".woff2", ".ttf", ".otf", ".eot",
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _ext_to_lang(ext: str) -> str:
    """Map a file extension to a highlight.js language name."""
    lang_map = {
        ".py": "python",
        ".js": "javascript",
        ".ts": "typescript",
        ".jsx": "jsx",
        ".tsx": "tsx",
        ".java": "java",
        ".c": "c",
        ".cpp": "cpp",
        ".h": "c",
        ".rs": "rust",
        ".go": "go",
        ".rb": "ruby",
        ".php": "php",
        ".sh": "bash",
        ".bat": "batch",
        ".ps1": "powershell",
        ".html": "html",
        ".css": "css",
        ".scss": "scss",
        ".less": "less",
        ".json": "json",
        ".yaml": "yaml",
        ".yml": "yaml",
        ".toml": "toml",
        ".xml": "xml",
        ".sql": "sql",
        ".r": "r",
        ".swift": "swift",
        ".kt": "kotlin",
        ".lua": "lua",
        ".dart": "dart",
        ".md": "markdown",
        ".markdown": "markdown",
        ".vue": "html",
        ".svelte": "html",
    }
    return lang_map.get(ext, "")


def _file_change_audit_path() -> str:
    return os.path.join(active_workspace_root(), ".metis", "audit", "file-change-transactions.jsonl")


def _cleanup_file_preview_roots(now: Optional[float] = None) -> None:
    current = now or time.time()
    expired = [
        token
        for token, record in _FILE_PREVIEW_ROOTS.items()
        if current - float(record.get("created_at") or 0) > _FILE_PREVIEW_TOKEN_TTL_SECONDS
    ]
    for token in expired:
        _FILE_PREVIEW_ROOTS.pop(token, None)


def _path_within(path: str, root: str) -> bool:
    root_path = os.path.abspath(root)
    target_path = os.path.abspath(path)
    return target_path == root_path or target_path.startswith(root_path + os.sep)


def _safe_preview_file(abs_path: str, workspace_root: str, *, allow_temp: bool = False) -> Any:
    safety = validate_path_access(abs_path, action="read", workspace_root=workspace_root)
    if not safety.allowed:
        return jsonify({"error": safety.code, "detail": safety.message}), 403

    allowed_roots = [workspace_root]
    if allow_temp:
        allowed_roots.append(os.path.abspath(tempfile.gettempdir()))
    if not any(_path_within(abs_path, root) for root in allowed_roots):
        return jsonify({"error": "path not in allowed directory"}), 403

    if not os.path.isfile(abs_path):
        return jsonify({"error": "file not found"}), 404

    ext = os.path.splitext(abs_path)[1].lower()
    if ext not in _FILE_PREVIEW_SAFE_EXTENSIONS:
        return jsonify({"error": f"extension {ext} not allowed for preview"}), 403
    return None


def _register_file_preview_root(html_path: str, workspace_root: str) -> str:
    _cleanup_file_preview_roots()
    token = uuid.uuid4().hex
    _FILE_PREVIEW_ROOTS[token] = {
        "created_at": time.time(),
        "root": os.path.dirname(os.path.abspath(html_path)),
        "workspace_root": os.path.abspath(workspace_root),
    }
    return token


def _inject_file_preview_base(html: str, token: str) -> str:
    base_tag = f'<base href="/file-preview-root/{token}/">'
    if "<base" in html[:2048].lower():
        return html
    lower = html.lower()
    head_index = lower.find("<head")
    if head_index >= 0:
        close_index = lower.find(">", head_index)
        if close_index >= 0:
            return f"{html[:close_index + 1]}\n{base_tag}\n{html[close_index + 1:]}"
    return f"{base_tag}\n{html}"


def _hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _read_text_file(path: str) -> Optional[str]:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8", errors="replace") as handle:
        return handle.read()


def _write_text_file(path: str, content: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as handle:
        handle.write(content)


def _resolve_workspace_write_path(file_path: str, workspace_root: str) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    if not file_path.strip():
        return None, {"status": "blocked", "message": "missing path"}
    abs_path = os.path.abspath(file_path)
    if not (abs_path.startswith(workspace_root + os.sep) or abs_path == workspace_root):
        return None, {
            "status": "blocked",
            "message": "path outside workspace",
            "path": file_path,
        }
    safety = validate_path_access(abs_path, action="write", workspace_root=workspace_root)
    if not safety.allowed:
        return None, {
            "status": "blocked",
            "message": safety.message,
            "code": safety.code,
            "path": file_path,
        }
    return abs_path, None


def _change_value(row: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = row.get(key)
        if isinstance(value, str):
            return value
    return ""


def _normalize_file_change(row: Any) -> Dict[str, Any]:
    source = row if isinstance(row, dict) else {}
    return {
        "id": _change_value(source, "id"),
        "path": _change_value(source, "path", "file_path", "filePath"),
        "kind": _change_value(source, "kind") or "unknown",
        "tool_name": _change_value(source, "tool_name", "toolName"),
        "before": _change_value(source, "before", "old_content", "oldContent"),
        "after": _change_value(source, "after", "content", "new_content", "newContent"),
    }


def _preflight_file_change_revert(change: Dict[str, Any], workspace_root: str) -> Dict[str, Any]:
    abs_path, blocked = _resolve_workspace_write_path(change["path"], workspace_root)
    if blocked:
        return {**blocked, "id": change["id"], "kind": change["kind"], "tool_name": change["tool_name"]}
    assert abs_path is not None

    before = change["before"]
    after = change["after"]
    kind = str(change["kind"] or "unknown").lower()
    tool_name = str(change["tool_name"] or "").lower()
    exists = os.path.exists(abs_path)
    current = _read_text_file(abs_path)
    current_hash = _hash_text(current) if current is not None else ""
    before_hash = _hash_text(before)
    after_hash = _hash_text(after)
    item = {
        "id": change["id"],
        "path": change["path"],
        "kind": kind,
        "tool_name": tool_name,
        "before_hash": before_hash,
        "after_hash": after_hash,
        "current_hash": current_hash,
    }

    is_delete = kind == "delete" or "delete" in tool_name or "remove" in tool_name
    is_create = kind == "create"

    if is_delete:
        if current is not None and current != after:
            return {
                **item,
                "status": "conflict",
                "message": "file exists and no longer matches the recorded deleted state",
            }
        return {**item, "status": "ready", "action": "write_before", "abs_path": abs_path, "content": before}

    if is_create:
        if not exists:
            return {**item, "status": "ready", "action": "noop", "abs_path": abs_path, "message": "created file is already missing"}
        if current != after:
            return {**item, "status": "conflict", "message": "file changed after agent edit"}
        return {**item, "status": "ready", "action": "delete_current", "abs_path": abs_path}

    if current is None:
        return {**item, "status": "conflict", "message": "file is missing"}
    if current != after:
        return {**item, "status": "conflict", "message": "file changed after agent edit"}
    return {**item, "status": "ready", "action": "write_before", "abs_path": abs_path, "content": before}


def _apply_file_change_revert(plan: Dict[str, Any]) -> Dict[str, Any]:
    action = plan.get("action")
    abs_path = str(plan.get("abs_path") or "")
    payload = {
        key: value
        for key, value in plan.items()
        if key not in {"abs_path", "content", "action"}
    }
    try:
        if action == "write_before":
            _write_text_file(abs_path, str(plan.get("content") or ""))
            return {**payload, "status": "reverted", "message": "restored previous content"}
        if action == "delete_current":
            os.remove(abs_path)
            return {**payload, "status": "reverted", "message": "removed created file"}
        if action == "noop":
            return {**payload, "status": "reverted", "message": plan.get("message") or "already reverted"}
        return {**payload, "status": "blocked", "message": "unknown revert action"}
    except OSError as exc:
        return {**payload, "status": "blocked", "message": str(exc)}


def _append_file_change_audit(entry: Dict[str, Any]) -> str:
    audit_path = _file_change_audit_path()
    os.makedirs(os.path.dirname(audit_path), exist_ok=True)
    with open(audit_path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n")
    return audit_path


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@workspace_bp.route("/workspace", methods=["GET"])
def workspace() -> Any:
    state = get_state()
    ws = get_workspace_manager().get_workspace(state.active_workspace_id) if state.active_workspace_id else None
    root = active_workspace_root()
    return jsonify(
        {
            "cwd": root,
            "workspace_id": state.active_workspace_id,
            "workspace_name": ws.name if ws else "",
        }
    )


@workspace_bp.route("/workspaces", methods=["GET"])
def list_workspaces() -> Any:
    """List all workspaces."""
    state = get_state()
    workspaces = get_workspace_manager().list_workspaces()
    return jsonify(
        {
            "workspaces": [
                {
                    "id": ws.id,
                    "name": ws.name,
                    "path": ws.path,
                    "created_at": ws.created_at,
                    "updated_at": ws.updated_at,
                }
                for ws in workspaces
            ],
            "active_id": state.active_workspace_id,
        }
    )


@workspace_bp.route("/workspaces", methods=["POST"])
def create_workspace_endpoint() -> Any:
    """Create a workspace from a given path."""
    data = request.get_json(silent=True) or {}
    path = str(data.get("path", "")).strip()
    if not path or not os.path.isdir(path):
        return jsonify({"error": "valid directory path required"}), 400
    ws = get_workspace_manager().create_workspace(path, name=str(data.get("name") or ""))
    return jsonify({"id": ws.id, "name": ws.name, "path": ws.path})


@workspace_bp.route("/workspaces/<workspace_id>/switch", methods=["POST"])
def switch_workspace(workspace_id: str) -> Any:
    """Switch the active workspace, CWD, and session context."""
    from backend.web.session_routes import (
        clear_active_session_state,
        load_latest_session_for_workspace,
        save_active_session,
    )

    state = get_state()
    ws = get_workspace_manager().get_workspace(workspace_id)
    if ws is None:
        return jsonify({"error": "workspace not found"}), 404
    if not os.path.isdir(ws.path):
        return jsonify({"error": "workspace directory no longer exists"}), 410

    save_active_session()
    os.chdir(ws.path)
    state.active_workspace_id = ws.id

    session_manager = get_session_manager()
    sessions = session_manager.list_sessions(workspace_id=ws.id)
    if sessions:
        load_latest_session_for_workspace(ws.id)
    else:
        clear_active_session_state()

    return jsonify(
        {
            "workspace_id": ws.id,
            "workspace_name": ws.name,
            "cwd": os.getcwd(),
            "session_id": state.active_session_id,
        }
    )


@workspace_bp.route("/workspaces/<workspace_id>", methods=["DELETE"])
def delete_workspace(workspace_id: str) -> Any:
    """Delete a workspace metadata entry."""
    from backend.web.session_routes import (
        clear_active_session_state,
        load_latest_session_for_workspace,
        save_active_session,
    )

    state = get_state()
    manager = get_workspace_manager()
    ws = manager.get_workspace(workspace_id)
    if ws is None:
        return jsonify({"error": "workspace not found"}), 404

    deleted_active = workspace_id == state.active_workspace_id
    if deleted_active:
        save_active_session()

    deleted = manager.delete_workspace(workspace_id)
    if not deleted:
        return jsonify({"error": "workspace not found"}), 404

    if deleted_active:
        replacement = next((item for item in manager.list_workspaces() if os.path.isdir(item.path)), None)
        if replacement is not None:
            os.chdir(replacement.path)
            state.active_workspace_id = replacement.id
            load_latest_session_for_workspace(replacement.id)
        else:
            state.active_workspace_id = ""
            clear_active_session_state()

    return jsonify({"ok": True, "active_workspace_id": state.active_workspace_id, "active_id": state.active_session_id})


@workspace_bp.route("/workspace/open-folder", methods=["POST"])
def open_folder_dialog() -> Any:
    """Open a native folder picker dialog and create a workspace from the result."""
    from backend.web.session_routes import (
        clear_active_session_state,
        load_latest_session_for_workspace,
        save_active_session,
    )

    try:
        from backend.runtime.desktop import get_webview_window
        window = get_webview_window()
    except ImportError:
        window = None

    if window is None:
        return jsonify({"error": "folder dialog is only available in desktop mode"}), 400

    import webview

    result = window.create_file_dialog(webview.FOLDER_DIALOG)
    if not result:
        return jsonify({"cancelled": True})

    folder_path = result[0] if isinstance(result, (list, tuple)) else str(result)
    if not os.path.isdir(folder_path):
        return jsonify({"error": "selected path is not a valid directory"}), 400

    state = get_state()
    save_active_session()
    ws = get_workspace_manager().create_workspace(folder_path)
    os.chdir(ws.path)
    state.active_workspace_id = ws.id

    session_manager = get_session_manager()
    sessions = session_manager.list_sessions(workspace_id=ws.id)
    if sessions:
        load_latest_session_for_workspace(ws.id)
    else:
        clear_active_session_state()

    return jsonify(
        {
            "workspace_id": ws.id,
            "workspace_name": ws.name,
            "cwd": ws.path,
            "session_id": state.active_session_id,
        }
    )


@workspace_bp.route("/workspace/tree", methods=["GET"])
def workspace_tree() -> Any:
    """Return the current workspace file tree as JSON."""
    base = active_workspace_root()
    rel_path = request.args.get("path", ".")
    try:
        depth = min(max(int(request.args.get("depth", "3")), 0), 5)
    except ValueError:
        depth = 3

    target = os.path.abspath(os.path.normpath(os.path.join(base, rel_path)))
    if not (target == base or target.startswith(base + os.sep)):
        return jsonify({"error": "path outside workspace"}), 403
    if not os.path.isdir(target):
        return jsonify({"error": "not a directory"}), 404

    skip_dirs = {
        "__pycache__", "node_modules", ".git", ".venv", "venv",
        "env", ".env", ".idea", ".vscode", ".mypy_cache",
        ".pytest_cache", "dist", "build", ".tox", ".eggs",
    }

    def build_tree(dir_path: str, current_depth: int) -> List[Dict[str, Any]]:
        entries: List[Dict[str, Any]] = []
        try:
            items = sorted(os.listdir(dir_path), key=lambda value: value.lower())
        except (OSError, PermissionError):
            return entries

        dirs: List[str] = []
        files: List[str] = []
        for name in items:
            if name.startswith(".") and name not in (".env",):
                continue
            full = os.path.join(dir_path, name)
            if os.path.isdir(full):
                if name.lower() in skip_dirs:
                    continue
                dirs.append(name)
            else:
                files.append(name)

        for name in dirs:
            full = os.path.join(dir_path, name)
            rel = os.path.relpath(full, base).replace(os.sep, "/")
            children = build_tree(full, current_depth + 1) if current_depth < depth else []
            entries.append({
                "name": name,
                "type": "directory",
                "path": rel,
                "children": children,
            })

        for name in files:
            full = os.path.join(dir_path, name)
            rel = os.path.relpath(full, base).replace(os.sep, "/")
            try:
                size = os.path.getsize(full)
            except OSError:
                size = 0
            entries.append({
                "name": name,
                "type": "file",
                "path": rel,
                "size": size,
            })
        return entries

    return jsonify({
        "root": os.path.basename(base),
        "cwd": base,
        "tree": build_tree(target, 0),
    })


@workspace_bp.route("/workspace/git-status", methods=["GET"])
def workspace_git_status() -> Any:
    """Return git status for the workspace if it is a git repository."""
    base = active_workspace_root()
    git_dir = os.path.join(base, ".git")
    if not os.path.exists(git_dir):
        return jsonify({"git": False})

    try:
        branch_result = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=base,
            capture_output=True,
            text=True,
            timeout=5,
        )
        branch = branch_result.stdout.strip() or "HEAD"
        status_result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=base,
            capture_output=True,
            text=True,
            timeout=5,
        )
        files: Dict[str, str] = {}
        for line in status_result.stdout.strip().splitlines():
            if len(line) < 4:
                continue
            xy = line[:2]
            filepath = line[3:].strip()
            if xy[0] == "?" or xy[1] == "?":
                status = "untracked"
            elif xy[0] == "A" or xy[1] == "A":
                status = "added"
            elif xy[0] == "D" or xy[1] == "D":
                status = "deleted"
            elif xy[0] == "M" or xy[1] == "M":
                status = "modified"
            elif xy[0] == "R":
                status = "renamed"
            else:
                status = "changed"
            files[filepath.replace(os.sep, "/")] = status

        return jsonify({
            "git": True,
            "branch": branch,
            "files": files,
            "clean": len(files) == 0,
        })
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return jsonify({"git": False, "error": "git command failed"})


@workspace_bp.route("/workspace/file-changes/revert", methods=["POST"])
def workspace_file_changes_revert() -> Any:
    """Revert reviewed file changes only when current content still matches the recorded after state."""
    if not request_client_is_loopback():
        return jsonify({"ok": False, "error": "forbidden"}), 403

    state = get_state()
    payload = request.get_json(silent=True) or {}
    raw_changes = payload.get("changes")
    if not isinstance(raw_changes, list) or not raw_changes:
        return jsonify({"ok": False, "error": "changes required"}), 400

    workspace_root = active_workspace_root()
    summary_id = str(payload.get("summary_id") or payload.get("summaryId") or "")
    changes = [_normalize_file_change(row) for row in raw_changes[:50]]
    preflight = [_preflight_file_change_revert(change, workspace_root) for change in changes]
    has_blocker = any(item.get("status") in {"blocked", "conflict"} for item in preflight)

    if has_blocker:
        items = [
            {key: value for key, value in item.items() if key not in {"abs_path", "content", "action"}}
            for item in preflight
        ]
        ok = False
    else:
        items = [_apply_file_change_revert(item) for item in preflight]
        ok = all(item.get("status") == "reverted" for item in items)

    audit = {
        "id": str(uuid.uuid4()),
        "created_at": int(time.time()),
        "workspace_id": state.active_workspace_id,
        "session_id": state.active_session_id,
        "cwd": workspace_root,
        "summary_id": summary_id,
        "ok": ok,
        "items": items,
    }
    audit_path = _append_file_change_audit(audit)
    reverted_count = sum(1 for item in items if item.get("status") == "reverted")
    conflict_count = sum(1 for item in items if item.get("status") == "conflict")
    blocked_count = sum(1 for item in items if item.get("status") == "blocked")

    return jsonify(
        {
            "ok": ok,
            "summary_id": summary_id,
            "reverted_count": reverted_count,
            "conflict_count": conflict_count,
            "blocked_count": blocked_count,
            "items": items,
            "audit_path": audit_path,
        }
    )


@workspace_bp.route("/file-preview", methods=["GET"])
def file_preview() -> Any:
    """Serve a local workspace or temp file for safe UI preview."""
    if not request_client_is_loopback():
        return jsonify({"error": "forbidden", "detail": "only loopback"}), 403

    file_path = request.args.get("path", "")
    if not file_path:
        return jsonify({"error": "missing path"}), 400

    abs_path = os.path.abspath(file_path)
    workspace_root = active_workspace_root()
    blocked = _safe_preview_file(abs_path, workspace_root, allow_temp=True)
    if blocked is not None:
        return blocked

    ext = os.path.splitext(abs_path)[1].lower()
    if ext in {".html", ".htm", ".xhtml"} and _path_within(abs_path, workspace_root):
        token = _register_file_preview_root(abs_path, workspace_root)
        with open(abs_path, "r", encoding="utf-8", errors="replace") as handle:
            html = handle.read()
        return Response(
            _inject_file_preview_base(html, token),
            mimetype=mimetypes.guess_type(abs_path)[0] or "text/html",
            headers={"Cache-Control": "no-store"},
        )

    mime = mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
    return send_file(abs_path, mimetype=mime)


@workspace_bp.route("/file-preview-root/<token>/", defaults={"relative_path": ""}, methods=["GET"])
@workspace_bp.route("/file-preview-root/<token>/<path:relative_path>", methods=["GET"])
def file_preview_root(token: str, relative_path: str) -> Any:
    """Serve relative assets for a previously registered HTML file preview."""
    if not request_client_is_loopback():
        return jsonify({"error": "forbidden", "detail": "only loopback"}), 403

    _cleanup_file_preview_roots()
    record = _FILE_PREVIEW_ROOTS.get(token)
    if not record:
        return jsonify({"error": "preview token expired"}), 404

    root = os.path.abspath(str(record.get("root") or ""))
    workspace_root = os.path.abspath(str(record.get("workspace_root") or active_workspace_root()))
    normalized_relative = os.path.normpath(relative_path or "index.html").replace("\\", os.sep)
    if normalized_relative.startswith(".." + os.sep) or normalized_relative == ".." or os.path.isabs(normalized_relative):
        return jsonify({"error": "path outside preview root"}), 403

    abs_path = os.path.abspath(os.path.join(root, normalized_relative))
    if not _path_within(abs_path, root) or not _path_within(abs_path, workspace_root):
        return jsonify({"error": "path outside preview root"}), 403

    blocked = _safe_preview_file(abs_path, workspace_root)
    if blocked is not None:
        return blocked

    mime = mimetypes.guess_type(abs_path)[0] or "application/octet-stream"
    return send_file(abs_path, mimetype=mime)


@workspace_bp.route("/workspace/file", methods=["GET"])
def workspace_file() -> Any:
    """Return file content and metadata for the workspace preview pane."""
    if not request_client_is_loopback():
        return jsonify({"error": "forbidden"}), 403

    file_path = request.args.get("path", "")
    if not file_path:
        return jsonify({"error": "missing path"}), 400

    abs_path = os.path.abspath(file_path)
    cwd_root = active_workspace_root()
    if not (abs_path.startswith(cwd_root + os.sep) or abs_path == cwd_root):
        return jsonify({"error": "path outside workspace"}), 403
    safety = validate_path_access(abs_path, action="read", workspace_root=cwd_root)
    if not safety.allowed:
        return jsonify({"error": safety.code, "detail": safety.message}), 403
    if not os.path.isfile(abs_path):
        return jsonify({"error": "file not found"}), 404

    ext = os.path.splitext(abs_path)[1].lower()
    name = os.path.basename(abs_path)
    size = os.path.getsize(abs_path)
    image_exts = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}
    text_exts = {
        ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".c", ".cpp", ".h",
        ".rs", ".go", ".rb", ".php", ".sh", ".bat", ".ps1",
        ".html", ".css", ".scss", ".less", ".vue", ".svelte",
        ".json", ".yaml", ".yml", ".toml", ".xml", ".ini", ".cfg", ".conf",
        ".md", ".markdown", ".txt", ".log", ".csv",
        ".sql", ".r", ".swift", ".kt", ".lua", ".dart",
        ".gitignore", ".dockerignore", ".editorconfig",
    }
    is_dotfile = name.startswith(".") and ext == ""

    if ext in image_exts:
        return jsonify(
            {
                "type": "image",
                "name": name,
                "path": file_path,
                "size": size,
                "preview_url": f"/file-preview?path={quote(file_path, safe='')}",
            }
        )

    if ext in text_exts or is_dotfile:
        max_preview_size = 256 * 1024
        language = _ext_to_lang(ext)
        if size > max_preview_size:
            return jsonify(
                {
                    "type": "text",
                    "name": name,
                    "path": file_path,
                    "size": size,
                    "truncated": True,
                    "content": "",
                    "language": language,
                }
            )
        try:
            with open(abs_path, "r", encoding="utf-8", errors="replace") as handle:
                content = handle.read()
        except OSError:
            return jsonify({"error": "could not read file"}), 500
        return jsonify(
            {
                "type": "markdown" if ext in (".md", ".markdown") else "text",
                "name": name,
                "path": file_path,
                "size": size,
                "truncated": False,
                "content": content,
                "language": language,
            }
        )

    return jsonify({"type": "binary", "name": name, "path": file_path, "size": size})

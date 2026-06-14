"""Shared helpers used across route Blueprints."""
from __future__ import annotations

import ipaddress
import os
from typing import Any, Dict, Optional

from flask import request

from backend.core.paths import metis_dir, metis_path
from backend.runtime.error_catalog import ErrorInfo
from backend.web.runtime_state import RuntimeState
from backend.web.sessions import get_session_manager
from backend.web.workspaces import get_workspace_manager

_runtime_state: Optional[RuntimeState] = None


def init_shared_state(state: RuntimeState) -> None:
    global _runtime_state
    _runtime_state = state


def get_state() -> RuntimeState:
    assert _runtime_state is not None, "call init_shared_state() first"
    return _runtime_state


def error_response_payload(info: ErrorInfo) -> Dict[str, Any]:
    return {
        "error": info.message,
        "code": info.code,
        "title": info.title,
        "message": info.message,
        "hint": info.hint,
        "recoverable": info.recoverable,
        "status": info.status,
        "details": (info.details or "")[:1000],
    }


def request_client_is_loopback() -> bool:
    addr = (request.remote_addr or "").strip()
    if not addr:
        return False
    try:
        return ipaddress.ip_address(addr.split("%")[0]).is_loopback
    except ValueError:
        return False


def workspace_root_for_id(workspace_id: str) -> str:
    workspace = get_workspace_manager().get_workspace(workspace_id) if workspace_id else None
    if workspace and workspace.path:
        return os.path.abspath(workspace.path)
    return ""


def active_workspace_root() -> str:
    return workspace_root_for_id(get_state().active_workspace_id) or os.path.abspath(os.getcwd())


def read_text(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read()
    except OSError:
        return ""


def write_text(path: str, content: str) -> None:
    directory = os.path.dirname(path)
    if directory:
        os.makedirs(directory, exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        handle.write(content)


def open_directory(path: str) -> bool:
    import subprocess
    import sys
    try:
        if sys.platform.startswith("win"):
            os.startfile(path)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", path])
        else:
            subprocess.Popen(["xdg-open", path])
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Skill / memory path utilities (shared by feature_routes and app.py internals)
# ---------------------------------------------------------------------------

def skills_dir() -> str:
    import re  # noqa: F811
    return str(metis_dir("skills"))


def safe_skill_slug(text: str) -> str:
    import re
    value = re.sub(r"[^a-zA-Z0-9一-鿿]+", "-", text.strip().lower()).strip("-")
    return (value[:48] or "metis-workflow").strip("-") or "metis-workflow"


def skill_title(content: str, fallback: str) -> str:
    for line in content.splitlines():
        if line.startswith("# "):
            return line[2:].strip() or fallback
    return fallback


def unique_skill_dir(slug: str) -> str:
    root = skills_dir()
    skill_dir = os.path.join(root, slug)
    suffix = 2
    while os.path.exists(skill_dir):
        skill_dir = os.path.join(root, f"{slug}-{suffix}")
        suffix += 1
    return skill_dir


def memory_paths_payload() -> Dict[str, str]:
    root = active_workspace_root()
    return {
        "global_path": str(metis_path("METIS.md")),
        "project_path": os.path.join(root, "METIS.md"),
    }

"""工作区 Agent 状态（与 todo_write / switch_mode 落盘文件对齐；海马体可读快照）。"""
import json
import os
from typing import Any, Dict, List, Optional

from backend.tools.coding.workflow_features.agent_state.state_paths import AGENT_MODE_FILE, AGENT_TODO_FILE

_files_read_this_session: set[str] = set()
_read_guard_bypass: set[str] = set()


def _normalize_session_path(workspace_root: str, file_path: str) -> str:
    raw_path = str(file_path or "").strip()
    if not raw_path:
        return ""
    if os.path.isabs(raw_path):
        return os.path.normcase(os.path.normpath(raw_path))
    base = workspace_root or os.getcwd()
    return os.path.normcase(os.path.normpath(os.path.join(base, raw_path)))


def record_file_read(workspace_root: str, file_path: str) -> None:
    normalized = _normalize_session_path(workspace_root, file_path)
    if normalized:
        _files_read_this_session.add(normalized)


def has_file_been_read(workspace_root: str, file_path: str) -> bool:
    normalized = _normalize_session_path(workspace_root, file_path)
    if not normalized:
        return True
    if normalized in _files_read_this_session:
        return True
    if normalized in _read_guard_bypass:
        return True
    _read_guard_bypass.add(normalized)
    return False


def clear_read_tracking() -> None:
    _files_read_this_session.clear()
    _read_guard_bypass.clear()


def read_agent_mode(workspace_root: str = ".") -> Optional[Dict[str, Any]]:
    path = os.path.join(workspace_root, AGENT_MODE_FILE)
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def read_agent_todos(workspace_root: str = ".") -> List[Dict[str, Any]]:
    path = os.path.join(workspace_root, AGENT_TODO_FILE)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = json.load(f)
        todos = raw.get("todos", [])
        if not isinstance(todos, list):
            return []
        return [t for t in todos if isinstance(t, dict)]
    except Exception:
        return []


def summarize_for_system_prompt(
    workspace_root: str = ".",
    *,
    max_todos: int = 12,
    max_line_len: int = 120,
) -> str:
    """
    供 context_builder 注入的短摘要；无相关文件则返回空串。
    与 Tools 层写入的 JSON  schema 一致（不重复写第二套存储格式）。
    """
    parts: List[str] = []
    mode = read_agent_mode(workspace_root)
    if mode:
        m = mode.get("mode", "?")
        raw_note = str(mode.get("note") or "")
        note = raw_note.strip()
        if note:
            if len(note) > max_line_len:
                note = note[:max_line_len] + "..."
            parts.append(f"mode: {m} ({note})")
        else:
            parts.append(f"mode: {m}")

    todos = read_agent_todos(workspace_root)
    if todos:
        try:
            from backend.runtime.loop_discipline import compact_todo_block

            todo_block = compact_todo_block(todos, max_items=max_todos)
        except Exception:
            todo_block = ""
        if todo_block:
            parts.append(todo_block)

    if not parts:
        return ""
    return "\n---\n[Metis agent state on disk]\n" + "\n".join(parts) + "\n"

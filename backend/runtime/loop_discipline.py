from __future__ import annotations

import os
from collections.abc import Iterable
from pathlib import Path
from typing import Any, List, Mapping


LOOP_DISCIPLINE_PROMPT = """---
[Loop Discipline]
1. For tasks expected to take 3 or more steps, call todo_write before acting.
2. Update todo_write immediately after finishing an item; do not save all status updates for the end.
3. When new necessary work appears, add it to the todo list instead of doing it invisibly.
4. For simple 1-2 step tasks, skip todo_write and work directly.
5. For broad codebase exploration or many-file inspection, prefer delegate_explore. Keep your own reads targeted.
6. After code changes, run the most relevant verification: run_tests, verify_compilation, or the original repro command.
7. Before saying work is complete, cite the verification result. If verification fails, report that honestly.
8. When fixing a bug: reproduce first, fix, then verify the same path no longer fails.
9. To understand project structure, use generate_repo_map or the repo map already in context, then grep_search to locate symbols. Read a file with read_file only when you need its actual implementation. Do NOT read files one by one to figure out the layout — that is slow and wastes context.
10. When writing a large file (roughly 300+ lines), do it in chunks: write_file the skeleton/first part, then append_to_file the rest. A single huge write can be cut off by the model's output limit and fail.
"""

VERIFY_TOOLS = {"run_tests", "verify_compilation", "execute_bash_command"}
EDIT_TOOLS = {
    "write_file",
    "append_to_file",
    "robust_replace_in_file",
    "apply_patch",
    "editCode",
    "edit_code_ast",
    "edit_notebook",
    "rename_file_update_refs",
    "delete_file",
    "delete_directory",
}
VERIFY_EXEMPT_EXTENSIONS = {".md", ".markdown", ".json", ".jsonc", ".txt", ".yaml", ".yml"}


class VerificationTracker:
    def __init__(self) -> None:
        self.edited_paths: set[str] = set()
        self.saw_verification = False

    def record(self, tool_name: str, arguments: Mapping[str, Any]) -> None:
        if tool_name in VERIFY_TOOLS:
            self.saw_verification = True
        if tool_name not in EDIT_TOOLS:
            return
        for path in _argument_paths(arguments):
            if path:
                self.edited_paths.add(path)

    def needs_nudge(self) -> bool:
        if not _verify_nudge_enabled():
            return False
        if self.saw_verification or not self.edited_paths:
            return False
        return any(not _is_verify_exempt(path) for path in self.edited_paths)

    def nudge_text(self) -> str:
        paths = sorted(self.edited_paths)
        preview = ", ".join(paths[:5])
        if len(paths) > 5:
            preview += f", ... (+{len(paths) - 5} more)"
        return (
            f"[Verification reminder] You modified {len(paths)} file(s)"
            f"{f': {preview}' if preview else ''} but have not run verification yet. "
            "Before summarizing completion, run the most relevant check "
            "(run_tests, verify_compilation, or the repro/build command) or explicitly explain why verification is not applicable."
        )


def compact_todo_block(todos: Iterable[Mapping[str, Any]], *, max_items: int = 12) -> str:
    items = [item for item in todos if isinstance(item, Mapping)]
    if not items:
        return ""
    parts: List[str] = []
    for index, item in enumerate(items[:max_items], start=1):
        status = str(item.get("status") or "").strip().lower()
        icon = "✅" if status in {"done", "completed", "complete"} else "▶️" if status in {"in_progress", "active", "doing"} else "⬜"
        content = str(item.get("content") or item.get("task") or item.get("title") or item.get("id") or "").strip()
        if len(content) > 64:
            content = content[:61].rstrip() + "..."
        if content:
            parts.append(f"{index}.{icon} {content}")
    if len(items) > max_items:
        parts.append(f"... +{len(items) - max_items} more")
    return "[任务清单] " + "  ".join(parts) if parts else ""


def workspace_todo_block(workspace_root: str = "") -> str:
    try:
        from backend.core.memory.workspace_state import read_agent_todos

        block = compact_todo_block(read_agent_todos(workspace_root or "."))
    except Exception:
        block = ""
    return f"\n\n---\n{block}\n" if block else ""


def _verify_nudge_enabled() -> bool:
    value = os.environ.get("METIS_VERIFY_NUDGE", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _argument_paths(arguments: Mapping[str, Any]) -> Iterable[str]:
    for key in ("file_path", "path", "old_path", "new_path"):
        value = arguments.get(key)
        if isinstance(value, str):
            yield value
    for key in ("file_paths", "paths"):
        value = arguments.get(key)
        if isinstance(value, str):
            yield value
        elif isinstance(value, Iterable):
            for item in value:
                if isinstance(item, str):
                    yield item


def _is_verify_exempt(path: str) -> bool:
    suffix = Path(str(path or "")).suffix.lower()
    return suffix in VERIFY_EXEMPT_EXTENSIONS

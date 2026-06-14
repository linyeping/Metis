from __future__ import annotations

import difflib
import os
import re
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

from backend.core.memory.workspace_state import record_file_read


EDIT_TOOLS = {"write_file", "robust_replace_in_file"}
READ_TOOLS = {"read_file", "read_multiple_files"}
LINE_NUMBER_PREFIX = re.compile(r"(?m)^\s*\d+\s*→")


@dataclass
class EditSnapshot:
    tool_name: str
    path: str
    before_text: Optional[str]


class EditGuard:
    """Run-scoped read-before-edit guard plus compact diff feedback."""

    def __init__(self, workspace_root: str = "") -> None:
        self.workspace_root = Path(workspace_root or os.getcwd()).resolve(strict=False)
        self.files_read: set[str] = set()
        self._lock = threading.RLock()

    @property
    def enabled(self) -> bool:
        value = os.environ.get("METIS_EDIT_GUARD", "1").strip().lower()
        return value not in {"0", "false", "no", "off"}

    def before_execute(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        if not self.enabled:
            return ""
        if tool_name == "robust_replace_in_file":
            search_text = str(arguments.get("search_text") or arguments.get("old_string") or "")
            if LINE_NUMBER_PREFIX.search(search_text):
                return (
                    "错误：search_text 中包含 read_file 输出的行号前缀。"
                    "行号和箭头只用于定位，请去掉形如 `12→` 的前缀，"
                    "只使用文件中的精确原文重试。"
                )
        if tool_name not in EDIT_TOOLS:
            return ""
        path = self._target_path(arguments)
        if not path:
            return ""
        target = self._resolve(path)
        if tool_name == "write_file" and not target.exists():
            return ""
        if tool_name == "robust_replace_in_file" and not target.exists():
            return ""
        if self._was_read(target):
            return ""
        return (
            f"错误：你还没有读取过 {self._display_path(target)}。"
            "请先用 read_file 查看文件实际内容，再进行修改。\n"
            "这能避免基于过时记忆的错误编辑。"
        )

    def capture_before(self, tool_name: str, arguments: Dict[str, Any]) -> Optional[EditSnapshot]:
        if tool_name not in EDIT_TOOLS:
            return None
        path = self._target_path(arguments)
        if not path:
            return None
        target = self._resolve(path)
        before_text = self._read_text(target) if target.is_file() else None
        return EditSnapshot(tool_name=tool_name, path=str(target), before_text=before_text)

    def after_execute(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        result: str,
        snapshot: Optional[EditSnapshot],
    ) -> str:
        self.record_tool_result(tool_name, arguments, result)
        if snapshot is None or self._looks_like_error(result):
            return result
        target = Path(snapshot.path)
        after_text = self._read_text(target) if target.is_file() else None
        if after_text is None or after_text == snapshot.before_text:
            return result
        self._record_read(target)
        diff = self._unified_diff(target, snapshot.before_text or "", after_text)
        if not diff:
            return result
        return f"{result}\n\n已修改 {self._display_path(target)}，变更摘要：\n{diff}"

    def record_tool_result(self, tool_name: str, arguments: Dict[str, Any], result: str) -> None:
        if self._looks_like_error(result):
            return
        if tool_name == "read_file":
            self._record_argument_path(arguments)
        elif tool_name == "read_multiple_files":
            for path in self._iter_argument_paths(arguments):
                self._record_path(path)
        elif tool_name == "grep_search":
            for path in self._paths_from_grep_result(result):
                self._record_path(path)
        elif tool_name == "write_file":
            self._record_argument_path(arguments)

    def _target_path(self, arguments: Dict[str, Any]) -> str:
        return str(arguments.get("file_path") or arguments.get("path") or "").strip()

    def _record_argument_path(self, arguments: Dict[str, Any]) -> None:
        path = self._target_path(arguments)
        if path:
            self._record_path(path)

    def _iter_argument_paths(self, arguments: Dict[str, Any]) -> Iterable[str]:
        raw = arguments.get("file_paths") or arguments.get("paths") or []
        if isinstance(raw, str):
            yield raw
        elif isinstance(raw, Iterable):
            for item in raw:
                if isinstance(item, str):
                    yield item

    def _paths_from_grep_result(self, result: str) -> Iterable[str]:
        for raw_line in str(result or "").splitlines():
            line = raw_line.strip()
            if not line or line.startswith(("===", "[", "未找到", "❌")):
                continue
            match = re.match(r"^(.+)[-:]\d+[-:]", line)
            candidate = match.group(1).strip() if match else line
            if candidate and not candidate.startswith("["):
                yield candidate

    def _record_path(self, path: str) -> None:
        target = self._resolve(path)
        if not target.is_file():
            return
        self._record_read(target)

    def _record_read(self, target: Path) -> None:
        normalized = self._normalize(target)
        with self._lock:
            self.files_read.add(normalized)
        record_file_read(str(self.workspace_root), str(target))

    def _was_read(self, target: Path) -> bool:
        normalized = self._normalize(target)
        with self._lock:
            return normalized in self.files_read

    def _resolve(self, path: str | Path) -> Path:
        raw = Path(os.path.expanduser(str(path)))
        if not raw.is_absolute():
            raw = self.workspace_root / raw
        return raw.resolve(strict=False)

    def _normalize(self, path: str | Path) -> str:
        return os.path.normcase(os.path.normpath(str(self._resolve(path))))

    def _display_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.workspace_root)).replace("\\", "/")
        except ValueError:
            return str(path)

    def _read_text(self, path: Path) -> Optional[str]:
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None

    def _unified_diff(self, path: Path, before: str, after: str) -> str:
        before_lines = before.splitlines(keepends=True)
        after_lines = after.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                before_lines,
                after_lines,
                fromfile=f"{self._display_path(path)} (before)",
                tofile=f"{self._display_path(path)} (after)",
                n=3,
            )
        )
        if not diff_lines:
            return ""
        truncated = diff_lines[:50]
        if len(diff_lines) > 50:
            truncated.append(f"[diff truncated: {len(diff_lines) - 50} more lines]\n")
        return "".join(truncated).rstrip("\n")

    def _looks_like_error(self, result: str) -> bool:
        text = str(result or "").lstrip()
        if text.startswith(("❌", "Error", "错误", "[Permission denied]", "[Cancelled]")):
            return True
        return "Traceback (most recent call last)" in text

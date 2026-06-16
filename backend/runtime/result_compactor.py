"""ResultCompactor — 工具结果的策略化压缩。

替代 agent_loop.py 中分散的 ``_compact_*`` 系列函数，
提供统一的入口和可扩展的压缩策略。
"""
from __future__ import annotations

import hashlib
from typing import Dict, Optional


# ---------------------------------------------------------------------------
# 全局预算
# ---------------------------------------------------------------------------
_MAX_RESULT_CHARS = 24_000  # ~6K tokens — 与 agent_loop 保持一致


# ---------------------------------------------------------------------------
# 工具分类集合（与 agent_loop 保持一致，统一管理）
# ---------------------------------------------------------------------------
_WRITE_TOOLS = {
    "write_file", "robust_replace_in_file", "apply_patch",
    "delete_file", "edit_notebook", "todo_write",
}
_FILE_READ_TOOLS = {"read_file", "read_multiple_files"}
_SHELL_TOOLS = {"execute_bash_command", "run_tests"}
_SEARCH_TOOLS = {"grep_search", "glob_search", "semantic_search", "web_search"}
_LIST_TOOLS = {"list_directory"}
_BROWSER_TOOLS = {"browse_web", "browse_and_extract", "browser_read_page", "web_search"}


# ---------------------------------------------------------------------------
# 压缩策略映射
# ---------------------------------------------------------------------------
_STRATEGY_MAP: Dict[str, str] = {
    "load_skill": "skill_keep",
    # write / edit
    "write_file": "write_confirm",
    "robust_replace_in_file": "write_confirm",
    "apply_patch": "write_confirm",
    "delete_file": "write_confirm",
    "edit_notebook": "write_confirm",
    "todo_write": "write_confirm",
    # shell
    "execute_bash_command": "tail_heavy",
    "run_tests": "tail_heavy",
    # search
    "grep_search": "dedup_lines",
    "glob_search": "dedup_lines",
    "semantic_search": "dedup_lines",
    # file read
    "read_file": "keep_structure",
    "read_multiple_files": "keep_structure",
    # directory
    "list_directory": "tree_compact",
    # desktop
    "desktop_window_list": "json_summary",
    "desktop_win2_status": "json_summary",
    "desktop_win2_observe": "json_summary",
    "desktop_win2_action": "json_summary",
    "desktop_win2_task": "json_summary",
    "desktop_win2_verify": "json_summary",
    "preview_browser_status": "json_summary",
    "preview_browser_navigate": "json_summary",
    "preview_browser_observe": "json_summary",
    "preview_browser_action": "json_summary",
    "preview_browser_screenshot": "json_summary",
    "preview_browser_verify": "json_summary",
    # browser
    "browse_web": "browser_cap",
    "browse_and_extract": "browser_cap",
    "browser_read_page": "browser_cap",
    "web_search": "dedup_lines",
}


class ResultCompactor:
    """工具结果的智能压缩器。

    使用示例::

        compactor = ResultCompactor()
        compacted = compactor.compact("grep_search", raw_result)
    """

    def __init__(self, max_chars: int = _MAX_RESULT_CHARS):
        self._max_chars = max_chars
        self._seen_files: Dict[str, str] = {}  # path -> content_hash

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    def compact(self, tool_name: str, result: str) -> str:
        """对 *result* 应用与 *tool_name* 匹配的压缩策略。"""
        strategy_name = _STRATEGY_MAP.get(tool_name, "head_tail")
        method = getattr(self, f"_strategy_{strategy_name}", self._strategy_head_tail)

        # 先尝试文件去重（read_file 类）
        if tool_name in _FILE_READ_TOOLS:
            deduped = self._dedup_file_content(result)
            if deduped is not None:
                return deduped

        compressed = method(result)
        return self._final_cap(compressed)

    def reset_seen_files(self) -> None:
        """清空文件去重缓存（新会话时调用）。"""
        self._seen_files.clear()

    # ------------------------------------------------------------------
    # 文件去重
    # ------------------------------------------------------------------

    def _dedup_file_content(self, result: str) -> Optional[str]:
        """如果文件内容在本轮已见过且未变，返回简短提示。"""
        content_hash = hashlib.sha256(
            result.encode("utf-8", errors="replace")
        ).hexdigest()[:16]

        path = ""
        for line in result.split("\n", 5):
            stripped = line.strip()
            if stripped.startswith("=== ") and stripped.endswith(" ==="):
                path = stripped[4:-4].strip()
                break
            if stripped.startswith("File: ") or stripped.startswith("Path: "):
                path = stripped.split(": ", 1)[1].strip()
                break
        if not path:
            path = f"__unnamed_{content_hash}"

        prev_hash = self._seen_files.get(path)
        if prev_hash == content_hash:
            return (
                f"[File already in context: {path} "
                f"(unchanged, {len(result)} chars). No need to re-read.]"
            )
        self._seen_files[path] = content_hash
        return None  # 不去重，交给策略处理

    # ------------------------------------------------------------------
    # 策略实现
    # ------------------------------------------------------------------

    def _strategy_write_confirm(self, result: str) -> str:
        """写操作：只保留首行确认。"""
        if len(result) <= 500:
            return result
        first_line = result.split("\n", 1)[0]
        return (
            first_line[:300]
            + f"\n[Write confirmed. Full echo omitted ({len(result)} chars).]"
        )

    def _strategy_tail_heavy(self, result: str) -> str:
        """Shell 输出：保留更多尾部（错误和总结通常在末尾）。"""
        if len(result) <= 4_000:
            return result
        lines = result.split("\n")
        head_count = min(10, len(lines))
        tail_count = min(50, len(lines) - head_count)
        if tail_count <= 0:
            return self._strategy_head_tail(result)
        head = lines[:head_count]
        tail = lines[-tail_count:]
        omitted = len(lines) - head_count - tail_count
        return (
            "\n".join(head)
            + f"\n\n[... {omitted} lines omitted ...]\n\n"
            + "\n".join(tail)
        )

    def _strategy_dedup_lines(self, result: str) -> str:
        """搜索结果：去重 + 限行。"""
        lines = result.split("\n")
        seen: set[str] = set()
        unique: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped not in seen:
                seen.add(stripped)
                unique.append(line)
        deduped = "\n".join(unique)
        if len(deduped) <= 8_000:
            return deduped
        # 仍然超标：取前 60 行 + 省略提示
        kept = unique[:60]
        return (
            "\n".join(kept)
            + f"\n\n[... {len(unique) - 60} more unique matches omitted. "
            "Narrow your search if needed. ...]"
        )

    def _strategy_keep_structure(self, result: str) -> str:
        """文件读取：保留首尾，中间省略但保持文件结构标记。"""
        if len(result) <= self._max_chars:
            return result
        # 尝试智能截取：保留 import 区域 + 类/函数签名行 + 尾部
        lines = result.split("\n")
        structure_lines: list[str] = []
        body_lines: list[str] = []
        for line in lines:
            stripped = line.strip()
            if (
                stripped.startswith(("import ", "from ", "class ", "def ", "async def "))
                or stripped.startswith(("export ", "function ", "const ", "interface "))
                or stripped.startswith(("=== ", "--- ", "File:", "Path:"))
                or not stripped
            ):
                structure_lines.append(line)
            else:
                body_lines.append(line)

        # 如果结构行本身就够紧凑，附加尾部正文
        struct_text = "\n".join(structure_lines)
        remaining_budget = self._max_chars - len(struct_text) - 200
        if remaining_budget > 500 and body_lines:
            tail_body = "\n".join(body_lines[-40:])
            if len(tail_body) <= remaining_budget:
                return (
                    struct_text
                    + f"\n\n[... {len(body_lines) - 40} body lines omitted ...]\n\n"
                    + tail_body
                )
        # fallback
        return self._strategy_head_tail(result)

    def _strategy_tree_compact(self, result: str) -> str:
        """目录列表：限制条目数。"""
        if len(result) <= 3_000:
            return result
        lines = result.split("\n")
        if len(lines) <= 100:
            return result
        kept = lines[:80]
        return (
            "\n".join(kept)
            + f"\n\n[... {len(lines) - 80} more entries omitted ...]"
        )

    def _strategy_json_summary(self, result: str) -> str:
        """JSON 列表：只保留关键字段摘要。"""
        if len(result) <= 3_000:
            return result
        # 尝试提取 JSON 数组中的关键信息
        try:
            import json
            data = json.loads(result)
            if isinstance(data, list):
                summary_items = []
                for item in data[:30]:
                    if isinstance(item, dict):
                        # 只保留 name/title/id/hwnd 等关键字段
                        compact = {
                            k: v for k, v in item.items()
                            if k in ("name", "title", "id", "hwnd", "pid",
                                     "class_name", "status", "type", "path")
                        }
                        summary_items.append(compact)
                    else:
                        summary_items.append(item)
                text = json.dumps(summary_items, ensure_ascii=False, indent=1)
                if len(data) > 30:
                    text += f"\n\n[... {len(data) - 30} more items omitted ...]"
                return text
        except Exception:
            pass
        return self._strategy_head_tail(result)

    def _strategy_browser_cap(self, result: str) -> str:
        """浏览器页面文本：硬上限。"""
        if len(result) <= 6_000:
            return result
        return (
            result[:5_500]
            + f"\n\n[... page text truncated, {len(result) - 5500} chars omitted ...]"
        )

    def _strategy_skill_keep(self, result: str) -> str:
        """Skills are loaded as durable run instructions; keep them intact."""
        return result

    def _strategy_head_tail(self, result: str) -> str:
        """通用兜底：头 + 尾。"""
        if len(result) <= self._max_chars:
            return result
        HEAD = self._max_chars * 2 // 3
        TAIL = self._max_chars // 3
        mid_lines = result[HEAD:-TAIL].count("\n")
        mid_chars = len(result) - HEAD - TAIL
        return (
            result[:HEAD]
            + f"\n\n[... truncated {mid_lines} lines / {mid_chars} chars."
            " Re-read the source file if you need full content. ...]\n\n"
            + result[-TAIL:]
        )

    # ------------------------------------------------------------------
    # 最终上限
    # ------------------------------------------------------------------

    def _final_cap(self, result: str) -> str:
        """确保结果不超过全局上限。"""
        if len(result) <= self._max_chars:
            return result
        return self._strategy_head_tail(result)

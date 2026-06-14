from __future__ import annotations

import re
from typing import Any, Dict


def teaching_error_text(
    tool_name: str,
    arguments: Dict[str, Any] | None,
    raw_error: Any,
    *,
    workspace_root: str = "",
) -> str:
    text = str(raw_error or "").strip()
    args = arguments or {}
    target = _target_path(args)
    lower = text.lower()

    if "path_outside_workspace" in lower or "outside the active workspace" in lower:
        root = workspace_root or _extract_workspace_root(text)
        suffix = f"只能访问 {root} 内的文件。" if root else "只能访问当前工作区内的文件。"
        return f"错误：路径在工作区之外，已拒绝（Access denied）。{suffix}请改用工作区内的相对路径。"

    if "path_sensitive" in lower or "protected" in lower:
        return "错误：目标路径受保护，已拒绝访问（Access denied）。请改用普通项目文件，避免读取或写入密钥、控制配置等敏感位置。"

    if _is_timeout(lower):
        seconds = _extract_timeout_seconds(text)
        over = f"超过 {seconds} 秒" if seconds else "超时"
        return (
            f"错误：命令运行{over}被终止。"
            "如果这是 dev server、watcher 或长任务，请改用 start_long_running_process；"
            "如果是测试/构建，请缩小命令范围或提高 timeout 后重试。"
        )

    if _is_missing_file(lower):
        path_hint = f" {target}" if target else ""
        return f"错误：文件{path_hint}不存在。请用 list_directory 或 glob_search 确认路径后重试。"

    if tool_name == "robust_replace_in_file" and _is_replace_not_found(lower):
        return (
            "错误：未找到要替换的文本。文件可能已经变化，"
            "请重新 read_file 后复制文件中的精确原文重试。"
            "注意空格、缩进、换行必须完全一致，且不要包含 read_file 左侧的行号箭头。"
        )

    if tool_name == "robust_replace_in_file" and _is_replace_ambiguous(lower):
        return (
            "错误：找到多处相同文本，无法确定要替换哪一处。"
            "请扩大 search_text/old_string 的上下文，使它在文件中唯一后重试。"
        )

    if "traceback (most recent call last)" in lower:
        return (
            f"错误：工具 {tool_name} 执行失败。"
            "请根据参数和上一步结果调整调用；不要重复完全相同的失败调用。"
        )

    return text


def looks_like_tool_error(result: Any) -> bool:
    text = str(result or "").lstrip()
    if text.startswith(("❌", "Error", "错误", "[Cancelled]", "[Permission denied]")):
        return True
    head = text[:100].lower()
    return (
        "traceback (most recent call last)" in head
        or "error executing" in head
        or "timed out" in head
        or "timeout" in head
    )


def _target_path(arguments: Dict[str, Any]) -> str:
    return str(arguments.get("file_path") or arguments.get("path") or "").strip()


def _extract_workspace_root(text: str) -> str:
    match = re.search(r"active workspace (.+?)(?:\.|$)", text)
    return match.group(1).strip() if match else ""


def _extract_timeout_seconds(text: str) -> str:
    match = re.search(r"(\d+(?:\.\d+)?)\s*seconds?", text, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    match = re.search(r"(\d+)\s*s\b", text, flags=re.IGNORECASE)
    return match.group(1) if match else ""


def _is_timeout(lower: str) -> bool:
    return any(token in lower for token in ("timeoutexpired", "timeout", "timed out", "超时"))


def _is_missing_file(lower: str) -> bool:
    return any(
        token in lower
        for token in (
            "filenotfounderror",
            "no such file",
            "file not found",
            "文件不存在",
            "不是可读文件",
            "cannot find the path",
        )
    )


def _is_replace_not_found(lower: str) -> bool:
    return any(
        token in lower
        for token in (
            "未找到待替换片段",
            "未找到要替换",
            "not found",
            "0 处",
            "共 0",
            "no match",
        )
    )


def _is_replace_ambiguous(lower: str) -> bool:
    return any(
        token in lower
        for token in (
            "multiple matches",
            "not unique",
            "ambiguous",
            "多处",
            "不唯一",
            "多个匹配",
        )
    )

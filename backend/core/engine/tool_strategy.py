"""Tool strategy hint generator for prompt runtime injection.

根据当前启用的工具集动态生成策略提示，引导 LLM 更高效地选择和组合工具。
注入层名 ``tool_strategy_hint``，通过 ``prompt_runtime.py`` 编排。
"""
from __future__ import annotations

from typing import List, Optional

# ---------------------------------------------------------------------------
# 策略规则：(required_tools, hint_text)
# 只有 required_tools 全部存在于 enabled_tools 时才会输出对应 hint。
# ---------------------------------------------------------------------------

_STRATEGY_RULES: list[tuple[set[str], str]] = [
    # --- 文件探索 ---
    (
        {"read_file", "generate_repo_map"},
        "File exploration: Use generate_repo_map first to get project structure, "
        "then read_file for specific files. Avoid reading files one by one.",
    ),
    # --- 搜索优先 ---
    (
        {"grep_search", "read_file"},
        "Code search: Prefer grep_search to locate references, then read_file for context. "
        "Don't read entire files to find a function — search first.",
    ),
    # --- 编辑策略 ---
    (
        {"write_file", "robust_replace_in_file"},
        "File editing: Prefer robust_replace_in_file for surgical patches on existing files. "
        "Use write_file only for new files or complete rewrites.",
    ),
    # --- Shell / 测试 ---
    (
        {"execute_bash_command"},
        "Shell commands: Run tests after making changes. Check exit codes carefully. "
        "Combine related commands with && into a single call to reduce steps.",
    ),
    # --- 桌面自动化 ---
    (
        {"desktop_win2_status", "desktop_win2_observe"},
        "Desktop automation: Prefer the Window2-style flow first: "
        "desktop_win2_status -> desktop_win2_observe -> desktop_win2_action, "
        "or desktop_win2_task for multi-step app workflows. Fall back to legacy "
        "desktop_vision_task only when Window2 cannot resolve or capture the target.",
    ),
    (
        {"desktop_window_list", "desktop_screenshot"},
        "Desktop automation: Use desktop_window_list -> desktop_window_capture -> "
        "desktop_window_action for targeted window operations. "
        "Use desktop_screenshot only for full-screen overview.",
    ),
    # --- 并行读取 ---
    (
        {"read_multiple_files"},
        "Batch reading: When you need 2+ files, use read_multiple_files in a single call "
        "instead of issuing multiple read_file calls.",
    ),
    # --- glob 优先于 shell find ---
    (
        {"glob_search", "execute_bash_command"},
        "File search: Prefer glob_search over shell find commands — it's faster and "
        "doesn't require spawning a subprocess.",
    ),
    # --- Web 浏览 ---
    (
        {"browse_web", "browse_and_extract"},
        "Web browsing: Use browse_and_extract when you already know the URL and what to extract. "
        "Use browse_web only when interactive navigation or form-filling is required.",
    ),
]


def generate_tool_strategy(enabled_tools: List[str]) -> str:
    """根据当前启用的工具集生成策略提示文本。

    Parameters
    ----------
    enabled_tools:
        工具名列表，例如 ``["read_file", "grep_search", ...]``

    Returns
    -------
    str
        Markdown 格式的策略块。如果没有匹配的规则，返回空字符串。
    """
    tool_set = set(enabled_tools)
    hints: list[str] = []
    for required, hint_text in _STRATEGY_RULES:
        if required.issubset(tool_set):
            hints.append(hint_text)
    if not hints:
        return ""
    return (
        "## Tool Usage Strategy\n\n"
        + "\n".join(f"- {h}" for h in hints)
    )


def tool_strategy_block(enabled_tools: Optional[List[str]] = None) -> str:
    """供 prompt_runtime 调用的高层接口。

    如果 enabled_tools 为 None 则尝试从全局注册表获取。
    """
    if enabled_tools is None:
        try:
            from backend.runtime.tool_registry import get_registry
            registry = get_registry(include_mcp=False)
            enabled_tools = list(registry.list_tools().keys())
        except Exception:
            return ""
    text = generate_tool_strategy(enabled_tools)
    if not text:
        return ""
    return "\n\n---\n[" + text + "]\n"

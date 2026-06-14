"""按文件名 / 路径模式查找文件（Glob）。"""
from pathlib import Path
from typing import List

from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError, validate_search_scope
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


def _cursor_style_glob_pattern(pattern: str) -> str:
    """C 习惯：纯文件名型模式（不含路径分隔符）自动加 **/ 以递归。"""
    p = pattern.strip()
    if not p or p.startswith("**/") or "/" in p:
        return p
    return "**/" + p


@trace_execution
def glob_search(
    pattern: str,
    root: str = ".",
    max_results: int = 200,
) -> str:
    """
    在 root 下匹配文件路径。支持 pathlib 风格，如 `**/*.py`、`*.md`。
    与 C 对齐：形如 `*.py` 的模式会自动变为 `**/*.py` 以递归搜索。

    Args:
        pattern: glob 模式。
        root: 搜索根目录。
        max_results: 超过则截断并提示。
    """
    try:
        pattern = _cursor_style_glob_pattern(pattern)
        try:
            base = validate_search_scope(root)
        except PathSecurityError as e:
            return str(e)
        if not base.exists():
            return f"❌ 根目录不存在: {root}"

        paths: List[Path] = []
        iterator = base.glob(pattern) if "**" in pattern else base.rglob(pattern)
        for p in iterator:
            if p.is_file():
                paths.append(p)
                if len(paths) >= max_results:
                    break

        if not paths:
            return f"未找到匹配模式 '{pattern}' 的文件 (根: {base})"

        rel_strs = []
        for p in sorted(paths):
            try:
                rel_strs.append(str(p.relative_to(base)))
            except ValueError:
                rel_strs.append(str(p))

        msg = f"=== Glob 匹配: {pattern} (根: {base}) ===\n" + "\n".join(rel_strs)
        if len(paths) >= max_results:
            msg += f"\n\n[结果已达上限 {max_results} 条，已截断]"
        return msg
    except Exception as e:
        return f"❌ Glob 搜索失败: {str(e)}"

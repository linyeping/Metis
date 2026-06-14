"""精确/模糊/AST 多层降级替换（robust_replace_in_file）。"""
from pathlib import Path

from backend.core.memory.workspace_state import has_file_been_read
from backend.tools.coding.foundation.core_mechanisms.fallback_manager import fallback_manager
from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError, safe_path_for_read
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def robust_replace_in_file(
    file_path: str,
    search_text: str,
    replace_text: str,
    *,
    replace_all: bool = False,
) -> str:
    """
    增强版代码替换 - 使用多层降级策略
    对应文档 03: 异常处理与降级策略

    降级链: 精确匹配 → 模糊匹配 → AST 编辑

    replace_all=True 时走整文件字面量全局替换（用于重命名等），不触发降级链。
    """
    try:
        safe_fp = safe_path_for_read(file_path)
    except PathSecurityError as e:
        return str(e)
    file_path = str(safe_fp)

    if not has_file_been_read("", file_path):
        return (
            "⚠️ You are modifying a file you haven't read yet in this session. "
            "This violates Principle #1 (Look up, don't guess). "
            "Please use read_file first to understand the current state, then retry this edit.\n\n"
            "To proceed anyway, call this tool again — the guard is advisory."
        )

    if replace_all:
        try:
            p = Path(file_path)
            if not p.is_file():
                return f"❌ 文件不存在: {file_path}"
            text = p.read_text(encoding="utf-8")
            if search_text == replace_text:
                return "❌ search_text 与 replace_text 相同，无需替换"
            n = text.count(search_text)
            if n == 0:
                return f"❌ 未找到待替换片段（共 0 处）: {file_path!r}"
            p.write_text(text.replace(search_text, replace_text), encoding="utf-8")
            return f"✅ replace_all 完成: {file_path}（{n} 处替换）"
        except Exception as e:
            return f"❌ replace_all 失败: {e}"

    result = fallback_manager.execute_with_fallback(file_path, search_text, replace_text)

    if result["success"]:
        strategy = result.get("strategy_used", "unknown")
        attempts = result.get("attempts", 1)
        message = result.get("message", "")

        return f"✅ {message}\n策略: {strategy} (尝试 {attempts} 次)\n请立即验证修改！"
    else:
        error = result.get("error", "未知错误")
        attempts = result.get("attempts", 0)
        suggestion = result.get("suggestion", "")

        return f"❌ 替换失败: {error}\n已尝试 {attempts} 种策略\n建议: {suggestion}"

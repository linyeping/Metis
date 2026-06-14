"""单文件内符号重命名（词边界替换，非语义级；复杂场景请用 IDE/rope）。"""
import os
import re

from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError, safe_path_for_read
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def rename_symbol(
    file_path: str,
    old_name: str,
    new_name: str,
    *,
    word_boundary: bool = True,
) -> str:
    """
    在单个文件中将 old_name 替换为 new_name。

    警告：基于正则词边界，可能误伤字符串/注释中的同名片段；请先备份。
    """
    try:
        safe_fp = safe_path_for_read(file_path)
    except PathSecurityError as e:
        return str(e)
    file_path = str(safe_fp)
    if not os.path.isfile(file_path):
        return f"❌ 文件不存在: {file_path}"
    if not old_name or old_name == new_name:
        return "❌ old_name 无效或与 new_name 相同"

    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()

        if word_boundary:
            pattern = re.compile(r"\b" + re.escape(old_name) + r"\b")
        else:
            pattern = re.compile(re.escape(old_name))

        new_text, n = pattern.subn(new_name, text)
        if n == 0:
            return f"⚠️ 未找到符号 {old_name!r}"

        with open(file_path, "w", encoding="utf-8", newline="\n") as f:
            f.write(new_text)

        return f"✅ 已在 {file_path} 替换 {n} 处: {old_name} → {new_name}\n请运行测试与 linter 验证。"
    except Exception as e:
        return f"❌ rename_symbol 失败: {str(e)}"

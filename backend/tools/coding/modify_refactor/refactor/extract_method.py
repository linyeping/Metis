"""从选中代码生成可粘贴的新函数骨架（辅助手工提取，非全自动重构）。"""
from typing import Optional

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def extract_method(
    source_snippet: str,
    new_function_name: str,
    *,
    indent: str = "    ",
    self_prefix: bool = True,
) -> str:
    """
    根据给定代码块生成「新函数定义 + 原位置调用占位」文本，由开发者粘贴调整。

    不做 AST 级移动，避免破坏作用域；适合快速起草提取结果。
    """
    if not source_snippet.strip():
        return "❌ source_snippet 为空"
    if not new_function_name.isidentifier():
        return f"❌ {new_function_name!r} 不是合法函数名"

    lines = source_snippet.rstrip().splitlines()
    body = "\n".join(indent + (ln if ln.strip() else "") for ln in lines)
    first = f"def {new_function_name}(self):\n" if self_prefix else f"def {new_function_name}():\n"
    func_block = first + body + "\n"
    call_line = f"{indent}{new_function_name}()\n"
    return (
        "📋 建议提取结果（请人工核对参数与 self）：\n\n"
        f"{func_block}\n"
        "———— 原位置可替换为 ————\n"
        f"{call_line}"
    )

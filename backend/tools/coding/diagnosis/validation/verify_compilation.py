"""Python 语法 / py_compile 校验（与 legacy check_syntax 一致）。"""
import ast
import os
import py_compile

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def verify_compilation(file_path: str) -> str:
    """ast.parse + py_compile"""
    if not file_path.endswith(".py"):
        return "⚠️ 仅支持 Python 文件"
    if not os.path.exists(file_path):
        return f"❌ 文件不存在: {file_path}"
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            ast.parse(f.read())
        py_compile.compile(file_path, doraise=True)
        return f"✅ 编译检查通过: {file_path}"
    except SyntaxError as e:
        return f"❌ 语法错误 (行 {e.lineno}): {e.msg}"
    except Exception as e:
        return f"❌ 检查异常: {str(e)}"

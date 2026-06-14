"""简易圈复杂度估计：按 AST 分支计数（Python）。"""
import ast
import os

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


def _complexity(node: ast.AST) -> int:
    base = 0
    for child in ast.iter_child_nodes(node):
        if isinstance(child, (ast.If, ast.For, ast.While, ast.Try, ast.With)):
            base += 1
        if isinstance(child, ast.BoolOp):
            base += len(child.values) - 1
        base += _complexity(child)
    return base


@trace_execution
def analyze_complexity(file_path: str) -> str:
    """对单个 .py 文件输出函数级复杂度估计。"""
    if not file_path.endswith(".py"):
        return "⚠️ 当前仅支持 Python 文件"
    if not os.path.isfile(file_path):
        return f"❌ 文件不存在: {file_path}"
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            tree = ast.parse(f.read())
        lines = [f"📊 复杂度估计: {file_path}"]
        for node in tree.body:
            if isinstance(node, ast.FunctionDef):
                c = 1 + _complexity(node)
                lines.append(f"  def {node.name}: 估计复杂度 ≈ {c}")
            elif isinstance(node, ast.ClassDef):
                for sub in node.body:
                    if isinstance(sub, ast.FunctionDef):
                        c = 1 + _complexity(sub)
                        lines.append(f"  {node.name}.{sub.name}: 估计复杂度 ≈ {c}")
        if len(lines) == 1:
            lines.append("  （未找到顶层函数/类方法）")
        return "\n".join(lines)
    except SyntaxError as e:
        return f"❌ 语法错误: {e}"
    except Exception as e:
        return f"❌ 分析失败: {str(e)}"

"""递归查看目录树。"""
import os

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def list_directory(dir_path: str = ".", max_depth: int = 2) -> str:
    """列出目录结构"""
    if not os.path.exists(dir_path):
        return f"❌ 错误：目录 {dir_path} 不存在"

    result = f"=== 目录结构: {os.path.abspath(dir_path)} ===\n"

    for root, dirs, files in os.walk(dir_path):
        depth = root.replace(dir_path, '').count(os.sep)
        if depth > max_depth:
            continue

        # 过滤隐藏目录
        dirs[:] = [d for d in dirs if not d.startswith('.') and d not in
                  ['__pycache__', 'node_modules', 'venv', 'env']]

        indent = "  " * depth
        result += f"{indent}{os.path.basename(root)}/\n"

        for file in files:
            result += f"{indent}  {file}\n"

    return result

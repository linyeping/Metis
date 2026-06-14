"""跨文件文本搜索（findstr / grep），与旧版 search_in_files 行为一致。"""
import os
import subprocess

from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError, validate_search_scope
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def search_in_files(pattern: str, file_pattern: str = "*.py", dir_path: str = ".") -> str:
    """在文件中搜索模式（类似 grep）"""
    try:
        try:
            abs_dir = validate_search_scope(dir_path)
        except PathSecurityError as e:
            return str(e)
        dir_path = str(abs_dir)

        # Windows 兼容性处理
        if os.name == 'nt':
            # Windows 使用 findstr
            cmd = f'findstr /s /n /i "{pattern}" {dir_path}\\{file_pattern}'
        else:
            # Linux/Mac 使用 grep
            cmd = f'grep -rn "{pattern}" --include="{file_pattern}" {dir_path}'

        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=30)
        output = result.stdout.strip()

        if output:
            # 限制结果数量
            lines = output.split('\n')
            if len(lines) > 50:
                output = '\n'.join(lines[:50])
                output += f"\n\n[结果过多，仅显示前 50 条]"
            return f"=== 搜索结果: '{pattern}' ===\n{output}"
        else:
            return f"未找到匹配 '{pattern}' 的内容"
    except subprocess.TimeoutExpired:
        return "❌ 搜索超时（30s）"
    except Exception as e:
        return f"❌ 搜索失败: {str(e)}"

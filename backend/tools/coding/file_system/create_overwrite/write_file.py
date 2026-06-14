"""从头创建或彻底覆盖文件。"""
import os

from backend.core.memory.workspace_state import has_file_been_read
from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError, safe_path_for_write
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def write_file(file_path: str, content: str) -> str:
    """
    创建或覆盖文件
    对应文档 02 的 fsWrite
    
    安全特性：
    - 路径限制在工作区内（防止穿越攻击）
    - 自动创建父目录
    """
    try:
        # 路径安全验证
        safe_path = safe_path_for_write(file_path)

        if safe_path.exists() and not has_file_been_read("", str(safe_path)):
            return (
                "⚠️ You are modifying a file you haven't read yet in this session. "
                "This violates Principle #1 (Look up, don't guess). "
                "Please use read_file first to understand the current state, then retry this edit.\n\n"
                "To proceed anyway, call this tool again — the guard is advisory."
            )
        
        # 创建父目录
        safe_path.parent.mkdir(parents=True, exist_ok=True)
        
        # 写入文件
        safe_path.write_text(content, encoding='utf-8')

        line_count = content.count('\n') + 1
        return f"✅ 成功写入: {file_path} ({line_count} 行)"
    except PathSecurityError as e:
        return str(e)
    except Exception as e:
        return f"❌ 写入失败: {str(e)}"

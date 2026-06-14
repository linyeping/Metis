"""删除单个文件（非目录）。"""
import os

from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError, safe_path_for_delete
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def delete_file(
    path: str,
    *,
    missing_ok: bool = False,
) -> str:
    """
    删除普通文件。
    
    安全特性：
    - 路径限制在工作区内（防止穿越攻击）
    - 拒绝删除目录（使用 delete_directory）
    """
    try:
        # 路径安全验证（如果 missing_ok，则允许不存在）
        if missing_ok:
            from backend.tools.coding.foundation.core_mechanisms.path_security import validate_path
            try:
                safe_path, _ = validate_path(path, must_exist=False, allow_create=False)
            except PathSecurityError as e:
                return str(e)
            
            if not safe_path.exists():
                return f"⚠️ 文件不存在（已忽略）: {path}"
        else:
            safe_path = safe_path_for_delete(path)
        
        # 检查是否是目录
        if safe_path.is_dir():
            return f"❌ 路径是目录，请使用 delete_directory: {path}"
        
        # 删除文件
        safe_path.unlink()
        return f"✅ 删除文件成功: {path}"
    except PathSecurityError as e:
        return str(e)
    except Exception as e:
        return f"❌ 删除失败: {str(e)}"

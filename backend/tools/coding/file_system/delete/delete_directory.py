"""递归删除目录。"""
import os
import shutil
from typing import Callable, Optional, Sequence

from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError, validate_path
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def delete_directory(
    path: str,
    *,
    recursive: bool = True,
    missing_ok: bool = False,
    ignore_errors: bool = False,
    onerror: Optional[Callable[..., None]] = None,
    exclude_names: Optional[Sequence[str]] = None,
) -> str:
    """删除目录。exclude_names 为顶层子目录名黑名单（仅当 recursive 且路径为要删根时一层过滤）。"""
    try:
        try:
            if missing_ok:
                safe_root, _ = validate_path(path, must_exist=False, allow_create=False)
                if not safe_root.exists():
                    return f"⚠️ 目录不存在（已忽略）: {path}"
            else:
                safe_root, _ = validate_path(path, must_exist=True, allow_create=False)
        except PathSecurityError as e:
            return str(e)

        path = str(safe_root)
        if not os.path.isdir(path):
            return f"❌ 不是目录: {path}"

        if recursive and exclude_names:
            for name in os.listdir(path):
                if name in exclude_names:
                    continue
                sub = os.path.join(path, name)
                if os.path.isdir(sub):
                    shutil.rmtree(sub, ignore_errors=ignore_errors, onerror=onerror)
                else:
                    try:
                        os.remove(sub)
                    except OSError:
                        if not ignore_errors:
                            raise
            return f"✅ 已删除目录内容（已排除 {list(exclude_names)}）: {path}"

        if recursive:
            shutil.rmtree(path, ignore_errors=ignore_errors, onerror=onerror)
            return f"✅ 递归删除目录成功: {path}"
        os.rmdir(path)
        return f"✅ 删除空目录成功: {path}"
    except Exception as e:
        return f"❌ 删除目录失败: {str(e)}"

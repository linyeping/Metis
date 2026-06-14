"""工作区与环境信息注入块（与 legacy get_workspace_info 一致）。"""
import os
import platform
import sys

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def populate_steering(workspace: str = ".") -> str:
    try:
        root = os.path.abspath(workspace)
        info = ["📊 steering / 工作区上下文:"]
        info.append(f"  工作区路径: {root}")
        info.append(f"  Python: {sys.version.split()[0]}")
        info.append(f"  操作系统: {platform.system()} {platform.release()}")
        info.append(f"  处理器: {platform.processor()}")
        try:
            info.append(f"  用户: {os.getlogin()}")
        except OSError:
            pass
        try:
            import shutil
            total, used, free = shutil.disk_usage(root if os.path.isdir(root) else ".")
            info.append(
                f"  磁盘: {used // (2**30)}GB / {total // (2**30)}GB (可用 {free // (2**30)}GB)"
            )
        except Exception:
            pass
        return "\n".join(info)
    except Exception as e:
        return f"❌ populate_steering 失败: {str(e)}"

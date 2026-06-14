"""从 .backups 恢复最近一次备份（与 legacy restore_backup 一致）。"""
import os
import shutil

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def undo_last_edit(file_path: str, backup_path: str = None) -> str:
    """将 file_path 恢复为指定备份或同名的最新 .backups 条目。"""
    try:
        if backup_path is None:
            backup_dir = "./.backups"
            if not os.path.exists(backup_dir):
                return f"❌ 备份目录不存在: {backup_dir}"
            filename = os.path.basename(file_path)
            backups = [f for f in os.listdir(backup_dir) if f.startswith(filename)]
            if not backups:
                return f"❌ 未找到 {filename} 的备份"
            backups.sort(reverse=True)
            backup_path = os.path.join(backup_dir, backups[0])

        if not os.path.exists(backup_path):
            return f"❌ 备份文件不存在: {backup_path}"

        shutil.copy2(backup_path, file_path)
        return f"✅ 恢复成功: {backup_path} → {file_path}"
    except Exception as e:
        return f"❌ 恢复失败: {str(e)}"

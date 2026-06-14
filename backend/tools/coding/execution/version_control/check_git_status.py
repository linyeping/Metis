"""Git 工作区快照。"""
import subprocess

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def check_git_status(cwd: str = ".") -> str:
    """git status --short"""
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            capture_output=True,
            text=True,
            timeout=30,
            cwd=cwd,
        )
        if result.returncode != 0:
            return "⚠️ 当前目录不是 Git 仓库或 Git 未安装"
        output = result.stdout.strip()
        if not output:
            return "📊 Git 状态: 工作区干净，无更改"
        return f"📊 Git 状态:\n{output}"
    except Exception as e:
        return f"❌ 获取 Git 状态失败: {str(e)}"

"""git add / commit / push 组合（PR 需在托管平台网页完成或用 gh CLI）。"""
import subprocess
from typing import Optional

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def git_commit_pr(
    message: str,
    cwd: str = ".",
    remote: str = "origin",
    branch: Optional[str] = None,
    push: bool = True,
) -> str:
    """
    执行 git add -A、git commit、可选 git push。
    创建 PR 需已安装 `gh` 时可扩展；当前仅返回 push 结果说明。
    """
    try:
        r = subprocess.run(
            ["git", "add", "-A"],
            capture_output=True,
            text=True,
            timeout=60,
            cwd=cwd,
        )
        if r.returncode != 0:
            return f"❌ git add 失败: {(r.stderr or r.stdout).strip()}"

        r2 = subprocess.run(
            ["git", "commit", "-m", message],
            capture_output=True,
            text=True,
            timeout=120,
            cwd=cwd,
        )
        if r2.returncode != 0:
            out = (r2.stderr or r2.stdout).strip()
            if "nothing to commit" in out.lower():
                return "⚠️ 没有可提交的更改"
            return f"❌ git commit 失败: {out}"

        if not push:
            return f"✅ 已提交（未 push）: {message}"

        args = ["git", "push", remote]
        if branch:
            args.append(branch)
        r3 = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=180,
            cwd=cwd,
        )
        if r3.returncode != 0:
            return f"⚠️ 提交成功但 push 失败: {(r3.stderr or r3.stdout).strip()}"

        return f"✅ 已提交并 push: {message}\n（PR 请在 GitHub/GitLab 上从当前分支发起）"
    except Exception as e:
        return f"❌ git_commit_pr 异常: {str(e)}"

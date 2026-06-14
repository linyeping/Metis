"""Enhanced git workflow helpers."""
from __future__ import annotations

import subprocess
from typing import List

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def git_diff(staged: bool = False, file_path: str = "", cwd: str = ".") -> str:
    """Show working tree or staged git diff."""
    command = ["git", "diff"]
    if staged:
        command.append("--cached")
    if file_path:
        command.extend(["--", file_path])
    return _run_git(command, cwd, "git diff", empty="✅ No changes" + (" (staged)" if staged else " (working tree)"), limit=5000)


@trace_execution
def git_stage(files: List[str], cwd: str = ".") -> str:
    """Stage specific files for commit."""
    if not files:
        return "❌ No files specified"
    result = _run(["git", "add", "--"] + [str(item) for item in files], cwd, 30)
    if result.returncode != 0:
        return f"❌ git add failed: {(result.stderr or result.stdout).strip()}"
    return f"✅ Staged {len(files)} file(s): {', '.join(str(item) for item in files)}"


@trace_execution
def git_create_branch(branch_name: str, cwd: str = ".") -> str:
    """Create and switch to a new branch."""
    result = _run(["git", "checkout", "-b", branch_name], cwd, 15)
    if result.returncode != 0:
        return f"❌ Failed to create branch: {(result.stderr or result.stdout).strip()}"
    return f"✅ Created and switched to branch: {branch_name}"


@trace_execution
def git_log(count: int = 5, oneline: bool = True, cwd: str = ".") -> str:
    """Show recent commit history."""
    command = ["git", "log", f"-{max(1, int(count))}"]
    if oneline:
        command.append("--oneline")
    return _run_git(command, cwd, "git log", empty="No commits yet", limit=5000)


def _run_git(command: List[str], cwd: str, label: str, empty: str, limit: int) -> str:
    try:
        result = _run(command, cwd, 30)
    except Exception as exc:
        return f"❌ {label} failed: {exc}"
    if result.returncode != 0:
        return f"❌ {label} failed: {(result.stderr or result.stdout).strip()}"
    output = result.stdout.strip()
    return output[-limit:] if output else empty


def _run(command: List[str], cwd: str, timeout: int) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True, timeout=timeout, cwd=cwd)

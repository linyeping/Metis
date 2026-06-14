"""Undo an uncommitted edit to a file using git restore/checkout."""
from __future__ import annotations

import os
import subprocess

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def undo_edit(file_path: str, cwd: str = ".") -> str:
    """Revert a file to its last committed state."""
    abs_path = os.path.abspath(os.path.join(cwd, file_path))
    if not os.path.exists(abs_path):
        return f"❌ File not found: {file_path}"

    try:
        status = subprocess.run(
            ["git", "status", "--porcelain", "--", file_path],
            capture_output=True,
            text=True,
            timeout=10,
            cwd=cwd,
        )
        if status.returncode != 0:
            return f"❌ git status failed: {(status.stderr or status.stdout).strip()}"
        if not status.stdout.strip():
            return f"✅ No changes to undo in {file_path}"

        result = subprocess.run(
            ["git", "checkout", "--", file_path],
            capture_output=True,
            text=True,
            timeout=15,
            cwd=cwd,
        )
        if result.returncode != 0:
            return f"❌ Undo failed: {(result.stderr or result.stdout).strip()}"
        return f"✅ Reverted {file_path} to last committed state"
    except Exception as exc:
        return f"❌ undo_edit failed: {exc}"

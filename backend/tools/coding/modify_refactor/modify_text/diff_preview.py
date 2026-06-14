"""Preview file edits as unified diffs without applying them."""
from __future__ import annotations

import difflib
import os

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def diff_preview(
    file_path: str,
    new_content: str = "",
    old_text: str = "",
    new_text: str = "",
    context_lines: int = 3,
) -> str:
    """Show a unified diff for a proposed edit without modifying the file."""
    abs_path = os.path.abspath(file_path)
    if not os.path.isfile(abs_path):
        return f"❌ File not found: {file_path}"

    try:
        with open(abs_path, "r", encoding="utf-8") as handle:
            original = handle.read()
    except OSError as exc:
        return f"❌ Cannot read file: {exc}"

    if new_content:
        modified = new_content
    elif old_text and new_text:
        if old_text not in original:
            return f"❌ old_text not found in {file_path}"
        modified = original.replace(old_text, new_text, 1)
    else:
        return "❌ Provide either new_content or old_text + new_text"

    diff_text = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            modified.splitlines(keepends=True),
            fromfile=f"a/{file_path}",
            tofile=f"b/{file_path}",
            n=max(0, int(context_lines)),
        )
    )
    if not diff_text:
        return "✅ No changes - file content is identical."

    added = sum(1 for line in diff_text.splitlines() if line.startswith("+") and not line.startswith("+++"))
    removed = sum(1 for line in diff_text.splitlines() if line.startswith("-") and not line.startswith("---"))
    return f"📋 Diff preview for {file_path} (+{added} -{removed} lines):\n\n{diff_text}"

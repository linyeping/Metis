"""加载项目工作流指南（.cursor/rules、AGENTS.md 等）。"""
import os
from pathlib import Path

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def load_workflow_guidelines(
    workspace: str = ".",
    max_chars: int = 12000,
) -> str:
    root = Path(workspace)
    candidates = list(root.glob(".cursor/rules/**/*.md"))
    for name in ("AGENTS.md", "CONTRIBUTING.md", "WORKFLOW.md"):
        p = root / name
        if p.is_file():
            candidates.append(p)

    chunks = []
    total = 0
    for p in sorted(set(candidates))[:20]:
        try:
            text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if total + len(text) > max_chars:
            text = text[: max_chars - total] + "\n... (截断)"
        chunks.append(f"=== {p} ===\n{text}")
        total += len(text)
        if total >= max_chars:
            break

    if not chunks:
        return "📭 未找到 .cursor/rules 下 md 或 AGENTS.md 等指南文件"
    return "\n\n".join(chunks)

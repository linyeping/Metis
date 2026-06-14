"""Read and update the METIS.md project memory file."""
from __future__ import annotations

import os
from typing import Optional

from backend.core.paths import legacy_miro_path, metis_path
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution

METIS_MD_FILENAME = "METIS.md"
MIRO_MD_FILENAME = "MIRO.md"


def _memory_path(scope: str, *, legacy: bool = False) -> str:
    if scope == "global":
        return str(legacy_miro_path(MIRO_MD_FILENAME) if legacy else metis_path(METIS_MD_FILENAME))
    return os.path.join(os.getcwd(), MIRO_MD_FILENAME if legacy else METIS_MD_FILENAME)


def _readable_memory_path(scope: str) -> str:
    primary = _memory_path(scope)
    legacy = _memory_path(scope, legacy=True)
    if os.path.isfile(primary) or not os.path.isfile(legacy):
        return primary
    return legacy


@trace_execution
def read_project_memory(scope: str = "project") -> str:
    """Read the current METIS.md content, falling back to MIRO.md."""
    path = _readable_memory_path(scope)
    if not os.path.isfile(path):
        return f"📝 No METIS.md found at {path}. Use update_project_memory to create one."

    try:
        with open(path, "r", encoding="utf-8") as handle:
            content = handle.read()
        return f"📝 {os.path.basename(path)} ({path}):\n\n{content}"
    except OSError as exc:
        return f"❌ Failed to read project memory: {exc}"


@trace_execution
def update_project_memory(
    content: str,
    mode: str = "append",
    section: Optional[str] = None,
    scope: str = "project",
) -> str:
    """Update the project or global METIS.md memory file."""
    path = _memory_path(scope)
    if scope == "global":
        os.makedirs(os.path.dirname(path), exist_ok=True)

    try:
        existing = ""
        read_path = path if os.path.isfile(path) else _memory_path(scope, legacy=True)
        if os.path.isfile(read_path):
            with open(read_path, "r", encoding="utf-8") as handle:
                existing = handle.read()

        if mode == "replace":
            new_content = content
        elif mode == "section":
            if not section:
                return "❌ mode='section' requires a section header"
            new_content = _replace_section(existing, section, content)
        else:
            prefix = existing
            if prefix and not prefix.endswith("\n"):
                prefix += "\n"
            new_content = f"{prefix}\n{content}\n"

        with open(path, "w", encoding="utf-8") as handle:
            handle.write(new_content)

        line_count = new_content.count("\n") + 1
        return f"✅ METIS.md updated ({line_count} lines) -> {path}"
    except OSError as exc:
        return f"❌ Failed to update METIS.md: {exc}"


def _replace_section(text: str, header: str, new_content: str) -> str:
    """Replace a Markdown section, appending it if absent."""
    lines = text.split("\n")
    header_text = header.strip()
    header_key = header_text.lower()
    header_level = len(header_text) - len(header_text.lstrip("#"))
    if header_level <= 0:
        header_level = 2
        header_text = f"## {header_text}"
        header_key = header_text.lower()

    result = []
    i = 0
    replaced = False
    while i < len(lines):
        line = lines[i]
        stripped = line.strip().lower()
        if stripped == header_key:
            result.append(header_text)
            result.append(new_content)
            replaced = True
            i += 1
            while i < len(lines):
                next_line = lines[i].strip()
                if next_line.startswith("#"):
                    next_level = len(next_line) - len(next_line.lstrip("#"))
                    if next_level <= header_level:
                        break
                i += 1
            continue
        result.append(line)
        i += 1

    if not replaced:
        if result and result[-1].strip():
            result.append("")
        result.append(header_text)
        result.append(new_content)

    return "\n".join(result)

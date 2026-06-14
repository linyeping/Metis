# -*- coding: utf-8 -*-
"""常见 CLI 是否在 PATH 中（只读）。"""

from __future__ import annotations

import shutil
from typing import Any

DEFAULT_CANDIDATES = (
    "python",
    "python3",
    "pip",
    "git",
    "node",
    "npm",
    "npx",
    "cursor",
    "code",
    "wt",
    "pwsh",
    "powershell",
    "ffmpeg",
    "gh",
)

INSTALL_HINTS: dict[str, str] = {
    "python": "winget install --id Python.Python.3.12",
    "python3": "winget install --id Python.Python.3.12",
    "pip": "winget install --id Python.Python.3.12",
    "git": "winget install --id Git.Git",
    "node": "winget install --id OpenJS.NodeJS.LTS",
    "npm": "winget install --id OpenJS.NodeJS.LTS",
    "npx": "winget install --id OpenJS.NodeJS.LTS",
    "cursor": "winget install --id Anysphere.Cursor",
    "code": "winget install --id Microsoft.VisualStudioCode",
    "wt": "winget install --id Microsoft.WindowsTerminal",
    "pwsh": "winget install --id Microsoft.PowerShell",
    "ffmpeg": "winget install --id Gyan.FFmpeg",
    "gh": "winget install --id GitHub.cli",
}


def scan_cli_candidates(names: tuple[str, ...] | None = None) -> dict[str, Any]:
    names = names or DEFAULT_CANDIDATES
    found: dict[str, str | None] = {}
    for name in names:
        path = shutil.which(name)
        found[name] = path
    missing = [k for k, v in found.items() if not v]
    return {
        "candidates": list(names),
        "resolved": {k: v for k, v in found.items() if v},
        "missing": missing,
        "install_hints": {name: INSTALL_HINTS[name] for name in missing if name in INSTALL_HINTS},
    }

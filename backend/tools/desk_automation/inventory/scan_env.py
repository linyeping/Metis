# -*- coding: utf-8 -*-
"""常用路径与环境变量快照。"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any


def snapshot_environment() -> dict[str, Any]:
    """关键路径 + 环境变量子集（隐藏敏感 token/key）。"""
    sensitive = {"key", "token", "secret", "password", "credential", "auth"}

    def _safe(k: str) -> bool:
        lk = k.lower()
        return not any(s in lk for s in sensitive)

    env_safe = {k: v for k, v in sorted(os.environ.items()) if _safe(k)}

    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))
    return {
        "platform": sys.platform,
        "python": sys.executable,
        "python_version": sys.version.split()[0],
        "home": str(home),
        "desktop": str(home / "Desktop"),
        "documents": str(home / "Documents"),
        "downloads": str(home / "Downloads"),
        "cwd": os.getcwd(),
        "path_dirs": os.environ.get("PATH", "").split(os.pathsep),
        "env_count": len(env_safe),
        "env_subset": dict(list(env_safe.items())[:60]),
    }

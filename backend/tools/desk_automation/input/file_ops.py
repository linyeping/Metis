# -*- coding: utf-8 -*-
"""文件辅助操作：复制到约定临时目录、打开文件管理器定位等。"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any


TEMP_SHARE = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~")) / ".miro" / "tmp"


def prepare_for_upload(*paths: str) -> dict[str, Any]:
    """把给定文件复制到 ~/.miro/tmp/ 下，返回绝对路径列表（供粘贴给别的 AI 或拖拽）。"""
    TEMP_SHARE.mkdir(parents=True, exist_ok=True)
    copied: list[str] = []
    errors: list[str] = []
    for p in paths:
        src = Path(p)
        if not src.is_file():
            errors.append(f"not_found: {p}")
            continue
        dst = TEMP_SHARE / src.name
        shutil.copy2(src, dst)
        copied.append(str(dst))
    return {"copied": copied, "errors": errors, "dir": str(TEMP_SHARE)}


def open_in_explorer(path: str) -> dict[str, Any]:
    """打开文件管理器并选中文件（Windows）。"""
    if sys.platform != "win32":
        return {"ok": False, "reason": "windows_only"}
    target = Path(path)
    if target.is_file():
        subprocess.Popen(["explorer", "/select,", str(target)])
    elif target.is_dir():
        subprocess.Popen(["explorer", str(target)])
    else:
        return {"ok": False, "reason": "not_found"}
    return {"ok": True, "path": str(target)}

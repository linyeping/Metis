# -*- coding: utf-8 -*-
"""Windows 增强：窗口列表、进程列表、开始菜单快捷方式。"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any


def list_visible_windows() -> list[dict[str, Any]]:
    """枚举可见窗口的 PID + 标题（PowerShell，无第三方依赖）。"""
    if sys.platform != "win32":
        return []
    ps = (
        "Get-Process | Where-Object {$_.MainWindowTitle -ne ''} "
        "| Select-Object Id, ProcessName, MainWindowTitle "
        "| ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode != 0:
            return []
        import json

        data = json.loads(r.stdout)
        if isinstance(data, dict):
            data = [data]
        return [
            {"pid": w.get("Id"), "name": w.get("ProcessName"), "title": w.get("MainWindowTitle")}
            for w in data
            if w.get("MainWindowTitle")
        ]
    except Exception:
        return []


def list_running_processes(top_n: int = 80) -> list[dict[str, Any]]:
    """按内存降序返回前 N 个进程（不需管理员权限）。"""
    if sys.platform != "win32":
        return []
    ps = (
        f"Get-Process | Sort-Object WorkingSet64 -Descending | Select-Object -First {top_n} "
        "Id, ProcessName, @{N='MemMB';E={[math]::Round($_.WorkingSet64/1MB,1)}} "
        "| ConvertTo-Json -Compress"
    )
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if r.returncode != 0:
            return []
        import json

        data = json.loads(r.stdout)
        if isinstance(data, dict):
            data = [data]
        return [{"pid": p.get("Id"), "name": p.get("ProcessName"), "mem_mb": p.get("MemMB")} for p in data]
    except Exception:
        return []


def list_start_menu_shortcuts() -> list[str]:
    """开始菜单 .lnk 列表——快速了解用户常用 GUI 软件。"""
    out: list[str] = []
    dirs = [
        Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
    ]
    for d in dirs:
        if not d.is_dir():
            continue
        for p in d.rglob("*.lnk"):
            out.append(p.stem)
    return sorted(set(out))

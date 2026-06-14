# -*- coding: utf-8 -*-
"""Windows：从注册表 Uninstall 枚举已安装程序（只读）。"""

from __future__ import annotations

import sys
from typing import Any


def _scan_windows() -> list[dict[str, str]]:
    import winreg

    out: list[dict[str, str]] = []
    roots: list[tuple[int, str]] = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
        ),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]

    for hive, path in roots:
        try:
            key = winreg.OpenKey(hive, path)
        except OSError:
            continue
        i = 0
        while True:
            try:
                sub_name = winreg.EnumKey(key, i)
            except OSError:
                break
            i += 1
            try:
                sk = winreg.OpenKey(key, sub_name)
                try:
                    disp = winreg.QueryValueEx(sk, "DisplayName")[0]
                except OSError:
                    winreg.CloseKey(sk)
                    continue
                if not disp or not isinstance(disp, str):
                    winreg.CloseKey(sk)
                    continue
                row: dict[str, str] = {"name": disp.strip()}
                try:
                    ver = winreg.QueryValueEx(sk, "DisplayVersion")[0]
                    if isinstance(ver, str):
                        row["version"] = ver
                except OSError:
                    pass
                try:
                    pub = winreg.QueryValueEx(sk, "Publisher")[0]
                    if isinstance(pub, str):
                        row["publisher"] = pub
                except OSError:
                    pass
                out.append(row)
                winreg.CloseKey(sk)
            except OSError:
                continue
        winreg.CloseKey(key)

    # 按名称去重（保留首次）
    seen: set[str] = set()
    dedup: list[dict[str, str]] = []
    for r in sorted(out, key=lambda x: x["name"].lower()):
        k = r["name"].lower()
        if k in seen:
            continue
        seen.add(k)
        dedup.append(r)
    return dedup


def scan_installed_software() -> dict[str, Any]:
    """返回 { platform, programs: [...] }。"""
    if sys.platform != "win32":
        return {
            "platform": sys.platform,
            "programs": [],
            "note": "当前仅实现 Windows 注册表枚举；其他平台可接 brew list / dpkg 等。",
        }
    progs = _scan_windows()
    return {"platform": "win32", "programs": progs, "count": len(progs)}

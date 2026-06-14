# -*- coding: utf-8 -*-
"""启动前环境感知（交接 part_07 §5）：桌面快捷方式、任务栏固定、常见安装路径。"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

# ─── 辅助 ───


def _user_profile() -> Path:
    return Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))


def _lnk_names_in(dir_path: Path, *, limit: int = 80) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not dir_path.is_dir():
        return out
    try:
        for p in sorted(dir_path.glob("*.lnk"))[:limit]:
            name = p.stem
            out.append({"name": name, "path": str(p.resolve()), "kind": "shortcut"})
    except OSError:
        pass
    return out


def _scan_desktop_shortcuts() -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for base in (
        _user_profile() / "Desktop",
        _user_profile() / "OneDrive" / "Desktop",
        Path(os.environ.get("PUBLIC") or (_user_profile().parent / "Public")) / "Desktop",
    ):
        items.extend(_lnk_names_in(base))
    # 去重 name
    seen: set[str] = set()
    uniq: list[dict[str, Any]] = []
    for it in items:
        k = it["name"].lower()
        if k in seen:
            continue
        seen.add(k)
        uniq.append(it)
    return uniq[:100]


def _scan_taskbar_pins() -> list[dict[str, Any]]:
    r"""任务栏固定项：Quick Launch\User Pinned\TaskBar 下的 .lnk。"""
    appdata = os.environ.get("APPDATA", "")
    if not appdata:
        return []
    tb = Path(appdata) / "Microsoft" / "Internet Explorer" / "Quick Launch" / "User Pinned" / "TaskBar"
    return _lnk_names_in(tb, limit=40)


def _known_install_paths() -> list[dict[str, Any]]:
    """探测常见 IDE/浏览器可执行文件是否存在。"""
    pf = os.environ.get("ProgramFiles", r"C:\Program Files")
    pf86 = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    local = os.environ.get("LOCALAPPDATA", "")

    candidates: list[tuple[str, Path]] = [
        ("Cursor", Path(local) / "Programs" / "cursor" / "Cursor.exe"),
        ("Visual Studio Code", Path(local) / "Programs" / "Microsoft VS Code" / "Code.exe"),
        ("Google Chrome", Path(pf) / "Google" / "Chrome" / "Application" / "chrome.exe"),
        ("Google Chrome", Path(pf86) / "Google" / "Chrome" / "Application" / "chrome.exe"),
        ("Microsoft Edge", Path(pf) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
        ("Firefox", Path(pf) / "Mozilla Firefox" / "firefox.exe"),
        ("WeChat", Path(pf) / "Tencent" / "WeChat" / "WeChat.exe"),
        ("Weixin", Path(pf) / "Tencent" / "Weixin" / "Weixin.exe"),
        ("Notepad++", Path(pf) / "Notepad++" / "notepad++.exe"),
        ("Windows Terminal", Path(pf) / "Windows Terminal" / "wt.exe"),
    ]

    found: list[dict[str, Any]] = []
    seen_name: set[str] = set()
    for label, exe in candidates:
        if label in seen_name:
            continue
        try:
            if exe.is_file():
                found.append(
                    {
                        "name": label,
                        "exe": str(exe),
                        "kind": "installed",
                        "hint": "可能在开始菜单或任务栏固定中启动",
                    }
                )
                seen_name.add(label)
        except OSError:
            continue
    return found


def startup_scan() -> dict[str, Any]:
    """同步扫描当前用户环境，返回结构化结果（可 JSON 序列化）。"""
    t0 = time.time()
    desktop = _scan_desktop_shortcuts()
    taskbar = _scan_taskbar_pins()
    known = _known_install_paths()
    return {
        "scanned_at": time.time(),
        "elapsed_sec": round(time.time() - t0, 3),
        "desktop_shortcuts": desktop,
        "taskbar_pins": taskbar,
        "known_apps_detected": known,
        "summary_counts": {
            "desktop": len(desktop),
            "taskbar": len(taskbar),
            "known": len(known),
        },
    }


def format_env_for_prompt(scan: dict[str, Any] | None = None) -> str:
    """拼成可注入第一条 User/系统上下文的 Markdown 块。"""
    if scan is None:
        scan = startup_scan()
    lines = [
        "## 环境感知（启动扫描，§5）",
        "",
        f"- 桌面快捷方式（.lnk）约 {scan['summary_counts']['desktop']} 个",
        f"- 任务栏固定约 {scan['summary_counts']['taskbar']} 个",
        f"- 探测到的常见安装 {scan['summary_counts']['known']} 个",
        "",
        "### 桌面快捷方式（名称 → 路径）",
    ]
    for it in scan.get("desktop_shortcuts", [])[:25]:
        lines.append(f"  - **{it['name']}** → `{it['path']}`")
    lines.append("")
    lines.append("### 任务栏固定")
    for it in scan.get("taskbar_pins", [])[:20]:
        lines.append(f"  - **{it['name']}** → `{it['path']}`")
    lines.append("")
    lines.append("### 本机探测到的应用")
    for it in scan.get("known_apps_detected", []):
        lines.append(f"  - **{it['name']}** — `{it['exe']}`")
    lines.append("")
    lines.append(
        "决策提示：若任务需要编辑器/浏览器，优先从上述列表选择对应图标；"
        "任务栏亮起或预览通常表示进程已在运行。"
    )
    return "\n".join(lines)


def startup_scan_json(scan: dict[str, Any] | None = None) -> str:
    """供 API/日志落盘的紧凑 JSON。"""
    if scan is None:
        scan = startup_scan()
    return json.dumps(scan, ensure_ascii=False, indent=2)

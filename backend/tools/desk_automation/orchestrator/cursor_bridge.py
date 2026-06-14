# -*- coding: utf-8 -*-
"""Cursor 自动化桥接：监控 Cursor 窗口、自动输入提示词、等待完成。

工作方式:
1. 通过窗口标题定位 Cursor 窗口
2. 截图判断当前状态（等待输入/正在运行/出错）
3. 必要时自动输入下一步提示词并回车
4. 轮询等待 Cursor 完成后继续下一步

注意：所有键鼠操作前都检查 config.assert_automation_allowed()。
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Any

from .. import config


def find_cursor_window() -> dict[str, Any]:
    """尝试定位 Cursor 主窗口。返回 {found, title, pid}。"""
    try:
        from ..inventory.scan_windows import list_visible_windows
        windows = list_visible_windows()
        for w in windows:
            title_lower = w.get("title", "").lower()
            if "cursor" in title_lower and ("agent" in title_lower or ".py" in title_lower or ".ts" in title_lower or "-" in w.get("title", "")):
                return {"found": True, "title": w["title"], "pid": w.get("pid", 0)}
        for w in windows:
            if "cursor" in w.get("title", "").lower():
                return {"found": True, "title": w["title"], "pid": w.get("pid", 0)}
    except Exception:
        pass
    return {"found": False, "title": "", "pid": 0}


def focus_cursor_window() -> dict[str, Any]:
    """将 Cursor 窗口置为前台。"""
    config.assert_automation_allowed()
    info = find_cursor_window()
    if not info["found"]:
        return {"ok": False, "error": "未找到 Cursor 窗口"}
    try:
        import ctypes
        import ctypes.wintypes as wt

        user32 = ctypes.windll.user32
        EnumWindows = user32.EnumWindows
        GetWindowTextW = user32.GetWindowTextW
        SetForegroundWindow = user32.SetForegroundWindow

        target_title = info["title"]
        hwnd_found = [None]

        @ctypes.WINFUNCTYPE(ctypes.c_bool, wt.HWND, wt.LPARAM)
        def enum_cb(hwnd, _):
            buf = ctypes.create_unicode_buffer(256)
            GetWindowTextW(hwnd, buf, 256)
            if buf.value == target_title:
                hwnd_found[0] = hwnd
                return False
            return True

        EnumWindows(enum_cb, 0)
        if hwnd_found[0]:
            SetForegroundWindow(hwnd_found[0])
            time.sleep(0.3)
            return {"ok": True, "title": target_title}
    except Exception as e:
        return {"ok": False, "error": str(e)}
    return {"ok": False, "error": "SetForegroundWindow failed"}


def send_prompt_to_cursor(prompt: str, press_enter: bool = True) -> dict[str, Any]:
    """向 Cursor 输入框发送提示词。

    流程: 聚焦窗口 → Ctrl+L 打开 chat → 输入文字 → Enter
    """
    config.assert_automation_allowed()
    from ..input.actions import press_key, type_text

    focus_result = focus_cursor_window()
    if not focus_result.get("ok"):
        return focus_result

    time.sleep(0.3)
    press_key("ctrl+l")
    time.sleep(0.5)

    type_text(prompt, interval=0.01)
    time.sleep(0.2)

    if press_enter:
        press_key("enter")
        time.sleep(0.3)

    return {"ok": True, "prompt_sent": prompt[:80] + ("..." if len(prompt) > 80 else "")}


def wait_cursor_idle(timeout: int = 300, poll_interval: float = 3.0) -> dict[str, Any]:
    """轮询等待 Cursor 完成当前操作。

    判断逻辑: 截图两次对比 → 如果画面不再变化且无 loading 指示则视为 idle。
    简化版: 检查窗口标题中是否包含 spinner 字样或 通过固定间隔等待。
    """
    start = time.time()
    prev_title = ""
    stable_count = 0

    while time.time() - start < timeout:
        if config.is_paused():
            return {"ok": False, "reason": "paused"}

        info = find_cursor_window()
        cur_title = info.get("title", "")

        if cur_title == prev_title:
            stable_count += 1
        else:
            stable_count = 0
        prev_title = cur_title

        if stable_count >= 3:
            return {"ok": True, "waited_sec": round(time.time() - start, 1), "final_title": cur_title}

        time.sleep(poll_interval)

    return {"ok": False, "reason": "timeout", "waited_sec": timeout}


def cursor_is_available() -> bool:
    """Cursor 可执行文件是否存在。"""
    common_paths = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "cursor" / "Cursor.exe",
        Path(os.environ.get("LOCALAPPDATA", "")) / "cursor" / "Cursor.exe",
    ]
    for p in common_paths:
        if p.is_file():
            return True
    try:
        result = subprocess.run(["where", "cursor"], capture_output=True, text=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def launch_cursor(workspace: str = ".") -> dict[str, Any]:
    """启动 Cursor 打开指定工作空间。"""
    if not cursor_is_available():
        return {"ok": False, "error": "Cursor not found"}
    try:
        subprocess.Popen(["cursor", workspace], shell=True)
        time.sleep(2)
        return {"ok": True, "workspace": workspace}
    except Exception as e:
        return {"ok": False, "error": str(e)}

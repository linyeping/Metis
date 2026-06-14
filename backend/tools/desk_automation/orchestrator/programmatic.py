# -*- coding: utf-8 -*-
"""skill 模式（exec_mode=skill，OpenClaw 式）：优先用 API/CLI/Win32，不跑 human 视觉链。

与 human 模式的区别:
- **human**: OCR → 帧差节流 → 局部/全图多模态 → 键鼠坐标
- **skill**: 解析意图 → 直接调 API/CLI/Win32（本模块）；后续可接 browser 扩展等

本模块提供一组"程序化执行器"，每个对应一类应用:
- 浏览器 → 调 PowerShell Start-Process 或 webbrowser 模块
- 文件管理 → shutil / os / subprocess
- 应用启动 → Start-Process / subprocess
- 文本编辑 → 直接写文件
- 系统设置 → 注册表 / PowerShell cmdlet
- 剪贴板 → PowerShell Set-Clipboard / Get-Clipboard
- Cursor/IDE → CLI 命令或 cursor_bridge

当"auto"模式时，先尝试程序化；如果任务无法程序化完成，降级到视觉模式。
"""

from __future__ import annotations

import os
import re
import subprocess
import webbrowser
from pathlib import Path
from typing import Any



# ─── 程序化执行结果 ───

class ProgramResult:
    def __init__(self, ok: bool, method: str, detail: str = "", fallback_to_vision: bool = False):
        self.ok = ok
        self.method = method
        self.detail = detail
        self.fallback_to_vision = fallback_to_vision

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "method": self.method,
            "detail": self.detail,
            "fallback_to_vision": self.fallback_to_vision,
        }


# ─── 意图→程序化执行路由 ───

def try_programmatic(goal: str, intent: str) -> ProgramResult:
    """尝试程序化完成任务。返回结果或标记需要降级到视觉模式。"""

    goal_lower = goal.lower()

    if intent == "navigate":
        return _try_navigate(goal, goal_lower)

    if intent == "search":
        return _try_search(goal, goal_lower)

    if intent == "transfer":
        return _try_transfer(goal, goal_lower)

    if intent == "create":
        return _try_create(goal, goal_lower)

    if intent == "configure":
        return _try_configure(goal, goal_lower)

    return ProgramResult(False, "none", "无对应的程序化方案", fallback_to_vision=True)


# ─── 导航类 ───

_APP_COMMANDS: dict[str, str | list[str]] = {
    "记事本": "notepad",
    "notepad": "notepad",
    "计算器": "calc",
    "calculator": "calc",
    "画图": "mspaint",
    "paint": "mspaint",
    "cmd": "cmd",
    "powershell": "powershell",
    "terminal": "powershell",
    "终端": "powershell",
    "资源管理器": "explorer",
    "explorer": "explorer",
    "文件管理器": "explorer",
    "控制面板": "control",
    "任务管理器": "taskmgr",
    "设置": "ms-settings:",
    "settings": "ms-settings:",
}


def _try_navigate(goal: str, gl: str) -> ProgramResult:
    for name, cmd in _APP_COMMANDS.items():
        if name in gl:
            try:
                if cmd.endswith(":"):
                    os.startfile(cmd)
                else:
                    subprocess.Popen(cmd, shell=True)
                return ProgramResult(True, "start_process", f"已启动 {cmd}")
            except Exception as e:
                return ProgramResult(False, "start_process", str(e))

    url_match = re.search(r'https?://\S+', goal)
    if url_match:
        webbrowser.open(url_match.group())
        return ProgramResult(True, "open_url", f"已打开 {url_match.group()}")

    web_keywords = ["网页", "网站", "浏览器", "browser", "百度", "google", "bing"]
    if any(kw in gl for kw in web_keywords):
        query = re.sub(r'(打开|搜索|查找|去|用|浏览器|网页|百度|google)', '', goal).strip()
        if query:
            webbrowser.open(f"https://www.bing.com/search?q={query}")
            return ProgramResult(True, "web_search", f"已搜索: {query}")
        else:
            webbrowser.open("https://www.bing.com")
            return ProgramResult(True, "open_browser", "已打开浏览器")

    if "cursor" in gl:
        try:
            subprocess.Popen("cursor .", shell=True)
            return ProgramResult(True, "start_cursor", "已启动 Cursor")
        except Exception:
            pass

    if "文件" in gl or "目录" in gl or "文件夹" in gl or "folder" in gl:
        path_match = re.search(r'[A-Za-z]:\\[^\s"]+|~/[^\s"]+|/[^\s"]+', goal)
        if path_match:
            target = path_match.group()
            if os.path.exists(target):
                os.startfile(target)
                return ProgramResult(True, "open_path", f"已打开 {target}")

    return ProgramResult(False, "none", fallback_to_vision=True)


# ─── 搜索类 ───

def _try_search(goal: str, gl: str) -> ProgramResult:
    if any(kw in gl for kw in ["文件", "file"]):
        query = re.sub(r'(搜索|查找|找|文件|file)', '', goal).strip()
        if query:
            try:
                result = subprocess.run(
                    ["powershell", "-NoProfile", "-Command",
                     f"Get-ChildItem -Recurse -Filter '*{query}*' -ErrorAction SilentlyContinue | Select-Object -First 20 FullName"],
                    capture_output=True, text=True, timeout=15, encoding="utf-8", errors="replace",
                )
                return ProgramResult(True, "file_search", result.stdout[:500] or "未找到")
            except Exception as e:
                return ProgramResult(False, "file_search", str(e))

    url_match = re.search(r'https?://\S+', goal)
    if not url_match:
        query = re.sub(r'(搜索|查找|找|search|look for)', '', goal).strip()
        if query:
            webbrowser.open(f"https://www.bing.com/search?q={query}")
            return ProgramResult(True, "web_search", f"已搜索: {query}")

    return ProgramResult(False, "none", fallback_to_vision=True)


# ─── 文件操作类 ───

def _try_transfer(goal: str, gl: str) -> ProgramResult:
    paths = re.findall(r'[A-Za-z]:\\[^\s"]+|~/[^\s"]+', goal)

    if ("复制" in gl or "copy" in gl) and len(paths) >= 2:
        src, dst = paths[0], paths[1]
        try:
            import shutil
            if os.path.isdir(src):
                shutil.copytree(src, dst, dirs_exist_ok=True)
            else:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
            return ProgramResult(True, "file_copy", f"{src} → {dst}")
        except Exception as e:
            return ProgramResult(False, "file_copy", str(e))

    if ("移动" in gl or "move" in gl) and len(paths) >= 2:
        src, dst = paths[0], paths[1]
        try:
            import shutil
            shutil.move(src, dst)
            return ProgramResult(True, "file_move", f"{src} → {dst}")
        except Exception as e:
            return ProgramResult(False, "file_move", str(e))

    if ("删除" in gl or "delete" in gl) and paths:
        target = paths[0]
        try:
            import shutil
            if os.path.isdir(target):
                shutil.rmtree(target)
            elif os.path.isfile(target):
                os.remove(target)
            return ProgramResult(True, "file_delete", f"已删除 {target}")
        except Exception as e:
            return ProgramResult(False, "file_delete", str(e))

    return ProgramResult(False, "none", fallback_to_vision=True)


# ─── 创建类 ───

def _try_create(goal: str, gl: str) -> ProgramResult:
    if "文件夹" in gl or "目录" in gl or "folder" in gl or "directory" in gl:
        path_match = re.search(r'[A-Za-z]:\\[^\s"]+|~/[^\s"]+', goal)
        if path_match:
            try:
                os.makedirs(path_match.group(), exist_ok=True)
                return ProgramResult(True, "mkdir", f"已创建 {path_match.group()}")
            except Exception as e:
                return ProgramResult(False, "mkdir", str(e))

    if "文件" in gl or "file" in gl:
        path_match = re.search(r'[A-Za-z]:\\[^\s"]+|~/[^\s"]+', goal)
        if path_match:
            try:
                p = Path(path_match.group())
                p.parent.mkdir(parents=True, exist_ok=True)
                if not p.exists():
                    p.write_text("", encoding="utf-8")
                return ProgramResult(True, "create_file", f"已创建 {p}")
            except Exception as e:
                return ProgramResult(False, "create_file", str(e))

    return ProgramResult(False, "none", fallback_to_vision=True)


# ─── 配置类 ───

def _try_configure(goal: str, gl: str) -> ProgramResult:
    if "音量" in gl or "volume" in gl:
        try:
            subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Start-Process ms-settings:sound"],
                timeout=5, capture_output=True,
            )
            return ProgramResult(True, "open_settings", "已打开声音设置")
        except Exception:
            pass

    if "壁纸" in gl or "wallpaper" in gl or "桌面背景" in gl:
        try:
            os.startfile("ms-settings:personalization-background")
            return ProgramResult(True, "open_settings", "已打开壁纸设置")
        except Exception:
            pass

    if "wifi" in gl or "网络" in gl or "network" in gl:
        try:
            os.startfile("ms-settings:network")
            return ProgramResult(True, "open_settings", "已打开网络设置")
        except Exception:
            pass

    if "显示" in gl or "display" in gl or "分辨率" in gl:
        try:
            os.startfile("ms-settings:display")
            return ProgramResult(True, "open_settings", "已打开显示设置")
        except Exception:
            pass

    return ProgramResult(False, "none", fallback_to_vision=True)


# ─── 剪贴板 ───

def clipboard_set(text: str) -> ProgramResult:
    try:
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"Set-Clipboard -Value @'\n{text}\n'@"],
            timeout=5, capture_output=True,
        )
        return ProgramResult(True, "clipboard_set", f"{len(text)} chars")
    except Exception as e:
        return ProgramResult(False, "clipboard_set", str(e))


def clipboard_get() -> str:
    try:
        r = subprocess.run(
            ["powershell", "-NoProfile", "-Command", "Get-Clipboard"],
            capture_output=True, text=True, timeout=5, encoding="utf-8", errors="replace",
        )
        return r.stdout.strip()
    except Exception:
        return ""


# ─── 通用 shell ───

def run_shell(cmd: str, timeout: int = 30) -> ProgramResult:
    try:
        r = subprocess.run(
            cmd, shell=True, capture_output=True, text=True,
            timeout=timeout, encoding="utf-8", errors="replace",
        )
        out = r.stdout[-500:] if r.stdout else ""
        err = r.stderr[-200:] if r.stderr else ""
        detail = f"exit={r.returncode}\n{out}\n{err}".strip()
        return ProgramResult(r.returncode == 0, "shell", detail)
    except subprocess.TimeoutExpired:
        return ProgramResult(False, "shell", f"超时 ({timeout}s)")
    except Exception as e:
        return ProgramResult(False, "shell", str(e))

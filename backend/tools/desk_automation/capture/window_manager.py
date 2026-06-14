# -*- coding: utf-8 -*-
"""窗口级操作管理器 —— 纯 ctypes 实现，不依赖 pywinauto / pywin32。

提供类似 Codex Computer Use 的窗口中心 API：
  - list_windows()        枚举可见窗口
  - get_window(hwnd)      获取单窗口详情
  - activate_window(hwnd) 激活/前台化窗口
  - capture_window(hwnd)  窗口级截图（支持被遮挡窗口）
  - window_to_screen()    窗口坐标 → 屏幕坐标
  - screen_to_window()    屏幕坐标 → 窗口坐标

设计参考 Codex 的 @oai/sky Window2 API，但使用 Python 原生实现。
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wt
import io
import logging
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 仅 Windows
# ---------------------------------------------------------------------------
if sys.platform != "win32":
    raise ImportError("window_manager requires Windows")

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
gdi32 = ctypes.windll.gdi32
dwmapi = ctypes.windll.dwmapi

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
SW_RESTORE = 9
SW_SHOW = 5
GWL_STYLE = -16
GWL_EXSTYLE = -20
WS_VISIBLE = 0x10000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_MINIMIZE = 0x20000000
GA_ROOTOWNER = 3

SRCCOPY = 0x00CC0020
DIB_RGB_COLORS = 0
BI_RGB = 0
PW_CLIENTONLY = 0x1
PW_RENDERFULLCONTENT = 0x2  # Windows 8.1+

DWMWA_EXTENDED_FRAME_BOUNDS = 9
DWMWA_CLOAKED = 14


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------
@dataclass
class WindowInfo:
    """窗口信息，对标 Codex 的 Window 类型。"""
    hwnd: int
    title: str
    class_name: str
    pid: int
    rect: Dict[str, int]  # {left, top, width, height}
    is_minimized: bool = False
    is_foreground: bool = False
    exe_name: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "hwnd": self.hwnd,
            "title": self.title,
            "class_name": self.class_name,
            "pid": self.pid,
            "rect": self.rect,
            "is_minimized": self.is_minimized,
            "is_foreground": self.is_foreground,
            "exe_name": self.exe_name,
        }


# ---------------------------------------------------------------------------
# 辅助：DPI 感知
# ---------------------------------------------------------------------------
_dpi_set = False


def _ensure_dpi_aware() -> None:
    global _dpi_set
    if _dpi_set:
        return
    _dpi_set = True
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 辅助：窗口属性
# ---------------------------------------------------------------------------
def _get_window_text(hwnd: int) -> str:
    length = user32.GetWindowTextLengthW(hwnd)
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return buf.value


def _get_class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def _get_window_pid(hwnd: int) -> int:
    pid = wt.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def _get_exe_name(pid: int) -> str:
    """通过 PID 获取进程名（无需 pywin32）。"""
    PROCESS_QUERY_LIMITED = 0x1000
    try:
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED, False, pid)
        if not h:
            return ""
        buf = ctypes.create_unicode_buffer(512)
        size = wt.DWORD(512)
        ok = kernel32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        kernel32.CloseHandle(h)
        if ok:
            import os
            return os.path.basename(buf.value)
    except Exception:
        pass
    return ""


def _get_window_rect(hwnd: int) -> Dict[str, int]:
    """获取窗口矩形（优先用 DWM 扩展矩形避免阴影）。"""
    rect = wt.RECT()

    # 优先 DwmGetWindowAttribute 获取精确边界（不含阴影）
    try:
        hr = dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_EXTENDED_FRAME_BOUNDS,
            ctypes.byref(rect), ctypes.sizeof(rect)
        )
        if hr == 0:
            return {
                "left": rect.left, "top": rect.top,
                "width": rect.right - rect.left,
                "height": rect.bottom - rect.top,
            }
    except Exception:
        pass

    # 回退到 GetWindowRect
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return {
        "left": rect.left, "top": rect.top,
        "width": rect.right - rect.left,
        "height": rect.bottom - rect.top,
    }


def _is_cloaked(hwnd: int) -> bool:
    """检查窗口是否被 DWM cloaked（虚拟桌面等）。"""
    val = ctypes.c_int(0)
    try:
        dwmapi.DwmGetWindowAttribute(
            hwnd, DWMWA_CLOAKED, ctypes.byref(val), ctypes.sizeof(val)
        )
    except Exception:
        return False
    return val.value != 0


def _is_real_window(hwnd: int) -> bool:
    """判断是否是用户可见的"真实"窗口（过滤工具窗口等）。"""
    if not user32.IsWindowVisible(hwnd):
        return False
    if _is_cloaked(hwnd):
        return False

    title = _get_window_text(hwnd)
    if not title.strip():
        return False

    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    ex_style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)

    # 跳过工具窗口（除非显式标记为 AppWindow）
    if (ex_style & WS_EX_TOOLWINDOW) and not (ex_style & WS_EX_APPWINDOW):
        return False

    # 检查根所有者是否是自身（Alt-Tab 可见性规则）
    root = user32.GetAncestor(hwnd, GA_ROOTOWNER)
    if root != hwnd:
        # 如果根所有者不是自己，且根所有者可见，跳过
        if user32.IsWindowVisible(root):
            root_title = _get_window_text(root)
            if root_title:
                return False

    return True


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------
def list_windows(include_minimized: bool = True) -> List[WindowInfo]:
    """枚举所有可见的顶层窗口。

    类似 Codex sky.list_windows()，返回 WindowInfo 列表。
    """
    _ensure_dpi_aware()

    WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
    results: List[WindowInfo] = []
    fg_hwnd = user32.GetForegroundWindow()

    def _callback(hwnd: int, _lparam: int) -> bool:
        if not _is_real_window(hwnd):
            return True

        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        is_min = bool(style & WS_MINIMIZE)

        if not include_minimized and is_min:
            return True

        pid = _get_window_pid(hwnd)
        rect = _get_window_rect(hwnd)

        # 跳过零尺寸窗口（系统后台窗口）
        if rect["width"] <= 0 and rect["height"] <= 0 and not is_min:
            return True

        results.append(WindowInfo(
            hwnd=hwnd,
            title=_get_window_text(hwnd),
            class_name=_get_class_name(hwnd),
            pid=pid,
            rect=rect,
            is_minimized=is_min,
            is_foreground=(hwnd == fg_hwnd),
            exe_name=_get_exe_name(pid),
        ))
        return True

    user32.EnumWindows(WNDENUMPROC(_callback), 0)
    return results


def get_window(hwnd: int) -> Optional[WindowInfo]:
    """获取单个窗口的详细信息。

    类似 Codex sky.get_window({ id })。
    """
    _ensure_dpi_aware()

    if not user32.IsWindow(hwnd):
        return None

    pid = _get_window_pid(hwnd)
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)

    return WindowInfo(
        hwnd=hwnd,
        title=_get_window_text(hwnd),
        class_name=_get_class_name(hwnd),
        pid=pid,
        rect=_get_window_rect(hwnd),
        is_minimized=bool(style & WS_MINIMIZE),
        is_foreground=(hwnd == user32.GetForegroundWindow()),
        exe_name=_get_exe_name(pid),
    )


def find_window(title_pattern: str) -> Optional[WindowInfo]:
    """按标题子串查找窗口（大小写不敏感）。"""
    pattern = title_pattern.lower()
    for w in list_windows():
        if pattern in w.title.lower():
            return w
    return None


def activate_window(hwnd: int) -> bool:
    """将窗口置为前台（激活 + 还原最小化）。

    类似 Codex sky.activate_window()。
    """
    if not user32.IsWindow(hwnd):
        return False

    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    if style & WS_MINIMIZE:
        user32.ShowWindow(hwnd, SW_RESTORE)

    # AllowSetForegroundWindow + SetForegroundWindow
    try:
        user32.AllowSetForegroundWindow(ctypes.c_ulong(-1))  # ASFW_ANY
    except Exception:
        pass

    result = user32.SetForegroundWindow(hwnd)
    if not result:
        # 备用方案：模拟 Alt 键绕过前台窗口限制
        try:
            user32.keybd_event(0x12, 0, 0, 0)  # Alt down
            user32.SetForegroundWindow(hwnd)
            user32.keybd_event(0x12, 0, 2, 0)  # Alt up
            result = True
        except Exception:
            pass

    return bool(result)


def capture_window(hwnd: int, *, client_only: bool = False) -> Optional[bytes]:
    """窗口级截图 —— 使用 PrintWindow API，支持截取被遮挡的窗口。

    类似 Codex 的 get_window_state(include_screenshot=true)。
    Windows.Graphics.Capture 需要 WinRT，这里用 PrintWindow 作为替代，
    同样可以截取被其他窗口遮挡的内容。

    Args:
        hwnd: 窗口句柄。
        client_only: True 只截客户区（不含标题栏/边框）。

    Returns:
        PNG bytes，失败返回 None。
    """
    _ensure_dpi_aware()

    if not user32.IsWindow(hwnd):
        return None

    # 还原最小化窗口
    style = user32.GetWindowLongW(hwnd, GWL_STYLE)
    was_minimized = bool(style & WS_MINIMIZE)
    if was_minimized:
        user32.ShowWindow(hwnd, SW_RESTORE)
        import time
        time.sleep(0.3)

    rect = _get_window_rect(hwnd)
    w, h = rect["width"], rect["height"]
    if w <= 0 or h <= 0:
        return None

    # 创建兼容 DC 和位图
    hwnd_dc = user32.GetWindowDC(hwnd) if not client_only else user32.GetDC(hwnd)
    if not hwnd_dc:
        return None

    mem_dc = gdi32.CreateCompatibleDC(hwnd_dc)
    bmp = gdi32.CreateCompatibleBitmap(hwnd_dc, w, h)
    old_bmp = gdi32.SelectObject(mem_dc, bmp)

    # PrintWindow — 可截取被遮挡窗口
    flags = PW_RENDERFULLCONTENT  # Windows 8.1+: 完整渲染
    if client_only:
        flags |= PW_CLIENTONLY

    ok = user32.PrintWindow(hwnd, mem_dc, flags)
    if not ok:
        # 回退到不带 PW_RENDERFULLCONTENT
        flags = PW_CLIENTONLY if client_only else 0
        ok = user32.PrintWindow(hwnd, mem_dc, flags)

    if not ok:
        # 最终回退：BitBlt（不能截被遮挡的，但更兼容）
        gdi32.BitBlt(mem_dc, 0, 0, w, h, hwnd_dc, 0, 0, SRCCOPY)

    # 读取位图数据
    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wt.DWORD),
            ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long),
            ("biPlanes", wt.WORD),
            ("biBitCount", wt.WORD),
            ("biCompression", wt.DWORD),
            ("biSizeImage", wt.DWORD),
            ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long),
            ("biClrUsed", wt.DWORD),
            ("biClrImportant", wt.DWORD),
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = w
    bmi.biHeight = -h  # 负值 = top-down
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = BI_RGB

    buf_size = w * h * 4
    buf = ctypes.create_string_buffer(buf_size)
    gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bmi), DIB_RGB_COLORS)

    # 清理 GDI 资源
    gdi32.SelectObject(mem_dc, old_bmp)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(hwnd, hwnd_dc)

    # BGRA → PNG
    try:
        from PIL import Image
        img = Image.frombuffer("RGBA", (w, h), buf, "raw", "BGRA", 0, 1)
        # 转 RGB（去 alpha）
        img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="PNG")
        return out.getvalue()
    except ImportError:
        logger.warning("PIL not available for PNG conversion")
        return None


def window_to_screen(hwnd: int, wx: int, wy: int) -> tuple[int, int]:
    """窗口坐标 → 屏幕坐标。

    类似 Codex 的窗口相对坐标系转换。
    """
    rect = _get_window_rect(hwnd)
    return rect["left"] + wx, rect["top"] + wy


def screen_to_window(hwnd: int, sx: int, sy: int) -> tuple[int, int]:
    """屏幕坐标 → 窗口坐标。"""
    rect = _get_window_rect(hwnd)
    return sx - rect["left"], sy - rect["top"]


def click_in_window(hwnd: int, wx: int, wy: int, button: str = "left") -> Dict[str, Any]:
    """在窗口内的相对坐标处点击。

    自动激活窗口 + 坐标转换 + pyautogui 点击。
    类似 Codex sky.click({ window, x, y })。
    """
    from ..input.actions import click_at
    activate_window(hwnd)
    import time
    time.sleep(0.15)  # 等待激活生效
    sx, sy = window_to_screen(hwnd, wx, wy)
    return click_at(sx, sy, button=button)


def type_in_window(hwnd: int, text: str) -> Dict[str, Any]:
    """在窗口中输入文本。

    自动激活窗口 + 输入。
    类似 Codex sky.type_text({ window, text })。
    """
    from ..input.actions import type_text, type_chinese
    activate_window(hwnd)
    import time
    time.sleep(0.15)
    if any(ord(c) > 127 for c in text):
        return type_chinese(text)
    return type_text(text)


def press_key_in_window(hwnd: int, key: str) -> Dict[str, Any]:
    """在窗口中按键。

    类似 Codex sky.press_key({ window, key })。
    """
    from ..input.actions import press_key
    activate_window(hwnd)
    import time
    time.sleep(0.1)
    return press_key(key)


def scroll_in_window(
    hwnd: int, wx: int, wy: int, delta: int = -3
) -> Dict[str, Any]:
    """在窗口中滚动。

    类似 Codex sky.scroll({ window, x, y, scrollY })。
    """
    from ..input.actions import scroll_pixels
    activate_window(hwnd)
    import time
    time.sleep(0.1)
    sx, sy = window_to_screen(hwnd, wx, wy)
    return scroll_pixels(delta, sx, sy)

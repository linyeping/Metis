# -*- coding: utf-8 -*-
"""物理执行层：鼠标/键盘 + SoM(API)→物理坐标映射 + 动作后摇 + 剪贴板等待。

工程约束（交接 part_07）：
  · 涉及坐标的动作：入参为 API 坐标系或 element_id 时，须结合 scaling 与 roi_offset 换算为虚拟桌面物理像素再调用 pyautogui。
  · 每个 pyautogui 动作后强制随机后摇：点击类 0.5~0.8s；type / hotkey / key 1.0~1.5s。
  · wait_clipboard_change：剪贴板内容哈希比对 + 超时。
"""

from __future__ import annotations

import hashlib
import os
import random
import sys
import time
from dataclasses import dataclass, field
from typing import Any

from .. import config
from ..capture import scaling
from ..policy import allow_capture_or_input

# 日志（可选）
try:
    from ..orchestrator import desk_log
except Exception:
    desk_log = None  # type: ignore

_dpi_set = False

# ── 截图帧缩放态（FABLEADV-20） ──
# 模型看到的截图被缩放到 <=1280 宽（image_utils.predict_display_dimensions），
# 但模型给的坐标是它所见图的像素坐标。记录"物理尺寸 / 显示尺寸"，
# 在裸 desktop_action 点击前把模型坐标映射回物理像素，修正高分屏点偏。
_last_frame_scale: dict[str, float] | None = None


def record_screenshot_frame(
    phys_w: int,
    phys_h: int,
    disp_w: int,
    disp_h: int,
) -> None:
    """记录最近一次全屏截图的物理/显示尺寸，供 map_model_point 换算。"""
    global _last_frame_scale
    if phys_w <= 0 or phys_h <= 0 or disp_w <= 0 or disp_h <= 0:
        _last_frame_scale = None
        return
    _last_frame_scale = {
        "phys_w": float(phys_w),
        "phys_h": float(phys_h),
        "disp_w": float(disp_w),
        "disp_h": float(disp_h),
    }


def clear_screenshot_frame() -> None:
    global _last_frame_scale
    _last_frame_scale = None


def _coord_space() -> str:
    """模型坐标约定（FABLEADV-34，让任意模型可配对）：
    - image（默认）：所见缩放截图的像素坐标（gpt-5.5 等）
    - normalized / 1000：归一化 0–1000
    - fraction：0–1 比例
    """
    return os.environ.get("METIS_CUA_COORD_SPACE", "image").strip().lower()


def map_model_point(x: int, y: int) -> tuple[int, int]:
    """模型坐标 → 物理像素。按 METIS_CUA_COORD_SPACE 解释坐标系；无帧或无缩放时安全降级。"""
    f = _last_frame_scale
    space = _coord_space()
    if f and space in {"normalized", "norm", "1000"}:
        return int(round(x / 1000.0 * f["phys_w"])), int(round(y / 1000.0 * f["phys_h"]))
    if f and space in {"fraction", "ratio", "norm1"}:
        return int(round(x * f["phys_w"])), int(round(y * f["phys_h"]))
    if not f:
        return int(x), int(y)
    sx = f["phys_w"] / f["disp_w"] if f["disp_w"] else 1.0
    sy = f["phys_h"] / f["disp_h"] if f["disp_h"] else 1.0
    return int(round(x * sx)), int(round(y * sy))


def get_screenshot_frame() -> dict[str, float] | None:
    """返回最近一次记录的截图帧缩放信息（物理/显示尺寸），无则 None。"""
    return dict(_last_frame_scale) if _last_frame_scale else None


def _log(level: str, msg: str) -> None:
    if desk_log is not None:
        try:
            desk_log.log(level, "actions", msg)
        except Exception:
            pass


def _ensure_dpi_aware() -> None:
    """Windows 高 DPI：让 pyautogui 坐标 = 物理像素（与 mss 截图一致）。"""
    global _dpi_set
    if _dpi_set or sys.platform != "win32":
        return
    _dpi_set = True
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # type: ignore[attr-defined]
    except Exception:
        try:
            import ctypes

            ctypes.windll.user32.SetProcessDPIAware()  # type: ignore[attr-defined]
        except Exception:
            pass


def _sleep_after_click() -> None:
    """点击 / 拖拽 / 滚轮等指针操作后的后摇。"""
    extra = float(config.get_input_timing().get("extra_settle_after_click_sec") or 0.0)
    time.sleep(random.uniform(0.5, 0.8) + extra)


def _sleep_after_keyboard() -> None:
    """打字、单键、组合键后的后摇（UI 提交往往更慢）。"""
    time.sleep(random.uniform(1.0, 1.5))


def _pg():
    _ensure_dpi_aware()
    from backend.runtime.pip_helper import ensure_import
    pyautogui = ensure_import("pyautogui", pip="pyautogui")
    pyautogui.FAILSAFE = True
    pyautogui.PAUSE = 0.05
    return pyautogui


# ── 剪贴板（wait_clipboard_change / type_chinese） ──


def _clipboard_text_snapshot() -> str:
    """读取当前剪贴板文本（尽量跨平台；Windows 用 Win32 API）。"""
    if sys.platform == "win32":
        try:
            import ctypes

            CF_UNICODETEXT = 13
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            if not user32.OpenClipboard(0):
                return ""
            try:
                h = user32.GetClipboardData(CF_UNICODETEXT)
                if not h:
                    return ""
                p = kernel32.GlobalLock(h)
                if not p:
                    return ""
                try:
                    return ctypes.wstring_at(p)
                finally:
                    kernel32.GlobalUnlock(h)
            finally:
                user32.CloseClipboard()
        except Exception:
            pass
    try:
        import pyperclip

        return str(pyperclip.paste() or "")
    except Exception:
        return ""


def _clipboard_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def wait_clipboard_change(timeout: float = 5.0, poll_interval: float = 0.12) -> bool:
    """轮询剪贴板文本哈希，若在 timeout 内发生变化则 True，否则 False。"""
    h0 = _clipboard_hash(_clipboard_text_snapshot())
    deadline = time.time() + max(0.1, timeout)
    while time.time() < deadline:
        time.sleep(poll_interval)
        if _clipboard_hash(_clipboard_text_snapshot()) != h0:
            _sleep_after_click()
            return True
    return False


def delegate_to_ai_log(query_summary: str, reasoning: str = "") -> dict[str, Any]:
    """delegate_to_ai：仅记录日志，不执行键鼠（SOP 由后续步骤完成）。"""
    _log(
        "INFO",
        f"delegate_to_ai query_summary={query_summary!r} reasoning={reasoning!r}",
    )
    return {"ok": True, "logged": True, "query_summary": query_summary}


# ── SoM / API 坐标上下文 ──


@dataclass
class SomFrameContext:
    """单帧 ROI 解析结果 + 缩放因子，用于 API→物理映射。"""

    scale_factor: float
    roi_offset_x: int
    roi_offset_y: int
    api_w: int
    api_h: int
    roi_label: str = ""
    elements: list[Any] = field(default_factory=list)  # list[UIElement]


def _normalize_element_id(raw: str | None) -> str | None:
    if raw is None:
        return None
    s = str(raw).strip()
    if not s:
        return None
    if s.startswith("~"):
        return s
    if s.isdigit():
        return f"~{s}"
    return s


def _find_ui_element(ctx: SomFrameContext, element_id: str) -> Any | None:
    from ..orchestrator.screen_parser import UIElement

    eid = _normalize_element_id(element_id)
    if not eid:
        return None
    for el in ctx.elements:
        if isinstance(el, UIElement) and el.element_id == eid:
            return el
    return None


def resolve_api_point_to_physical(
    api_x: int,
    api_y: int,
    ctx: SomFrameContext,
) -> tuple[int, int]:
    """API 坐标系下一点 → 虚拟桌面物理像素。"""
    return scaling.api_center_to_physical_xy(
        int(api_x),
        int(api_y),
        float(ctx.scale_factor),
        int(ctx.roi_offset_x),
        int(ctx.roi_offset_y),
    )


def resolve_element_to_physical_center(
    element_id: str,
    ctx: SomFrameContext,
) -> tuple[int, int]:
    """element_id（~n）→ 该框在 API 图上的中心 → 物理像素。"""
    el = _find_ui_element(ctx, element_id)
    if el is None:
        raise ValueError(f"未知 element_id: {element_id!r}（当前帧 SoM 列表中不存在）")
    api_xyxy = scaling.roi_bbox_xyxy_to_api_xyxy(
        el.bbox_roi_xyxy, float(ctx.scale_factor)
    )
    return scaling.api_bbox_center_physical(
        api_xyxy,
        float(ctx.scale_factor),
        int(ctx.roi_offset_x),
        int(ctx.roi_offset_y),
    )


# ── 鼠标（底层，坐标已为物理像素） ──


def click_at(x: int, y: int, button: str = "left", clicks: int = 1) -> dict[str, Any]:
    allow_capture_or_input()
    pg = _pg()
    pg.click(x, y, clicks=clicks, button=button)
    _sleep_after_click()
    return {"ok": True, "x": x, "y": y, "button": button, "clicks": clicks}


def double_click(x: int, y: int) -> dict[str, Any]:
    allow_capture_or_input()
    pg = _pg()
    pg.click(x, y, clicks=2, button="left")
    _sleep_after_click()
    return {"ok": True, "x": x, "y": y}


def right_click(x: int, y: int) -> dict[str, Any]:
    allow_capture_or_input()
    pg = _pg()
    pg.click(x, y, clicks=1, button="right")
    _sleep_after_click()
    return {"ok": True, "x": x, "y": y}


def move_to(x: int, y: int, duration: float = 0.3) -> dict[str, Any]:
    allow_capture_or_input()
    pg = _pg()
    pg.moveTo(x, y, duration=duration)
    _sleep_after_click()
    return {"ok": True, "x": x, "y": y}


def drag_to(
    start_x: int,
    start_y: int,
    end_x: int,
    end_y: int,
    duration: float = 0.5,
    button: str = "left",
) -> dict[str, Any]:
    """从 (start_x,start_y) 拖拽到 (end_x,end_y)（物理像素）。"""
    allow_capture_or_input()
    pg = _pg()
    pg.moveTo(start_x, start_y, duration=0.1)
    time.sleep(0.05)
    pg.mouseDown(button=button)
    time.sleep(0.05)
    pg.moveTo(end_x, end_y, duration=duration)
    time.sleep(0.05)
    pg.mouseUp(button=button)
    _sleep_after_click()
    return {"ok": True, "from": [start_x, start_y], "to": [end_x, end_y]}


def scroll_pixels(clicks: int = -3, x: int | None = None, y: int | None = None) -> dict[str, Any]:
    """滚轮：正数向上，负数向下。可选先在 (x,y) 定位。"""
    allow_capture_or_input()
    pg = _pg()
    if x is not None and y is not None:
        pg.moveTo(x, y, duration=0.1)
        time.sleep(0.05)
    pg.scroll(int(clicks))
    _sleep_after_click()
    return {"ok": True, "clicks": clicks, "x": x, "y": y}


# 兼容旧名
def scroll(clicks: int = -3, x: int | None = None, y: int | None = None) -> dict[str, Any]:
    return scroll_pixels(clicks, x, y)


# ── 键盘 ──


def type_text(text: str, interval: float = 0.02) -> dict[str, Any]:
    allow_capture_or_input()
    pre = float(config.get_input_timing().get("extra_settle_before_type_sec") or 0.0)
    if pre > 0:
        time.sleep(pre)
    pg = _pg()
    pg.write(text, interval=interval)
    _sleep_after_keyboard()
    return {"ok": True, "chars": len(text)}


def type_chinese(text: str) -> dict[str, Any]:
    """中文/Unicode：剪贴板 + Ctrl+V。"""
    allow_capture_or_input()
    pre = float(config.get_input_timing().get("extra_settle_before_type_sec") or 0.0)
    if pre > 0:
        time.sleep(pre)
    import subprocess

    subprocess.run(
        [
            "powershell",
            "-NoProfile",
            "-Command",
            f"Set-Clipboard -Value '{text.replace(chr(39), chr(39)+chr(39))}'",
        ],
        timeout=8,
        capture_output=True,
    )
    pg = _pg()
    pg.hotkey("ctrl", "v")
    _sleep_after_keyboard()
    return {"ok": True, "chars": len(text), "method": "clipboard"}


def press_key(key: str, presses: int = 1, interval: float = 0.05) -> dict[str, Any]:
    allow_capture_or_input()
    pg = _pg()
    for _ in range(presses):
        pg.press(key)
        time.sleep(interval)
    _sleep_after_keyboard()
    return {"ok": True, "key": key, "presses": presses}


def hotkey(*keys: str) -> dict[str, Any]:
    allow_capture_or_input()
    pg = _pg()
    pg.hotkey(*keys)
    _sleep_after_keyboard()
    return {"ok": True, "keys": list(keys)}


def get_mouse_position() -> dict[str, int]:
    pg = _pg()
    pos = pg.position()
    return {"x": pos.x, "y": pos.y}


def get_screen_size() -> dict[str, int]:
    pg = _pg()
    s = pg.size()
    return {"width": s.width, "height": s.height}


# ── SoM 感知动作调度（API / element_id → scaling → 物理） ──


def _physical_point_for_click(
    params: dict[str, Any],
    ctx: SomFrameContext | None,
) -> tuple[int, int]:
    eid = params.get("element_id") or params.get("element_confirmed")
    eid = _normalize_element_id(eid) if eid else None
    if ctx is not None and eid:
        return resolve_element_to_physical_center(eid, ctx)
    if ctx is not None and "x" in params and "y" in params:
        return resolve_api_point_to_physical(int(params["x"]), int(params["y"]), ctx)
    # 无上下文：视为已是物理坐标（兼容旧链路）
    return int(params.get("x", 0)), int(params.get("y", 0))


def execute_som_action(
    action: str,
    params: dict[str, Any],
    ctx: SomFrameContext | None = None,
) -> dict[str, Any]:
    """执行一条 SoM / 多模态 JSON 动作。action 为小写 snake_case 名。

    返回 dict 含 ok、result 摘要；终端类动作带 status 字段供 vision_loop 处理。
    """
    a = str(action or "").strip().lower()
    p = params or {}
    out: dict[str, Any] = {"ok": True, "action": a}

    if a in ("done", "fail", "ask_user"):
        out["status"] = a
        out["result"] = "OK"
        return out

    if a == "screenshot":
        out["result"] = "OK"
        out["noop_refresh"] = True
        return out

    if a == "delegate_to_ai":
        q = str(p.get("query_summary", ""))
        delegate_to_ai_log(q, str(p.get("reasoning", "")))
        out["result"] = "LOGGED"
        return out

    if a == "wait_clipboard_change":
        timeout = float(p.get("timeout", 5.0))
        changed = wait_clipboard_change(timeout=timeout)
        out["changed"] = changed
        out["result"] = "OK" if changed else "CLIPBOARD_UNCHANGED"
        return out

    if a == "wait":
        raw = p.get("seconds", p.get("timeout", 1.0))
        try:
            secs = float(raw)
        except (TypeError, ValueError):
            secs = 1.0
        secs = min(60.0, max(0.0, secs))
        time.sleep(secs)
        out["result"] = "OK"
        return out

    if a == "click":
        x, y = _physical_point_for_click(p, ctx)
        out.update(click_at(x, y, button="left", clicks=1))
        return out

    if a == "double_click":
        x, y = _physical_point_for_click(p, ctx)
        out.update(double_click(x, y))
        return out

    if a == "right_click":
        x, y = _physical_point_for_click(p, ctx)
        out.update(right_click(x, y))
        return out

    if a == "move":
        x, y = _physical_point_for_click(p, ctx)
        out.update(move_to(x, y))
        return out

    if a == "scroll":
        direction = str(p.get("direction", "down")).lower()
        n = int(p.get("clicks", 3))
        clicks_val = -abs(n) if direction == "down" else abs(n)
        if ctx is not None and p.get("element_id"):
            ex, ey = resolve_element_to_physical_center(str(p["element_id"]), ctx)
            out.update(scroll_pixels(clicks_val, ex, ey))
        elif ctx is not None and "x" in p and "y" in p:
            px, py = resolve_api_point_to_physical(int(p["x"]), int(p["y"]), ctx)
            out.update(scroll_pixels(clicks_val, px, py))
        elif ctx is not None:
            cx, cy = resolve_api_point_to_physical(ctx.api_w // 2, ctx.api_h // 2, ctx)
            out.update(scroll_pixels(clicks_val, cx, cy))
        else:
            out.update(
                scroll_pixels(
                    clicks_val,
                    int(p["x"]) if p.get("x") is not None else None,
                    int(p["y"]) if p.get("y") is not None else None,
                )
            )
        return out

    if a == "drag":
        fe = p.get("from_element_id")
        te = p.get("to_element_id")
        if ctx is not None and fe and te:
            x1, y1 = resolve_element_to_physical_center(str(fe), ctx)
            x2, y2 = resolve_element_to_physical_center(str(te), ctx)
            out.update(drag_to(x1, y1, x2, y2))
            return out
        raise ValueError("drag 需要 from_element_id 与 to_element_id（SoM 模式）")

    if a in ("drag_abs",):
        fa = p.get("from_api") or p.get("from")
        ta = p.get("to_api") or p.get("to")
        if ctx is None or not fa or not ta:
            raise ValueError("drag_abs 需要 ctx 与 from_api/to_api 两点 [x,y]（API 坐标）")
        x1, y1 = resolve_api_point_to_physical(int(fa[0]), int(fa[1]), ctx)
        x2, y2 = resolve_api_point_to_physical(int(ta[0]), int(ta[1]), ctx)
        out.update(drag_to(x1, y1, x2, y2))
        return out

    if a == "type":
        text = str(p.get("text", ""))
        if any(ord(c) > 127 for c in text):
            out.update(type_chinese(text))
        else:
            out.update(type_text(text))
        return out

    if a == "key":
        out.update(press_key(str(p.get("key", "enter"))))
        return out

    if a == "hotkey":
        keys = p.get("keys") or []
        if isinstance(keys, str):
            keys = [keys]
        out.update(hotkey(*[str(k) for k in keys]))
        return out

    raise ValueError(f"未知动作: {action!r}")

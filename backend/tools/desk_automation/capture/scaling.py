# -*- coding: utf-8 -*-
"""ROI 截图 + 虚拟桌面坐标 + 仅缩小（防糊）缩放。

与交接 part_07 对齐：
  · 三级 ROI：前台 Win32 窗口 → 鼠标中心区域 → 全屏（虚拟桌面）
  · roi_offset = ROI 左上角在虚拟屏上的绝对坐标，供「API→ROI→物理」反向映射
  · 防糊：仅当 ROI 大于 API 上限（默认 1366×768）时才缩小；绝不 Upscale，scale_factor 恒 ≤ 1.0
"""

from __future__ import annotations

import io
import sys
from dataclasses import dataclass
from typing import Any, Literal

from PIL import Image

from .. import config
from ..policy import allow_capture_or_input

# DPI 与 screenshot.py 一致：物理像素坐标
_dpi_set = False


def _ensure_dpi_aware() -> None:
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


def get_roi_config() -> dict[str, Any]:
    """读取 ~/.miro/desk_automation.json 的 roi 段；缺省与 part_07 §四 一致。"""
    cfg = config.load_config()
    roi = cfg.get("roi") or {}
    return {
        "strategy": str(roi.get("strategy", "auto")),
        "mouse_region_w": int(roi.get("mouse_region_w", 800)),
        "mouse_region_h": int(roi.get("mouse_region_h", 600)),
        "fallback_order": list(
            roi.get("fallback_order", ["foreground", "mouse", "fullscreen"])
        ),
        "api_max_w": int(roi.get("api_max_w", 1366)),
        "api_max_h": int(roi.get("api_max_h", 768)),
    }


def _virtual_monitor_dict(sct: Any) -> dict[str, int]:
    """mss: monitors[0] 为覆盖所有显示器的虚拟桌面矩形（left/top 可为负）。"""
    mons = sct.monitors
    if not mons:
        raise RuntimeError("mss: 无显示器信息")
    return dict(mons[0])


def _grab_region_bgra(sct: Any, region: dict[str, int]) -> tuple[Image.Image, dict[str, int]]:
    """对给定 left/top/width/height 截图，返回 RGB PIL 图与实际使用的 region。"""
    shot = sct.grab(region)
    img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
    actual = {
        "left": shot.left,
        "top": shot.top,
        "width": shot.width,
        "height": shot.height,
    }
    return img, actual


def _clamp_rect_to_virtual(
    left: int,
    top: int,
    width: int,
    height: int,
    virt: dict[str, int],
) -> dict[str, int]:
    """将矩形裁剪到虚拟桌面内，避免 mss.grab 越界。"""
    vleft, vtop = virt["left"], virt["top"]
    vw, vh = virt["width"], virt["height"]
    vright, vbottom = vleft + vw, vtop + vh

    rleft = max(left, vleft)
    rtop = max(top, vtop)
    rright = min(left + width, vright)
    rbottom = min(top + height, vbottom)
    rw = max(0, rright - rleft)
    rh = max(0, rbottom - rtop)
    if rw < 2 or rh < 2:
        return {"left": vleft, "top": vtop, "width": vw, "height": vh}
    return {"left": rleft, "top": rtop, "width": rw, "height": rh}


def _win_foreground_window_rect() -> tuple[int, int, int, int] | None:
    """GetForegroundWindow + GetWindowRect → 物理像素（依赖进程 DPI 感知）。"""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        h = user32.GetForegroundWindow()
        if not h:
            return None
        if user32.IsIconic(h):
            return None

        class RECT(ctypes.Structure):
            _fields_ = [
                ("left", wintypes.LONG),
                ("top", wintypes.LONG),
                ("right", wintypes.LONG),
                ("bottom", wintypes.LONG),
            ]

        r = RECT()
        if not user32.GetWindowRect(h, ctypes.byref(r)):
            return None
        w = r.right - r.left
        hgt = r.bottom - r.top
        if w < 8 or hgt < 8:
            return None
        return int(r.left), int(r.top), int(r.right), int(r.bottom)
    except Exception:
        return None


def _cursor_pos_virtual() -> tuple[int, int] | None:
    """当前鼠标在虚拟桌面上的绝对坐标。"""
    if sys.platform == "win32":
        try:
            import ctypes
            from ctypes import wintypes

            class POINT(ctypes.Structure):
                _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

            pt = POINT()
            if ctypes.windll.user32.GetCursorPos(ctypes.byref(pt)):
                return int(pt.x), int(pt.y)
        except Exception:
            pass
    try:
        import pyautogui

        p = pyautogui.position()
        return int(p.x), int(p.y)
    except Exception:
        return None


RoiStrategyName = Literal["foreground", "mouse", "fullscreen"]


@dataclass
class RoiCaptureResult:
    """单次 ROI 截图结果（坐标均为虚拟桌面绝对系下的 ROI 原点 + 尺寸）。"""

    # ROI 左上角在虚拟桌面上的绝对坐标 —— 与 pyautogui / mss 一致，多屏时可能为负或超出单屏
    roi_offset_x: int
    roi_offset_y: int
    width: int
    height: int
    strategy: RoiStrategyName
    roi_label: str
    image_rgb: Image.Image
    png_bytes: bytes

    def to_png_bytes(self) -> bytes:
        return self.png_bytes


def _pil_to_png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _capture_one_strategy(
    sct: Any,
    virt: dict[str, int],
    strategy: RoiStrategyName,
    mouse_w: int,
    mouse_h: int,
) -> RoiCaptureResult | None:
    if strategy == "foreground":
        fr = _win_foreground_window_rect()
        if fr is None:
            return None
        left, t, r, b = fr
        region = _clamp_rect_to_virtual(left, t, r - left, b - t, virt)
        img, actual = _grab_region_bgra(sct, region)
        label = "前台窗口（Win32 GetForegroundWindow）"
        strat: RoiStrategyName = "foreground"
    elif strategy == "mouse":
        cur = _cursor_pos_virtual()
        if cur is None:
            return None
        cx, cy = cur
        half_w, half_h = mouse_w // 2, mouse_h // 2
        left = cx - half_w
        top = cy - half_h
        region = _clamp_rect_to_virtual(left, top, mouse_w, mouse_h, virt)
        img, actual = _grab_region_bgra(sct, region)
        label = f"鼠标周围区域 · 中心约({cx},{cy}) · {actual['width']}×{actual['height']}"
        strat = "mouse"
    else:
        region = {k: int(virt[k]) for k in ("left", "top", "width", "height")}
        img, actual = _grab_region_bgra(sct, region)
        label = f"全屏虚拟桌面 · {actual['width']}×{actual['height']}"
        strat = "fullscreen"

    png = _pil_to_png_bytes(img)
    return RoiCaptureResult(
        roi_offset_x=int(actual["left"]),
        roi_offset_y=int(actual["top"]),
        width=int(actual["width"]),
        height=int(actual["height"]),
        strategy=strat,
        roi_label=label,
        image_rgb=img,
        png_bytes=png,
    )


def capture_roi_png(
    strategy: Literal["auto", "foreground", "mouse", "fullscreen"] | None = None,
) -> RoiCaptureResult:
    """按交接三级策略截取 ROI，返回 PIL 图、PNG、roi_offset 与策略说明。

    - auto: 按配置 fallback_order 依次尝试 foreground → mouse → fullscreen。
    """
    allow_capture_or_input()
    _ensure_dpi_aware()

    from backend.runtime.pip_helper import ensure_packages
    ensure_packages({"mss": "mss", "PIL": "pillow"})
    from mss import mss

    rc = get_roi_config()
    strat = (strategy or rc["strategy"] or "auto").strip().lower()
    mouse_w, mouse_h = rc["mouse_region_w"], rc["mouse_region_h"]
    order: list[RoiStrategyName]
    if strat == "auto":
        raw_order = rc["fallback_order"]
        order = []
        for x in raw_order:
            xs = str(x).strip().lower()
            if xs in ("foreground", "mouse", "fullscreen") and xs not in order:
                order.append(xs)  # type: ignore[arg-type]
        if not order:
            order = ["foreground", "mouse", "fullscreen"]
    elif strat in ("foreground", "mouse", "fullscreen"):
        order = [strat]  # type: ignore[list-item]
    else:
        order = ["foreground", "mouse", "fullscreen"]

    last_err: str | None = None
    with mss() as sct:
        virt = _virtual_monitor_dict(sct)
        for name in order:
            try:
                res = _capture_one_strategy(sct, virt, name, mouse_w, mouse_h)
                if res is not None:
                    return res
            except Exception as e:
                last_err = str(e)
                continue

    if last_err:
        raise RuntimeError(f"ROI 截图失败: {last_err}")
    raise RuntimeError("ROI 截图失败：所有策略均未得到图像")


def compute_downscale_params(
    src_w: int,
    src_h: int,
    api_max_w: int | None = None,
    api_max_h: int | None = None,
) -> tuple[float, int, int]:
    """防糊：仅缩小。若原图已小于等于 API 上限，scale_factor=1.0，输出尺寸=原尺寸。

    返回 (scale_factor, out_w, out_h)，保证 0 < scale_factor <= 1.0。
    """
    rc = get_roi_config()
    mw = int(api_max_w if api_max_w is not None else rc["api_max_w"])
    mh = int(api_max_h if api_max_h is not None else rc["api_max_h"])
    if src_w <= 0 or src_h <= 0:
        return 1.0, max(1, src_w), max(1, src_h)

    sx = mw / float(src_w)
    sy = mh / float(src_h)
    s = min(sx, sy, 1.0)
    if s >= 1.0:
        return 1.0, src_w, src_h
    out_w = max(1, int(round(src_w * s)))
    out_h = max(1, int(round(src_h * s)))
    # 统一用实际像素反推因子，避免 round 漂移
    s_eff = min(out_w / float(src_w), out_h / float(src_h))
    return float(s_eff), out_w, out_h


def downscale_image_only(
    image_rgb: Image.Image,
    api_max_w: int | None = None,
    api_max_h: int | None = None,
) -> tuple[Image.Image, float, int, int]:
    """对 PIL RGB 图做仅缩小缩放；返回 (新图, scale_factor, out_w, out_h)。"""
    w, h = image_rgb.size
    s, ow, oh = compute_downscale_params(w, h, api_max_w, api_max_h)
    if s >= 1.0:
        return image_rgb.copy(), 1.0, w, h
    # LANCZOS：缩小质量较好；禁止放大故上面已 return
    resized = image_rgb.resize((ow, oh), Image.Resampling.LANCZOS)
    return resized, s, ow, oh


def roi_bbox_xyxy_to_api_xyxy(
    bbox_roi: tuple[int, int, int, int],
    scale_factor: float,
) -> tuple[int, int, int, int]:
    """ROI 像素系 xyxy → API 图坐标系 xyxy（与 downscale 使用同一 scale_factor）。"""
    x1, y1, x2, y2 = bbox_roi
    s = scale_factor
    return (
        int(round(x1 * s)),
        int(round(y1 * s)),
        int(round(x2 * s)),
        int(round(y2 * s)),
    )


def api_center_to_physical_xy(
    api_cx: int,
    api_cy: int,
    scale_factor: float,
    roi_offset_x: int,
    roi_offset_y: int,
) -> tuple[int, int]:
    """API 坐标系下的中心点 → 虚拟桌面物理像素（用于 PyAutoGUI）。"""
    if scale_factor <= 0:
        scale_factor = 1.0
    rx = api_cx / scale_factor
    ry = api_cy / scale_factor
    return int(round(roi_offset_x + rx)), int(round(roi_offset_y + ry))


def api_bbox_center_physical(
    bbox_api_xyxy: tuple[int, int, int, int],
    scale_factor: float,
    roi_offset_x: int,
    roi_offset_y: int,
) -> tuple[int, int]:
    """API 框中心 → 物理像素。"""
    x1, y1, x2, y2 = bbox_api_xyxy
    cx = (x1 + x2) // 2
    cy = (y1 + y2) // 2
    return api_center_to_physical_xy(cx, cy, scale_factor, roi_offset_x, roi_offset_y)


def prepare_dual_api_payload(
    raw_roi_rgb: Image.Image,
    som_roi_rgb: Image.Image,
    api_max_w: int | None = None,
    api_max_h: int | None = None,
) -> dict[str, Any]:
    """将「原图 ROI + SoM ROI」同步缩放到 API 尺寸，返回 PNG bytes 与 scale_factor。"""
    raw_ds, s, w1, h1 = downscale_image_only(raw_roi_rgb, api_max_w, api_max_h)
    som_ds, _, _, _ = downscale_image_only(som_roi_rgb, api_max_w, api_max_h)
    # 二者 ROI 应同尺寸；若 SoM 与 raw 不一致则强制对齐到 raw_ds（避免双图尺寸漂移）
    if som_ds.size != (w1, h1):
        som_ds = som_roi_rgb.resize((w1, h1), Image.Resampling.LANCZOS)
    return {
        "api_raw_png": _pil_to_png_bytes(raw_ds),
        "api_som_png": _pil_to_png_bytes(som_ds),
        "scale_factor": float(s),
        "api_w": w1,
        "api_h": h1,
    }

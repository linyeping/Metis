# -*- coding: utf-8 -*-
"""主显示器截图 → PNG bytes。依赖可选：mss + Pillow。"""

from __future__ import annotations

import sys
from typing import Any

from ..policy import allow_capture_or_input

_dpi_set = False

def _ensure_dpi_aware():
    """与 input/actions.py 一致：确保截图坐标 = 物理像素。"""
    global _dpi_set
    if _dpi_set or sys.platform != "win32":
        return
    _dpi_set = True
    try:
        import ctypes
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            import ctypes
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


def grab_screen_png() -> bytes:
    allow_capture_or_input()
    _ensure_dpi_aware()
    import io

    from backend.runtime.pip_helper import ensure_packages
    ensure_packages({"mss": "mss", "PIL": "pillow"})
    from mss import mss
    from PIL import Image

    with mss() as sct:
        mon = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        shot = sct.grab(mon)
        img = Image.frombytes("RGB", shot.size, shot.bgra, "raw", "BGRX")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        return buf.getvalue()


def monitor_info() -> dict[str, Any]:
    """不截图，仅显示器几何信息（**不**要求总开关，便于排障）。"""
    from backend.runtime.pip_helper import ensure_import
    ensure_import("mss", pip="mss")
    from mss import mss
    with mss() as sct:
        prim = sct.monitors[1] if len(sct.monitors) > 1 else sct.monitors[0]
        return {"monitors": len(sct.monitors), "primary": dict(prim)}

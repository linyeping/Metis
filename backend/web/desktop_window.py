from __future__ import annotations

import ctypes
import threading
from ctypes import wintypes
from typing import Any, Dict


SW_RESTORE = 9
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
MONITOR_DEFAULTTONEAREST = 0x00000002
_WINAPI_CONFIGURED = False


class RECT(ctypes.Structure):
    _fields_ = [
        ("left", wintypes.LONG),
        ("top", wintypes.LONG),
        ("right", wintypes.LONG),
        ("bottom", wintypes.LONG),
    ]


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


class DesktopWindowController:
    def __init__(self) -> None:
        self._maximized = False
        self._restore_rect: RECT | None = None
        self._restore_bounds: Any | None = None

    def handle(self, action: str) -> Dict[str, Any]:
        window = self._get_window()
        if window is None:
            return {"ok": False, "error": "desktop window unavailable", "status": 404}

        action = (action or "").strip().lower()
        try:
            if action == "minimize":
                window.minimize()
            elif action == "maximize":
                if not self._toggle_work_area_maximize(window):
                    if self._maximized:
                        window.restore()
                        self._maximized = False
                    else:
                        window.maximize()
                        self._maximized = True
            elif action == "close":
                try:
                    from backend.runtime.desktop import hide_window_to_tray

                    hide_window_to_tray()
                except Exception:
                    window.hide()
            elif action == "quit":
                try:
                    from backend.runtime.desktop import quit_application

                    quit_application()
                except Exception:
                    threading.Timer(0.05, window.destroy).start()
            else:
                return {"ok": False, "error": "unknown window action", "status": 400}
        except Exception as exc:
            return {"ok": False, "error": str(exc) or exc.__class__.__name__, "status": 500}

        return {"ok": True, "action": action, "maximized": self._maximized}

    @staticmethod
    def _get_window() -> Any:
        try:
            from backend.runtime.desktop import get_webview_window
        except Exception:
            return None
        return get_webview_window()

    def _toggle_work_area_maximize(self, window: Any) -> bool:
        if self._toggle_winforms_work_area_maximize(window):
            return True
        self._configure_winapi()
        hwnd = self._window_handle(window)
        if not hwnd:
            return False
        if self._maximized and self._restore_rect is not None:
            rect = self._restore_rect
            ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
            self._set_rect(hwnd, rect)
            self._maximized = False
            self._restore_rect = None
            return True

        rect = RECT()
        if not ctypes.windll.user32.GetWindowRect(hwnd, ctypes.byref(rect)):
            return False
        work = self._monitor_work_area(hwnd)
        if work is None:
            return False
        self._restore_rect = rect
        ctypes.windll.user32.ShowWindow(hwnd, SW_RESTORE)
        self._set_rect(hwnd, work)
        self._maximized = True
        return True

    def _toggle_winforms_work_area_maximize(self, window: Any) -> bool:
        native = getattr(window, "native", None)
        if native is None or not hasattr(native, "Bounds"):
            return False
        try:
            import clr

            clr.AddReference("System.Windows.Forms")
            from System import Func, Type
            import System.Windows.Forms as WinForms
        except Exception:
            return False

        applied = {"ok": False}

        def _apply() -> None:
            if self._maximized and self._restore_bounds is not None:
                native.WindowState = WinForms.FormWindowState.Normal
                native.Bounds = self._restore_bounds
                self._maximized = False
                self._restore_bounds = None
                self._restore_rect = None
                applied["ok"] = True
                return

            screen = WinForms.Screen.FromControl(native)
            if screen is None:
                return
            self._restore_bounds = native.Bounds
            native.WindowState = WinForms.FormWindowState.Normal
            native.Bounds = screen.WorkingArea
            self._maximized = True
            self._restore_rect = None
            applied["ok"] = True

        try:
            if getattr(native, "InvokeRequired", False):
                native.Invoke(Func[Type](_apply))
            else:
                _apply()
        except Exception:
            return False
        return applied["ok"]

    @staticmethod
    def _window_handle(window: Any) -> int:
        native = getattr(window, "native", None)
        handle = getattr(native, "Handle", None)
        if handle is None:
            return 0
        for method_name in ("ToInt64", "ToInt32"):
            method = getattr(handle, method_name, None)
            if callable(method):
                try:
                    return int(method())
                except Exception:
                    continue
        try:
            return int(handle)
        except Exception:
            return 0

    @staticmethod
    def _monitor_work_area(hwnd: int) -> RECT | None:
        monitor = ctypes.windll.user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
        if not monitor:
            return None
        info = MONITORINFO()
        info.cbSize = ctypes.sizeof(MONITORINFO)
        if not ctypes.windll.user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
            return None
        return info.rcWork

    @staticmethod
    def _set_rect(hwnd: int, rect: RECT) -> None:
        ctypes.windll.user32.SetWindowPos(
            hwnd,
            None,
            int(rect.left),
            int(rect.top),
            int(rect.right - rect.left),
            int(rect.bottom - rect.top),
            SWP_NOZORDER | SWP_NOACTIVATE,
        )

    @staticmethod
    def _configure_winapi() -> None:
        global _WINAPI_CONFIGURED
        if _WINAPI_CONFIGURED:
            return
        user32 = ctypes.windll.user32
        user32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
        user32.GetWindowRect.restype = wintypes.BOOL
        user32.MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
        user32.MonitorFromWindow.restype = wintypes.HMONITOR
        user32.GetMonitorInfoW.argtypes = [wintypes.HMONITOR, ctypes.POINTER(MONITORINFO)]
        user32.GetMonitorInfoW.restype = wintypes.BOOL
        user32.SetWindowPos.argtypes = [
            wintypes.HWND,
            wintypes.HWND,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wintypes.UINT,
        ]
        user32.SetWindowPos.restype = wintypes.BOOL
        user32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
        user32.ShowWindow.restype = wintypes.BOOL
        _WINAPI_CONFIGURED = True


_CONTROLLER = DesktopWindowController()


def handle_window_action(action: str) -> Dict[str, Any]:
    return _CONTROLLER.handle(action)

"""window_manager 单元测试 —— 验证数据结构、坐标转换、窗口过滤逻辑。

由于 window_manager 依赖 Win32 ctypes，测试在 Windows 上运行。
对实际 API 调用进行 mock，验证上层逻辑的正确性。
"""
from __future__ import annotations

import sys
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

# 跳过非 Windows 平台
pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows only")


# ---------------------------------------------------------------------------
# WindowInfo 数据结构测试
# ---------------------------------------------------------------------------


class TestWindowInfo:
    def test_to_dict(self):
        from backend.tools.desk_automation.capture.window_manager import WindowInfo

        info = WindowInfo(
            hwnd=12345,
            title="Test Window",
            class_name="TestClass",
            pid=1000,
            rect={"left": 0, "top": 0, "width": 800, "height": 600},
            is_minimized=False,
            is_foreground=True,
            exe_name="test.exe",
        )
        d = info.to_dict()
        assert d["hwnd"] == 12345
        assert d["title"] == "Test Window"
        assert d["class_name"] == "TestClass"
        assert d["pid"] == 1000
        assert d["rect"]["width"] == 800
        assert d["rect"]["height"] == 600
        assert d["is_minimized"] is False
        assert d["is_foreground"] is True
        assert d["exe_name"] == "test.exe"

    def test_default_values(self):
        from backend.tools.desk_automation.capture.window_manager import WindowInfo

        info = WindowInfo(
            hwnd=1, title="T", class_name="C", pid=2,
            rect={"left": 0, "top": 0, "width": 100, "height": 100},
        )
        assert info.is_minimized is False
        assert info.is_foreground is False
        assert info.exe_name == ""


# ---------------------------------------------------------------------------
# 坐标转换
# ---------------------------------------------------------------------------


class TestCoordinateConversion:
    def test_window_to_screen(self):
        from backend.tools.desk_automation.capture.window_manager import (
            _get_window_rect,
            window_to_screen,
        )

        with patch(
            "backend.tools.desk_automation.capture.window_manager._get_window_rect",
            return_value={"left": 100, "top": 200, "width": 800, "height": 600},
        ):
            sx, sy = window_to_screen(999, 50, 30)
            assert sx == 150  # 100 + 50
            assert sy == 230  # 200 + 30

    def test_screen_to_window(self):
        from backend.tools.desk_automation.capture.window_manager import screen_to_window

        with patch(
            "backend.tools.desk_automation.capture.window_manager._get_window_rect",
            return_value={"left": 100, "top": 200, "width": 800, "height": 600},
        ):
            wx, wy = screen_to_window(999, 150, 230)
            assert wx == 50  # 150 - 100
            assert wy == 30  # 230 - 200

    def test_roundtrip_conversion(self):
        from backend.tools.desk_automation.capture.window_manager import (
            screen_to_window,
            window_to_screen,
        )

        rect = {"left": 300, "top": 100, "width": 1024, "height": 768}
        with patch(
            "backend.tools.desk_automation.capture.window_manager._get_window_rect",
            return_value=rect,
        ):
            sx, sy = window_to_screen(1, 200, 150)
            wx, wy = screen_to_window(1, sx, sy)
            assert (wx, wy) == (200, 150)

    def test_zero_origin(self):
        from backend.tools.desk_automation.capture.window_manager import window_to_screen

        with patch(
            "backend.tools.desk_automation.capture.window_manager._get_window_rect",
            return_value={"left": 0, "top": 0, "width": 800, "height": 600},
        ):
            sx, sy = window_to_screen(1, 0, 0)
            assert (sx, sy) == (0, 0)

    def test_negative_offset(self):
        from backend.tools.desk_automation.capture.window_manager import screen_to_window

        with patch(
            "backend.tools.desk_automation.capture.window_manager._get_window_rect",
            return_value={"left": 500, "top": 300, "width": 800, "height": 600},
        ):
            wx, wy = screen_to_window(1, 400, 200)
            assert wx == -100  # outside window to the left
            assert wy == -100


# ---------------------------------------------------------------------------
# _get_window_text / _get_class_name helpers
# ---------------------------------------------------------------------------


class TestHelpers:
    def test_get_window_text(self):
        from backend.tools.desk_automation.capture.window_manager import _get_window_text

        # Mock user32 calls
        with patch(
            "backend.tools.desk_automation.capture.window_manager.user32"
        ) as mock_user32:
            mock_user32.GetWindowTextLengthW.return_value = 0
            result = _get_window_text(0)
            assert result == ""

    def test_get_class_name(self):
        from backend.tools.desk_automation.capture.window_manager import _get_class_name

        with patch(
            "backend.tools.desk_automation.capture.window_manager.user32"
        ) as mock_user32:
            # GetClassNameW writes to buffer; mock it to do nothing
            mock_user32.GetClassNameW.return_value = 0
            result = _get_class_name(0)
            # Returns empty string from zeroed buffer
            assert isinstance(result, str)

    def test_get_window_pid(self):
        from backend.tools.desk_automation.capture.window_manager import _get_window_pid

        with patch(
            "backend.tools.desk_automation.capture.window_manager.user32"
        ) as mock_user32:
            mock_user32.GetWindowThreadProcessId.return_value = 123
            pid = _get_window_pid(0)
            assert isinstance(pid, int)


# ---------------------------------------------------------------------------
# find_window
# ---------------------------------------------------------------------------


class TestFindWindow:
    def test_find_by_title_substring(self):
        from backend.tools.desk_automation.capture.window_manager import (
            WindowInfo,
            find_window,
        )

        fake_windows = [
            WindowInfo(hwnd=1, title="Notepad", class_name="Notepad", pid=10,
                       rect={"left": 0, "top": 0, "width": 800, "height": 600}),
            WindowInfo(hwnd=2, title="Chrome - Google", class_name="Chrome_WidgetWin_1", pid=20,
                       rect={"left": 0, "top": 0, "width": 1024, "height": 768}),
        ]
        with patch(
            "backend.tools.desk_automation.capture.window_manager.list_windows",
            return_value=fake_windows,
        ):
            result = find_window("Chrome")
            assert result is not None
            assert result.hwnd == 2

    def test_find_case_insensitive(self):
        from backend.tools.desk_automation.capture.window_manager import (
            WindowInfo,
            find_window,
        )

        fake_windows = [
            WindowInfo(hwnd=1, title="Visual Studio Code", class_name="vscode", pid=10,
                       rect={"left": 0, "top": 0, "width": 800, "height": 600}),
        ]
        with patch(
            "backend.tools.desk_automation.capture.window_manager.list_windows",
            return_value=fake_windows,
        ):
            result = find_window("visual studio")
            assert result is not None
            assert result.hwnd == 1

    def test_find_no_match(self):
        from backend.tools.desk_automation.capture.window_manager import find_window

        with patch(
            "backend.tools.desk_automation.capture.window_manager.list_windows",
            return_value=[],
        ):
            result = find_window("NonExistent")
            assert result is None


# ---------------------------------------------------------------------------
# activate_window
# ---------------------------------------------------------------------------


class TestActivateWindow:
    def test_activate_nonexistent_window(self):
        from backend.tools.desk_automation.capture.window_manager import activate_window

        with patch(
            "backend.tools.desk_automation.capture.window_manager.user32"
        ) as mock_user32:
            mock_user32.IsWindow.return_value = False
            result = activate_window(99999)
            assert result is False

    def test_activate_minimized_window_restores(self):
        from backend.tools.desk_automation.capture.window_manager import (
            SW_RESTORE,
            WS_MINIMIZE,
            activate_window,
        )

        with patch(
            "backend.tools.desk_automation.capture.window_manager.user32"
        ) as mock_user32:
            mock_user32.IsWindow.return_value = True
            mock_user32.GetWindowLongW.return_value = WS_MINIMIZE
            mock_user32.SetForegroundWindow.return_value = True
            activate_window(100)
            mock_user32.ShowWindow.assert_called_once_with(100, SW_RESTORE)


# ---------------------------------------------------------------------------
# get_window
# ---------------------------------------------------------------------------


class TestGetWindow:
    def test_get_invalid_hwnd(self):
        from backend.tools.desk_automation.capture.window_manager import get_window

        with patch(
            "backend.tools.desk_automation.capture.window_manager.user32"
        ) as mock_user32:
            mock_user32.IsWindow.return_value = False
            result = get_window(0)
            assert result is None


# ---------------------------------------------------------------------------
# capture_window 边界
# ---------------------------------------------------------------------------


class TestCaptureWindowEdge:
    def test_capture_invalid_hwnd(self):
        from backend.tools.desk_automation.capture.window_manager import capture_window

        with patch(
            "backend.tools.desk_automation.capture.window_manager.user32"
        ) as mock_user32:
            mock_user32.IsWindow.return_value = False
            result = capture_window(0)
            assert result is None

    def test_capture_zero_size_window(self):
        from backend.tools.desk_automation.capture.window_manager import capture_window

        with patch(
            "backend.tools.desk_automation.capture.window_manager.user32"
        ) as mock_user32, patch(
            "backend.tools.desk_automation.capture.window_manager._get_window_rect",
            return_value={"left": 0, "top": 0, "width": 0, "height": 0},
        ), patch(
            "backend.tools.desk_automation.capture.window_manager._ensure_dpi_aware",
        ):
            mock_user32.IsWindow.return_value = True
            mock_user32.GetWindowLongW.return_value = 0  # not minimized
            result = capture_window(1)
            assert result is None

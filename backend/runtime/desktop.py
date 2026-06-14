from __future__ import annotations

import os
import socket
import sys
import threading
import time
import inspect
from typing import Any, Optional

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_REPO_ROOT = os.path.dirname(_BACKEND_ROOT)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)


_webview_window: Any = None
_tray_icon: Any = None
_quit_requested = False


def _set_webview_window(win: Any) -> None:
    global _webview_window
    _webview_window = win


def get_webview_window() -> Any:
    """Return the pywebview window instance, or None outside desktop mode."""
    return _webview_window


def show_window() -> None:
    """Restore the desktop window from the tray."""
    window = get_webview_window()
    if window is None:
        return
    try:
        window.show()
        window.restore()
    except Exception:
        pass


def hide_window_to_tray() -> None:
    """Hide the desktop window while keeping the tray icon alive."""
    window = get_webview_window()
    if window is None:
        return
    try:
        window.hide()
    except Exception:
        try:
            window.minimize()
        except Exception:
            pass


def quit_application() -> None:
    """Stop the tray icon and destroy the desktop window."""
    global _quit_requested
    _quit_requested = True
    tray = _tray_icon
    if tray is not None:
        try:
            tray.stop()
        except Exception:
            pass
    window = get_webview_window()
    if window is not None:
        try:
            threading.Timer(0.05, window.destroy).start()
        except Exception:
            pass


def _find_free_port(start: int = 5100, end: int = 5200) -> int:
    for port in range(start, end):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(("127.0.0.1", port)) != 0:
                return port
    return start


def _wait_for_server(port: int, timeout: float = 10.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            if sock.connect_ex(("127.0.0.1", port)) == 0:
                return True
        time.sleep(0.1)
    return False


def _create_tray_icon(window: Any) -> Any:
    import pystray
    from PIL import Image as PilImage

    icon_path = os.path.join(_BACKEND_ROOT, "assets", "logo.png")
    if not os.path.isfile(icon_path):
        icon_path = os.path.join(_BACKEND_ROOT, "assets", "metis-M-256.png")
    image = PilImage.open(icon_path).convert("RGBA").resize((64, 64))

    def on_show(_icon: Any, _item: Any = None) -> None:
        show_window()

    def on_quit(_icon: Any, _item: Any = None) -> None:
        quit_application()

    menu = pystray.Menu(
        pystray.MenuItem("显示 Metis", on_show, default=True),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("退出", on_quit),
    )
    return pystray.Icon("Metis", image, "Metis", menu)


def _start_tray(window: Any) -> None:
    global _tray_icon
    try:
        tray = _create_tray_icon(window)
    except Exception as exc:
        print(f"[tray] disabled: {type(exc).__name__}: {exc}")
        return

    _tray_icon = tray
    run_detached = getattr(tray, "run_detached", None)
    if callable(run_detached):
        try:
            run_detached()
            return
        except Exception as exc:
            print(f"[tray] run_detached failed: {type(exc).__name__}: {exc}")

    threading.Thread(target=tray.run, daemon=True, name="metis-tray").start()


def _bind_close_to_tray(window: Any) -> None:
    def on_closing() -> bool:
        if _quit_requested:
            return True
        hide_window_to_tray()
        return False

    try:
        window.events.closing += on_closing
    except Exception as exc:
        print(f"[tray] close hook disabled: {type(exc).__name__}: {exc}")


def launch(
    port: Optional[int] = None,
    title: str = "Metis",
    width: int = 1200,
    height: int = 820,
    debug: bool = False,
) -> None:
    """Launch Metis as a native desktop window backed by Flask."""
    import webview

    selected_port = port or _find_free_port()
    os.environ["METIS_PORT"] = str(selected_port)
    webview.settings["DRAG_REGION_SELECTOR"] = ".window-drag-region"
    webview.settings["DRAG_REGION_DIRECT_TARGET_ONLY"] = False

    def run_flask() -> None:
        import logging

        from backend.web.app import app

        logging.getLogger("werkzeug").setLevel(logging.WARNING)
        app.run(
            host="127.0.0.1",
            port=selected_port,
            threaded=True,
            debug=False,
            use_reloader=False,
        )

    flask_thread = threading.Thread(target=run_flask, daemon=True, name="miro-flask")
    flask_thread.start()

    if not _wait_for_server(selected_port):
        print(f"Error: Flask server did not start on port {selected_port}")
        return

    icon_path = None
    for filename in ("logo.ico", "logo.png"):
        candidate = os.path.join(_BACKEND_ROOT, "assets", filename)
        if os.path.exists(candidate):
            icon_path = candidate
            break

    url = f"http://127.0.0.1:{selected_port}"
    window_kwargs = {
        "width": width,
        "height": height,
        "min_size": (800, 600),
        "frameless": True,
        "easy_drag": False,
        "background_color": "#0c0c0f",
        "text_select": True,
    }
    window = webview.create_window(title, url, **window_kwargs)
    _set_webview_window(window)
    _bind_close_to_tray(window)
    _start_tray(window)
    try:
        start_params = inspect.signature(webview.start).parameters
    except (TypeError, ValueError):
        start_params = {}
    start_kwargs = {"debug": debug}
    if icon_path and "icon" in start_params:
        start_kwargs["icon"] = icon_path
    webview.start(**start_kwargs)


if __name__ == "__main__":
    launch(debug="--debug" in sys.argv)

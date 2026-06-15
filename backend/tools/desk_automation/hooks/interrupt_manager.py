# -*- coding: utf-8 -*-
"""全局 ESC 急停：pynput 后台线程 + Event + 暂停标志 + 回调（供 SSE/WebSocket 感知 Paused）。

依赖可选：pip install pynput
未安装时 start_esc_listener() 为 no-op，仍可通过 config.set_paused / vision stop 暂停。
"""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

from .. import config

# ESC 触发的急停（与 vision_loop._stop、desk_automation.pause 文件并列）
_esc_stop_event = threading.Event()
_callbacks: list[Callable[[dict[str, Any]], None]] = []
_lock = threading.RLock()
_listener: Any = None  # pynput.keyboard.Listener
_listener_thread: threading.Thread | None = None
_started = False


def register_interrupt_callback(fn: Callable[[dict[str, Any]], None]) -> None:
    """注册回调：急停触发时调用，参数示例 {"source": "esc", "status": "paused", "ts": ...}。"""
    with _lock:
        if fn not in _callbacks:
            _callbacks.append(fn)


def unregister_interrupt_callback(fn: Callable[[dict[str, Any]], None]) -> None:
    with _lock:
        try:
            _callbacks.remove(fn)
        except ValueError:
            pass


def _emit(reason: str, source: str) -> None:
    payload = {
        "event": "interrupt",
        "source": source,
        "reason": reason,
        "status": "paused",
        "paused": True,
        "ts": time.time(),
    }
    with _lock:
        cbs = list(_callbacks)
    for cb in cbs:
        try:
            cb(payload)
        except Exception:
            pass


def esc_stop_event() -> threading.Event:
    """供 vision_loop 组合判断：ESC 急停 Event。"""
    return _esc_stop_event


def is_esc_stop_set() -> bool:
    return _esc_stop_event.is_set()


def trigger_interrupt(source: str = "api", reason: str = "manual") -> None:
    """程序化触发与 ESC 相同效果（急停 + 写暂停标志 + 回调）。"""
    _esc_stop_event.set()
    try:
        config.set_paused(True)
    except Exception:
        pass
    _emit(reason, source)


def reset_esc_stop_for_new_run() -> None:
    """新任务启动时清除 ESC Event（不自动清除磁盘 pause；用户需网页「继续」unlink pause）。"""
    _esc_stop_event.clear()


def clear_all_interrupt_and_pause() -> None:
    """继续运行：清除 ESC 标志 + 去掉 pause 文件（供前端「继续」一键调用）。"""
    _esc_stop_event.clear()
    try:
        config.set_paused(False)
    except Exception:
        pass


def _on_press(key: Any) -> None:
    try:
        from pynput import keyboard
    except Exception:
        return
    try:
        if key == keyboard.Key.esc:
            _esc_stop_event.set()
            try:
                config.set_paused(True)
            except Exception:
                pass
            _emit("esc_pressed", "esc")
    except Exception:
        pass


def start_esc_listener() -> bool:
    """启动全局 ESC 监听（幂等）。成功返回 True；无 pynput 返回 False。"""
    global _listener, _listener_thread, _started
    with _lock:
        if _started:
            return True
        try:
            # 优先直接导入（打包版已收录 pynput）；dev 环境缺失时按需安装，与输入层(actions.py)一致。
            try:
                from pynput import keyboard
            except ImportError:
                from backend.runtime.pip_helper import ensure_import

                keyboard = ensure_import("pynput", pip="pynput").keyboard
        except Exception:
            return False

        def _run() -> None:
            global _listener
            try:
                _listener = keyboard.Listener(on_press=_on_press)
                _listener.start()
                if _listener is not None:
                    _listener.join()
            except Exception:
                pass

        _listener_thread = threading.Thread(
            target=_run,
            name="desk_esc_listener",
            daemon=True,
        )
        _listener_thread.start()
        _started = True
        return True


def stop_esc_listener() -> None:
    """停止 pynput 监听（一般无需调用；进程退出随 daemon 结束）。"""
    global _listener, _started
    with _lock:
        try:
            if _listener is not None:
                _listener.stop()
        except Exception:
            pass
        _listener = None
        _started = False


def is_listener_running() -> bool:
    with _lock:
        return _started

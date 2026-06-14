# -*- coding: utf-8 -*-
"""Desk 桌面自动化：SSE 广播中心。

- ESC / trigger_interrupt 经 interrupt_manager 回调转发为 `event: interrupt`
- vision_loop 在关键节点调用 `broadcast()` 推送 `vision_state`
- 多客户端：每连接一个 queue，broadcast 时 fan-out
"""
from __future__ import annotations

import json
import queue
import threading
from typing import Any

_MAX_QUEUE: int = 64
_subscribers: list[queue.Queue[str]] = []
_sub_lock = threading.RLock()
_interrupt_forwarder_registered: bool = False


def broadcast(payload: dict[str, Any]) -> None:
    """向所有已连接的 EventSource 客户端推送一条 JSON 行（非阻塞；队列满则丢旧留新）。"""
    line = json.dumps(payload, ensure_ascii=False)
    with _sub_lock:
        clients = list(_subscribers)
    for q in clients:
        try:
            q.put_nowait(line)
        except queue.Full:
            try:
                while True:
                    q.get_nowait()
            except queue.Empty:
                pass
            try:
                q.put_nowait(line)
            except Exception:
                pass


def subscribe() -> queue.Queue[str]:
    q: queue.Queue[str] = queue.Queue(maxsize=_MAX_QUEUE)
    with _sub_lock:
        _subscribers.append(q)
    return q


def unsubscribe(q: queue.Queue[str]) -> None:
    with _sub_lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


def _interrupt_payload_to_sse(raw: dict[str, Any]) -> None:
    """interrupt_manager._emit → 合并当前 vision 快照，供前端零延迟弹窗 + 徽章。"""
    try:
        from backend.tools.desk_automation.orchestrator import vision_loop

        vs = vision_loop.get_state()
        broadcast(
            {
                "event": "interrupt",
                "paused": True,
                "source": str(raw.get("source", "")),
                "reason": str(raw.get("reason", "")),
                "ts": raw.get("ts"),
                "vision_status": vs.get("status", "idle"),
                "vision_running": vision_loop.is_running(),
                "vision_goal": vs.get("goal", ""),
                "vision_step": vs.get("step_count", 0),
                "vision_max_steps": vs.get("max_steps", 0),
            }
        )
    except Exception:
        pass


def ensure_interrupt_forwarder() -> None:
    """幂等注册：ESC 与 trigger_interrupt 共用一条 SSE 广播链。"""
    global _interrupt_forwarder_registered
    with _sub_lock:
        if _interrupt_forwarder_registered:
            return
        _interrupt_forwarder_registered = True
    from backend.tools.desk_automation.hooks.interrupt_manager import register_interrupt_callback

    register_interrupt_callback(_interrupt_payload_to_sse)


def initial_snapshot_payload() -> dict[str, Any]:
    """新 SSE 连接首包：当前视觉状态（避免刷新页面后徽章空白到下一轮轮询）。"""
    try:
        from backend.tools.desk_automation import config
        from backend.tools.desk_automation.orchestrator import vision_loop

        vs = vision_loop.get_state()
        return {
            "event": "hello",
            "paused": bool(config.is_paused()),
            "vision_status": vs.get("status", "idle"),
            "vision_running": vision_loop.is_running(),
            "vision_goal": vs.get("goal", ""),
            "vision_step": vs.get("step_count", 0),
            "vision_max_steps": vs.get("max_steps", 0),
        }
    except Exception:
        return {
            "event": "hello",
            "paused": False,
            "vision_status": "idle",
            "vision_running": False,
            "vision_goal": "",
            "vision_step": 0,
            "vision_max_steps": 0,
        }

# -*- coding: utf-8 -*-
"""human 模式在画面「稳定」后，提示可挂接 OpenClaw 式技能（browser/coding-agent/delegate 等）。

不直接 import OpenClaw；由宿主或网关把本日志事件接到技能调度器。
"""

from __future__ import annotations

from . import task_state


def notify_stable_for_skills(goal: str, streak: int, threshold: int) -> bool:
    """若连续稳定帧数达到阈值，写一条日志（幂等由调用方保证）。"""
    if streak < threshold:
        return False
    task_state.append_log(
        "info",
        f"[skill] 画面已连续稳定 {streak} 帧（≥{threshold}），"
        f"可挂接技能链：browser 扩展 / coding-agent / desk_delegate 委派 — 目标: {goal[:80]}",
    )
    return True

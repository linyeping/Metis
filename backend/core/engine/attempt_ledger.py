"""工具调用 attempt 环形记录 + 连续失败恢复提示 + 可选 JSONL 落盘（恢复面起步）。"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import deque
from typing import Deque, List, Tuple

def _deque_maxlen() -> int:
    try:
        return max(8, min(int(os.environ.get("MIRO_ATTEMPT_LEDGER_MAX", "64")), 256))
    except ValueError:
        return 64


_RING: Deque[Tuple[str, bool, float]] = deque(maxlen=_deque_maxlen())
_LOCK = threading.Lock()


def ledger_enabled() -> bool:
    return os.environ.get("MIRO_ATTEMPT_LEDGER", "1").strip().lower() not in (
        "0",
        "false",
        "no",
        "off",
    )


def clear_ledger() -> None:
    """清空内存环形缓冲（验收 / 调试）。"""
    with _LOCK:
        _RING.clear()


def _is_failure_result(result: str) -> bool:
    s = (result or "").strip()
    return s.startswith("❌") or "不允许执行" in s


def _consecutive_failure_streak(canonical: str) -> int:
    """从环形缓冲尾部向前数，同一工具连续失败次数（尾部必须为失败）。"""
    n = 0
    with _LOCK:
        snap: List[Tuple[str, bool, float]] = list(_RING)
    for name, ok, _ in reversed(snap):
        if name != canonical:
            break
        if ok:
            break
        n += 1
    return n


def _append_record(canonical: str, ok: bool) -> None:
    with _LOCK:
        _RING.append((canonical, ok, time.time()))


def _maybe_persist(canonical: str, ok: bool, result: str) -> None:
    if os.environ.get("MIRO_ATTEMPT_LEDGER_PERSIST", "0").strip().lower() not in (
        "1",
        "true",
        "yes",
        "on",
    ):
        return
    try:
        from .mode_router import resolve_mode_workspace_root

        root = resolve_mode_workspace_root()
    except ImportError:
        root = os.getcwd()
    try:
        path = os.path.join(root, ".miro_attempt_ledger.jsonl")
        rec = {
            "tool": canonical,
            "ok": ok,
            "ts": time.time(),
            "snippet": (result or "")[:500],
        }
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError:
        pass


def finalize_tool_result(canonical: str, result: str) -> str:
    """
    在 `execute_tool` 返回前调用：写入环形缓冲、可选落盘、连续失败时附加恢复提示。
    """
    if not ledger_enabled():
        return result
    ok = not _is_failure_result(result)
    _append_record(canonical, ok)
    _maybe_persist(canonical, ok, result)
    streak = _consecutive_failure_streak(canonical)
    if not ok and streak >= 2:
        return (
            result
            + "\n\n---\n[Miro recovery]\n"
            + f"工具 `{canonical}` 已连续失败 **{streak}** 次。建议：核对参数与路径、扩大 read 上下文、"
            + "换用替代工具，或 `switch_mode`；必要时向用户澄清需求。\n"
        )
    return result

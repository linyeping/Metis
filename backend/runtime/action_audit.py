# -*- coding: utf-8 -*-
"""FABLEADV-24: 全量动作审计。

每次工具调用（参数 / 结果摘要 / 状态 / 耗时 / 轮次）追加到工作区
``.metis/audit/agent-actions.jsonl``，可追溯、可导出。与既有的"权限审计"
（tool-permissions.jsonl）互补：那个记"要不要批准"，这个记"实际做了什么"。

设计约束（与项目一致）：本地优先、无遥测——审计只写本地文件，绝不外发。
默认开启（企业合规），``METIS_ACTION_AUDIT=0`` 可关。线程安全（并行工具）。
"""
from __future__ import annotations

import json
import os
import threading
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

_lock = threading.Lock()

_MAX_FIELD_CHARS = 2000
_MAX_FILE_BYTES = 16 * 1024 * 1024  # 16MB 后滚动一次，避免无限增长


def audit_enabled() -> bool:
    value = os.environ.get("METIS_ACTION_AUDIT", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _audit_path(workspace_root: str) -> str:
    root = os.path.abspath(workspace_root or os.getcwd())
    return os.path.join(root, ".metis", "audit", "agent-actions.jsonl")


def _truncate(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= _MAX_FIELD_CHARS else value[:_MAX_FIELD_CHARS] + f"...(+{len(value) - _MAX_FIELD_CHARS} chars)"
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        text = str(value)
    if len(text) <= _MAX_FIELD_CHARS:
        return value
    return text[:_MAX_FIELD_CHARS] + f"...(+{len(text) - _MAX_FIELD_CHARS} chars)"


def _looks_like_error(result: str) -> bool:
    text = (result or "").lstrip()
    if text.startswith("Error"):
        return True
    if text.startswith("[Expert") and "failed" in text[:80]:
        return True
    return "❌" in text[:8]


def _rotate_if_needed(path: str) -> None:
    try:
        if os.path.getsize(path) > _MAX_FILE_BYTES:
            backup = f"{path}.{int(time.time())}.bak"
            os.replace(path, backup)
    except OSError:
        pass


def record_actions(
    results: Iterable[Tuple[Any, str]],
    *,
    workspace_root: str = "",
    turn: int = 0,
    session_id: str = "",
) -> int:
    """把本轮的 (tool_call, result) 批量写入审计。返回写入条数。"""
    if not audit_enabled():
        return 0
    if not str(workspace_root or "").strip():
        return 0  # 无明确工作区时不写，避免落到 cwd 污染仓库
    rows: List[Dict[str, Any]] = []
    now = time.time()
    for tool_call, result in results:
        name = getattr(tool_call, "name", "") or ""
        call_id = getattr(tool_call, "id", "") or ""
        args = getattr(tool_call, "arguments", None)
        result_text = "" if result is None else str(result)
        rows.append(
            {
                "id": str(uuid.uuid4()),
                "ts": now,
                "iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
                "session_id": session_id,
                "turn": turn,
                "tool": name,
                "call_id": call_id,
                "args": _truncate(args),
                "result": _truncate(result_text),
                "status": "error" if _looks_like_error(result_text) else "success",
                "result_chars": len(result_text),
            }
        )
    if not rows:
        return 0
    path = _audit_path(workspace_root)
    try:
        with _lock:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _rotate_if_needed(path)
            with open(path, "a", encoding="utf-8") as handle:
                for row in rows:
                    handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError:
        return 0
    return len(rows)


def read_recent(workspace_root: str = "", limit: int = 100) -> List[Dict[str, Any]]:
    """读取最近 N 条审计（供导出 / 查看 / 测试）。"""
    path = _audit_path(workspace_root)
    if not os.path.isfile(path):
        return []
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as handle:
            lines = handle.readlines()
    except OSError:
        return []
    for line in lines[-max(0, limit):]:
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out

# -*- coding: utf-8 -*-
"""P6：Task 会话状态落盘（<workspace>/.delegate_sessions/<session_id>.json）。"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

# 中性目录名（避免其它产品名）；目标形态见 MIRO_FILE_INDEX 右列 <workspace>/.miro/…
DELEGATE_SESSIONS_DIRNAME = ".delegate_sessions"


def task_sessions_dir(workspace_root: str) -> Path:
    return Path(workspace_root).resolve() / DELEGATE_SESSIONS_DIRNAME


def resume_state_file_path(workspace_root: str, session_id: str) -> Path:
    return task_sessions_dir(workspace_root) / f"{session_id.strip()}.json"


def resume_state_file_missing_message(workspace_root: str, resume: str) -> Optional[str]:
    """resume 非空且状态文件不存在时返回 ❌ 错误文案；否则 None。"""
    rid = (resume or "").strip()
    if not rid:
        return None
    p = resume_state_file_path(workspace_root, rid)
    if not p.is_file():
        return f"❌ resume 状态文件不存在: {p}（禁止静默新开会话）"
    return None


def write_task_session_state(
    workspace_root: str,
    session_id: str,
    node_id: str,
    result_text: str,
    finished: bool,
    extra: Optional[Dict[str, Any]] = None,
) -> None:
    """
    MUST 字段：session_id, node_id, last_messages_digest（SHA256 hex）, finished, utc_timestamp。
    last_messages_digest：对 result_text 的 UTF-8 字节做 SHA256，输出 64 位小写 hex。
    """
    sid = (session_id or "").strip()
    if not sid:
        return
    d = task_sessions_dir(workspace_root)
    d.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256((result_text or "").encode("utf-8")).hexdigest()
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    doc: Dict[str, Any] = {
        "session_id": sid,
        "node_id": str(node_id),
        "last_messages_digest": digest,
        "finished": bool(finished),
        "utc_timestamp": ts,
    }
    if extra:
        doc.update(extra)
    p = d / f"{sid}.json"
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=0), encoding="utf-8")


def read_task_session_json(workspace_root: str, session_id: str) -> Optional[Dict[str, Any]]:
    p = resume_state_file_path(workspace_root, session_id)
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None

from __future__ import annotations

import json
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.core.paths import metis_dir


_SAFE_SESSION_RE = re.compile(r"[^A-Za-z0-9_.-]+")


def save_checkpoint(session_id: str, state: Dict[str, Any]) -> str:
    sid = _require_session_id(session_id)
    checkpoint_id = f"rtchk_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    payload = json.dumps(state or {}, ensure_ascii=False, default=str)
    created_at = time.time()
    with _connect(sid) as db:
        _ensure_schema(db)
        db.execute(
            "INSERT INTO checkpoints(checkpoint_id, session_id, created_at, state_json) VALUES (?, ?, ?, ?)",
            (checkpoint_id, sid, created_at, payload),
        )
    return checkpoint_id


def list_checkpoints(session_id: str, *, limit: int = 50) -> List[Dict[str, Any]]:
    sid = _require_session_id(session_id)
    path = _db_path(sid)
    if not path.exists():
        return []
    with _connect(sid) as db:
        _ensure_schema(db)
        rows = db.execute(
            """
            SELECT checkpoint_id, session_id, created_at, state_json
            FROM checkpoints
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (max(1, int(limit)),),
        ).fetchall()
    return [_row_payload(row, include_state=False) for row in rows]


def load_latest(session_id: str) -> Optional[Dict[str, Any]]:
    sid = _require_session_id(session_id)
    path = _db_path(sid)
    if not path.exists():
        return None
    with _connect(sid) as db:
        _ensure_schema(db)
        row = db.execute(
            """
            SELECT checkpoint_id, session_id, created_at, state_json
            FROM checkpoints
            ORDER BY created_at DESC
            LIMIT 1
            """
        ).fetchone()
    return _row_payload(row, include_state=True) if row is not None else None


def load_checkpoint(session_id: str, checkpoint_id: str) -> Optional[Dict[str, Any]]:
    sid = _require_session_id(session_id)
    cid = str(checkpoint_id or "").strip()
    if not cid:
        return None
    path = _db_path(sid)
    if not path.exists():
        return None
    with _connect(sid) as db:
        _ensure_schema(db)
        row = db.execute(
            """
            SELECT checkpoint_id, session_id, created_at, state_json
            FROM checkpoints
            WHERE checkpoint_id = ?
            LIMIT 1
            """,
            (cid,),
        ).fetchone()
    return _row_payload(row, include_state=True) if row is not None else None


def _connect(session_id: str) -> sqlite3.Connection:
    path = _db_path(session_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(str(path))
    db.row_factory = sqlite3.Row
    return db


def _ensure_schema(db: sqlite3.Connection) -> None:
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS checkpoints (
            checkpoint_id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            created_at REAL NOT NULL,
            state_json TEXT NOT NULL
        )
        """
    )
    db.execute(
        "CREATE INDEX IF NOT EXISTS idx_checkpoints_created_at ON checkpoints(created_at DESC)"
    )


def _row_payload(row: sqlite3.Row, *, include_state: bool) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "checkpoint_id": str(row["checkpoint_id"]),
        "session_id": str(row["session_id"]),
        "created_at": float(row["created_at"] or 0.0),
    }
    state = _decode_state(str(row["state_json"] or "{}"))
    payload["reason"] = str(state.get("reason") or state.get("kind") or "runtime")
    runtime = state.get("runtime") if isinstance(state.get("runtime"), dict) else {}
    payload["turn"] = int(runtime.get("turn") or 0)
    payload["tool_calls"] = int(runtime.get("tool_calls") or 0)
    message_count = len(state.get("history") or state.get("messages") or [])
    payload["message_count"] = int(message_count)
    if include_state:
        payload["state"] = state
    return payload


def _decode_state(raw: str) -> Dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def _db_path(session_id: str) -> Path:
    safe = _SAFE_SESSION_RE.sub("_", session_id).strip("._") or "session"
    return metis_dir("checkpoints") / f"{safe}.sqlite"


def _require_session_id(session_id: str) -> str:
    sid = str(session_id or "").strip()
    if not sid:
        raise ValueError("session_id is required")
    return sid

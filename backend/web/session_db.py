from __future__ import annotations

import json
import logging
import os
import re
import shutil
import sqlite3
import time
import uuid
from typing import Any, Dict, Iterable, List, Optional

from backend.core.paths import legacy_miro_home, metis_home

try:
    from backend.runtime.llm_backends._common import sanitize_for_log
except ImportError:  # pragma: no cover - supports package imports
    from backend.runtime.llm_backends._common import sanitize_for_log


JSON_MIGRATION_KEY = "json_migrated_v1"
SCHEMA_VERSION = 2
logger = logging.getLogger(__name__)


def default_data_root() -> str:
    return str(metis_home())


def legacy_data_root() -> str:
    return str(legacy_miro_home())


def _safe_id(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]", "", str(value or ""))
    return safe or "invalid"


def _json_load(path: str) -> Any:
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle)
    except (json.JSONDecodeError, OSError):
        return None


def _json_dump(path: str, data: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def _to_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _norm_path(path: str) -> str:
    return os.path.normcase(os.path.abspath(str(path or "")))


def _content_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                parts.append(str(block.get("text") or block.get("content") or ""))
        return "\n".join(part for part in parts if part)
    return str(content or "")


class MetisSessionDB:
    """SQLite authority for Metis workspaces, sessions, and session search."""

    def __init__(self, data_root: Optional[str] = None) -> None:
        self.data_root = os.path.abspath(data_root or default_data_root())
        self.legacy_root = os.path.join(os.path.dirname(self.data_root), ".miro")
        if data_root is None:
            self.legacy_root = legacy_data_root()
        self.sessions_dir = os.path.join(self.data_root, "sessions")
        self.workspaces_dir = os.path.join(self.data_root, "workspaces")
        self.db_path = os.path.join(self.data_root, "session-state.db")
        os.makedirs(self.sessions_dir, exist_ok=True)
        os.makedirs(self.workspaces_dir, exist_ok=True)
        self.ensure_schema()
        self.migrate_legacy_json_once()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=30.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    def ensure_schema(self) -> None:
        os.makedirs(self.data_root, exist_ok=True)
        self._ensure_integrity_or_rebuild()
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS schema_migrations ("
                "version INTEGER PRIMARY KEY, applied_at REAL NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS workspaces ("
                "id TEXT PRIMARY KEY, "
                "name TEXT NOT NULL, "
                "path TEXT NOT NULL, "
                "norm_path TEXT NOT NULL UNIQUE, "
                "created_at REAL NOT NULL, "
                "updated_at REAL NOT NULL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS sessions ("
                "id TEXT PRIMARY KEY, "
                "title TEXT NOT NULL, "
                "history_json TEXT NOT NULL, "
                "compact_state_json TEXT NOT NULL DEFAULT '{}', "
                "mode TEXT NOT NULL, "
                "workspace_id TEXT NOT NULL DEFAULT '', "
                "created_at REAL NOT NULL, "
                "updated_at REAL NOT NULL, "
                "deleted_at REAL NOT NULL DEFAULT 0)"
            )
            self._ensure_session_columns(connection)
            connection.execute(
                "CREATE INDEX IF NOT EXISTS idx_sessions_workspace_updated "
                "ON sessions(workspace_id, deleted_at, updated_at DESC)"
            )
            try:
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts "
                    "USING fts5(session_id, title, role, content, ts UNINDEXED, tokenize='trigram')"
                )
            except sqlite3.OperationalError:
                connection.execute(
                    "CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts "
                    "USING fts5(session_id, title, role, content, ts UNINDEXED, tokenize='unicode61')"
                )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, time.time()),
            )
            connection.commit()

    def _ensure_session_columns(self, connection: sqlite3.Connection) -> None:
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(sessions)").fetchall()
        }
        if "compact_state_json" not in columns:
            connection.execute("ALTER TABLE sessions ADD COLUMN compact_state_json TEXT NOT NULL DEFAULT '{}'")

    def _ensure_integrity_or_rebuild(self) -> None:
        if not os.path.exists(self.db_path):
            return
        try:
            with self.connect() as connection:
                row = connection.execute("PRAGMA integrity_check").fetchone()
                if row and str(row[0]).lower() == "ok":
                    return
                reason = str(row[0]) if row else "no integrity_check result"
        except Exception as exc:
            reason = f"{type(exc).__name__}: {exc}"
        backup_path = f"{self.db_path}.corrupt.{int(time.time())}"
        try:
            shutil.copy2(self.db_path, backup_path)
        except OSError:
            backup_path = ""
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(f"{self.db_path}{suffix}")
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning(
                    "Failed to remove corrupt database file %s: %s",
                    sanitize_for_log(f"{self.db_path}{suffix}"),
                    sanitize_for_log(exc),
                )
        logger.warning(
            "Database corrupted, rebuilt. Backup: %s Reason: %s",
            sanitize_for_log(backup_path or "unavailable"),
            sanitize_for_log(reason),
        )

    def journal_mode(self) -> str:
        with self.connect() as connection:
            row = connection.execute("PRAGMA journal_mode").fetchone()
        return str(row[0]).lower() if row else ""

    def migrate_legacy_json_once(self) -> None:
        with self.connect() as connection:
            row = connection.execute("SELECT value FROM meta WHERE key = ?", (JSON_MIGRATION_KEY,)).fetchone()
            if row and row["value"] == "1":
                return

        for root, prefer in ((self.legacy_root, False), (self.data_root, True)):
            self._migrate_workspaces_from_root(root, prefer=prefer)
            self._migrate_sessions_from_root(root, prefer=prefer)

        self.rebuild_search_index()
        self.write_all_json_mirrors()
        with self.connect() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO meta(key, value) VALUES (?, ?)",
                (JSON_MIGRATION_KEY, "1"),
            )
            connection.commit()

    def list_workspaces(self) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, name, path, created_at, updated_at FROM workspaces "
                "ORDER BY created_at ASC, id ASC"
            ).fetchall()
        return [dict(row) for row in rows]

    def create_workspace(self, path: str, name: str = "") -> Dict[str, Any]:
        abs_path = os.path.abspath(path)
        norm = _norm_path(abs_path)
        now = time.time()
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id, name, path, created_at, updated_at FROM workspaces WHERE norm_path = ?",
                (norm,),
            ).fetchone()
            if existing:
                row = dict(existing)
                if name and name != row["name"]:
                    row["name"] = name
                    row["updated_at"] = now
                    connection.execute(
                        "UPDATE workspaces SET name = ?, updated_at = ? WHERE id = ?",
                        (row["name"], row["updated_at"], row["id"]),
                    )
                    connection.commit()
                    self.write_workspaces_json_mirror()
                return row

            row = {
                "id": str(uuid.uuid4()),
                "name": name or os.path.basename(abs_path) or abs_path,
                "path": abs_path,
                "created_at": now,
                "updated_at": now,
            }
            connection.execute(
                "INSERT INTO workspaces(id, name, path, norm_path, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (row["id"], row["name"], row["path"], norm, row["created_at"], row["updated_at"]),
            )
            connection.commit()
        self.write_workspaces_json_mirror()
        return row

    def get_workspace(self, workspace_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, name, path, created_at, updated_at FROM workspaces WHERE id = ?",
                (str(workspace_id),),
            ).fetchone()
        return dict(row) if row else None

    def delete_workspace(self, workspace_id: str) -> bool:
        with self.connect() as connection:
            cursor = connection.execute("DELETE FROM workspaces WHERE id = ?", (str(workspace_id),))
            changed = cursor.rowcount > 0
            connection.commit()
        if changed:
            self.write_workspaces_json_mirror()
        return changed

    def list_sessions(self, workspace_id: Optional[str] = None) -> List[Dict[str, Any]]:
        params: tuple[Any, ...]
        where = "deleted_at = 0"
        if workspace_id is not None:
            where += " AND workspace_id = ?"
            params = (str(workspace_id),)
        else:
            params = ()
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, title, mode, workspace_id, created_at, updated_at "
                f"FROM sessions WHERE {where} "
                "ORDER BY updated_at DESC, created_at DESC, id DESC",
                params,
            ).fetchall()
        return [dict(row) for row in rows]

    def create_session(self, title: str, workspace_id: str = "", mode: str = "auto") -> Dict[str, Any]:
        now = self.next_timestamp()
        session = {
            "id": str(uuid.uuid4()),
            "title": title,
            "history": [],
            "compact_state": {},
            "mode": mode or "auto",
            "created_at": now,
            "updated_at": now,
            "workspace_id": str(workspace_id or ""),
        }
        self.upsert_session(session)
        return session

    def get_session(self, session_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT id, title, history_json, compact_state_json, mode, workspace_id, created_at, updated_at "
                "FROM sessions WHERE id = ? AND deleted_at = 0",
                (str(session_id),),
            ).fetchone()
        if not row:
            return None
        return self._session_from_row(row)

    def upsert_session(self, session: Dict[str, Any]) -> None:
        data = self._normalize_session(session)
        history_json = json.dumps(data["history"], ensure_ascii=False)
        compact_state_json = json.dumps(data["compact_state"], ensure_ascii=False)
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO sessions(id, title, history_json, compact_state_json, mode, workspace_id, created_at, updated_at, deleted_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0) "
                "ON CONFLICT(id) DO UPDATE SET "
                "title = excluded.title, "
                "history_json = excluded.history_json, "
                "compact_state_json = excluded.compact_state_json, "
                "mode = excluded.mode, "
                "workspace_id = excluded.workspace_id, "
                "created_at = excluded.created_at, "
                "updated_at = excluded.updated_at, "
                "deleted_at = 0",
                (
                    data["id"],
                    data["title"],
                    history_json,
                    compact_state_json,
                    data["mode"],
                    data["workspace_id"],
                    data["created_at"],
                    data["updated_at"],
                ),
            )
            connection.commit()
        self.index_session(data)
        self.write_session_json(data)
        self.write_sessions_index_json()

    def update_session_fields(
        self,
        session_id: str,
        *,
        title: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        mode: Optional[str] = None,
        workspace_id: Optional[str] = None,
        compact_state: Optional[Dict[str, Any]] = None,
        updated_at: Optional[float] = None,
    ) -> Optional[Dict[str, Any]]:
        session = self.get_session(session_id)
        if session is None:
            return None
        if title is not None:
            session["title"] = title
        if history is not None:
            session["history"] = history
        if mode is not None:
            session["mode"] = mode
        if workspace_id is not None:
            session["workspace_id"] = workspace_id
        if compact_state is not None:
            session["compact_state"] = compact_state
        session["updated_at"] = updated_at if updated_at is not None else self.next_timestamp()
        self.upsert_session(session)
        return session

    def assign_unscoped_sessions(self, workspace_id: str) -> None:
        sessions = self.list_sessions(workspace_id="")
        if not sessions:
            return
        for meta in sessions:
            self.update_session_fields(meta["id"], workspace_id=str(workspace_id or ""))

    def delete_session(self, session_id: str) -> bool:
        session_id = str(session_id)
        with self.connect() as connection:
            existed = connection.execute(
                "SELECT 1 FROM sessions WHERE id = ? AND deleted_at = 0",
                (session_id,),
            ).fetchone()
            connection.execute("DELETE FROM messages_fts WHERE session_id = ?", (session_id,))
            connection.execute("DELETE FROM sessions WHERE id = ?", (session_id,))
            connection.commit()
        self.remove_session_json(session_id)
        self.write_sessions_index_json()
        return bool(existed)

    def delete_sessions_for_workspace(self, workspace_id: str) -> int:
        target = str(workspace_id or "")
        sessions = self.list_sessions(workspace_id=target)
        for session in sessions:
            self.delete_session(str(session["id"]))
        return len(sessions)

    def next_timestamp(self) -> float:
        with self.connect() as connection:
            row = connection.execute("SELECT MAX(updated_at) AS latest FROM sessions WHERE deleted_at = 0").fetchone()
        latest = _to_float(row["latest"] if row else 0.0)
        return max(time.time(), latest + 0.000001)

    def index_session(self, session: Any) -> None:
        data = self._normalize_session(session)
        session_id = str(data["id"])
        title = str(data["title"] or "Metis Chat")
        ts = float(data["updated_at"] or time.time())
        with self.connect() as connection:
            connection.execute("DELETE FROM messages_fts WHERE session_id = ?", (session_id,))
            connection.execute(
                "INSERT INTO messages_fts(session_id, title, role, content, ts) VALUES (?, ?, ?, ?, ?)",
                (session_id, title, "title", title, ts),
            )
            for message in data["history"]:
                if not isinstance(message, dict):
                    continue
                role = str(message.get("role") or "message")
                content = _content_to_text(message.get("content"))
                if not content.strip():
                    continue
                connection.execute(
                    "INSERT INTO messages_fts(session_id, title, role, content, ts) VALUES (?, ?, ?, ?, ?)",
                    (session_id, title, role, content, ts),
                )
            connection.commit()

    def delete_search_session(self, session_id: str) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM messages_fts WHERE session_id = ?", (str(session_id),))
            connection.commit()

    def rebuild_search_index(self) -> None:
        with self.connect() as connection:
            connection.execute("DELETE FROM messages_fts")
            rows = connection.execute(
                "SELECT id, title, history_json, compact_state_json, mode, workspace_id, created_at, updated_at "
                "FROM sessions WHERE deleted_at = 0"
            ).fetchall()
            connection.commit()
        for row in rows:
            self.index_session(self._session_from_row(row))

    def search_sessions(self, query: str, limit: int = 20) -> List[Dict[str, Any]]:
        query = (query or "").strip()
        if not query:
            return []
        match_query = self._match_query(query)
        if not match_query:
            return []

        rows: List[sqlite3.Row] = []
        fts_failed = False
        with self.connect() as connection:
            try:
                rows = connection.execute(
                    """
                    SELECT
                        messages_fts.session_id,
                        messages_fts.title,
                        snippet(messages_fts, 3, '<mark>', '</mark>', '...', 16) AS snippet,
                        messages_fts.ts,
                        bm25(messages_fts) AS score
                    FROM messages_fts
                    JOIN sessions ON sessions.id = messages_fts.session_id
                    WHERE messages_fts MATCH ? AND sessions.deleted_at = 0
                    ORDER BY score
                    LIMIT ?
                    """,
                    (match_query, int(limit)),
                ).fetchall()
            except sqlite3.OperationalError:
                fts_failed = True
                rows = []

        results = self._dedupe_search_rows(rows)
        if results:
            return results
        if fts_failed:
            return self._fallback_like_search(query, limit)
        return []

    def write_all_json_mirrors(self) -> None:
        self.write_workspaces_json_mirror()
        self.write_sessions_index_json()
        for session in self.iter_full_sessions():
            self.write_session_json(session)

    def write_workspaces_json_mirror(self) -> None:
        items = self.list_workspaces()
        _json_dump(os.path.join(self.workspaces_dir, "index.json"), items)

    def write_sessions_index_json(self) -> None:
        items = [
            {
                "id": item["id"],
                "title": item["title"],
                "created_at": item["created_at"],
                "updated_at": item["updated_at"],
                "workspace_id": item["workspace_id"],
            }
            for item in self.list_sessions()
        ]
        _json_dump(os.path.join(self.sessions_dir, "index.json"), items)

    def write_session_json(self, session: Any) -> None:
        data = self._normalize_session(session)
        _json_dump(os.path.join(self.sessions_dir, f"{_safe_id(data['id'])}.json"), data)

    def remove_session_json(self, session_id: str) -> None:
        for root in (self.data_root, self.legacy_root):
            path = os.path.join(root, "sessions", f"{_safe_id(session_id)}.json")
            try:
                if os.path.isfile(path):
                    os.remove(path)
            except OSError:
                pass

    def iter_full_sessions(self) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, title, history_json, compact_state_json, mode, workspace_id, created_at, updated_at "
                "FROM sessions WHERE deleted_at = 0 "
                "ORDER BY updated_at DESC, created_at DESC, id DESC"
            ).fetchall()
        return [self._session_from_row(row) for row in rows]

    def _migrate_workspaces_from_root(self, root: str, *, prefer: bool) -> None:
        path = os.path.join(root, "workspaces", "index.json")
        items = _json_load(path)
        if not isinstance(items, list):
            return
        for item in items:
            if not isinstance(item, dict) or not item.get("id") or not item.get("path"):
                continue
            self._upsert_migrated_workspace(item, prefer=prefer)

    def _migrate_sessions_from_root(self, root: str, *, prefer: bool) -> None:
        sessions_by_id: Dict[str, Dict[str, Any]] = {}
        index_items = _json_load(os.path.join(root, "sessions", "index.json"))
        if isinstance(index_items, list):
            for item in index_items:
                if isinstance(item, dict) and item.get("id"):
                    sessions_by_id[str(item["id"])] = dict(item)

        sessions_dir = os.path.join(root, "sessions")
        try:
            filenames = os.listdir(sessions_dir)
        except OSError:
            filenames = []
        for filename in filenames:
            if not filename.endswith(".json") or filename == "index.json":
                continue
            data = _json_load(os.path.join(sessions_dir, filename))
            if not isinstance(data, dict) or not data.get("id"):
                continue
            existing = sessions_by_id.get(str(data["id"]), {})
            merged = {**existing, **data}
            sessions_by_id[str(data["id"])] = merged

        for item in sessions_by_id.values():
            self._upsert_migrated_session(item, prefer=prefer)

    def _upsert_migrated_workspace(self, item: Dict[str, Any], *, prefer: bool) -> None:
        path = str(item.get("path") or "")
        if not path:
            return
        now = time.time()
        row = {
            "id": str(item.get("id") or uuid.uuid4()),
            "name": str(item.get("name") or os.path.basename(path) or path),
            "path": os.path.abspath(path),
            "created_at": _to_float(item.get("created_at"), now),
            "updated_at": _to_float(item.get("updated_at"), now),
        }
        norm = _norm_path(row["path"])
        with self.connect() as connection:
            existing = connection.execute(
                "SELECT id, name, path, created_at, updated_at FROM workspaces WHERE norm_path = ?",
                (norm,),
            ).fetchone()
            if existing:
                name = row["name"] if prefer or not existing["name"] else existing["name"]
                connection.execute(
                    "UPDATE workspaces SET name = ?, created_at = ?, updated_at = ? WHERE id = ?",
                    (
                        name,
                        min(_to_float(existing["created_at"]), row["created_at"]),
                        max(_to_float(existing["updated_at"]), row["updated_at"]),
                        existing["id"],
                    ),
                )
            else:
                connection.execute(
                    "INSERT INTO workspaces(id, name, path, norm_path, created_at, updated_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (row["id"], row["name"], row["path"], norm, row["created_at"], row["updated_at"]),
                )
            connection.commit()

    def _upsert_migrated_session(self, item: Dict[str, Any], *, prefer: bool) -> None:
        if not item.get("id"):
            return
        now = time.time()
        data = self._normalize_session(
            {
                "id": str(item.get("id")),
                "title": str(item.get("title") or "New chat"),
                "history": item.get("history") if isinstance(item.get("history"), list) else [],
                "compact_state": item.get("compact_state") if isinstance(item.get("compact_state"), dict) else {},
                "mode": str(item.get("mode") or "auto"),
                "workspace_id": str(item.get("workspace_id") or ""),
                "created_at": _to_float(item.get("created_at"), now),
                "updated_at": _to_float(item.get("updated_at"), now),
            }
        )
        existing = self.get_session(data["id"])
        if existing and not prefer and _to_float(existing["updated_at"]) > _to_float(data["updated_at"]):
            return
        self.upsert_session(data)

    def _normalize_session(self, session: Any) -> Dict[str, Any]:
        if isinstance(session, dict):
            source = session
            session_id = str(source.get("id") or "")
            title = str(source.get("title") or "New chat")
            history = source.get("history") if isinstance(source.get("history"), list) else []
            compact_state = source.get("compact_state")
            if not isinstance(compact_state, dict):
                compact_state = source.get("compactState")
            compact_state = compact_state if isinstance(compact_state, dict) else {}
            mode = str(source.get("mode") or "auto")
            workspace_id = str(source.get("workspace_id") or "")
            created_at = _to_float(source.get("created_at"), time.time())
            updated_at = _to_float(source.get("updated_at"), created_at)
        else:
            session_id = str(getattr(session, "id", "") or "")
            title = str(getattr(session, "title", "") or "New chat")
            raw_history = getattr(session, "history", [])
            history = raw_history if isinstance(raw_history, list) else []
            raw_compact_state = getattr(session, "compact_state", {})
            compact_state = raw_compact_state if isinstance(raw_compact_state, dict) else {}
            mode = str(getattr(session, "mode", "") or "auto")
            workspace_id = str(getattr(session, "workspace_id", "") or "")
            created_at = _to_float(getattr(session, "created_at", None), time.time())
            updated_at = _to_float(getattr(session, "updated_at", None), created_at)
        if not session_id:
            session_id = str(uuid.uuid4())
        return {
            "id": session_id,
            "title": title,
            "history": history,
            "compact_state": dict(compact_state),
            "mode": mode,
            "workspace_id": workspace_id,
            "created_at": created_at,
            "updated_at": updated_at,
        }

    def _session_from_row(self, row: sqlite3.Row) -> Dict[str, Any]:
        try:
            history = json.loads(row["history_json"])
        except (json.JSONDecodeError, TypeError):
            history = []
        try:
            compact_state_raw = row["compact_state_json"]
        except (IndexError, KeyError):
            compact_state_raw = "{}"
        try:
            compact_state = json.loads(compact_state_raw)
        except (json.JSONDecodeError, TypeError):
            compact_state = {}
        return {
            "id": str(row["id"]),
            "title": str(row["title"] or "New chat"),
            "history": history if isinstance(history, list) else [],
            "compact_state": compact_state if isinstance(compact_state, dict) else {},
            "mode": str(row["mode"] or "auto"),
            "workspace_id": str(row["workspace_id"] or ""),
            "created_at": _to_float(row["created_at"]),
            "updated_at": _to_float(row["updated_at"]),
        }

    def _match_query(self, query: str) -> str:
        safe = query.replace('"', " ").strip()
        return f'"{safe}"' if safe else ""

    def _dedupe_search_rows(self, rows: Iterable[sqlite3.Row]) -> List[Dict[str, Any]]:
        seen: set[str] = set()
        results: List[Dict[str, Any]] = []
        for row in rows:
            session_id = str(row["session_id"])
            if session_id in seen:
                continue
            seen.add(session_id)
            results.append(
                {
                    "session_id": session_id,
                    "title": row["title"],
                    "snippet": row["snippet"],
                    "ts": float(row["ts"] or 0),
                    "score": float(row["score"] or 0),
                }
            )
        return results

    def _fallback_like_search(self, query: str, limit: int) -> List[Dict[str, Any]]:
        pattern = f"%{query}%"
        with self.connect() as connection:
            rows = connection.execute(
                "SELECT id, title, history_json, updated_at FROM sessions "
                "WHERE deleted_at = 0 AND (title LIKE ? OR history_json LIKE ?) "
                "ORDER BY updated_at DESC LIMIT ?",
                (pattern, pattern, int(limit)),
            ).fetchall()
        results: List[Dict[str, Any]] = []
        for row in rows:
            results.append(
                {
                    "session_id": str(row["id"]),
                    "title": str(row["title"] or "New chat"),
                    "snippet": self._snippet_from_history(str(row["history_json"] or ""), query),
                    "ts": float(row["updated_at"] or 0),
                    "score": 0.0,
                }
            )
        return results

    def _snippet_from_history(self, history_json: str, query: str) -> str:
        text = history_json
        idx = text.lower().find(query.lower())
        if idx < 0:
            return ""
        start = max(0, idx - 40)
        end = min(len(text), idx + len(query) + 80)
        return text[start:end]


_default_db: Optional[MetisSessionDB] = None


def get_session_db() -> MetisSessionDB:
    global _default_db
    if _default_db is None:
        _default_db = MetisSessionDB()
    return _default_db

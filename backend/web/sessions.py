from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.core.paths import legacy_miro_path, metis_dir

from .session_db import MetisSessionDB


def _sessions_dir() -> str:
    """Return the user session mirror directory, creating it if needed."""
    return str(metis_dir("sessions"))


def _legacy_sessions_dir() -> str:
    return str(legacy_miro_path("sessions"))


@dataclass
class SessionMeta:
    """Lightweight session metadata for the sidebar list."""

    id: str
    title: str
    created_at: float
    updated_at: float
    workspace_id: str = ""


@dataclass
class Session:
    """Full session with chat history."""

    id: str
    title: str
    history: List[Dict[str, Any]]
    compact_state: Dict[str, Any] = field(default_factory=dict)
    mode: str = "auto"
    created_at: float = 0.0
    updated_at: float = 0.0
    workspace_id: str = ""


class SessionManager:
    """Manages session lifecycle using SQLite as the authority."""

    def __init__(self, data_root: Optional[str] = None, db: Optional[MetisSessionDB] = None) -> None:
        self._db = db or MetisSessionDB(data_root=data_root)

    def list_sessions(self, workspace_id: Optional[str] = None) -> List[SessionMeta]:
        """Return sessions with the most recently updated session first.

        If workspace_id is given, only sessions belonging to that workspace
        are returned. Pass None to return all sessions.
        """
        return [self._meta_from_dict(item) for item in self._db.list_sessions(workspace_id=workspace_id)]

    def create_session(self, title: str = "", workspace_id: str = "") -> Session:
        """Create a new empty session."""
        data = self._db.create_session(
            title=title or self._default_title(),
            workspace_id=workspace_id,
        )
        return self._session_from_dict(data)

    def get_session(self, session_id: str) -> Optional[Session]:
        """Load a full session by ID."""
        data = self._db.get_session(session_id)
        return self._session_from_dict(data) if data is not None else None

    def update_session(
        self,
        session_id: str,
        *,
        title: Optional[str] = None,
        history: Optional[List[Dict[str, Any]]] = None,
        mode: Optional[str] = None,
        workspace_id: Optional[str] = None,
        compact_state: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Update specific fields of a session. Returns True if found."""
        updated = self._db.update_session_fields(
            session_id,
            title=title,
            history=history,
            mode=mode,
            workspace_id=workspace_id,
            compact_state=compact_state,
        )
        return updated is not None

    def assign_unscoped_sessions(self, workspace_id: str) -> None:
        """Attach legacy sessions without a workspace to the default workspace."""
        self._db.assign_unscoped_sessions(workspace_id)

    def delete_session(self, session_id: str) -> bool:
        """Delete a session. Returns True if it existed."""
        return self._db.delete_session(session_id)

    def delete_sessions_for_workspace(self, workspace_id: str) -> int:
        """Delete every session assigned to a workspace."""
        return self._db.delete_sessions_for_workspace(workspace_id)

    def _default_title(self) -> str:
        return datetime.now().strftime("Chat %Y-%m-%d %H:%M")

    def _meta_from_dict(self, item: Dict[str, Any]) -> SessionMeta:
        return SessionMeta(
            id=str(item["id"]),
            title=str(item.get("title") or "New chat"),
            created_at=float(item.get("created_at") or 0.0),
            updated_at=float(item.get("updated_at") or 0.0),
            workspace_id=str(item.get("workspace_id") or ""),
        )

    def _session_from_dict(self, data: Dict[str, Any]) -> Session:
        history = data.get("history") if isinstance(data.get("history"), list) else []
        compact_state = data.get("compact_state") if isinstance(data.get("compact_state"), dict) else {}
        return Session(
            id=str(data["id"]),
            title=str(data.get("title") or "New chat"),
            history=history,
            compact_state=dict(compact_state),
            mode=str(data.get("mode") or "auto"),
            created_at=float(data.get("created_at") or 0.0),
            updated_at=float(data.get("updated_at") or 0.0),
            workspace_id=str(data.get("workspace_id") or ""),
        )


_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager

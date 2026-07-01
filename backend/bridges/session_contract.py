"""Session and workspace boundary for future durable storage migration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, NewType, Optional, Protocol, Sequence


SessionId = NewType("SessionId", str)
WorkspaceId = NewType("WorkspaceId", str)


@dataclass(frozen=True)
class SessionRecord:
    session_id: SessionId
    workspace_id: WorkspaceId
    title: str
    created_at: datetime
    updated_at: datetime
    message_count: int = 0
    mode: str = "chat"
    metadata: Mapping[str, Any] = field(default_factory=dict)


class SessionStoreProtocol(Protocol):
    def list_workspaces(self) -> Sequence[WorkspaceId]:
        """Return known workspace identifiers."""

    def list_sessions(self, workspace_id: Optional[WorkspaceId] = None) -> Sequence[SessionRecord]:
        """Return sessions, optionally filtered by workspace."""

    def get_session(self, session_id: SessionId) -> Optional[SessionRecord]:
        """Return one session or None."""

    def upsert_session(self, record: SessionRecord) -> None:
        """Create or update a session record."""

    def delete_session(self, session_id: SessionId) -> bool:
        """Delete a session and return whether anything changed."""

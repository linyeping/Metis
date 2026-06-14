from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.core.memory.workspace_state import clear_read_tracking


@dataclass
class RuntimeState:
    """Mutable desktop runtime state for the single local Flask process.

    This is deliberately small. Persistence remains in `web.sessions`; this
    object only owns the currently selected workspace/session and the in-flight
    conversation buffer that used to live as scattered module globals.
    """

    active_session_id: Optional[str] = None
    active_workspace_id: str = ""
    chat_history: List[Dict[str, Any]] = field(default_factory=list)
    compact_state: Dict[str, Any] = field(default_factory=dict)
    execution_mode: str = "auto"
    last_compact_status: Dict[str, Any] = field(default_factory=dict)
    learning_nudged_sessions: set[str] = field(default_factory=set)

    def clear_session(self) -> None:
        self.active_session_id = None
        self.chat_history = []
        self.compact_state = {}
        self.execution_mode = "auto"
        clear_read_tracking()

    def activate_session(
        self,
        session_id: Optional[str],
        *,
        history: Optional[List[Dict[str, Any]]] = None,
        compact_state: Optional[Dict[str, Any]] = None,
        mode: str = "auto",
    ) -> None:
        self.active_session_id = session_id
        self.chat_history = list(history or [])
        self.compact_state = dict(compact_state or {})
        self.execution_mode = mode or "auto"
        clear_read_tracking()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "active_session_id": self.active_session_id,
            "active_workspace_id": self.active_workspace_id,
            "history_length": len(self.chat_history),
            "execution_mode": self.execution_mode,
            "compact": self.last_compact_status or {"running": False},
        }

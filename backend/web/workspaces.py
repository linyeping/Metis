from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from backend.core.paths import legacy_miro_path, metis_dir

from .session_db import MetisSessionDB


def _workspaces_dir() -> str:
    """Return the workspace metadata mirror directory, creating it if needed."""
    return str(metis_dir("workspaces"))


def _legacy_workspaces_dir() -> str:
    return str(legacy_miro_path("workspaces"))


@dataclass
class Workspace:
    """A workspace is a root project directory."""

    id: str
    name: str
    path: str
    created_at: float = 0.0
    updated_at: float = 0.0


class WorkspaceManager:
    """Manages workspace lifecycle using SQLite as the authority."""

    def __init__(self, data_root: Optional[str] = None, db: Optional[MetisSessionDB] = None) -> None:
        self._db = db or MetisSessionDB(data_root=data_root)

    def list_workspaces(self) -> List[Workspace]:
        """Return all workspaces in a stable created_at order."""
        return [self._workspace_from_dict(item) for item in self._db.list_workspaces()]

    def create_workspace(self, path: str, name: str = "") -> Workspace:
        """Create a workspace from a directory path."""
        return self._workspace_from_dict(self._db.create_workspace(path, name=name))

    def get_workspace(self, workspace_id: str) -> Optional[Workspace]:
        """Look up a workspace by ID."""
        data = self._db.get_workspace(workspace_id)
        return self._workspace_from_dict(data) if data is not None else None

    def delete_workspace(self, workspace_id: str) -> bool:
        """Delete workspace metadata without touching files on disk."""
        return self._db.delete_workspace(workspace_id)

    def _workspace_from_dict(self, item: Dict[str, Any]) -> Workspace:
        return Workspace(
            id=str(item["id"]),
            name=str(item.get("name") or ""),
            path=str(item.get("path") or ""),
            created_at=float(item.get("created_at") or 0.0),
            updated_at=float(item.get("updated_at") or 0.0),
        )


_manager: Optional[WorkspaceManager] = None


def get_workspace_manager() -> WorkspaceManager:
    global _manager
    if _manager is None:
        _manager = WorkspaceManager()
    return _manager

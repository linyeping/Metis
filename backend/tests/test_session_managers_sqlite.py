from __future__ import annotations

from pathlib import Path

from backend.web import session_db as session_db_module
from backend.web import session_search
from backend.web.session_db import MetisSessionDB
from backend.web.sessions import SessionManager
from backend.web.workspaces import WorkspaceManager


def test_workspace_manager_deduplicates_same_path(tmp_path: Path) -> None:
    db = MetisSessionDB(data_root=str(tmp_path / ".metis"))
    manager = WorkspaceManager(db=db)
    project = tmp_path / "project"

    first = manager.create_workspace(str(project), name="First")
    second = manager.create_workspace(str(project), name="Second")

    assert first.id == second.id
    assert second.name == "Second"
    assert len(manager.list_workspaces()) == 1


def test_session_manager_create_update_delete_lifecycle(tmp_path: Path) -> None:
    db = MetisSessionDB(data_root=str(tmp_path / ".metis"))
    workspace_manager = WorkspaceManager(db=db)
    session_manager = SessionManager(db=db)
    workspace = workspace_manager.create_workspace(str(tmp_path / "project"), name="Project")

    session = session_manager.create_session("Lifecycle", workspace_id=workspace.id)
    assert session_manager.get_session(session.id) is not None
    assert len(session_manager.list_sessions(workspace_id=workspace.id)) == 1

    assert session_manager.update_session(
        session.id,
        title="Updated",
        history=[{"role": "user", "content": "updated session content"}],
        compact_state={"summary": "updated summary", "boundary_index": 1},
        mode="plan",
    )
    updated = session_manager.get_session(session.id)
    assert updated is not None
    assert updated.title == "Updated"
    assert updated.mode == "plan"
    assert updated.history[0]["content"] == "updated session content"
    assert updated.compact_state["summary"] == "updated summary"

    assert session_manager.delete_session(session.id) is True
    assert session_manager.get_session(session.id) is None
    assert session_manager.list_sessions(workspace_id=workspace.id) == []


def test_delete_sessions_for_workspace_only_deletes_target_workspace(tmp_path: Path) -> None:
    db = MetisSessionDB(data_root=str(tmp_path / ".metis"))
    workspace_manager = WorkspaceManager(db=db)
    session_manager = SessionManager(db=db)
    workspace_a = workspace_manager.create_workspace(str(tmp_path / "a"), name="A")
    workspace_b = workspace_manager.create_workspace(str(tmp_path / "b"), name="B")
    session_a = session_manager.create_session("A", workspace_id=workspace_a.id)
    session_b = session_manager.create_session("B", workspace_id=workspace_b.id)

    assert session_manager.delete_sessions_for_workspace(workspace_a.id) == 1
    assert session_manager.get_session(session_a.id) is None
    assert session_manager.get_session(session_b.id) is not None
    assert len(session_manager.list_sessions(workspace_id=workspace_b.id)) == 1


def test_assign_unscoped_sessions(tmp_path: Path) -> None:
    db = MetisSessionDB(data_root=str(tmp_path / ".metis"))
    workspace_manager = WorkspaceManager(db=db)
    session_manager = SessionManager(db=db)
    workspace = workspace_manager.create_workspace(str(tmp_path / "project"), name="Project")
    session = session_manager.create_session("Unscoped")

    assert session_manager.get_session(session.id).workspace_id == ""

    session_manager.assign_unscoped_sessions(workspace.id)
    assert session_manager.get_session(session.id).workspace_id == workspace.id
    assert len(session_manager.list_sessions(workspace_id=workspace.id)) == 1


def test_session_search_module_uses_session_db(tmp_path: Path) -> None:
    db = MetisSessionDB(data_root=str(tmp_path / ".metis"))
    old_default = session_db_module._default_db
    session_db_module._default_db = db
    try:
        session_manager = SessionManager(db=db)
        session = session_manager.create_session("Search module")
        session_manager.update_session(
            session.id,
            history=[{"role": "user", "content": "module search content"}],
        )

        results = session_search.search_sessions("module")
        assert results and results[0]["session_id"] == session.id

        session_search.delete_session(session.id)
        assert session_search.search_sessions("module") == []
    finally:
        session_db_module._default_db = old_default

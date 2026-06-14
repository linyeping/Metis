from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from backend.web.session_db import MetisSessionDB


def _write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_session_db_schema_and_wal(tmp_path: Path) -> None:
    db = MetisSessionDB(data_root=str(tmp_path / ".metis"))

    assert db.db_path.endswith("session-state.db")
    assert db.journal_mode() == "wal"

    with sqlite3.connect(db.db_path) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'virtual')"
            ).fetchall()
        }
    assert {"workspaces", "sessions", "messages_fts"} <= tables


def test_metis_json_migration_to_sqlite(tmp_path: Path) -> None:
    root = tmp_path / ".metis"
    workspace = {
        "id": "ws-metis",
        "name": "Metis Workspace",
        "path": str(tmp_path / "project"),
        "created_at": 10.0,
        "updated_at": 11.0,
    }
    session_index = {
        "id": "s-metis",
        "title": "Index title",
        "created_at": 20.0,
        "updated_at": 21.0,
        "workspace_id": "ws-metis",
    }
    session_file = {
        **session_index,
        "title": "File title",
        "history": [{"role": "user", "content": "hello migrated metis"}],
        "mode": "auto",
    }
    _write_json(root / "workspaces" / "index.json", [workspace])
    _write_json(root / "sessions" / "index.json", [session_index])
    _write_json(root / "sessions" / "s-metis.json", session_file)

    db = MetisSessionDB(data_root=str(root))

    assert db.get_workspace("ws-metis")["name"] == "Metis Workspace"
    session = db.get_session("s-metis")
    assert session is not None
    assert session["title"] == "File title"
    assert session["history"][0]["content"] == "hello migrated metis"
    assert db.search_sessions("migrated")


def test_miro_legacy_json_migration_to_sqlite(tmp_path: Path) -> None:
    legacy_root = tmp_path / ".miro"
    workspace = {
        "id": "ws-legacy",
        "name": "Legacy Workspace",
        "path": str(tmp_path / "legacy-project"),
        "created_at": 10.0,
        "updated_at": 12.0,
    }
    session = {
        "id": "s-legacy",
        "title": "Legacy title",
        "history": [{"role": "assistant", "content": "旧卷书斋 legacy content"}],
        "mode": "auto",
        "created_at": 20.0,
        "updated_at": 22.0,
        "workspace_id": "ws-legacy",
    }
    _write_json(legacy_root / "workspaces" / "index.json", [workspace])
    _write_json(legacy_root / "sessions" / "index.json", [session])
    _write_json(legacy_root / "sessions" / "s-legacy.json", session)

    db = MetisSessionDB(data_root=str(tmp_path / ".metis"))

    assert db.get_workspace("ws-legacy")["name"] == "Legacy Workspace"
    loaded = db.get_session("s-legacy")
    assert loaded is not None
    assert loaded["workspace_id"] == "ws-legacy"
    assert db.search_sessions("旧卷书斋")


def test_json_mirror_is_written(tmp_path: Path) -> None:
    db = MetisSessionDB(data_root=str(tmp_path / ".metis"))
    workspace = db.create_workspace(str(tmp_path / "project"), name="Mirror")
    session = db.create_session("Mirror session", workspace_id=workspace["id"])
    db.update_session_fields(
        session["id"],
        history=[{"role": "user", "content": "mirror content"}],
        compact_state={"summary": "mirror summary", "boundary_index": 1},
    )

    index_path = tmp_path / ".metis" / "sessions" / "index.json"
    session_path = tmp_path / ".metis" / "sessions" / f"{session['id']}.json"
    workspace_index_path = tmp_path / ".metis" / "workspaces" / "index.json"

    assert index_path.is_file()
    assert session_path.is_file()
    assert workspace_index_path.is_file()
    assert json.loads(session_path.read_text(encoding="utf-8"))["history"][0]["content"] == "mirror content"
    assert json.loads(session_path.read_text(encoding="utf-8"))["compact_state"]["summary"] == "mirror summary"


def test_search_deleted_session_is_removed(tmp_path: Path) -> None:
    db = MetisSessionDB(data_root=str(tmp_path / ".metis"))
    workspace = db.create_workspace(str(tmp_path / "project"), name="Search")
    session = db.create_session("Search session", workspace_id=workspace["id"])
    db.update_session_fields(
        session["id"],
        history=[{"role": "user", "content": "needle 中文内容"}],
    )

    assert db.search_sessions("needle")
    assert db.search_sessions("中文内容")

    assert db.delete_session(session["id"]) is True
    assert db.get_session(session["id"]) is None
    assert db.search_sessions("needle") == []
    assert db.search_sessions("中文内容") == []


def test_delete_workspace_metadata_does_not_delete_directory(tmp_path: Path) -> None:
    project = tmp_path / "real-project"
    project.mkdir()
    keep_file = project / "keep.txt"
    keep_file.write_text("keep", encoding="utf-8")

    db = MetisSessionDB(data_root=str(tmp_path / ".metis"))
    workspace = db.create_workspace(str(project), name="Real")

    assert db.delete_workspace(workspace["id"]) is True
    assert project.is_dir()
    assert keep_file.read_text(encoding="utf-8") == "keep"


def test_rebuild_search_index_from_sqlite_sessions(tmp_path: Path) -> None:
    db = MetisSessionDB(data_root=str(tmp_path / ".metis"))
    workspace = db.create_workspace(str(tmp_path / "project"), name="Rebuild")
    session = db.create_session("Rebuild session", workspace_id=workspace["id"])
    db.update_session_fields(
        session["id"],
        history=[{"role": "assistant", "content": "rebuildable content"}],
    )
    db.delete_search_session(session["id"])
    assert db.search_sessions("rebuildable") == []

    db.rebuild_search_index()
    assert db.search_sessions("rebuildable")

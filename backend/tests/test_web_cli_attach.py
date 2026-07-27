from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from backend.web import app as web_app
from backend.web import cli_attach
from backend.web.session_db import MetisSessionDB
from backend.web.sessions import SessionManager
from backend.web.workspaces import WorkspaceManager
from flask import jsonify


@pytest.fixture
def attach_web_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db = MetisSessionDB(data_root=str(tmp_path / ".metis"))
    sessions = SessionManager(db=db)
    workspaces = WorkspaceManager(db=db)
    monkeypatch.setattr(web_app, "get_session_manager", lambda: sessions)
    monkeypatch.setattr(web_app, "get_workspace_manager", lambda: workspaces)
    monkeypatch.setattr(cli_attach, "_ATTACH_TOKEN", "test-token-" + "x" * 32)
    monkeypatch.setattr(cli_attach, "_ATTACH_INSTANCE_ID", "instance-" + "y" * 32)
    web_app._permission_request_store.clear()
    return web_app.app, sessions


def _headers(token: str = "test-token-" + "x" * 32) -> dict[str, str]:
    return {"X-Metis-CLI-Token": token}


def test_attach_routes_require_token(attach_web_app: Any) -> None:
    app, _ = attach_web_app

    with app.test_client() as client:
        missing = client.get("/api/cli/v1/hello")
        wrong = client.get("/api/cli/v1/hello", headers=_headers("wrong"))
        allowed = client.get("/api/cli/v1/hello", headers=_headers())

    assert missing.status_code == 403
    assert wrong.status_code == 403
    assert allowed.status_code == 200
    assert allowed.get_json()["protocol"] == cli_attach.ATTACH_PROTOCOL


def test_attach_session_is_created_without_switching_desktop_active_session(
    attach_web_app: Any,
    tmp_path: Path,
) -> None:
    app, sessions = attach_web_app
    workspace = tmp_path / "project"
    workspace.mkdir()
    web_app._runtime_state.clear_session()
    active = sessions.create_session(title="Desktop", mode="chat")
    web_app._runtime_state.activate_session(active.id, history=[], compact_state={}, mode="chat")

    with app.test_client() as client:
        response = client.post(
            "/api/cli/v1/sessions",
            headers=_headers(),
            json={"workspace": str(workspace), "mode": "code", "title": "CLI session"},
        )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["schema"] == "metis.cli_attach.session.v1"
    assert payload["created"] is True
    assert payload["workspace"] == str(workspace)
    assert web_app._runtime_state.active_session_id == active.id


def test_attach_run_wrapper_authorizes_before_forwarding(
    attach_web_app: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, _ = attach_web_app
    calls = {"count": 0}

    def fake_create_run():
        calls["count"] += 1
        return jsonify({"ok": True, "run_id": "run-1"})

    monkeypatch.setattr(web_app, "create_run", fake_create_run)
    with app.test_client() as client:
        denied = client.post("/api/cli/v1/runs", json={"message": "task"})
        allowed = client.post(
            "/api/cli/v1/runs",
            headers=_headers(),
            json={"message": "task"},
        )

    assert denied.status_code == 403
    assert allowed.status_code == 200
    assert calls["count"] == 1


def test_attach_session_rejects_missing_workspace(attach_web_app: Any) -> None:
    app, _ = attach_web_app

    with app.test_client() as client:
        response = client.post("/api/cli/v1/sessions", headers=_headers(), json={})

    assert response.status_code == 400


def test_attach_resume_prefix_uses_desktop_session_workspace(
    attach_web_app: Any,
    tmp_path: Path,
) -> None:
    app, sessions = attach_web_app
    workspace_path = tmp_path / "resume-project"
    workspace_path.mkdir()
    workspace = web_app.get_workspace_manager().create_workspace(str(workspace_path))
    session = sessions.create_session(title="Resume", workspace_id=workspace.id, mode="code")

    with app.test_client() as client:
        response = client.post(
            "/api/cli/v1/sessions",
            headers=_headers(),
            json={"session_id": session.id[:12]},
        )

    payload = response.get_json()
    assert response.status_code == 200
    assert payload["created"] is False
    assert payload["session_id"] == session.id
    assert payload["workspace"] == str(workspace_path)


def test_start_server_publishes_selected_port_and_clears_discovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    published: list[tuple[str, int]] = []
    cleared = {"count": 0}
    attempts = {"count": 0}

    def fake_publish(*, host: str, port: int):
        published.append((host, port))

    def fake_clear():
        cleared["count"] += 1

    def fake_run(**kwargs: Any):
        attempts["count"] += 1
        if attempts["count"] == 1:
            raise OSError("Address already in use")

    monkeypatch.setattr(web_app, "publish_attach_discovery", fake_publish)
    monkeypatch.setattr(web_app, "clear_attach_discovery", fake_clear)
    monkeypatch.setattr(web_app.app, "run", fake_run)

    web_app.start_server(5100, host="127.0.0.1", max_attempts=2)

    assert published == [("127.0.0.1", 5100), ("127.0.0.1", 5101)]
    assert cleared["count"] >= 2


def test_desktop_can_poll_pending_attached_permissions(attach_web_app: Any) -> None:
    app, _ = attach_web_app
    web_app._permission_request_store.create(
        request_id="permission-1",
        call_id="call-1",
        run_id="run-1",
        session_id="session-1",
        tool_name="write_file",
        metadata={
            "default_choice": "once",
            "explainer": {"risk_level": "high", "explanation": "writes a file"},
        },
    )

    with app.test_client() as client:
        response = client.get("/permissions/requests")

    assert response.status_code == 200
    requests = response.get_json()["requests"]
    assert [item["request_id"] for item in requests] == ["permission-1"]
    assert requests[0]["default_choice"] == "once"
    assert requests[0]["explainer"]["risk_level"] == "high"

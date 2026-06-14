from __future__ import annotations

import threading
from types import SimpleNamespace
from pathlib import Path
from typing import Any

import pytest

from backend.web import app as web_app


@pytest.fixture
def isolated_permissions(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Any:
    project = tmp_path / "project"
    project.mkdir()
    monkeypatch.chdir(project)
    web_app._runtime_state.activate_session("session-permission-smoke")
    web_app._runtime_state.active_workspace_id = "workspace-permission-smoke"
    monkeypatch.setattr(web_app, "_permission_locks", {})
    monkeypatch.setattr(web_app, "_permission_results", {})
    monkeypatch.setattr(web_app, "_permission_contexts", {})
    return web_app.app.test_client()


def test_permissions_crud_roundtrip(isolated_permissions: Any) -> None:
    client = isolated_permissions

    created = client.post(
        "/permissions",
        json={"tool": "write_file", "action": "allow", "args_match": {"path": "*.md"}},
    )
    assert created.status_code == 200
    rule = created.get_json()["rule"]
    assert rule["tool"] == "write_file"
    assert rule["action"] == "allow"
    assert rule["args_match"] == {"path": "*.md"}

    listed = client.get("/permissions")
    assert listed.status_code == 200
    payload = listed.get_json()
    assert payload["rules"][0]["id"] == rule["id"]
    assert payload["path"].endswith(".metis\\permissions.json") or payload["path"].endswith(".metis/permissions.json")

    deleted = client.delete(f"/permissions/{rule['id']}")
    assert deleted.status_code == 200
    assert client.get("/permissions").get_json()["rules"] == []


def test_permission_remember_writes_rule_and_audit(isolated_permissions: Any) -> None:
    client = isolated_permissions
    request_id = "perm-test-1"
    lock = threading.Event()
    web_app._permission_locks[request_id] = lock
    web_app._permission_contexts[request_id] = {
        "request_id": request_id,
        "call_id": "call-test-1",
        "tool": "delete_file",
        "arguments": {"path": "danger.txt"},
    }

    response = client.post(
        "/permission",
        json={"request_id": request_id, "approved": False, "remember": "deny"},
    )
    assert response.status_code == 200
    assert response.get_json()["remember"] == "deny"
    assert lock.is_set()
    assert web_app._permission_results[request_id] is False

    payload = client.get("/permissions").get_json()
    assert payload["rules"][0]["tool"] == "delete_file"
    assert payload["rules"][0]["action"] == "deny"
    assert payload["audit"][0]["tool"] == "delete_file"
    assert payload["audit"][0]["approved"] is False
    assert payload["audit"][0]["remember"] == "deny"


def test_permission_audit_redacts_and_truncates_arguments(isolated_permissions: Any) -> None:
    client = isolated_permissions
    request_id = "perm-test-2"
    web_app._permission_locks[request_id] = threading.Event()
    web_app._permission_contexts[request_id] = {
        "request_id": request_id,
        "call_id": "call-test-2",
        "tool": "write_file",
        "arguments": {
            "path": "secret.txt",
            "api_key": "do-not-store-this",
            "content": "x" * 500,
        },
    }

    response = client.post(
        "/permission",
        json={"request_id": request_id, "approved": True, "remember": "allow"},
    )
    assert response.status_code == 200

    audit = client.get("/permissions").get_json()["audit"][0]
    assert audit["arguments"]["api_key"] == "***"
    assert "do-not-store-this" not in str(audit)
    assert len(audit["arguments"]["content"]) < 230


def test_composer_full_access_enables_cross_workspace_read_boundary(isolated_permissions: Any) -> None:
    client = isolated_permissions

    assert web_app._tool_boundary_overrides("read_file", {"path": "E:\\notes.txt"}) == {}

    response = client.post(
        "/permissions",
        json={"tool": "*", "action": "allow", "source": "composer_access"},
    )
    assert response.status_code == 200

    overrides = web_app._tool_boundary_overrides("read_file", {"path": "E:\\notes.txt"})
    assert overrides["allow_paths_outside_workspace"] is True
    assert overrides["allow_search_outside_workspace"] is True
    assert "allow_shell_cwd_outside_workspace" not in overrides

    assert web_app._tool_boundary_overrides("write_file", {"path": "E:\\notes.txt"}) == {}


def test_permission_checker_uses_active_workspace_root_not_process_cwd(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    backend_cwd = tmp_path / "backend"
    workspace = tmp_path / "project"
    backend_cwd.mkdir()
    workspace.mkdir()

    class WorkspaceManager:
        def get_workspace(self, workspace_id: str) -> Any:
            if workspace_id == "workspace-active-root":
                return SimpleNamespace(id=workspace_id, path=str(workspace), name="project")
            return None

    monkeypatch.chdir(backend_cwd)
    monkeypatch.setattr(web_app, "get_workspace_manager", lambda: WorkspaceManager())
    web_app._runtime_state.active_workspace_id = "workspace-active-root"

    inside = workspace / "notes.md"
    outside = backend_cwd / "notes.md"

    assert web_app._check_permission_rules("write_file", {"path": str(inside)}) is None
    assert web_app._check_permission_rules("write_file", {"path": str(outside)}) == "deny"

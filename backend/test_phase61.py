from __future__ import annotations

import os
import re
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
FRONTEND = ROOT / "Frontend" / "index.html"


@contextmanager
def isolated_home() -> Iterator[Path]:
    old_home = os.environ.get("HOME")
    old_userprofile = os.environ.get("USERPROFILE")
    with tempfile.TemporaryDirectory() as tmp:
        home = Path(tmp)
        os.environ["HOME"] = str(home)
        os.environ["USERPROFILE"] = str(home)
        try:
            yield home
        finally:
            if old_home is None:
                os.environ.pop("HOME", None)
            else:
                os.environ["HOME"] = old_home
            if old_userprofile is None:
                os.environ.pop("USERPROFILE", None)
            else:
                os.environ["USERPROFILE"] = old_userprofile


def frontend_function(name: str) -> str:
    source = FRONTEND.read_text(encoding="utf-8")
    match = re.search(rf"async function {re.escape(name)}\([^)]*\) \{{", source)
    assert match is not None, f"{name} not found"
    depth = 0
    for index in range(match.end() - 1, len(source)):
        char = source[index]
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return source[match.start() : index + 1]
    raise AssertionError(f"{name} body is not closed")


def test_workspace_survives_when_all_sessions_are_deleted() -> None:
    from backend.web.sessions import SessionManager
    from backend.web.workspaces import WorkspaceManager

    with isolated_home(), tempfile.TemporaryDirectory() as tmp:
        project = Path(tmp) / "Project"
        project.mkdir()
        workspace_manager = WorkspaceManager()
        session_manager = SessionManager()

        workspace = workspace_manager.create_workspace(str(project))
        session_manager.create_session(title="keep workspace", workspace_id=workspace.id)

        assert session_manager.delete_sessions_for_workspace(workspace.id) == 1
        assert workspace_manager.get_workspace(workspace.id) is not None
        assert session_manager.list_sessions(workspace_id=workspace.id) == []


def test_list_sessions_returns_most_recently_updated_first() -> None:
    from backend.web.sessions import SessionManager

    with isolated_home():
        manager = SessionManager()
        older = manager.create_session(title="older", workspace_id="workspace-1")
        newer = manager.create_session(title="newer", workspace_id="workspace-1")

        manager.update_session(older.id, history=[{"role": "user", "content": "fresh"}])

        sessions = manager.list_sessions(workspace_id="workspace-1")
        assert [session.id for session in sessions[:2]] == [older.id, newer.id]


def test_workspace_env_blank_values_do_not_mask_project_env() -> None:
    from backend.web import llm_state

    old_file = llm_state.__file__
    old_cwd = os.getcwd()
    with tempfile.TemporaryDirectory() as tmp:
        fake_root = Path(tmp) / "miro"
        fake_web = fake_root / "web"
        workspace = Path(tmp) / "workspace"
        fake_web.mkdir(parents=True)
        workspace.mkdir()
        (fake_root / ".env").write_text(
            "METIS_LLM_API_KEY=root-key\n"
            "METIS_LLM_BASE_URL=https://api.deepseek.com/v1\n",
            encoding="utf-8",
        )
        (workspace / ".env").write_text(
            "METIS_LLM_API_KEY=\n"
            "METIS_LLM_MODEL=deepseek-v4-flash\n",
            encoding="utf-8",
        )

        try:
            llm_state.__file__ = str(fake_web / "llm_state.py")
            os.chdir(workspace)
            values = llm_state._env_file_values()
        finally:
            llm_state.__file__ = old_file
            os.chdir(old_cwd)

    assert values["METIS_LLM_API_KEY"] == "root-key"
    assert values["METIS_LLM_MODEL"] == "deepseek-v4-flash"
    assert (
        llm_state.normalize_base_url("openai", values["METIS_LLM_BASE_URL"])
        == "https://api.deepseek.com"
    )


def test_frontend_delete_uses_backend_refresh_as_single_source() -> None:
    source = FRONTEND.read_text(encoding="utf-8")
    assert "deletedSessionIds" not in source

    delete_session = frontend_function("deleteSessionById")
    assert "renderSessionList();" not in delete_session
    assert delete_session.index("await loadSessions();") < delete_session.index(
        "showToast(t('sessionDeleted'))"
    )

    delete_workspace = frontend_function("deleteWorkspaceSessions")
    assert "renderSessionList();" not in delete_workspace
    assert delete_workspace.index("await loadSessions();") < delete_workspace.index(
        "showToast(t('workspaceSessionsDeleted'))"
    )

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

from backend.core import paths as metis_paths


@pytest.fixture(autouse=True)
def clear_metis_cache() -> Iterator[None]:
    metis_paths.clear_metis_home_cache()
    yield
    metis_paths.clear_metis_home_cache()


def test_metis_home_prefers_environment(monkeypatch: Any, tmp_path: Path) -> None:
    home = tmp_path / "Metis Data"
    monkeypatch.setenv("METIS_HOME", str(home))

    assert metis_paths.metis_home() == home.resolve()
    assert metis_paths.metis_path("config.json") == home.resolve() / "config.json"
    assert metis_paths.metis_dir("sessions") == home.resolve() / "sessions"
    assert (home / "sessions").is_dir()


def test_metis_home_uses_portable_marker(monkeypatch: Any, tmp_path: Path) -> None:
    install_dir = tmp_path / "MetisPortable"
    install_dir.mkdir()
    (install_dir / "metis-portable.marker").write_text("", encoding="utf-8")
    monkeypatch.delenv("METIS_HOME", raising=False)
    monkeypatch.setattr(metis_paths.sys, "executable", str(install_dir / "metis-backend.exe"))

    assert metis_paths.metis_home() == (install_dir / "data" / "metis").resolve()


def test_backend_data_paths_follow_metis_home(monkeypatch: Any, tmp_path: Path) -> None:
    home = tmp_path / "data" / "metis"
    monkeypatch.setenv("METIS_HOME", str(home))
    metis_paths.clear_metis_home_cache()

    from backend.runtime import mcp_client, plugin_loader, tool_registry
    from backend.tools.coding.workflow_features.agent_state import update_project_memory
    from backend.web import helpers, llm_state, scheduler, sessions, workspaces
    from backend.web.runtime_state import RuntimeState
    from backend.web.session_db import MetisSessionDB, default_data_root

    helpers.init_shared_state(RuntimeState())

    assert Path(default_data_root()) == home.resolve()
    assert Path(sessions._sessions_dir()) == home.resolve() / "sessions"
    assert Path(workspaces._workspaces_dir()) == home.resolve() / "workspaces"
    assert Path(llm_state.config_path()) == home.resolve() / "config.json"
    assert Path(scheduler._cron_path()) == home.resolve() / "cron.json"
    assert Path(helpers.skills_dir()) == home.resolve() / "skills"
    assert Path(helpers.memory_paths_payload()["global_path"]) == home.resolve() / "METIS.md"
    assert Path(update_project_memory._memory_path("global")) == home.resolve() / "METIS.md"
    assert home.joinpath("sessions").is_dir()
    assert home.joinpath("workspaces").is_dir()
    assert home.joinpath("skills").is_dir()

    mcp_paths = mcp_client._default_config_paths()
    tool_paths = tool_registry._tool_config_paths()
    plugin_roots = plugin_loader._plugin_roots()
    assert home.resolve() / "mcp.json" in [path.resolve() for path in mcp_paths]
    assert home.resolve() / "tools.json" in [path.resolve() for path in tool_paths]
    assert home.resolve() / "plugins" in [path.resolve() for path in plugin_roots]

    db = MetisSessionDB()
    assert Path(db.db_path) == home.resolve() / "session-state.db"
    assert Path(db.sessions_dir) == home.resolve() / "sessions"
    assert Path(db.workspaces_dir) == home.resolve() / "workspaces"


def test_persistent_config_loads_python_path(monkeypatch: Any, tmp_path: Path) -> None:
    home = tmp_path / "data" / "metis"
    home.mkdir(parents=True)
    monkeypatch.setenv("METIS_HOME", str(home))
    monkeypatch.delenv("METIS_PYTHON", raising=False)
    metis_paths.clear_metis_home_cache()
    (home / "config.json").write_text(json.dumps({"python_path": sys.executable}), encoding="utf-8")

    from backend.web import llm_state

    llm_state.load_persistent_config()

    assert os.environ["METIS_PYTHON"] == sys.executable

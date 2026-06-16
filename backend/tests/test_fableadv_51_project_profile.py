from __future__ import annotations

import json

from backend.core.engine.prompt_runtime import compile_prompt_runtime
from backend.core.memory.project_profile import ensure_project_profile, infer_project_profile
from backend.runtime.context_budget import context_ledger


def _write_metis_fixture(root) -> None:
    desktop = root / "desktop"
    desktop.mkdir()
    (root / "backend").mkdir()
    (root / "docs" / "dev-log").mkdir(parents=True)
    (root / "pyproject.toml").write_text("[tool.ruff]\nline-length = 120\n", encoding="utf-8")
    (desktop / "package.json").write_text(
        json.dumps(
            {
                "scripts": {
                    "dev": "node scripts/dev-launcher.mjs",
                    "dev:renderer": "vite --host 127.0.0.1 --port 5174",
                    "dev:electron": (
                        "wait-on http://127.0.0.1:5174 && "
                        "cross-env METIS_DESKTOP_DEV_SERVER=http://127.0.0.1:5174 electron ."
                    ),
                    "typecheck": "tsc --noEmit",
                    "test": "vitest run",
                    "test:contracts": "node --test scripts/contracts.mjs",
                    "smoke:desktop": "node scripts/desktop-smoke-runner.mjs",
                }
            }
        ),
        encoding="utf-8",
    )


def test_project_profile_infers_commands_ports_and_preferences(tmp_path):
    _write_metis_fixture(tmp_path)

    profile = infer_project_profile(str(tmp_path))

    assert profile.name == tmp_path.name
    assert profile.project_type == "Python backend + Electron/React TypeScript desktop"
    assert "backend/ — Python backend, runtime loop, tools, web routes, tests" in profile.structure
    assert "docs/dev-log/ — append-only implementation plans and construction logs" in profile.structure
    assert "cd desktop && npm run dev" in profile.startup_commands
    assert "python -m pytest" in profile.test_commands
    assert "cd desktop && npm run typecheck" in profile.test_commands
    assert profile.common_ports == ["localhost:5174 (METIS_DESKTOP_DEV_SERVER)"]
    assert any("Default to Chinese replies" in item for item in profile.user_preferences)
    assert any("Do not publish new GitHub release" in item for item in profile.release_rules)


def test_ensure_project_profile_persists_local_ignored_profile(tmp_path):
    _write_metis_fixture(tmp_path)

    profile = ensure_project_profile(str(tmp_path))
    profile_path = tmp_path / ".metis" / "project-profile.json"
    gitignore_path = tmp_path / ".metis" / ".gitignore"

    assert profile.path == profile_path
    assert profile_path.exists()
    assert gitignore_path.exists()
    assert "project-profile.json" in gitignore_path.read_text(encoding="utf-8")
    payload = json.loads(profile_path.read_text(encoding="utf-8"))
    assert payload["common_ports"] == ["localhost:5174 (METIS_DESKTOP_DEV_SERVER)"]
    assert "workspace_root" not in payload


def test_prompt_runtime_loads_project_profile_by_default(tmp_path, monkeypatch):
    _write_metis_fixture(tmp_path)
    monkeypatch.delenv("METIS_CONTEXT_PROJECT_PROFILE", raising=False)

    snapshot = compile_prompt_runtime(
        "Base",
        workspace_root=str(tmp_path),
        include_agent_state_hint=False,
        include_open_files_hint=False,
        include_terminal_hint=False,
        include_repo_map_hint=False,
        include_skills_index=False,
        include_workspace_memory_hint=False,
    )

    assert "project_profile" in snapshot.layer_names()
    assert "[Metis Project Profile]" in snapshot.final_system_prompt
    assert "localhost:5174 (METIS_DESKTOP_DEV_SERVER)" in snapshot.final_system_prompt


def test_prompt_runtime_can_disable_project_profile(tmp_path):
    _write_metis_fixture(tmp_path)

    snapshot = compile_prompt_runtime(
        "Base",
        workspace_root=str(tmp_path),
        include_project_profile=False,
        include_agent_state_hint=False,
        include_open_files_hint=False,
        include_terminal_hint=False,
        include_repo_map_hint=False,
        include_skills_index=False,
        include_workspace_memory_hint=False,
    )

    assert "project_profile" not in snapshot.layer_names()
    assert "[Metis Project Profile]" not in snapshot.final_system_prompt


def test_context_ledger_counts_project_profile_as_memory():
    ledger = context_ledger(
        [
            {
                "role": "system",
                "content": (
                    "Base prompt.\n\n"
                    "---\n[Metis Project Profile]\n"
                    "Project: Miro\n"
                    "Startup commands:\n"
                    "- cd desktop && npm run dev\n"
                ),
            }
        ],
        [],
    )

    assert ledger["system_breakdown"]["memory"] > 0
    assert ledger["system_breakdown"]["system_prompt"] > 0

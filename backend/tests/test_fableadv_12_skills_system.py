from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest

from backend.core.paths import clear_metis_home_cache
from backend.core.engine.prompt_runtime import compile_prompt_runtime
from backend.runtime import tool_registry
from backend.runtime.skill_loader import (
    build_skills_index,
    discover_skills,
    expand_user_skill_command,
    load_skill_content,
    parse_frontmatter,
)


@pytest.fixture()
def metis_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    home = tmp_path / "metis-home"
    monkeypatch.setenv("METIS_HOME", str(home))
    clear_metis_home_cache()
    tool_registry._REGISTRY = None  # type: ignore[attr-defined]
    yield home
    tool_registry._REGISTRY = None  # type: ignore[attr-defined]
    clear_metis_home_cache()


def write_skill(root: Path, name: str, content: str) -> Path:
    directory = root / name
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "SKILL.md"
    path.write_text(content, encoding="utf-8")
    return path


def test_skill_loader_parses_frontmatter_and_legacy_description(metis_home: Path) -> None:
    frontmatter, body = parse_frontmatter(
        "---\n"
        "name: debug-workflow\n"
        "description: 修 bug 时使用\n"
        "paths:\n"
        "  - \"**/*.py\"\n"
        "user-invocable: false\n"
        "---\n"
        "# Debug\n\nBody\n"
    )

    assert frontmatter["name"] == "debug-workflow"
    assert frontmatter["paths"] == ["**/*.py"]
    assert frontmatter["user-invocable"] is False
    assert body.startswith("# Debug")

    skills_root = metis_home / "skills"
    write_skill(skills_root, "legacy", "# Legacy Skill\n\nUse this old skill when needed.\n")
    legacy = next(skill for skill in discover_skills(workspace_root="", include_shadowed=True) if skill.name == "legacy")

    assert legacy.description == "Use this old skill when needed."


def test_project_skill_overrides_global_and_load_skill_uses_workspace(metis_home: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_skill(
        metis_home / "skills",
        "debug-workflow",
        "---\nname: debug-workflow\ndescription: global debug\n---\n# Global Debug\n",
    )
    write_skill(
        workspace / ".metis" / "skills",
        "debug-workflow",
        "---\nname: debug-workflow\ndescription: project debug\n---\n# Project Debug\n",
    )

    resolved = [skill for skill in discover_skills(workspace_root=str(workspace)) if skill.name == "debug-workflow"]
    assert len(resolved) == 1
    assert resolved[0].source == "project"
    assert resolved[0].description == "project debug"
    assert "Project Debug" in load_skill_content("debug-workflow", workspace_root=str(workspace))


def test_skills_index_respects_model_invocation_paths_and_budget(metis_home: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "app.py").write_text("print('hello')\n", encoding="utf-8")
    write_skill(
        metis_home / "skills",
        "hidden-deploy",
        "---\nname: hidden-deploy\ndescription: deploy only\ndisable-model-invocation: true\n---\n# Hidden\n",
    )

    index = build_skills_index(workspace_root=str(workspace), context_window=128_000)

    assert "[可用技能" in index
    assert "load_skill(name)" in index
    assert "python-project" in index
    assert "hidden-deploy" not in index
    assert len(index) <= 1280 * 4


def test_prompt_runtime_injects_session_stable_skills_index(metis_home: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    snapshot = compile_prompt_runtime(
        "Base system prompt",
        workspace_root=str(workspace),
        model_context_window=128_000,
        include_repo_map_hint=False,
        include_desk_skill=False,
    )

    assert "skills_index" in snapshot.layer_names()
    layer = next(layer for layer in snapshot.layers if layer.name == "skills_index")
    assert layer.stability == "session"
    assert "debug-workflow" in layer.content


def test_load_skill_tool_is_in_lean_profile_and_expands_slash_command(metis_home: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    write_skill(
        metis_home / "skills",
        "custom-skill",
        "---\nname: custom-skill\ndescription: custom use\n---\n# Custom\n\nTask: $ARGUMENTS\n",
    )

    registry = tool_registry.get_registry(include_mcp=False, include_desktop=False, include_experts=False)
    lean_names = {
        schema["function"]["name"]
        for schema in registry.get_schemas_for_profile("lean", format="openai", include_desktop=False)
    }
    assert "load_skill" in lean_names

    result = registry.execute("load_skill", {"name": "custom-skill", "arguments": "fix parser"}, workspace_root=str(workspace))
    assert "[Loaded Metis skill: custom-skill]" in result
    assert "Task: fix parser" in result

    expanded = expand_user_skill_command("/custom-skill fix parser", workspace_root=str(workspace))
    assert "[Original user request after skill invocation]" in expanded
    assert "Task: fix parser" in expanded

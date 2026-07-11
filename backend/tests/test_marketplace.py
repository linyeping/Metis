from __future__ import annotations

import json
import zipfile
from contextlib import nullcontext
from pathlib import Path

import pytest

from backend.core.paths import clear_metis_home_cache
from backend.runtime import marketplace
from backend.runtime import marketplace_sources


@pytest.fixture()
def isolated_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "metis-home"
    monkeypatch.setenv("METIS_HOME", str(home))
    clear_metis_home_cache()
    marketplace._DYNAMIC_ITEMS.clear()
    marketplace._REGISTRY_CACHE.clear()
    yield home
    clear_metis_home_cache()


def test_bundled_manifest_exposes_colored_assets(isolated_home: Path) -> None:
    catalog = marketplace.list_catalog()

    assert catalog["schema"] == marketplace.SCHEMA
    assert catalog["counts"] == {"skill": 2, "mcp": 1, "plugin": 1}
    documents = next(item for item in catalog["items"] if item["id"] == "metis.documents")
    assert documents["brandColor"] == "#4285F4"
    assert documents["iconDataUrl"].startswith("data:image/svg+xml;base64,")
    assert documents["descriptions"]["zh"] == "创建、编辑、渲染并验证 Word 文档。"
    assert documents["descriptions"]["en"].startswith("Create, edit")
    assert documents["installed"] is False


def test_default_remote_sources_are_available_without_network(isolated_home: Path) -> None:
    sources = marketplace_sources.list_sources()["sources"]

    assert [source["id"] for source in sources] == ["openai-plugins", "anthropic-skills"]
    assert all(source["trust"] == "official" for source in sources)
    assert all(source["itemCount"] == 0 for source in sources)


def test_cached_source_items_join_catalog_and_filter_by_source(isolated_home: Path) -> None:
    marketplace_sources._write_cache(
        "openai-plugins",
        {
            "schema": marketplace.SCHEMA,
            "items": [
                {
                    "id": "openai:test-plugin",
                    "kind": "plugin",
                    "name": "Test Plugin",
                    "version": "1.0.0",
                    "description": "cached",
                    "publisher": "OpenAI",
                    "category": "Testing",
                    "brandColor": "#10A37F",
                    "source": {"type": "remote-plugin", "marketplace": "openai-plugins", "url": "https://example.test/plugin"},
                }
            ],
        },
    )

    filtered = marketplace.list_catalog(source="openai-plugins")
    metis = marketplace.list_catalog(source="metis-official")

    assert [item["id"] for item in filtered["items"]] == ["openai:test-plugin"]
    assert filtered["items"][0]["descriptions"] == {"en": "cached"}
    assert all(not item["id"].startswith("openai:") for item in metis["items"])


def test_official_plugin_description_catalog_adds_chinese_without_overwriting_source_english(isolated_home: Path) -> None:
    marketplace_sources._write_cache(
        "openai-plugins",
        {
            "schema": marketplace.SCHEMA,
            "items": [
                {
                    "id": "openai:linear",
                    "kind": "plugin",
                    "name": "Linear",
                    "version": "1.0.0",
                    "description": "Fresh source description for Linear.",
                    "publisher": "OpenAI",
                    "category": "Productivity",
                    "source": {"type": "remote-plugin", "marketplace": "openai-plugins"},
                }
            ],
        },
    )

    item = marketplace.list_catalog(query="需求跟踪", source="openai-plugins")["items"][0]

    assert item["descriptions"]["zh"].startswith("查找并引用 Linear")
    assert item["descriptions"]["en"] == "Fresh source description for Linear."


def test_skill_install_is_disabled_until_explicit_enable(isolated_home: Path) -> None:
    installed = marketplace.install_item("metis.documents")
    skill_dir = isolated_home / "skills" / "documents"

    assert installed["installed"] is True
    assert installed["enabled"] is False
    assert (skill_dir / "SKILL.md").is_file()
    assert (skill_dir / ".disabled").is_file()

    enabled = marketplace.set_item_enabled("metis.documents", True)
    assert enabled["enabled"] is True
    assert not (skill_dir / ".disabled").exists()

    marketplace.uninstall_item("metis.documents", force=True)
    assert not skill_dir.exists()


def test_mcp_configuration_keeps_secrets_out_of_json(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    item = {
        "id": "registry:secret-test@1.0.0",
        "kind": "mcp",
        "name": "Secret test",
        "version": "1.0.0",
        "description": "test",
        "publisher": "test",
        "category": "test",
        "source": {"type": "registry"},
        "mcp": {
            "serverName": "secret-test",
            "command": "npx",
            "args": ["-y", "secret-test"],
            "environmentVariables": [
                {"name": "SECRET_TOKEN", "required": True, "secret": True},
                {"name": "PUBLIC_ROOT", "required": True, "secret": False},
            ],
        },
    }
    marketplace._DYNAMIC_ITEMS[item["id"]] = item
    marketplace.install_item(item["id"])

    configured = marketplace.configure_item(
        item["id"],
        {"PUBLIC_ROOT": "D:/workspace", "SECRET_TOKEN": "must-not-be-written"},
        ["SECRET_TOKEN"],
    )
    raw = json.loads((isolated_home / "mcp.json").read_text(encoding="utf-8"))
    entry = raw["mcpServers"]["secret-test"]

    assert configured["needsSetup"] is False
    assert entry["disabled"] is True
    assert entry["env"] == {"PUBLIC_ROOT": "D:/workspace"}
    assert "must-not-be-written" not in json.dumps(raw)

    monkeypatch.setattr("backend.runtime.tool_registry.reload_mcp_tools", lambda **_: {"ok": True})
    assert marketplace.set_item_enabled(item["id"], True)["enabled"] is True


def test_plugin_tracks_component_ownership(isolated_home: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("backend.runtime.tool_registry.reload_mcp_tools", lambda **_: {"ok": True})
    plugin = marketplace.install_item("metis.workspace-toolkit")
    state = json.loads((isolated_home / "marketplace-state.json").read_text(encoding="utf-8"))

    assert plugin["installed"] is True
    assert state["items"]["metis.documents"]["owners"] == ["metis.workspace-toolkit"]
    assert state["items"]["metis.filesystem-mcp"]["owners"] == ["metis.workspace-toolkit"]

    marketplace.uninstall_item("metis.workspace-toolkit", force=True)
    state = json.loads((isolated_home / "marketplace-state.json").read_text(encoding="utf-8"))
    assert "metis.workspace-toolkit" not in state["items"]
    assert "metis.documents" not in state["items"]
    assert "metis.filesystem-mcp" not in state["items"]


def test_skill_frontmatter_supports_nested_interface_logo(isolated_home: Path) -> None:
    skill_dir = isolated_home / "skills" / "color-skill"
    assets = skill_dir / "assets"
    assets.mkdir(parents=True)
    (assets / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    (skill_dir / "SKILL.md").write_text(
        "---\nname: color-skill\ndescription: colorful\ninterface:\n  icon_small: ./assets/logo.svg\n  brand_color: '#22C55E'\n---\n# Color\n",
        encoding="utf-8",
    )

    from backend.runtime.skill_loader import discover_skills, skill_to_payload

    skill = next(row for row in discover_skills(install_builtins=False) if row.name == "color-skill")
    payload = skill_to_payload(skill)
    assert payload["brand_color"] == "#22C55E"
    assert payload["icon_data_url"].startswith("data:image/svg+xml;base64,")


def test_install_codex_style_plugin_package(isolated_home: Path, tmp_path: Path) -> None:
    package = tmp_path / "sample-plugin"
    (package / ".codex-plugin").mkdir(parents=True)
    (package / "skills" / "sample-skill").mkdir(parents=True)
    (package / "assets").mkdir()
    (package / "assets" / "logo.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"/>', encoding="utf-8")
    (package / "skills" / "sample-skill" / "SKILL.md").write_text(
        "---\nname: sample-skill\ndescription: sample\n---\n# Sample\n",
        encoding="utf-8",
    )
    (package / ".mcp.json").write_text(
        json.dumps({"mcpServers": {"sample-server": {"command": "npx", "args": ["-y", "sample-server"], "env": {"SAMPLE_TOKEN": "placeholder"}}}}),
        encoding="utf-8",
    )
    (package / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "sample-plugin",
                "version": "1.2.0",
                "description": "sample plugin",
                "author": {"name": "Metis Test"},
                "skills": "./skills/",
                "mcpServers": "./.mcp.json",
                "interface": {"displayName": "Sample Plugin", "shortDescription": "sample plugin", "developerName": "Metis Test", "category": "Testing", "brandColor": "#22C55E", "logo": "./assets/logo.svg"},
            }
        ),
        encoding="utf-8",
    )

    installed = marketplace.install_source(str(package))
    assert installed["kind"] == "plugin"
    assert installed["installed"] is True
    assert {component["name"] for component in installed["components"]} == {"sample-skill", "sample-server"}
    assert installed["iconDataUrl"].startswith("data:image/svg+xml;base64,")
    assert (isolated_home / "skills" / "sample-skill" / ".disabled").is_file()
    config = json.loads((isolated_home / "mcp.json").read_text(encoding="utf-8"))
    assert config["mcpServers"]["sample-server"]["disabled"] is True
    assert "placeholder" not in json.dumps(config)

    marketplace._DYNAMIC_ITEMS.clear()
    restored = marketplace.get_item(installed["id"])
    assert {component["name"] for component in restored["components"]} == {"sample-skill", "sample-server"}
    marketplace.uninstall_item(installed["id"], force=True)
    assert not (isolated_home / "skills" / "sample-skill").exists()


def test_plugin_package_supports_skill_path_arrays(isolated_home: Path, tmp_path: Path) -> None:
    package = tmp_path / "array-plugin"
    (package / ".codex-plugin").mkdir(parents=True)
    for name in ("alpha", "beta"):
        skill = package / "extensions" / name
        skill.mkdir(parents=True)
        (skill / "SKILL.md").write_text(f"---\nname: {name}\ndescription: {name}\n---\n", encoding="utf-8")
    (package / ".codex-plugin" / "plugin.json").write_text(
        json.dumps(
            {
                "name": "array-plugin",
                "version": "1.0.0",
                "description": "array plugin",
                "skills": ["./extensions/alpha", {"path": "./extensions/beta"}],
            }
        ),
        encoding="utf-8",
    )

    installed = marketplace.install_source(str(package))

    assert [component["name"] for component in installed["components"]] == ["alpha", "beta"]
    assert (isolated_home / "skills" / "alpha" / ".disabled").is_file()
    assert (isolated_home / "skills" / "beta" / ".disabled").is_file()


def test_zip_extraction_rejects_excessive_uncompressed_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "oversized.zip"
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as package:
        package.writestr("large.txt", b"x" * 4096)
    monkeypatch.setattr(marketplace, "_MAX_ZIP_UNCOMPRESSED_BYTES", 1024)

    with pytest.raises(marketplace.MarketplaceError, match="extraction size limit"):
        marketplace._safe_extract_zip(archive, tmp_path / "output")


def test_source_zip_extraction_rejects_too_many_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    archive = tmp_path / "many.zip"
    with zipfile.ZipFile(archive, "w") as package:
        package.writestr("one.txt", "1")
        package.writestr("two.txt", "2")
    monkeypatch.setattr(marketplace_sources, "_MAX_ZIP_FILES", 1)

    with pytest.raises(marketplace_sources.MarketplaceSourceError, match="too many files"):
        marketplace_sources._safe_extract_zip(archive, tmp_path / "source-output")


def test_remote_skill_prefers_repository_url_over_relative_path(
    isolated_home: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    skill_root = tmp_path / "remote-skill"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text(
        "---\nname: remote-skill\ndescription: remote\n---\n",
        encoding="utf-8",
    )
    item = {
        "id": "anthropic:remote-skill",
        "kind": "skill",
        "name": "Remote Skill",
        "skillName": "remote-skill",
        "version": "0.0.0",
        "description": "remote",
        "publisher": "Anthropic",
        "category": "Agent Skills",
        "source": {
            "type": "remote-skill",
            "path": "skills/remote-skill",
            "url": "https://github.com/anthropics/skills/tree/main/skills/remote-skill",
        },
    }
    captured: list[str] = []

    def staged(source: str):
        captured.append(source)
        return nullcontext(skill_root)

    marketplace._DYNAMIC_ITEMS[item["id"]] = item
    monkeypatch.setattr(marketplace, "_staged_source", staged)

    installed = marketplace.install_item(item["id"])

    assert installed["installed"] is True
    assert captured == [item["source"]["url"]]
    assert (isolated_home / "skills" / "remote-skill" / ".disabled").is_file()


def test_filesystem_names_are_safe_on_windows() -> None:
    assert marketplace._safe_fs_name("openai:atlassian/rovo") == "openai-atlassian-rovo"
    assert marketplace._safe_fs_name("CON") == "_CON"
    assert marketplace._safe_fs_name("trailing. ") == "trailing"


def test_staged_tree_rejects_excessive_total_size(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = tmp_path / "source"
    source.mkdir()
    (source / "one.txt").write_bytes(b"a" * 600)
    (source / "two.txt").write_bytes(b"b" * 600)
    monkeypatch.setattr(marketplace, "_MAX_ZIP_UNCOMPRESSED_BYTES", 1024)

    with pytest.raises(marketplace.MarketplaceError, match="installation size limit"):
        marketplace._validate_staged_tree(source)


def test_openai_plugin_detail_content_collects_skill_markdown(tmp_path: Path) -> None:
    plugin_root = tmp_path / "calendar-plugin"
    skill_root = plugin_root / "skills" / "calendar"
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\nname: calendar\ndescription: Schedule meetings\n---\n# Calendar\n\n## Overview\n\nFind available time slots.\n",
        encoding="utf-8",
    )

    content = marketplace_sources._plugin_detail_content(
        plugin_root,
        {"skills": "./skills", "description": "Calendar tools"},
        {"longDescription": "Manage schedules and conflicts."},
    )

    assert "Manage schedules and conflicts." in content
    assert "# Calendar" in content
    assert "Find available time slots." in content
    assert "description: Schedule meetings" not in content

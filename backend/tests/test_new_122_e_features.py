from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from backend.runtime import mcp_client, tool_registry
from backend.runtime.plugin_loader import discover_plugins, register_plugins
from backend.runtime.tool_registry import ToolDefinition, ToolRegistry


def test_plugin_loader_discovers_and_registers_plugin_tool(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugins"
    tool_dir = plugin_root / "hello"
    tool_dir.mkdir(parents=True)
    (tool_dir / "tool.json").write_text(
        json.dumps(
            {
                "name": "hello_plugin",
                "description": "Say hello from a plugin",
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}},
                    "required": ["name"],
                },
                "requires_approval": False,
                "destructive": False,
                "aliases": ["hello_alias"],
            }
        ),
        encoding="utf-8",
    )
    (tool_dir / "handler.py").write_text(
        "def execute(name: str, **kwargs):\n"
        "    return f'Hello, {name}!'\n",
        encoding="utf-8",
    )

    plugins = discover_plugins(str(plugin_root))
    assert len(plugins) == 1

    registry = ToolRegistry()
    assert register_plugins(registry, str(plugin_root)) == 1
    assert registry.resolve_name("hello_alias") == "hello_plugin"
    assert registry.execute("hello_alias", {"name": "Metis"}) == "Hello, Metis!"

    profile = registry.get_tool_profile("hello_plugin")
    assert profile is not None
    assert profile.source == "plugin"
    assert profile.toolset == "plugin"
    assert profile.approval == "never"


def test_tools_json_disables_tools_and_applies_overrides(tmp_path: Path) -> None:
    config_path = tmp_path / "tools.json"
    config_path.write_text(
        json.dumps(
            {
                "disabled_tools": ["Read"],
                "tool_overrides": {
                    "write_file": {
                        "requires_approval": True,
                        "destructive": True,
                        "usage_hint": "Only use after reading the file.",
                    }
                },
            }
        ),
        encoding="utf-8",
    )

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="read_file",
            description="Read",
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=lambda: "read",
        )
    )
    registry.register(
        ToolDefinition(
            name="write_file",
            description="Write",
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=lambda: "write",
        )
    )
    registry.register_alias("Read", "read_file")

    summary = tool_registry.apply_user_tool_config(registry, str(config_path))

    assert summary["disabled_tools"] == ["read_file"]
    assert registry.is_available("read_file") is False
    assert {schema["function"]["name"] for schema in registry.get_all_schemas()} == {"write_file"}
    write_tool = registry.get("write_file")
    assert write_tool is not None
    assert write_tool.requires_approval is True
    assert write_tool.destructive is True
    assert write_tool.usage_hint == "Only use after reading the file."


def test_reload_mcp_tools_removes_old_mcp_tools_and_registers_new(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("METIS_TOOLS_CONFIG", str(tmp_path / "missing-tools.json"))
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="mcp_old_tool",
            description="Old MCP",
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=lambda: "old",
            source="mcp:old",
        )
    )
    registry.register(
        ToolDefinition(
            name="read_file",
            description="Builtin",
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=lambda: "builtin",
            source="builtin",
        )
    )

    manager = mcp_client.MCPManager()
    monkeypatch.setattr(mcp_client, "_global_manager", manager)

    def fake_register(target: ToolRegistry, config_path: str = "") -> int:
        assert config_path == "custom-mcp.json"
        target.register(
            ToolDefinition(
                name="mcp_new_tool",
                description="New MCP",
                parameters={"type": "object", "properties": {}, "required": []},
                execute_fn=lambda: "new",
                source="mcp:new",
            )
        )
        return 1

    monkeypatch.setattr(mcp_client, "register_mcp_tools", fake_register)

    result = tool_registry.reload_mcp_tools(registry, "custom-mcp.json")

    assert result["ok"] is True
    assert result["removed"] == 1
    assert result["registered"] == 1
    assert registry.get("mcp_old_tool") is None
    assert registry.get("mcp_new_tool") is not None
    assert registry.get("read_file") is not None

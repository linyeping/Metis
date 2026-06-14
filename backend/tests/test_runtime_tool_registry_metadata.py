from __future__ import annotations

from typing import Any

from backend.bridges.tool_registry_adapter import profiles_from_runtime_registry
from backend.runtime import tool_registry as runtime_tool_registry
from backend.runtime.agent_loop import AgentConfig, _permission_action, _tool_schemas_for_config
from backend.runtime.tool_registry import ToolDefinition, ToolRegistry


def test_runtime_registry_metadata_and_unavailable_behavior() -> None:
    calls: list[str] = []

    def unavailable_tool() -> str:
        calls.append("called")
        return "should not run"

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="read_file",
            description="Read a file",
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=lambda: "read ok",
        )
    )
    registry.register(
        ToolDefinition(
            name="write_file",
            description="Write a file",
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=lambda: "write ok",
        )
    )
    registry.register(
        ToolDefinition(
            name="unavailable_tool",
            description="Unavailable",
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=unavailable_tool,
            check_fn=lambda: False,
        )
    )
    registry.register_alias("Read", "read_file")

    assert registry.resolve_name("Read") == "read_file"
    assert registry.is_available("read_file") is True
    assert registry.is_available("unavailable_tool") is False
    assert "filesystem" in registry.get_toolsets()
    assert "read_file" in registry.get_tool_names_for_toolset("filesystem")

    schemas = registry.get_all_schemas(format="openai")
    names = {(schema.get("function") or {}).get("name") for schema in schemas}
    assert "read_file" in names
    assert "write_file" in names
    assert "unavailable_tool" not in names

    result = registry.execute("unavailable_tool", {})
    assert "not available" in result
    assert calls == []


def test_runtime_registry_profiles_and_adapter() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="grep_search",
            description="Search code",
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=lambda: "ok",
        )
    )
    profile = registry.get_tool_profile("grep_search")
    assert profile is not None
    assert profile.toolset == "search"
    assert profile.approval == "never"

    profiles = profiles_from_runtime_registry(registry)
    assert len(profiles) == 1
    assert profiles[0].toolset == "search"


def test_runtime_registry_schemas_are_sorted_by_tool_name() -> None:
    registry = ToolRegistry()
    for name in ("zeta_tool", "alpha_tool", "middle_tool"):
        registry.register(
            ToolDefinition(
                name=name,
                description=f"{name} description",
                parameters={"type": "object", "properties": {}, "required": []},
                execute_fn=lambda: "ok",
            )
        )

    schemas = registry.get_all_schemas(format="openai")
    names = [(schema.get("function") or {}).get("name") for schema in schemas]

    assert names == ["alpha_tool", "middle_tool", "zeta_tool"]


def test_runtime_registry_non_builtin_tools_survive_tier_filtering() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="slow_cancel_tool",
            description="Custom test tool",
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=lambda: "ok",
            source="test",
        )
    )

    schemas = _tool_schemas_for_config(
        registry,
        AgentConfig(llm_backend="fake", llm_model="fake-small-model"),
    )
    names = [(schema.get("function") or {}).get("name") for schema in schemas]

    assert names == ["slow_cancel_tool"]


def test_builtin_registry_profiles_are_available_offline() -> None:
    runtime_tool_registry._REGISTRY = None
    runtime_tool_registry._LOADED_MCP_CONFIGS.clear()
    registry = runtime_tool_registry.get_registry(include_desktop=False, include_mcp=False)

    profiles = {str(profile.name): profile for profile in registry.list_tool_profiles()}
    assert "read_file" in profiles
    assert "execute_bash_command" in profiles
    assert "delete_file" in profiles
    assert "grep_search" in profiles

    assert profiles["read_file"].approval == "never"
    assert profiles["execute_bash_command"].destructive is True
    assert profiles["delete_file"].destructive is True
    assert profiles["grep_search"].toolset == "search"
    assert "search" in registry.get_toolsets()
    assert "grep_search" in registry.get_tool_names_for_toolset("search")


def test_agent_approval_uses_registry_metadata() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="read_file",
            description="Read",
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=lambda: "ok",
        )
    )
    registry.register(
        ToolDefinition(
            name="write_file",
            description="Write",
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=lambda: "ok",
        )
    )
    registry.register(
        ToolDefinition(
            name="execute_bash_command",
            description="Shell",
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=lambda: "ok",
        )
    )

    assert _permission_action("edit", "read_file", {}, registry=registry) == "allow"
    assert _permission_action("edit", "write_file", {}, registry=registry) == "ask"
    assert _permission_action("edit", "execute_bash_command", {}, registry=registry) == "ask"
    assert _permission_action("ask", "read_file", {}, registry=registry) == "ask"
    assert _permission_action("auto", "write_file", {}, registry=registry) == "allow"
    assert _permission_action("plan", "execute_bash_command", {}, registry=registry) == "allow"


def test_rule_checker_still_overrides_registry() -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="write_file",
            description="Write",
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=lambda: "ok",
        )
    )

    def allow_all(_name: str, _arguments: dict[str, Any]) -> str:
        return "allow"

    def deny_all(_name: str, _arguments: dict[str, Any]) -> str:
        return "deny"

    assert _permission_action("edit", "write_file", {}, allow_all, registry=registry) == "allow"
    assert _permission_action("auto", "write_file", {}, deny_all, registry=registry) == "deny"

from __future__ import annotations

from backend.bridges.tool_profiles import (
    infer_tool_profile,
    infer_toolset,
    is_destructive_tool,
    is_safe_tool,
)


def test_infer_safe_read_profile() -> None:
    profile = infer_tool_profile("read_file", description="Read a file")
    assert profile.toolset == "filesystem"
    assert profile.approval == "never"
    assert profile.destructive is False
    assert is_safe_tool("read_file") is True


def test_infer_write_delete_shell_are_destructive() -> None:
    for name in ("write_file", "delete_file", "execute_bash_command"):
        profile = infer_tool_profile(name)
        assert profile.destructive is True
        assert profile.approval == "always"
        assert is_destructive_tool(name) is True


def test_infer_source_specific_toolsets() -> None:
    assert infer_toolset("anything", source="mcp") == "mcp"
    assert infer_toolset("desktop_action", source="desktop") == "desktop"
    assert infer_tool_profile("grep_search").toolset == "search"
    assert infer_tool_profile("git_diff").toolset == "git"
    assert infer_tool_profile("web_fetch").toolset == "web"
    assert infer_tool_profile("todo_write").toolset == "memory"
    assert infer_tool_profile("task_dispatch").toolset == "workflow"

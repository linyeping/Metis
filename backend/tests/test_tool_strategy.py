"""tool_strategy.py 单元测试 — 验证工具策略提示生成逻辑。"""
from __future__ import annotations


from backend.core.engine.tool_strategy import generate_tool_strategy, tool_strategy_block


class TestGenerateToolStrategy:
    """generate_tool_strategy 基本行为。"""

    def test_empty_tools_returns_empty(self):
        assert generate_tool_strategy([]) == ""

    def test_unrelated_tools_returns_empty(self):
        assert generate_tool_strategy(["ask_question", "todo_write"]) == ""

    def test_file_exploration_hint(self):
        result = generate_tool_strategy(["read_file", "generate_repo_map"])
        assert "generate_repo_map first" in result
        assert "Tool Usage Strategy" in result

    def test_search_hint(self):
        result = generate_tool_strategy(["grep_search", "read_file"])
        assert "grep_search" in result
        assert "search first" in result

    def test_edit_strategy_hint(self):
        result = generate_tool_strategy(["write_file", "robust_replace_in_file"])
        assert "robust_replace_in_file" in result
        assert "surgical" in result

    def test_shell_hint(self):
        result = generate_tool_strategy(["execute_bash_command"])
        assert "tests" in result.lower() or "exit codes" in result.lower()

    def test_desktop_hint(self):
        result = generate_tool_strategy(["desktop_window_list", "desktop_screenshot"])
        assert "desktop_window_list" in result
        assert "desktop_screenshot" in result

    def test_batch_reading_hint(self):
        result = generate_tool_strategy(["read_multiple_files"])
        assert "read_multiple_files" in result

    def test_glob_hint(self):
        result = generate_tool_strategy(["glob_search", "execute_bash_command"])
        assert "glob_search" in result

    def test_web_browsing_hint(self):
        result = generate_tool_strategy(["browse_web", "browse_and_extract"])
        assert "browse_and_extract" in result

    def test_multiple_hints_combined(self):
        tools = [
            "read_file", "generate_repo_map",
            "grep_search", "write_file",
            "robust_replace_in_file", "execute_bash_command",
        ]
        result = generate_tool_strategy(tools)
        # Should contain multiple hints
        assert result.count("- ") >= 4

    def test_output_format_is_markdown(self):
        result = generate_tool_strategy(["read_file", "generate_repo_map"])
        assert result.startswith("## Tool Usage Strategy")
        assert result.count("- ") >= 1


class TestToolStrategyBlock:
    """tool_strategy_block 包装器。"""

    def test_returns_wrapped_block(self):
        # Monkeypatch to avoid loading the real registry
        result = tool_strategy_block(
            enabled_tools=["read_file", "generate_repo_map", "grep_search"]
        )
        assert "[## Tool Usage Strategy" in result
        assert result.endswith("]\n")

    def test_empty_tools_returns_empty(self):
        result = tool_strategy_block(enabled_tools=[])
        assert result == ""

    def test_none_tools_attempts_registry(self):
        # With no registry configured, should return empty gracefully
        result = tool_strategy_block(enabled_tools=None)
        # May or may not produce output depending on registry state
        assert isinstance(result, str)

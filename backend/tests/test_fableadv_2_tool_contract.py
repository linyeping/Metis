from __future__ import annotations

from pathlib import Path

from backend.runtime.agent_loop import AgentConfig, _tool_schemas_for_config
from backend.runtime.edit_guard import EditGuard
from backend.runtime.llm_backends.toolcall_repair import repair_arguments, repair_tool_calls
from backend.runtime.tool_errors import teaching_error_text
from backend.runtime.tool_profiles import LEAN_PROFILE
from backend.runtime.tool_registry import ToolDefinition, ToolRegistry
from backend.tools.coding.read_search.read_single.read_file import read_file


def _schema_registry(names: set[str]) -> ToolRegistry:
    registry = ToolRegistry()
    for name in sorted(names):
        registry.register(
            ToolDefinition(
                name=name,
                description=f"{name} test tool",
                parameters={"type": "object", "properties": {}, "required": []},
                execute_fn=lambda **kwargs: "ok",
            )
        )
    return registry


def _schema_names(schemas: list[dict]) -> set[str]:
    return {str((schema.get("function") or {}).get("name") or "") for schema in schemas}


def test_lean_profile_is_default_and_hides_overlap_tools(monkeypatch) -> None:
    monkeypatch.setenv("METIS_TOOL_TIER", "1")
    monkeypatch.delenv("METIS_TOOL_PROFILE", raising=False)
    registry = _schema_registry(set(LEAN_PROFILE) | {"semantic_search", "read_file_chunk", "edit_notebook"})

    schemas = _tool_schemas_for_config(registry, AgentConfig(llm_backend="fake", llm_model="fake"))
    names = _schema_names(schemas)

    assert len(names) <= len(LEAN_PROFILE)
    assert "read_file" in names
    assert "robust_replace_in_file" in names
    assert "browse_web" in names
    assert "load_skill" in names
    # Document tools are route-gated: absent on a default (non-document) route,
    # present when the task routes to a document/artifact workflow.
    assert "pdf_extract_text" not in names
    assert "docx_create" not in names
    doc_schemas = _tool_schemas_for_config(
        registry, AgentConfig(llm_backend="fake", llm_model="fake", routing_task_type="artifact_workflow")
    )
    doc_names = _schema_names(doc_schemas)
    assert "pdf_extract_text" in doc_names
    assert "docx_create" in doc_names
    assert "semantic_search" not in names
    assert "read_file_chunk" not in names


def test_full_profile_restores_hidden_tools(monkeypatch) -> None:
    monkeypatch.setenv("METIS_TOOL_TIER", "1")
    monkeypatch.setenv("METIS_TOOL_PROFILE", "full")
    registry = _schema_registry(set(LEAN_PROFILE) | {"semantic_search", "read_file_chunk"})

    schemas = _tool_schemas_for_config(registry, AgentConfig(llm_backend="fake", llm_model="fake"))
    names = _schema_names(schemas)

    assert "semantic_search" in names
    assert "read_file_chunk" in names


def test_read_file_returns_line_numbers_and_offset_limit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "sample.py"
    target.write_text("one\n\ntwo\nthree\n", encoding="utf-8")

    result = read_file("sample.py", offset=2, limit=2)

    assert "=== sample.py (lines 2-3 of 4) ===" in result
    assert "   2→" in result
    assert "   3→two" in result
    assert "offset=4" in result


def test_read_file_large_default_paginates(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "large.txt"
    target.write_text("".join(f"line {index}\n" for index in range(1, 1003)), encoding="utf-8")

    result = read_file("large.txt")

    assert "=== large.txt (lines 1-1000 of 1002) ===" in result
    assert "1000→line 1000" in result
    assert "1001→line 1001" not in result
    assert "offset=1001" in result


def test_edit_guard_blocks_unread_existing_file_and_allows_new_file(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("old = True\n", encoding="utf-8")
    guard = EditGuard(str(tmp_path))

    blocked = guard.before_execute(
        "robust_replace_in_file",
        {"file_path": "app.py", "search_text": "old = True", "replace_text": "old = False"},
    )

    assert "还没有读取过" in blocked
    assert guard.before_execute("write_file", {"file_path": "new.py", "content": "x = 1\n"}) == ""


def test_edit_guard_counts_reads_rejects_line_prefix_and_returns_diff(tmp_path: Path) -> None:
    target = tmp_path / "app.py"
    target.write_text("old = True\n", encoding="utf-8")
    guard = EditGuard(str(tmp_path))

    guard.record_tool_result("read_file", {"file_path": "app.py"}, "ok")
    assert guard.before_execute(
        "robust_replace_in_file",
        {"file_path": "app.py", "search_text": "   1→old = True", "replace_text": "old = False"},
    ).startswith("错误：search_text")

    args = {"file_path": "app.py", "content": "old = False\n"}
    snapshot = guard.capture_before("write_file", args)
    target.write_text("old = False\n", encoding="utf-8")
    result = guard.after_execute("write_file", args, "✅ 成功写入: app.py", snapshot)

    assert "变更摘要" in result
    assert "-old = True" in result
    assert "+old = False" in result


def test_teaching_errors_cover_common_failure_modes() -> None:
    assert "工作区之外" in teaching_error_text(
        "write_file",
        {"file_path": "../x.txt"},
        "Error: Access denied [PATH_OUTSIDE_WORKSPACE]: x is outside the active workspace D:\\repo.",
        workspace_root="D:\\repo",
    )
    assert "确认路径" in teaching_error_text("read_file", {"file_path": "missing.py"}, "FileNotFoundError")
    assert "精确原文" in teaching_error_text("robust_replace_in_file", {}, "❌ 未找到待替换片段（共 0 处）")
    assert "扩大 search_text" in teaching_error_text("robust_replace_in_file", {}, "multiple matches")
    assert "start_long_running_process" in teaching_error_text("execute_bash_command", {}, "TimeoutExpired after 30 seconds")


def test_tool_registry_exception_does_not_return_traceback() -> None:
    registry = ToolRegistry()

    def boom() -> str:
        raise RuntimeError("kaput")

    registry.register(
        ToolDefinition(
            name="boom",
            description="boom",
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=boom,
        )
    )

    result = registry.execute("boom", {})

    assert "工具 boom 执行失败" in result
    assert "Traceback" not in result


def test_toolcall_repair_handles_malformed_arguments(monkeypatch) -> None:
    monkeypatch.delenv("METIS_PARALLEL_TOOLCALLS", raising=False)
    tools = [
        {
            "type": "function",
            "function": {
                "name": "demo",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "flag": {"type": "boolean"},
                        "count": {"type": "integer"},
                        "paths": {"type": "array", "items": {"type": "string"}},
                        "config": {"type": "object"},
                    },
                },
            },
        }
    ]

    assert repair_arguments('{"flag": "true", "count": "7"}', tools[0]["function"]["parameters"]) == {
        "flag": True,
        "count": 7,
    }
    assert repair_arguments('"{\\"flag\\": \\"false\\"}"', tools[0]["function"]["parameters"]) == {"flag": False}
    assert repair_arguments("```json\n{\"count\": \"9\"}\n```", tools[0]["function"]["parameters"]) == {"count": 9}
    assert repair_arguments({"paths": "[\"a.py\", \"b.py\"]"}, tools[0]["function"]["parameters"]) == {
        "paths": ["a.py", "b.py"]
    }
    assert repair_arguments({"config": "{\"mode\": \"fast\"}"}, tools[0]["function"]["parameters"]) == {
        "config": {"mode": "fast"}
    }
    assert repair_arguments('"hello"') == {"value": "hello"}
    assert repair_arguments("{bad json") == {"_raw": "{bad json"}

    calls = repair_tool_calls(
        [
            {"id": "1", "function": {"name": "demo", "arguments": "{\"count\": \"1\"}"}},
            {"id": "2", "function": {"name": "demo", "arguments": "{\"count\": \"2\"}"}},
        ],
        tools=tools,
    )
    assert len(calls) == 1
    assert calls[0].arguments == {"count": 1}

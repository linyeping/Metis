from __future__ import annotations

import json

from backend.bridges.event_contract import EVENT_SCHEMA, agent_event_contract_payload
from backend.bridges.event_serializer import agent_event_payload, sse_data
from backend.runtime.agent_loop import (
    CompactEvent,
    ContentDeltaEvent,
    ContentEvent,
    DoneEvent,
    ErrorEvent,
    PermissionRequestEvent,
    TextDeltaEvent,
    ThinkingEvent,
    ToolCallEvent,
    ToolResultEvent,
    TodoUpdateEvent,
)


def test_text_delta_event_envelope_keeps_legacy_fields() -> None:
    payload = agent_event_payload(TextDeltaEvent(text="hello"))
    assert payload["schema"] == EVENT_SCHEMA
    assert payload["kind"] == "text_delta"
    assert payload["type"] == "text_delta"
    assert payload["text"] == "hello"
    assert payload["payload"]["text"] == "hello"
    assert payload["event_id"].startswith("evt_text_delta_")


def test_content_delta_event_envelope_keeps_legacy_fields() -> None:
    payload = agent_event_payload(ContentDeltaEvent(text="hello"))
    assert payload["schema"] == EVENT_SCHEMA
    assert payload["kind"] == "content_delta"
    assert payload["type"] == "content_delta"
    assert payload["text"] == "hello"
    assert payload["payload"]["text"] == "hello"
    assert payload["event_id"].startswith("evt_content_delta_")


def test_content_and_thinking_events() -> None:
    content = agent_event_payload(ContentEvent(text="final"))
    thinking = agent_event_payload(ThinkingEvent(text="reasoning"))
    assert content["kind"] == "content"
    assert content["text"] == "final"
    assert thinking["kind"] == "thinking"
    assert thinking["text"] == "reasoning"


def test_tool_call_event_fields() -> None:
    payload = agent_event_payload(
        ToolCallEvent(tool_name="read_file", arguments={"path": "x.py"}, call_id="call_1")
    )
    assert payload["kind"] == "tool_call"
    assert payload["tool"] == "read_file"
    assert payload["toolName"] == "read_file"
    assert payload["args"] == {"path": "x.py"}
    assert payload["arguments"] == {"path": "x.py"}
    assert payload["call_id"] == "call_1"
    assert payload["payload"]["call_id"] == "call_1"


def test_tool_result_is_truncated() -> None:
    payload = agent_event_payload(
        ToolResultEvent(tool_name="read_file", result="x" * 2500, call_id="call_1")
    )
    assert payload["kind"] == "tool_result"
    assert payload["tool"] == "read_file"
    assert len(payload["result"]) == 2003
    assert payload["result"].endswith("...")
    assert payload["payload"]["result"] == payload["result"]


def test_permission_request_event_fields() -> None:
    payload = agent_event_payload(
        PermissionRequestEvent(
            tool_name="write_file",
            arguments={"path": "x.py"},
            call_id="call_2",
            request_id="req_1",
        )
    )
    assert payload["kind"] == "permission_request"
    assert payload["tool"] == "write_file"
    assert payload["args"] == {"path": "x.py"}
    assert payload["request_id"] == "req_1"
    assert payload["requestId"] == "req_1"


def test_error_event_details_are_truncated() -> None:
    payload = agent_event_payload(
        ErrorEvent(
            code="LLM_TLS_ERROR",
            title="TLS",
            message="failed",
            hint="check proxy",
            recoverable=True,
            status=0,
            details="d" * 1500,
        )
    )
    assert payload["kind"] == "error"
    assert payload["code"] == "LLM_TLS_ERROR"
    assert payload["recoverable"] is True
    assert len(payload["details"]) == 1003
    assert payload["details"].endswith("...")


def test_done_event_usage_payload() -> None:
    payload = agent_event_payload(
        DoneEvent(
            total_turns=2,
            total_tool_calls=3,
            prompt_tokens=11,
            completion_tokens=13,
            total_tokens=24,
        )
    )
    assert payload["kind"] == "done"
    assert payload["turns"] == 2
    assert payload["tool_calls"] == 3
    assert payload["usage"] == {
        "prompt_tokens": 11,
        "completion_tokens": 13,
        "total_tokens": 24,
        "prompt_cache_hit_tokens": 0,
        "prompt_cache_miss_tokens": 0,
    }


def test_compact_event_payload() -> None:
    payload = agent_event_payload(
        CompactEvent(before_count=10, after_count=4, summary_preview="summary")
    )
    assert payload["kind"] == "compact"
    assert payload["before_count"] == 10
    assert payload["after_count"] == 4
    assert payload["summary_preview"] == "summary"


def test_dict_subagent_and_memory_events_are_normalized() -> None:
    subagent = agent_event_payload(
        {
            "type": "subagent_start",
            "task_id": "task_1",
            "name": "delegate_explore",
            "progress": 5,
            "status": "running",
        }
    )
    assert subagent["schema"] == EVENT_SCHEMA
    assert subagent["kind"] == "subagent_start"
    assert subagent["taskId"] == "task_1"
    assert subagent["payload"]["task_id"] == "task_1"

    memory = agent_event_payload(
        {
            "type": "memory_nudge",
            "message": "saved",
            "memory_count": 2,
            "skill_count": 1,
        }
    )
    assert memory["kind"] == "memory_nudge"
    assert memory["message"] == "saved"
    assert memory["memory_count"] == 2


def test_todo_update_event_fields() -> None:
    payload = agent_event_payload(
        TodoUpdateEvent(
            todos=[{"id": "1", "content": "plan", "status": "in_progress"}],
            summary="[任务清单] 1.▶️ plan",
            call_id="call_todo",
        )
    )

    assert payload["kind"] == "todo_update"
    assert payload["summary"].startswith("[任务清单]")
    assert payload["todos"][0]["content"] == "plan"
    assert payload["call_id"] == "call_todo"


def test_sse_data_done_and_json_payload() -> None:
    assert sse_data("[DONE]") == "data: [DONE]\n\n"
    line = sse_data(TextDeltaEvent(text="hello"))
    assert line.startswith("data: ")
    parsed = json.loads(line.split("data: ", 1)[1])
    assert parsed["schema"] == EVENT_SCHEMA
    assert parsed["type"] == "text_delta"


def test_agent_event_contract_payload_lists_supported_kinds() -> None:
    payload = agent_event_contract_payload()
    assert payload["schema"] == EVENT_SCHEMA
    assert payload["version"] == 1
    assert payload["transport"] == "sse"
    for kind in ("text_delta", "content_delta", "tool_call", "permission_request", "runtime_status", "todo_update", "done"):
        assert kind in payload["event_kinds"]
        assert kind in payload["legacy_compat_fields"]
    assert "payload" in payload["envelope_required"]

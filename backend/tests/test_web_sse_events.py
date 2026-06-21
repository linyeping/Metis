from __future__ import annotations

import json

from backend.web.app import _event_payload, _sse
from backend.runtime.agent_loop import ContentDeltaEvent, DoneEvent, RuntimeStatusEvent, TextDeltaEvent, ToolCallEvent


def _json_from_sse(chunk: str) -> dict:
    assert chunk.startswith("data: ")
    assert chunk.endswith("\n\n")
    return json.loads(chunk[len("data: ") :].strip())


def test_web_event_payload_uses_standard_schema() -> None:
    payload = _event_payload(ToolCallEvent(tool_name="read_file", arguments={"path": "x"}, call_id="c1"))
    assert payload["schema"] == "metis.agent_event.v1"
    assert payload["kind"] == "tool_call"
    assert payload["type"] == "tool_call"
    assert payload["tool"] == "read_file"
    assert payload["args"] == {"path": "x"}
    assert payload["payload"]["tool"] == "read_file"


def test_web_sse_keeps_done_marker() -> None:
    assert _sse("[DONE]") == "data: [DONE]\n\n"


def test_web_sse_serializes_runtime_event_with_legacy_fields() -> None:
    parsed = _json_from_sse(_sse(TextDeltaEvent(text="chunk")))
    assert parsed["schema"] == "metis.agent_event.v1"
    assert parsed["kind"] == "text_delta"
    assert parsed["type"] == "text_delta"
    assert parsed["text"] == "chunk"
    assert parsed["payload"]["text"] == "chunk"


def test_web_sse_serializes_content_delta_event() -> None:
    parsed = _json_from_sse(_sse(ContentDeltaEvent(text="chunk")))
    assert parsed["schema"] == "metis.agent_event.v1"
    assert parsed["kind"] == "content_delta"
    assert parsed["type"] == "content_delta"
    assert parsed["text"] == "chunk"
    assert parsed["payload"]["text"] == "chunk"


def test_web_sse_normalizes_dict_subagent_event() -> None:
    parsed = _json_from_sse(
        _sse(
            {
                "type": "subagent_done",
                "task_id": "task_1",
                "name": "delegate_shell",
                "progress": 100,
                "status": "done",
                "result": "ok",
            }
        )
    )
    assert parsed["schema"] == "metis.agent_event.v1"
    assert parsed["kind"] == "subagent_done"
    assert parsed["taskId"] == "task_1"
    assert parsed["result"] == "ok"


def test_web_sse_done_event_usage() -> None:
    parsed = _json_from_sse(
        _sse(DoneEvent(total_turns=1, total_tool_calls=2, prompt_tokens=3, completion_tokens=4, total_tokens=7))
    )
    assert parsed["kind"] == "done"
    assert parsed["usage"]["prompt_tokens"] == 3
    assert parsed["usage"]["completion_tokens"] == 4
    assert parsed["usage"]["total_tokens"] == 7
    assert parsed["usage"]["prompt_cache_hit_tokens"] == 0
    assert parsed["usage"]["prompt_cache_miss_tokens"] == 0


def test_web_sse_runtime_status_event() -> None:
    parsed = _json_from_sse(
        _sse(
            RuntimeStatusEvent(
                phase="llm_request",
                message="Calling LLM",
                turn=2,
                tool_calls=1,
                tool_name="read_file",
                call_id="call_1",
                details={"request_model": "pro", "served_model": "pro"},
            )
        )
    )
    assert parsed["schema"] == "metis.agent_event.v1"
    assert parsed["kind"] == "runtime_status"
    assert parsed["type"] == "runtime_status"
    assert parsed["phase"] == "llm_request"
    assert parsed["message"] == "Calling LLM"
    assert parsed["tool"] == "read_file"
    assert parsed["payload"]["phase"] == "llm_request"
    assert parsed["details"]["request_model"] == "pro"


def test_web_contract_endpoint() -> None:
    from backend.web.app import app

    with app.test_client() as flask_client:
        response = flask_client.get("/contract/agent-events")
    assert response.status_code == 200
    data = response.get_json()
    assert data["schema"] == "metis.agent_event.v1"
    assert "text_delta" in data["event_kinds"]
    assert "content_delta" in data["event_kinds"]
    assert "runtime_status" in data["event_kinds"]

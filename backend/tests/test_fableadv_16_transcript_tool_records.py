from __future__ import annotations

from backend.runtime import agent_loop
from backend.runtime.agent_loop import AgentConfig
from backend.web.app import _truncate_tool_record, _tool_result_is_error


def test_transcript_tool_records_are_filtered_from_model_request() -> None:
    """FABLEADV-16: metis_kind=tool transcript records must never reach the LLM
    (they are not valid OpenAI tool messages and would break the request)."""
    config = AgentConfig(
        llm_backend="openai",
        llm_base_url="https://api.example.com/v1",
        llm_model="gpt-5.5",
    )
    messages = [
        {"role": "user", "content": "读取 paths.py"},
        {
            "role": "assistant",
            "content": "",
            "metis_kind": "tool",
            "metis_tool": {"call_id": "c1", "name": "read_file", "result": "=== paths.py ==="},
        },
        {"role": "assistant", "content": "解析顺序是…"},
    ]

    request = agent_loop._messages_for_llm_request(messages, config)

    # The tool record is dropped; only the real user + assistant turns remain.
    assert len(request) == 2
    assert [m["role"] for m in request] == ["user", "assistant"]
    for m in request:
        assert "metis_kind" not in m
        assert "metis_tool" not in m


def test_truncate_tool_record_bounds_large_output() -> None:
    small = "ok"
    assert _truncate_tool_record(small) == "ok"

    big = "x" * 9000
    out = _truncate_tool_record(big)
    assert len(out) < len(big)
    assert "结果已截断" in out


def test_tool_result_is_error_detection() -> None:
    assert _tool_result_is_error("Error: boom") is True
    assert _tool_result_is_error("❌ failed") is True
    assert _tool_result_is_error("错误：路径不存在") is True
    assert _tool_result_is_error("=== file.py ===\nok") is False

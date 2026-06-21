from __future__ import annotations

import json
from typing import Any, Dict, Generator, List, Optional

from backend.runtime import action_audit
from backend.runtime.agent_loop import AgentConfig, ToolResultEvent, run_stream
from backend.runtime.llm_backends import LLMBackend, LLMResponse, ToolCall
from backend.runtime.tool_registry import ToolDefinition, ToolRegistry
from backend.runtime.tool_visibility import sanitize_tool_result


class VisibilityBackend(LLMBackend):
    def __init__(self) -> None:
        self.calls = 0
        self.messages_per_call: List[List[Dict[str, Any]]] = []

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> LLMResponse:
        self.calls += 1
        self.messages_per_call.append(messages)
        if self.calls == 1:
            return LLMResponse(tool_calls=[ToolCall("call_1", "metis_runtime_status", {})])
        return LLMResponse(content="done")

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> Generator[str, None, LLMResponse]:
        response = self.chat(messages, tools, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
        if response.content:
            yield response.content
        return response


def test_sensitive_tool_result_gets_public_view() -> None:
    raw = json.dumps(
        {
            "status": "failed",
            "debug_summary": r"Runtime pack missing at D:\Metis\cache\rootfs.vhdx",
            "debug_next_action": "download it with api_key=sk-aaaaaaaaaaaaaaaa",
            "internal_path": r"D:\Metis\cache\manifest.json",
        }
    )

    visible = sanitize_tool_result("metis_runtime_status", raw)

    assert r"D:\Metis" not in visible.public_result
    assert "sk-aaaaaaaaaaaaaaaa" not in visible.public_result
    assert "internal_path" not in visible.public_result
    assert json.loads(visible.diagnostic_result)["internal_path"] == r"D:\Metis\cache\manifest.json"
    assert "sk-aaaaaaaaaaaaaaaa" not in visible.diagnostic_result


def test_agent_loop_sends_public_result_but_audits_raw(monkeypatch, tmp_path) -> None:
    monkeypatch.setenv("METIS_ACTION_AUDIT", "1")
    backend = VisibilityBackend()
    registry = ToolRegistry()
    raw = json.dumps(
        {
            "status": "failed",
            "debug_summary": r"Guest handshake failed at D:\Metis\vm\sessiondata.vhdx",
            "debug_next_action": "retry after checking token=sk-bbbbbbbbbbbbbbbb",
            "trace": r"D:\Metis\vm\logs\guest.log",
        }
    )
    registry.register(
        ToolDefinition(
            name="metis_runtime_status",
            description="fake runtime status",
            parameters={"type": "object", "properties": {}},
            execute_fn=lambda: raw,
            requires_approval=False,
        )
    )

    events = list(
        run_stream(
            [{"role": "user", "content": "check runtime"}],
            AgentConfig(llm_backend="fake", llm_model="fake", workspace_root=str(tmp_path), max_turns=3),
            registry=registry,
            backend=backend,
        )
    )

    event_result = next(event.result for event in events if isinstance(event, ToolResultEvent))
    assert r"D:\Metis" not in event_result
    assert "sk-bbbbbbbbbbbbbbbb" not in event_result

    tool_messages = [msg for msg in backend.messages_per_call[-1] if msg.get("role") == "tool"]
    assert tool_messages
    assert r"D:\Metis" not in tool_messages[-1]["content"]
    assert "sk-bbbbbbbbbbbbbbbb" not in tool_messages[-1]["content"]

    rows = action_audit.read_recent(str(tmp_path), limit=1)
    assert rows
    assert rows[0]["visibility"] == "diagnostic_raw"
    assert json.loads(rows[0]["result"])["trace"] == r"D:\Metis\vm\logs\guest.log"
    assert "sk-bbbbbbbbbbbbbbbb" not in rows[0]["result"]
    assert r"D:\Metis" not in rows[0]["result_public"]

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Dict, Generator, Iterable, List, Optional

from backend.core import paths as metis_paths
from backend.evals import runner as eval_runner
from backend.evals.tasks.task_spec import EvalSuite
from backend.bridges.provider_registry import (
    parallel_tool_calls_enabled,
    requires_reasoning_passback_enabled,
)
from backend.runtime import agent_loop
from backend.runtime.agent_loop import AgentConfig, DoneEvent
from backend.runtime.llm_backends import LLMBackend, LLMResponse, ToolCall
from backend.runtime.llm_backends import openai_compat
from backend.runtime.llm_backends.openai_compat import OpenAICompatBackend
from backend.runtime.provider_conformance import load_provider_conformance, save_provider_conformance
from backend.runtime.tool_registry import ToolDefinition, ToolRegistry
from backend.web import app as web_app


class _JsonResponse:
    headers: Dict[str, str] = {}
    status_code = 200

    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload

    def json(self) -> Dict[str, Any]:
        return self._payload

    def raise_for_status(self) -> None:
        return None


class _SseResponse:
    headers: Dict[str, str] = {}
    status_code = 200

    def __init__(self, lines: Iterable[bytes]) -> None:
        self._lines = list(lines)

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self, decode_unicode: bool = False) -> Iterable[bytes]:
        assert decode_unicode is False
        return iter(self._lines)

    def close(self) -> None:
        return None


def test_openai_compat_preserves_non_stream_reasoning_content(monkeypatch: Any) -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "content": "done",
                    "reasoning_content": "private chain summary",
                },
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3},
    }
    monkeypatch.setattr(openai_compat, "post_with_retries", lambda *args, **kwargs: _JsonResponse(payload))

    backend = OpenAICompatBackend("https://api.deepseek.com", "test-key", "deepseek-v4-flash")
    response = backend.chat([{"role": "user", "content": "hi"}])

    assert response.content == "done"
    assert response.thinking == "private chain summary"
    assert response.reasoning_content == "private chain summary"


def test_openai_compat_preserves_stream_reasoning_content(monkeypatch: Any) -> None:
    def chunk(delta: Dict[str, Any], finish_reason: Optional[str] = None) -> bytes:
        payload = {"choices": [{"delta": delta, "finish_reason": finish_reason}]}
        return f"data: {json.dumps(payload)}".encode("utf-8")

    response = _SseResponse(
        [
            chunk({"reasoning_content": "think-1 "}),
            chunk({"content": "visible"}),
            chunk({"reasoning_content": "think-2"}, finish_reason="stop"),
            b"data: [DONE]",
        ]
    )
    monkeypatch.setattr(openai_compat, "post_with_retries", lambda *args, **kwargs: response)

    backend = OpenAICompatBackend("https://api.deepseek.com", "test-key", "deepseek-v4-flash")
    stream = backend.chat_stream([{"role": "user", "content": "hi"}])
    chunks: list[str] = []
    while True:
        try:
            chunks.append(next(stream))
        except StopIteration as stop:
            final = stop.value
            break

    assert chunks == ["visible"]
    assert final.content == "visible"
    assert final.thinking == "think-1 think-2"
    assert final.reasoning_content == "think-1 think-2"


class ReasoningRoundTripBackend(LLMBackend):
    def __init__(self) -> None:
        self.calls = 0
        self.second_request_messages: List[Dict[str, Any]] = []

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        cancel_event: Optional[threading.Event] = None,
    ) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                reasoning_content="round-one-thinking",
                tool_calls=[ToolCall(id="call_probe", name="probe_tool", arguments={})],
                stop_reason="tool_use",
            )
        self.second_request_messages = messages
        return LLMResponse(content="done")

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        cancel_event: Optional[threading.Event] = None,
    ) -> Generator[str, None, LLMResponse]:
        response = self.chat(messages, tools, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
        if response.content:
            yield response.content
        return response


def test_agent_loop_replays_deepseek_reasoning_content_on_second_tool_round(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setattr(agent_loop, "_build_environment_context", lambda: "")
    backend = ReasoningRoundTripBackend()
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="probe_tool",
            description="probe",
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=lambda: "ok",
            requires_approval=False,
        )
    )

    events = list(
        agent_loop.run(
            [{"role": "user", "content": "use a tool"}],
            AgentConfig(
                llm_backend="deepseek",
                llm_base_url="https://api.deepseek.com",
                llm_model="deepseek-v4-flash",
                workspace_root=str(tmp_path),
                enabled_tools=["probe_tool"],
                max_turns=3,
            ),
            registry=registry,
            backend=backend,
        )
    )

    assistant_message = next(
        message for message in backend.second_request_messages if message.get("role") == "assistant"
    )
    assert assistant_message["reasoning_content"] == "round-one-thinking"
    assert "_metis_reasoning_model" not in assistant_message
    assert any(isinstance(event, DoneEvent) for event in events)


def test_agent_loop_strips_reasoning_content_for_other_provider_or_model() -> None:
    deepseek_config = AgentConfig(
        llm_backend="deepseek",
        llm_base_url="https://api.deepseek.com",
        llm_model="deepseek-v4-flash",
    )
    message = agent_loop._format_assistant_message(
        LLMResponse(content="visible", reasoning_content="private"),
        deepseek_config,
    )

    same_model = agent_loop._messages_for_llm_request([message], deepseek_config)
    assert same_model[0]["reasoning_content"] == "private"

    other_model = agent_loop._messages_for_llm_request(
        [message],
        AgentConfig(
            llm_backend="deepseek",
            llm_base_url="https://api.deepseek.com",
            llm_model="deepseek-v4-pro",
        ),
    )
    assert "reasoning_content" not in other_model[0]

    openai_request = agent_loop._messages_for_llm_request(
        [message],
        AgentConfig(
            llm_backend="openai",
            llm_base_url="https://api.openai.com/v1",
            llm_model="gpt-4o-mini",
        ),
    )
    assert "reasoning_content" not in openai_request[0]


def test_provider_conformance_is_persisted_and_overrides_capabilities(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setenv("METIS_HOME", str(tmp_path))
    metis_paths.clear_metis_home_cache()
    try:
        result = {
            "provider_id": "deepseek",
            "model": "deepseek-v4-flash",
            "ok": True,
            "requires_reasoning_passback": False,
            "parallel_tool_calls": False,
        }
        path = save_provider_conformance(result)

        assert path.is_file()
        loaded = load_provider_conformance("deepseek", "deepseek-v4-flash")
        assert loaded is not None
        assert loaded["requires_reasoning_passback"] is False
        assert requires_reasoning_passback_enabled("deepseek", model="deepseek-v4-flash") is False
        assert parallel_tool_calls_enabled("deepseek", model="deepseek-v4-flash") is False
    finally:
        metis_paths.clear_metis_home_cache()


def test_greeting_without_tools_does_not_record_memory_or_skill(monkeypatch: Any, tmp_path: Path) -> None:
    calls: list[str] = []
    monkeypatch.setenv("METIS_AUTO_MEMORY", "1")
    monkeypatch.setenv("METIS_AUTO_SKILLS", "1")
    monkeypatch.setattr(web_app, "_append_project_memory", lambda entry: calls.append(f"memory:{entry}") or str(tmp_path / "METIS.md"))
    monkeypatch.setattr(web_app, "_create_skill_from_session", lambda summary, tools: calls.append(f"skill:{summary}") or str(tmp_path / "SKILL.md"))
    web_app._runtime_state.learning_nudged_sessions.clear()

    result = web_app._maybe_record_learning(
        DoneEvent(total_turns=4, total_tool_calls=0),
        [],
        session_id="greeting-session",
        history=[{"role": "user", "content": "你好，请用一句话介绍你自己"}],
    )

    assert result is None
    assert calls == []


def test_capability_eval_suite_uses_provider_conformance_probe(monkeypatch: Any, tmp_path: Path) -> None:
    calls: list[Dict[str, Any]] = []

    def fake_probe(**kwargs: Any) -> Dict[str, Any]:
        calls.append(kwargs)
        return {"ok": True, "provider_id": kwargs["provider_id"], "model": kwargs["model"]}

    monkeypatch.setattr(eval_runner, "run_provider_conformance_probe", fake_probe)
    suite = EvalSuite(name="capability", tasks=[], metadata={"kind": "capability-probes"})

    result = eval_runner.run_suite(
        suite,
        backend_name="deepseek",
        model="deepseek-v4-flash",
        api_key="test-key",
        repeat=1,
        output_dir=tmp_path,
    )

    assert calls == [
        {
            "provider_id": "deepseek",
            "base_url": "https://api.deepseek.com",
            "api_key": "test-key",
            "model": "deepseek-v4-flash",
        }
    ]
    assert result["provider_conformance"]["ok"] is True

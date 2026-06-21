from __future__ import annotations

from typing import Any, Dict, Generator, List, Optional

from backend.bridges.event_serializer import agent_event_payload
from backend.runtime.agent_loop import (
    AgentConfig,
    ContentDeltaEvent,
    ContentEvent,
    DoneEvent,
    ErrorEvent,
    PermissionRequestEvent,
    RuntimeStatusEvent,
    ToolCallEvent,
    ToolResultEvent,
    run_stream,
)
from backend.runtime.llm_backends import LLMBackend, LLMResponse, ToolCall, Usage
from backend.runtime.tool_registry import ToolDefinition, ToolRegistry
from backend.tools.coding.foundation.core_mechanisms.execution_boundary_context import get_effective_sub_allow


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class FakeHTTPError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.response = FakeResponse(status_code)


class StreamingBackend(LLMBackend):
    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> LLMResponse:
        return LLMResponse(content="Hello runtime", usage=Usage(1, 2, 3))

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> Generator[str, None, LLMResponse]:
        yield "Hello "
        yield "runtime"
        return LLMResponse(content="Hello runtime", usage=Usage(1, 2, 3))


class ToolBackend(LLMBackend):
    def __init__(self) -> None:
        self.calls = 0

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
        if self.calls == 1:
            return LLMResponse(tool_calls=[ToolCall("call_1", "echo", {"value": "ok"})])
        return LLMResponse(content="Tool completed", usage=Usage(3, 4, 7))

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


class CrashingStreamBackend(StreamingBackend):
    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> Generator[str, None, LLMResponse]:
        yield "partial"
        raise RuntimeError("stream blew up")


class AuthErrorBackend(StreamingBackend):
    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> Generator[str, None, LLMResponse]:
        if False:
            yield ""
        raise FakeHTTPError(401, "401 unauthorized invalid api key")


class EndlessToolBackend(ToolBackend):
    def chat(self, *args: Any, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        return LLMResponse(tool_calls=[ToolCall(f"call_{self.calls}", "echo", {"value": "again"})])


class ApprovalBackend(ToolBackend):
    def chat(self, *args: Any, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(tool_calls=[ToolCall("approval_call_1", "write_file", {"path": "fake.txt", "content": "ok"})])
        return LLMResponse(content="Approval flow completed", usage=Usage(2, 3, 5))


class BoundaryBackend(ToolBackend):
    def chat(self, *args: Any, **kwargs: Any) -> LLMResponse:
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(tool_calls=[ToolCall("boundary_call_1", "read_file", {"path": "E:\\notes.txt"})])
        return LLMResponse(content="Boundary flow completed", usage=Usage(2, 3, 5))


class ToolCallRepairBackend(LLMBackend):
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
            return LLMResponse(content="", stop_reason="tool_use", tool_calls=[], usage=Usage(2, 1, 3))
        if self.calls == 2:
            return LLMResponse(tool_calls=[ToolCall("call_repaired", "echo", {"value": "repaired"})], usage=Usage(3, 1, 4))
        return LLMResponse(content="Repair completed", stop_reason="end_turn", usage=Usage(4, 2, 6))

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


def _messages() -> List[Dict[str, Any]]:
    return [{"role": "user", "content": "smoke"}]


def _config(**kwargs: Any) -> AgentConfig:
    return AgentConfig(
        llm_backend="fake",
        llm_model="fake-runtime",
        timeout=1,
        max_turns=kwargs.pop("max_turns", 4),
        max_consecutive_errors=kwargs.pop("max_consecutive_errors", 1),
        **kwargs,
    )


def _registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="echo",
            description="Return a test value.",
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
            execute_fn=lambda value: f"echo:{value}",
            requires_approval=False,
        )
    )
    return registry


def _approval_registry(executed: List[str]) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="write_file",
            description="Fake write for approval tests.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                "required": ["path", "content"],
            },
            execute_fn=lambda path, content: executed.append(f"{path}:{content}") or "fake write ok",
            requires_approval=True,
        )
    )
    return registry


def _boundary_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="read_file",
            description="Fake read for boundary override tests.",
            parameters={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
            execute_fn=lambda path: f"allow_paths:{get_effective_sub_allow('allow_paths_outside_workspace')}",
            requires_approval=False,
        )
    )
    return registry


def _events(backend: LLMBackend, *, config: Optional[AgentConfig] = None) -> List[Any]:
    return list(run_stream(_messages(), config or _config(), registry=_registry(), backend=backend))


def _events_with_permission(approved: bool, executed: List[str]) -> List[Any]:
    gen = run_stream(
        _messages(),
        # "ask" prompts for every tool — exercises the approval stream itself.
        # (Accept-edits auto-applies file edits, so it would not prompt here.)
        _config(execution_mode="ask"),
        registry=_approval_registry(executed),
        backend=ApprovalBackend(),
    )
    events: List[Any] = []
    send_value: Optional[bool] = None
    while True:
        try:
            event = gen.send(send_value) if send_value is not None else next(gen)
            send_value = None
        except StopIteration:
            break
        events.append(event)
        if isinstance(event, PermissionRequestEvent):
            send_value = approved
    return events


def _phases(events: List[Any]) -> List[str]:
    return [event.phase for event in events if isinstance(event, RuntimeStatusEvent)]


def test_run_stream_emits_status_content_delta_content_and_done() -> None:
    events = _events(StreamingBackend())
    assert _phases(events) == ["starting", "llm_request", "streaming", "llm_response", "completed"]
    assert [event.text for event in events if isinstance(event, ContentDeltaEvent)] == ["Hello runtime"]
    assert any(isinstance(event, DoneEvent) and event.total_tokens == 3 for event in events)


def test_run_stream_tool_path_emits_tool_status_and_result() -> None:
    events = _events(ToolBackend())
    phases = _phases(events)
    assert "tool_running" in phases
    assert "tool_done" in phases
    assert any(isinstance(event, ToolCallEvent) and event.tool_name == "echo" for event in events)
    assert any(isinstance(event, ToolResultEvent) and event.result.startswith("echo:ok") for event in events)
    assert any(isinstance(event, DoneEvent) and event.total_tool_calls == 1 for event in events)


def test_run_stream_permission_approve_executes_tool_and_finishes() -> None:
    executed: List[str] = []
    events = _events_with_permission(True, executed)

    assert executed == ["fake.txt:ok"]
    assert any(isinstance(event, PermissionRequestEvent) and event.tool_name == "write_file" for event in events)
    assert any(isinstance(event, ToolResultEvent) and event.result.startswith("fake write ok") for event in events)
    assert any(isinstance(event, ToolResultEvent) and "Principle #5 reminder" in event.result for event in events)
    assert any(isinstance(event, DoneEvent) and event.total_tool_calls == 1 for event in events)


def test_run_stream_permission_deny_skips_tool_and_finishes() -> None:
    executed: List[str] = []
    events = _events_with_permission(False, executed)
    result = next(event for event in events if isinstance(event, ToolResultEvent))

    assert executed == []
    assert "Permission denied" in result.result
    assert "User declined" in result.result
    assert any(isinstance(event, DoneEvent) and event.total_tool_calls == 1 for event in events)


def test_run_stream_applies_tool_boundary_overrides() -> None:
    events = list(
        run_stream(
            _messages(),
            _config(
                tool_boundary_overrides=lambda _name, _arguments: {
                    "allow_paths_outside_workspace": True,
                }
            ),
            registry=_boundary_registry(),
            backend=BoundaryBackend(),
        )
    )

    assert any(
        isinstance(event, ToolResultEvent) and event.result == "allow_paths:True"
        for event in events
    )
    assert get_effective_sub_allow("allow_paths_outside_workspace") is False


def test_run_stream_repairs_empty_tool_call_response() -> None:
    backend = ToolCallRepairBackend()
    events = _events(backend)
    phases = _phases(events)

    assert backend.calls == 3
    assert "tool_call_repair" in phases
    assert any("Repair it now by returning exactly one native tool/function call" in str(message.get("content")) for message in backend.messages_per_call[1])
    assert any(isinstance(event, ToolCallEvent) and event.call_id == "call_repaired" for event in events)
    assert any(isinstance(event, ToolResultEvent) and event.result == "echo:repaired" for event in events)
    assert any(isinstance(event, DoneEvent) and event.total_tool_calls == 1 for event in events)


def test_stream_crash_emits_failed_error_and_done() -> None:
    events = _events(CrashingStreamBackend())
    assert "failed" in _phases(events)
    error = next(event for event in events if isinstance(event, ErrorEvent))
    assert error.code == "LLM_ERROR"
    assert error.recoverable is False
    assert any(isinstance(event, DoneEvent) for event in events)


def test_auth_error_is_non_retryable_and_classified() -> None:
    backend = AuthErrorBackend()
    events = _events(backend, config=_config(max_consecutive_errors=3))
    error = next(event for event in events if isinstance(event, ErrorEvent))
    assert error.code == "LLM_AUTH_FAILED"
    assert error.recoverable is False
    assert _phases(events).count("llm_request") == 1
    assert any(isinstance(event, DoneEvent) for event in events)


def test_max_turns_emits_runtime_failure_error_and_done() -> None:
    events = _events(EndlessToolBackend(), config=_config(max_turns=2))
    assert "failed" in _phases(events)
    error = next(event for event in events if isinstance(event, ErrorEvent))
    assert error.code == "RUNTIME_MAX_TURNS"
    assert any(isinstance(event, DoneEvent) and event.total_turns == 2 for event in events)


def test_runtime_status_serializes_to_agent_event_envelope() -> None:
    payload = agent_event_payload(
        RuntimeStatusEvent(
            phase="llm_request",
            message="Calling LLM",
            turn=2,
            tool_calls=1,
            tool_name="echo",
            call_id="call_1",
            details={"request_model": "pro", "served_model": "pro"},
        )
    )
    assert payload["kind"] == "runtime_status"
    assert payload["type"] == "runtime_status"
    assert payload["phase"] == "llm_request"
    assert payload["message"] == "Calling LLM"
    assert payload["tool"] == "echo"
    assert payload["payload"]["phase"] == "llm_request"
    assert payload["details"]["request_model"] == "pro"
    assert payload["payload"]["details"]["served_model"] == "pro"


# FABLEADV-19: truncation continuation


class _TruncatedThenCompleteBackend(LLMBackend):
    """First turn is cut off by max_tokens with partial text; second turn finishes."""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools=None, *, temperature=0.3, max_tokens=4096, timeout=120.0):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(content="前半段", stop_reason="max_tokens", usage=Usage(1, 2, 3))
        return LLMResponse(content="后半段", stop_reason="end_turn", usage=Usage(1, 2, 3))

    def chat_stream(self, messages, tools=None, *, temperature=0.3, max_tokens=4096, timeout=120.0):
        response = self.chat(messages, tools, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
        if response.content:
            yield response.content
        return response


class _TruncatedToolThenTextBackend(LLMBackend):
    """First turn truncates a tool call (no content, stop_reason=max_tokens); second finishes."""

    def __init__(self) -> None:
        self.calls = 0

    def chat(self, messages, tools=None, *, temperature=0.3, max_tokens=4096, timeout=120.0):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(content="", stop_reason="max_tokens", usage=Usage(1, 2, 3))
        return LLMResponse(content="完成", stop_reason="end_turn", usage=Usage(1, 2, 3))

    def chat_stream(self, messages, tools=None, *, temperature=0.3, max_tokens=4096, timeout=120.0):
        response = self.chat(messages, tools, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
        if response.content:
            yield response.content
        return response


def test_truncated_output_continues_and_concatenates() -> None:
    backend = _TruncatedThenCompleteBackend()
    events = _events(backend)
    # continued once (2 model calls), not ended on first truncation
    assert backend.calls == 2
    contents = [event.text for event in events if isinstance(event, ContentEvent)]
    # final ContentEvent stitches partial + continuation, no "No response"
    assert contents[-1] == "前半段后半段"
    assert all("No response from LLM" not in c for c in contents)


def test_truncated_tool_call_guides_and_recovers() -> None:
    backend = _TruncatedToolThenTextBackend()
    events = _events(backend)
    assert backend.calls == 2  # guided to retry instead of ending
    contents = [event.text for event in events if isinstance(event, ContentEvent)]
    assert contents[-1] == "完成"
    assert all("No response from LLM" not in c for c in contents)

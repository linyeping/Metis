from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from backend.bridges.event_serializer import agent_event_payload
from backend.core.engine.prompt_runtime import compile_prompt_runtime
from backend.core.memory.workspace_state import summarize_for_system_prompt
from backend.runtime import agent_loop
from backend.runtime.agent_loop import AgentConfig, ContentEvent, DoneEvent, TodoUpdateEvent
from backend.runtime.llm_backends import LLMBackend, LLMResponse, ToolCall
from backend.runtime.loop_discipline import VerificationTracker, compact_todo_block
from backend.runtime.subagent_prompts import EXPLORE_TOOLS, SHELL_TOOLS, compress_subagent_result
from backend.runtime.tool_registry import ToolDefinition, ToolRegistry
from backend.tools.coding.workflow_features.agent_state.state_paths import AGENT_TODO_FILE


class VerifyNudgeBackend(LLMBackend):
    def __init__(self) -> None:
        self.calls = 0
        self.messages: List[List[Dict[str, Any]]] = []

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        cancel_event: Any = None,
    ) -> LLMResponse:
        self.calls += 1
        self.messages.append([dict(message) for message in messages])
        if self.calls == 1:
            return LLMResponse(tool_calls=[ToolCall("call_write", "write_file", {"file_path": "app.py", "content": "x = 1\n"})])
        return LLMResponse(content=f"final {self.calls}")

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        cancel_event: Any = None,
    ) -> Generator[str, None, LLMResponse]:
        response = self.chat(messages, tools, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
        if response.content:
            yield response.content
        return response


class TodoBackend(LLMBackend):
    def __init__(self) -> None:
        self.calls = 0
        self.messages: List[List[Dict[str, Any]]] = []

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        cancel_event: Any = None,
    ) -> LLMResponse:
        self.calls += 1
        self.messages.append([dict(message) for message in messages])
        if self.calls == 1:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        "call_todo",
                        "todo_write",
                        {"todos": [{"id": "1", "content": "定位问题", "status": "in_progress"}]},
                    )
                ]
            )
        return LLMResponse(content="done")

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        cancel_event: Any = None,
    ) -> Generator[str, None, LLMResponse]:
        response = self.chat(messages, tools, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
        if response.content:
            yield response.content
        return response


def _registry(tmp_path: Path) -> ToolRegistry:
    registry = ToolRegistry()

    def write_file(file_path: str, content: str) -> str:
        target = tmp_path / file_path
        target.write_text(content, encoding="utf-8")
        return "✅ wrote"

    def todo_write(todos: list[dict[str, Any]], merge: bool = True, path: str = str(tmp_path / AGENT_TODO_FILE)) -> str:
        from backend.tools.coding.workflow_features.agent_state.todo_write import todo_write as real_todo_write

        return real_todo_write(todos, merge=merge, path=path)

    registry.register(
        ToolDefinition(
            name="write_file",
            description="write",
            parameters={"type": "object", "properties": {}},
            execute_fn=write_file,
            requires_approval=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="todo_write",
            description="todo",
            parameters={"type": "object", "properties": {}},
            execute_fn=todo_write,
            requires_approval=False,
        )
    )
    return registry


def test_loop_discipline_prompt_and_todo_summary(tmp_path: Path) -> None:
    (tmp_path / AGENT_TODO_FILE).write_text(
        '{"todos":[{"id":"1","content":"定位 bug 根因","status":"done"},{"id":"2","content":"补回归测试","status":"in_progress"}]}',
        encoding="utf-8",
    )

    snapshot = compile_prompt_runtime(
        "Base.",
        workspace_root=str(tmp_path),
        include_repo_map_hint=False,
        include_agent_state_hint=True,
        include_open_files_hint=False,
        include_terminal_hint=False,
    )

    assert "[Loop Discipline]" in snapshot.final_system_prompt
    assert "[任务清单]" in summarize_for_system_prompt(str(tmp_path))
    assert "1.✅ 定位 bug 根因" in snapshot.final_system_prompt
    assert "2.▶️ 补回归测试" in snapshot.final_system_prompt


def test_todo_update_event_and_dynamic_injection(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(agent_loop, "_build_environment_context", lambda: "")
    backend = TodoBackend()

    events = list(
        agent_loop.run(
            [{"role": "user", "content": "do work"}],
            AgentConfig(system_prompt="Base", workspace_root=str(tmp_path), llm_model="fake", max_turns=3),
            registry=_registry(tmp_path),
            backend=backend,
        )
    )

    assert any(isinstance(event, TodoUpdateEvent) for event in events)
    todo_payload = agent_event_payload(next(event for event in events if isinstance(event, TodoUpdateEvent)))
    assert todo_payload["kind"] == "todo_update"
    assert todo_payload["summary"].startswith("[任务清单]")
    assert any("[Metis dynamic todo state]" in str(message.get("content") or "") for message in backend.messages[-1])
    assert any(isinstance(event, DoneEvent) for event in events)


def test_verify_nudge_inserts_extra_turn_for_unverified_code_edit(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(agent_loop, "_build_environment_context", lambda: "")
    backend = VerifyNudgeBackend()

    events = list(
        agent_loop.run(
            [{"role": "user", "content": "edit app"}],
            AgentConfig(system_prompt="Base", workspace_root=str(tmp_path), llm_model="fake", max_turns=4),
            registry=_registry(tmp_path),
            backend=backend,
        )
    )

    assert backend.calls == 3
    assert any("Verification reminder" in str(message.get("content") or "") for message in backend.messages[-1])
    assert [event.text for event in events if isinstance(event, ContentEvent)] == ["final 3"]


def test_verification_tracker_exempts_docs_and_counts_verification() -> None:
    tracker = VerificationTracker()
    tracker.record("write_file", {"file_path": "notes.md"})
    assert tracker.needs_nudge() is False

    tracker.record("write_file", {"file_path": "app.py"})
    assert tracker.needs_nudge() is True
    tracker.record("run_tests", {"command": "pytest"})
    assert tracker.needs_nudge() is False


def test_subagent_contracts_are_constrained() -> None:
    assert "semantic_search" not in EXPLORE_TOOLS
    assert not any(name.startswith("delegate_") for name in EXPLORE_TOOLS + SHELL_TOOLS)
    assert len(compress_subagent_result("x" * 3000)) <= 1500
    assert "truncated" in compress_subagent_result("x" * 3000)


def test_compact_todo_block_icons() -> None:
    block = compact_todo_block(
        [
            {"content": "done item", "status": "done"},
            {"content": "active item", "status": "in_progress"},
            {"content": "pending item", "status": "pending"},
        ]
    )
    assert "1.✅ done item" in block
    assert "2.▶️ active item" in block
    assert "3.⬜ pending item" in block

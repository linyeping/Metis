from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

import pytest

from backend.core.paths import clear_metis_home_cache
from backend.runtime.agent_loop import AgentConfig, DoneEvent, run
from backend.runtime.hook_lifecycle_bus import (
    HOOK_LIFECYCLE_SCHEMA,
    emit_hook_lifecycle,
    recent_hook_lifecycle_events,
    reset_hook_lifecycle_bus_for_tests,
    subscribe_hook_lifecycle,
)
from backend.runtime.llm_backends import LLMBackend, LLMResponse
from backend.runtime.tool_registry import ToolDefinition, ToolRegistry


@pytest.fixture(autouse=True)
def _isolated_hook_bus(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("METIS_HOME", str(tmp_path / "metis-home"))
    monkeypatch.setenv("METIS_HOOK_COMMANDS", "0")
    clear_metis_home_cache()
    reset_hook_lifecycle_bus_for_tests()
    yield
    reset_hook_lifecycle_bus_for_tests()
    clear_metis_home_cache()


class DoneBackend(LLMBackend):
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
        del messages, tools, temperature, max_tokens, timeout, cancel_event
        return LLMResponse(content="finished", stop_reason="stop")

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
        response = self.chat(
            messages,
            tools,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            cancel_event=cancel_event,
        )
        yield response.content
        return response


def test_hook_lifecycle_bus_subscriber_audit_and_redaction(tmp_path: Path) -> None:
    seen = []

    def handler(event):
        seen.append(event)

    def broken(_event):
        raise RuntimeError("boom")

    subscribe_hook_lifecycle(handler, kinds=["tool.start"])
    subscribe_hook_lifecycle(broken, kinds=["tool.start"])

    result = emit_hook_lifecycle(
        "tool.start",
        workspace_root=str(tmp_path),
        tool_name="demo",
        arguments={"api_key": "secret", "path": "x.py"},
        status="starting",
    )

    assert result.event.schema == HOOK_LIFECYCLE_SCHEMA
    assert len(seen) == 1
    assert result.handler_errors == ["RuntimeError: boom"]

    recent = recent_hook_lifecycle_events()
    assert recent[-1]["arguments"]["api_key"] == "[redacted]"
    audit_path = tmp_path / "metis-home" / "audit" / "hook-lifecycle.jsonl"
    rows = [json.loads(line) for line in audit_path.read_text(encoding="utf-8").splitlines()]
    assert rows[-1]["schema"] == HOOK_LIFECYCLE_SCHEMA
    assert rows[-1]["arguments"]["api_key"] == "[redacted]"


def test_configured_command_hook_runs_from_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METIS_HOOK_COMMANDS", "1")
    hooks_dir = tmp_path / ".metis"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": [
                    {
                        "event": "tool.finish",
                        "tool": "echo",
                        "description": "echo hook",
                        "command": "python -c \"print('hook-{tool_name}')\"",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    result = emit_hook_lifecycle(
        "tool.finish",
        workspace_root=str(tmp_path),
        tool_name="echo",
        result="ok",
        ok=True,
    )

    assert "Hook [echo hook]: ok: hook-echo" in result.display_output


def test_tool_registry_emits_tool_lifecycle_and_appends_hook_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("METIS_HOOK_COMMANDS", "1")
    hooks_dir = tmp_path / ".metis"
    hooks_dir.mkdir()
    (hooks_dir / "hooks.json").write_text(
        json.dumps(
            {
                "hooks": [
                    {
                        "trigger": "post:echo",
                        "description": "legacy post",
                        "command": "python -c \"print('legacy-{tool_name}')\"",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    events = []
    subscribe_hook_lifecycle(lambda event: events.append(event), kinds=["tool.start", "tool.finish"])

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="echo",
            description="Echo value.",
            parameters={"type": "object", "properties": {"value": {"type": "string"}}},
            execute_fn=lambda value: f"echo:{value}",
            requires_approval=False,
        )
    )

    out = registry.execute("echo", {"value": "hi"}, workspace_root=str(tmp_path))

    assert "echo:hi" in out
    assert "Hook [legacy post]: ok: legacy-echo" in out
    assert [event.kind for event in events] == ["tool.start", "tool.finish"]
    assert events[1].ok is True


def test_write_like_tool_emits_file_changed(tmp_path: Path) -> None:
    events = []
    subscribe_hook_lifecycle(lambda event: events.append(event), kinds=["file.changed"])

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="write_file",
            description="Write file.",
            parameters={"type": "object", "properties": {"file_path": {"type": "string"}}},
            execute_fn=lambda file_path, content="": "write ok",
            requires_approval=False,
        )
    )

    out = registry.execute("write_file", {"file_path": "x.py", "content": "print(1)"}, workspace_root=str(tmp_path))

    assert "write ok" in out
    assert len(events) == 1
    assert events[0].kind == "file.changed"
    assert events[0].metadata["operation"] == "written"
    assert events[0].metadata["path"] == "x.py"


def test_agent_run_emits_start_and_stop(tmp_path: Path) -> None:
    events = []
    subscribe_hook_lifecycle(lambda event: events.append(event), kinds=["agent.start", "agent.stop"])

    registry = ToolRegistry()
    config = AgentConfig(
        llm_backend="fake",
        llm_model="fake-model",
        workspace_root=str(tmp_path),
        max_turns=2,
    )

    rows = list(
        run(
            [{"role": "user", "content": "hello"}],
            config,
            registry=registry,
            backend=DoneBackend(),
        )
    )

    assert any(isinstance(row, DoneEvent) for row in rows)
    assert [event.kind for event in events] == ["agent.start", "agent.stop"]
    assert events[0].metadata["model"] == "fake-model"
    assert events[1].metadata["turns"] == 1
    assert events[1].workspace_root == str(tmp_path)

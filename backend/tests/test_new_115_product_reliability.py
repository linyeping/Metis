from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

import pytest

from backend.runtime.agent_loop import AgentConfig, _tool_schemas_for_config
from backend.runtime.llm_backends import LLMBackend, LLMResponse, ToolCall, Usage
from backend.runtime.mini_agent import MiniAgentConfig, MiniAgentResult, run_mini_agent
from backend.runtime.tool_registry import ToolDefinition, ToolRegistry
from backend.runtime.tool_tiers import TIER_2_TOOLS, TIER_3_TOOLS, tools_for_tier
from backend.tools.coding.workflow_features.subagents.custom_agent_creator import custom_agent_creator
from backend.tools.coding.workflow_features.subagents.delegate_browser import delegate_browser
from backend.web import app as web_app
from backend.web import desk_blueprint


class MiniAgentBackend(LLMBackend):
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
        cancel_event: Any = None,
    ) -> LLMResponse:
        del messages, temperature, max_tokens, timeout, cancel_event
        self.calls += 1
        if self.calls == 1:
            assert tools is not None and len(tools) == 1
            return LLMResponse(
                tool_calls=[ToolCall("mini_call_1", "echo", {"value": "hello"})],
                stop_reason="tool_use",
            )
        return LLMResponse(content="mini agent finished", usage=Usage(1, 1, 2))

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
        if response.content:
            yield response.content
        return response


class CapturingRegistry(ToolRegistry):
    def __init__(self) -> None:
        super().__init__()
        self.last_workspace_root: Optional[str] = None

    def execute(
        self,
        name: str,
        arguments: Dict[str, Any],
        *,
        cancel_event: Any = None,
        workspace_root: Optional[str] = None,
    ) -> str:
        self.last_workspace_root = workspace_root
        return super().execute(
            name,
            arguments,
            cancel_event=cancel_event,
            workspace_root=workspace_root,
        )


def _tool_registry() -> CapturingRegistry:
    registry = CapturingRegistry()
    registry.register(
        ToolDefinition(
            name="echo",
            description="Return the provided value.",
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


def _schema_registry() -> ToolRegistry:
    registry = ToolRegistry()
    for name in ("read_file", "edit_code_ast", "task_dispatch"):
        registry.register(
            ToolDefinition(
                name=name,
                description=f"Test tool {name}",
                parameters={"type": "object", "properties": {}},
                execute_fn=lambda **kwargs: kwargs,
                requires_approval=False,
            )
        )
    return registry


def test_new_115_mini_agent_result_string_forms() -> None:
    assert str(MiniAgentResult(ok=True, output="done")) == "done"
    assert "Sub-agent failed" in str(MiniAgentResult(ok=False, error="boom"))


def test_new_115_mini_agent_executes_real_tool_loop() -> None:
    registry = _tool_registry()
    result = run_mini_agent(
        task="Use echo once.",
        config=MiniAgentConfig(tool_names=["echo"], workspace_root="D:\\workspace"),
        registry=registry,
        backend=MiniAgentBackend(),
    )

    assert result.ok is True
    assert result.output == "mini agent finished"
    assert result.tool_calls_made == 1
    assert result.turns_used == 2
    assert registry.last_workspace_root == "D:\\workspace"


def test_new_115_delegate_explore_source_uses_mini_agent() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "coding"
        / "workflow_features"
        / "subagents"
        / "delegate_explore.py"
    ).read_text(encoding="utf-8")

    assert "run_mini_agent" in source
    assert "建议:" not in source


def test_new_115_remaining_placeholder_delegates_are_honest() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "coding"
        / "workflow_features"
        / "subagents"
        / "delegate_browser.py"
    ).read_text(encoding="utf-8")

    assert "run_browser_task" in source
    assert "not yet available" not in source
    assert "not yet available" in custom_agent_creator("tester", "help")


def test_new_115_tool_tiers_are_consistent() -> None:
    assert len(TIER_3_TOOLS) >= 14
    assert len(TIER_2_TOOLS) >= 28
    assert TIER_3_TOOLS.issubset(TIER_2_TOOLS)
    assert tools_for_tier(1) is None
    assert tools_for_tier(2) == TIER_2_TOOLS
    assert tools_for_tier(3) == TIER_3_TOOLS


def test_new_115_tool_schemas_filter_for_weaker_models() -> None:
    registry = _schema_registry()
    config = AgentConfig(
        llm_backend="fake",
        llm_model="gpt-4o-mini",
        enabled_tools=["read_file", "edit_code_ast", "task_dispatch"],
    )

    schemas = _tool_schemas_for_config(registry, config)
    names = {((schema.get("function") or {}).get("name") or "") for schema in schemas}

    assert "read_file" in names
    assert "task_dispatch" in names
    assert "edit_code_ast" not in names


def test_new_115_capabilities_endpoint_reports_expected_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        web_app,
        "_load_config",
        lambda: AgentConfig(llm_backend="fake", llm_model="fake-default"),
    )

    with web_app.app.test_client() as client:
        response = client.get("/api/model/capabilities?backend=fake&model=gpt-4o-mini")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["tier"] == 3
    assert payload["tierLabel"] == "基础"
    assert payload["family"] == "openai"
    assert payload["supportsVision"] is False
    assert payload["supportsToolCalling"] is True
    assert payload["toolCount"] <= payload["totalToolCount"]
    assert "effectiveContext" in payload
    assert "instructionAdherence" in payload


def test_new_115_vision_start_rejects_unsupported_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(desk_blueprint, "_check_vision_support", lambda: (False, "vision unavailable"))

    with web_app.app.test_client() as client:
        response = client.post("/api/vision/start", json={"goal": "open browser"})

    assert response.status_code == 400
    payload = response.get_json()
    assert payload == {"ok": False, "error": "vision unavailable"}

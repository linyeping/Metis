# -*- coding: utf-8 -*-
"""FABLEADV-23: 工具按需加载（deferred tools）。

deferred 工具默认不进 schema，只在目录里列名；search_tools 检索并激活后才入 schema。
全程 METIS_DEFERRED_TOOLS 门控，默认关 = 行为与改动前完全一致。
"""
from __future__ import annotations


from backend.runtime.tool_registry import ToolDefinition, ToolRegistry


def _mk(name: str, *, source: str = "builtin", deferred: bool = False, desc: str = "") -> ToolDefinition:
    return ToolDefinition(
        name=name,
        description=desc or f"desc of {name}",
        parameters={"type": "object", "properties": {}, "required": []},
        execute_fn=lambda **_: "ok",
        source=source,
        deferred=deferred,
    )


def _registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(_mk("read_file", source="builtin"))
    reg.register(_mk("mcp_slack_send", source="mcp:slack", desc="Send a message to a Slack channel"))
    reg.register(_mk("mcp_db_query", source="mcp:db", desc="Run a SQL query against the database"))
    return reg


def _names(schemas):
    return {s["function"]["name"] for s in schemas}


def test_flag_off_includes_all_no_deferral(monkeypatch):
    monkeypatch.delenv("METIS_DEFERRED_TOOLS", raising=False)
    reg = _registry()
    names = _names(reg.get_all_schemas())
    # 关闭门控：mcp 工具照旧入 schema（与改动前一致）
    assert {"read_file", "mcp_slack_send", "mcp_db_query"} <= names
    assert reg.deferred_catalog() == []


def test_flag_on_defers_mcp_tools(monkeypatch):
    monkeypatch.setenv("METIS_DEFERRED_TOOLS", "1")
    reg = _registry()
    names = _names(reg.get_all_schemas())
    # 开启门控：mcp 工具被延迟，不进 schema
    assert "read_file" in names
    assert "mcp_slack_send" not in names
    assert "mcp_db_query" not in names
    catalog = {n for n, _ in reg.deferred_catalog()}
    assert catalog == {"mcp_slack_send", "mcp_db_query"}


def test_activated_tool_enters_schema(monkeypatch):
    monkeypatch.setenv("METIS_DEFERRED_TOOLS", "1")
    reg = _registry()
    names = _names(reg.get_all_schemas(activated={"mcp_slack_send"}))
    assert "mcp_slack_send" in names  # 激活后入 schema
    assert "mcp_db_query" not in names  # 未激活仍排除


def test_search_deferred_ranks_and_returns_names(monkeypatch):
    monkeypatch.setenv("METIS_DEFERRED_TOOLS", "1")
    reg = _registry()
    hit_names, text = reg.search_deferred("slack message")
    assert hit_names[0] == "mcp_slack_send"
    assert "mcp_slack_send" in text
    # 数据库查询走另一条
    db_names, _ = reg.search_deferred("sql database")
    assert "mcp_db_query" in db_names


def test_search_deferred_excludes_already_activated(monkeypatch):
    monkeypatch.setenv("METIS_DEFERRED_TOOLS", "1")
    reg = _registry()
    names, _ = reg.search_deferred("query", activated={"mcp_db_query"})
    assert "mcp_db_query" not in names


def test_search_deferred_miss(monkeypatch):
    monkeypatch.setenv("METIS_DEFERRED_TOOLS", "1")
    reg = _registry()
    names, text = reg.search_deferred("xyzzy-nonexistent")
    assert names == []
    assert "未找到" in text


# --- 端到端 loop 测试：search_tools 激活后，deferred 工具变得可调用 ---

from typing import Any, Dict, Generator, List, Optional

from backend.runtime.agent_loop import AgentConfig, ContentEvent, run_stream
from backend.runtime.llm_backends import LLMBackend, LLMResponse, ToolCall, Usage


class _DeferredFlowBackend(LLMBackend):
    def __init__(self) -> None:
        self.calls = 0
        self.tools_per_call: List[set] = []

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
        self.tools_per_call.append({t["function"]["name"] for t in (tools or [])})
        if self.calls == 1:
            return LLMResponse(tool_calls=[ToolCall("c1", "search_tools", {"query": "slack message"})])
        if self.calls == 2:
            return LLMResponse(tool_calls=[ToolCall("c2", "mcp_slack_send", {})])
        return LLMResponse(content="完成", usage=Usage(1, 1, 2))

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


def _loop_registry() -> ToolRegistry:
    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            name="search_tools",
            description="search and load tools",
            parameters={"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
            execute_fn=lambda query="": reg.search_deferred(query)[1],
            source="builtin",
        )
    )
    reg.register(
        ToolDefinition(
            name="mcp_slack_send",
            description="Send a message to a Slack channel",
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=lambda **_: "sent",
            source="mcp:slack",
        )
    )
    return reg


def test_loop_search_tools_activates_deferred(monkeypatch):
    monkeypatch.setenv("METIS_DEFERRED_TOOLS", "1")
    backend = _DeferredFlowBackend()
    config = AgentConfig(llm_backend="fake", llm_model="fake-model", execution_mode="execute", max_turns=6)
    events = list(run_stream([{"role": "user", "content": "post to slack"}], config, registry=_loop_registry(), backend=backend))

    # 第 1 轮：mcp 工具未激活，不在 tools；search_tools 已注入
    assert "mcp_slack_send" not in backend.tools_per_call[0]
    assert "search_tools" in backend.tools_per_call[0]
    # 第 2 轮：search_tools 命中后，mcp 工具被激活、进入 tools，可被调用
    assert "mcp_slack_send" in backend.tools_per_call[1]
    # 任务正常收尾
    assert any(isinstance(e, ContentEvent) and "完成" in e.text for e in events)


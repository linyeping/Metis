from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from backend.bridges.event_serializer import agent_event_payload
from backend.core.engine import prompt_runtime
from backend.core.engine.prompt_runtime import compile_prompt_runtime
from backend.runtime import agent_loop
from backend.runtime.agent_loop import AgentConfig, CompactEvent, DoneEvent
from backend.runtime.context_budget import context_ledger
from backend.runtime.context_eviction import evict_tool_results
from backend.runtime.llm_backends import LLMBackend, LLMResponse, Usage
from backend.runtime.llm_backends._common import usage_from_openai
from backend.runtime.tool_registry import ToolRegistry


class CaptureBackend(LLMBackend):
    def __init__(self) -> None:
        self.messages: List[Dict[str, Any]] = []

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
        self.messages = [dict(message) for message in messages]
        return LLMResponse(content="done", usage=Usage(prompt_tokens=11, completion_tokens=2, total_tokens=13))

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
        yield response.content
        return response


def test_fableadv_1_usage_cache_fields_parse_and_serialize() -> None:
    usage = usage_from_openai(
        {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 5,
                "total_tokens": 105,
                "prompt_cache_hit_tokens": 64,
                "prompt_cache_miss_tokens": 36,
            }
        }
    )

    payload = agent_event_payload(
        DoneEvent(
            total_turns=1,
            total_tool_calls=0,
            prompt_tokens=usage.prompt_tokens,
            completion_tokens=usage.completion_tokens,
            total_tokens=usage.total_tokens,
            prompt_cache_hit_tokens=usage.prompt_cache_hit_tokens,
            prompt_cache_miss_tokens=usage.prompt_cache_miss_tokens,
        )
    )

    assert usage.prompt_cache_hit_tokens == 64
    assert usage.prompt_cache_miss_tokens == 36
    assert payload["usage"]["prompt_cache_hit_tokens"] == 64
    assert payload["usage"]["prompt_cache_miss_tokens"] == 36


def test_fableadv_1_prompt_layers_are_sorted_by_stability(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setattr(
        "backend.core.engine.tool_strategy.tool_strategy_block",
        lambda: "\n\n---\n[Tool strategy]\n",
    )

    snapshot = compile_prompt_runtime(
        "Base prompt.",
        user_memory_text="Remember workspace convention.",
        workspace_root=str(tmp_path),
        include_agent_state_hint=False,
        include_open_files_hint=False,
        include_terminal_hint=False,
        include_mode_router_hint=False,
        include_workflow_hint=False,
        include_repo_map_hint=False,
        include_desk_skill=False,
        include_tool_strategy_hint=True,
        include_workspace_memory_hint=False,
    )
    names = snapshot.layer_names()

    assert names.index("tool_strategy_hint") < names.index("workspace_hint")
    assert names.index("workspace_hint") < names.index("user_memory")
    assert {layer.stability for layer in snapshot.layers[:2]} == {"static"}


def test_fableadv_1_repo_map_hint_defaults_on(monkeypatch: Any) -> None:
    monkeypatch.delenv("MIRO_CONTEXT_REPO_MAP_HINT", raising=False)

    assert prompt_runtime._repo_map_hint_enabled() is True


def test_fableadv_1_context_ledger_tracks_schema_history_and_cache() -> None:
    ledger = context_ledger(
        [
            {"role": "system", "content": "base"},
            {"role": "user", "content": "hello"},
        ],
        [{"type": "function", "function": {"name": "read_file", "parameters": {}}}],
        usage=Usage(prompt_tokens=20, completion_tokens=3, total_tokens=23, prompt_cache_hit_tokens=12, prompt_cache_miss_tokens=8),
        model="gpt-4o",
    )

    assert ledger["system_tokens"] > 0
    assert ledger["schema_tokens"] > 0
    assert ledger["history_tokens"] > 0
    assert ledger["cache_hit_tokens"] == 12
    assert ledger["cache_miss_tokens"] == 8


def test_fableadv_1_tool_result_eviction_protects_errors_and_recent() -> None:
    old_large = "File: old.py\n" + ("x" * 1200)
    old_error = "Error: failed\n" + ("y" * 1200)
    recent_large = "File: recent.py\n" + ("z" * 1200)
    messages = [
        {"role": "system", "content": "base"},
        {"role": "tool", "name": "read_file", "content": old_large},
        {"role": "tool", "name": "read_file", "content": old_error},
        {"role": "tool", "name": "read_file", "content": recent_large},
    ]

    evicted, count = evict_tool_results(messages, force=True, min_chars=100, protect_recent=1)

    assert count == 1
    assert evicted[1]["content"].startswith("[Archived tool result]")
    assert evicted[2]["content"] == old_error
    assert evicted[3]["content"] == recent_large


def test_fableadv_1_runtime_auto_compact_emits_event(monkeypatch: Any) -> None:
    monkeypatch.setenv("METIS_AUTO_COMPACT_RATIO", "0.001")
    monkeypatch.setattr(agent_loop, "_build_environment_context", lambda: "")
    backend = CaptureBackend()
    messages = [{"role": "user", "content": f"turn {index} " + ("x" * 1000)} for index in range(12)]

    events = list(
        agent_loop.run(
            messages,
            AgentConfig(system_prompt="Base prompt.", llm_model="gpt-4o", max_turns=1),
            registry=ToolRegistry(),
            backend=backend,
        )
    )

    assert any(isinstance(event, CompactEvent) for event in events)
    assert any(isinstance(event, DoneEvent) for event in events)
    assert any("[Auto compacted context summary]" in str(message.get("content") or "") for message in backend.messages)

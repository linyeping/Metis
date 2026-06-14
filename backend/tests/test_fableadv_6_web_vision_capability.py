from __future__ import annotations

import threading
from typing import Any, Dict, Generator, List, Optional

from backend.bridges.provider_registry import (
    get_provider_profile,
    parallel_tool_calls_enabled,
)
from backend.core.engine.prompt_runtime import compile_prompt_runtime
from backend.runtime import agent_loop
from backend.runtime.agent_loop import AgentConfig, DoneEvent, ToolResultEvent
from backend.runtime.llm_backends import LLMBackend, LLMResponse, ToolCall
from backend.runtime.llm_backends._common import parse_openai_tool_calls
from backend.runtime.tool_profiles import LEAN_PROFILE
from backend.runtime.tool_registry import ToolDefinition, ToolRegistry
from backend.tools.browser_automation.tools import _normalize_browser_content
from backend.tools.coding.network_external.web import web_content
from backend.tools.coding.network_external.web.web_fetch import web_fetch


def test_extract_html_markdown_fallback_keeps_title_links_and_text(monkeypatch: Any) -> None:
    monkeypatch.setattr(web_content, "_extract_with_trafilatura", lambda _text, base_url="": "")
    html = """
    <!doctype html>
    <html>
      <head><title>Python</title><script>bad()</script></head>
      <body><main><h1>Welcome</h1><p>Read the <a href="/news">News</a>.</p></main></body>
    </html>
    """

    result = web_content.extract_html_markdown(html, base_url="https://python.org/")

    assert "# Python" in result
    assert "Welcome" in result
    assert "[News](https://python.org/news)" in result
    assert "<main>" not in result
    assert "bad()" not in result


def test_web_fetch_returns_clean_markdown_by_default_and_raw_when_requested(monkeypatch: Any) -> None:
    html = """<!doctype html>
    <html><head><title>Example</title></head>
    <body><main><h1>正文 Markdown</h1><p>Hello <a href="/next">Next</a></p></main></body></html>
    """

    class FakeResponse:
        url = "https://example.com/"
        headers = {"content-type": "text/html; charset=utf-8"}
        content = html.encode("utf-8")

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr("requests.get", lambda *args, **kwargs: FakeResponse())

    cleaned = web_fetch("https://example.com", max_chars=2000)
    raw = web_fetch("https://example.com", raw=True, max_chars=2000)

    assert "正文 Markdown" in cleaned
    assert "<!doctype html>" not in cleaned.lower()
    assert "正文 Markdown" in raw
    assert "原始 HTML" in raw
    assert "<!doctype html>" in raw.lower()


def test_browser_content_normalizer_extracts_html_to_markdown(monkeypatch: Any) -> None:
    monkeypatch.setattr(web_content, "_extract_with_trafilatura", lambda _text, base_url="": "")
    result = _normalize_browser_content(
        "<html><head><title>SPA</title></head><body><main><h1>Loaded App</h1></main></body></html>",
        "https://example.com/app",
    )

    assert "SPA" in result
    assert "Loaded App" in result
    assert "<html>" not in result


def test_provider_parallel_tool_call_capability_and_env_override(monkeypatch: Any) -> None:
    monkeypatch.delenv("METIS_PARALLEL_TOOLCALLS", raising=False)

    assert get_provider_profile("openai").parallel_tool_calls is True
    assert get_provider_profile("anthropic").parallel_tool_calls is True
    assert get_provider_profile("gemini").parallel_tool_calls is True
    assert get_provider_profile("deepseek").parallel_tool_calls is True
    assert parallel_tool_calls_enabled("openai-compatible", model="gpt-5.5") is True
    assert parallel_tool_calls_enabled("openai-compatible", model="deepseek-v4-pro") is False

    monkeypatch.setenv("METIS_PARALLEL_TOOLCALLS", "1")
    assert parallel_tool_calls_enabled("deepseek") is True
    monkeypatch.setenv("METIS_PARALLEL_TOOLCALLS", "0")
    assert parallel_tool_calls_enabled("openai") is False


def test_openai_tool_call_parser_obeys_parallel_capability() -> None:
    raw_calls = [
        {"id": "1", "function": {"name": "demo", "arguments": "{\"count\": \"1\"}"}},
        {"id": "2", "function": {"name": "demo", "arguments": "{\"count\": \"2\"}"}},
    ]

    assert len(parse_openai_tool_calls(raw_calls, parallel=True)) == 2
    assert len(parse_openai_tool_calls(raw_calls, parallel=False)) == 1


def test_lean_profile_restores_browser_path_and_prompt_guides_web_strategy() -> None:
    prompt = compile_prompt_runtime("Base", include_repo_map_hint=False).final_system_prompt

    assert "web_fetch" in LEAN_PROFILE
    assert "browse_web" in LEAN_PROFILE
    assert "Use web_fetch first" in prompt
    assert "Use browse_web only" in prompt


class ParallelReadBackend(LLMBackend):
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
        self.calls += 1
        if self.calls == 1:
            return LLMResponse(
                tool_calls=[
                    ToolCall("call_read", "read_file", {"file_path": "a.txt"}),
                    ToolCall("call_grep", "grep_search", {"pattern": "needle"}),
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


def test_agent_loop_executes_parallel_readonly_calls_in_one_turn(monkeypatch: Any, tmp_path) -> None:
    monkeypatch.setattr(agent_loop, "_build_environment_context", lambda: "")
    registry = ToolRegistry()
    events: list[str] = []
    lock = threading.Lock()
    both_started = threading.Event()

    def mark_start(name: str) -> None:
        with lock:
            events.append(f"{name}:start")
            if len([event for event in events if event.endswith(":start")]) == 2:
                both_started.set()

    def read_file(file_path: str) -> str:
        mark_start("read_file")
        if not both_started.wait(2):
            events.append("read_file:timeout")
        with lock:
            events.append("read_file:end")
        return f"read:{file_path}"

    def grep_search(pattern: str) -> str:
        mark_start("grep_search")
        if not both_started.wait(2):
            events.append("grep_search:timeout")
        with lock:
            events.append("grep_search:end")
        return f"grep:{pattern}"

    registry.register(
        ToolDefinition(
            name="read_file",
            description="read",
            parameters={"type": "object", "properties": {}},
            execute_fn=read_file,
            requires_approval=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="grep_search",
            description="grep",
            parameters={"type": "object", "properties": {}},
            execute_fn=grep_search,
            requires_approval=False,
        )
    )

    run_events = list(
        agent_loop.run(
            [{"role": "user", "content": "read two things"}],
            AgentConfig(
                llm_backend="fake",
                llm_model="fake",
                workspace_root=str(tmp_path),
                enabled_tools=["read_file", "grep_search"],
                max_turns=3,
            ),
            registry=registry,
            backend=ParallelReadBackend(),
        )
    )

    assert "read_file:timeout" not in events
    assert "grep_search:timeout" not in events
    assert max(events.index("read_file:start"), events.index("grep_search:start")) < min(
        events.index("read_file:end"),
        events.index("grep_search:end"),
    )
    assert [event.tool_name for event in run_events if isinstance(event, ToolResultEvent)] == [
        "read_file",
        "grep_search",
    ]
    assert any(isinstance(event, DoneEvent) and event.total_tool_calls == 2 for event in run_events)

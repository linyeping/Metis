from __future__ import annotations

import threading
from typing import Any, Dict, Generator, List, Optional

from backend.runtime import agent_loop
from backend.runtime.agent_loop import AgentConfig, ContentEvent, RuntimeStatusEvent
from backend.runtime.llm_backends import LLMBackend, LLMResponse
from backend.runtime.model_router import ROUTE_MARKER, build_task_route, runtime_policy_for_task
from backend.runtime.tool_registry import ToolRegistry, register_desktop_tools


def test_router_keeps_explicit_deepseek_model_for_code_tasks() -> None:
    route = build_task_route(
        [{"role": "user", "content": "修复 desktop/src/App.tsx 里的状态问题并跑 typecheck"}],
        llm_backend="deepseek",
        llm_base_url="https://api.deepseek.com",
        llm_model="deepseek-v4-flash",
    )

    assert route.task_type == "code"
    assert route.model_role == "code"
    assert route.execution_boundary == "repo_plus_runtime"
    assert route.runtime_policy["recommended_tool"] == "metis_runtime_job"
    assert route.selected_model == "deepseek-v4-flash"
    assert route.preferred_tools[:3] == ["metis_runtime_job", "metis_runtime_job_status", "generate_repo_map"]


def test_router_does_not_downgrade_explicit_pro_chat_model() -> None:
    route = build_task_route(
        [{"role": "user", "content": "你是什么模型？"}],
        llm_backend="deepseek",
        llm_base_url="https://api.deepseek.com",
        llm_model="deepseek-v4-pro",
    )

    assert route.task_type == "chat"
    assert route.model_role == "fast"
    assert route.selected_model == "deepseek-v4-pro"
    assert route.fallback_models[0] == "deepseek-v4-pro"


def test_router_prioritizes_preview_browser_for_local_page_tasks() -> None:
    route = build_task_route(
        [{"role": "user", "content": "检查 localhost 页面为什么白屏，抓 console error"}],
        llm_backend="openai",
        llm_base_url="https://api.openai.com/v1",
        llm_model="gpt-4o-mini",
    )

    assert route.task_type == "browser"
    assert route.preferred_tools[:4] == [
        "preview_browser_status",
        "preview_browser_navigate",
        "preview_browser_observe",
        "preview_browser_action",
    ]


def test_router_prioritizes_web_research_when_deep_research_is_enabled() -> None:
    route = build_task_route(
        [{"role": "user", "content": "查一下 Claude Code 最近更新了什么，要多来源核实"}],
        llm_backend="openai",
        llm_base_url="https://api.openai.com/v1",
        llm_model="gpt-4o-mini",
        deep_research=True,
    )

    assert route.task_type == "external_lookup"
    assert route.preferred_tools[0] == "web_research"
    assert route.preferred_tools[1] == "web_search"
    assert "Deep research is explicitly enabled" in route.tool_guidance


def test_router_default_web_search_guidance_allows_audited_single_escalation() -> None:
    route = build_task_route(
        [{"role": "user", "content": "今天有哪些 Codex 更新？"}],
        llm_backend="openai",
        llm_base_url="https://api.openai.com/v1",
        llm_model="gpt-4o-mini",
    )

    assert route.task_type == "external_lookup"
    assert route.preferred_tools[:3] == ["web_search", "web_fetch", "web_research"]
    assert "once this turn" in route.tool_guidance
    assert "reason argument" in route.tool_guidance


def test_deep_research_toggle_rescues_keyword_free_comparison_request() -> None:
    # Real bug report: this exact sentence has none of _EXTERNAL_LOOKUP_KEYWORDS
    # (no "最新/搜索/查一下"), so the classifier alone calls it "chat" and the
    # deep-research toggle silently did nothing — the model fell back to
    # web_fetch on a raw google.com/search URL and got a 403.
    text = "Claude Sonnet 4.6 和 GPT-5.5 在编码能力上的对比，需要多个独立来源互相核实，并标注每条结论的引用网址。"

    route_off = build_task_route(
        [{"role": "user", "content": text}],
        llm_backend="openai",
        llm_base_url="https://api.openai.com/v1",
        llm_model="gpt-4o-mini",
        deep_research=False,
    )
    assert route_off.task_type == "external_lookup"  # keyword list now also catches "核实"/"引用网址"

    route_on = build_task_route(
        [{"role": "user", "content": text}],
        llm_backend="openai",
        llm_base_url="https://api.openai.com/v1",
        llm_model="gpt-4o-mini",
        deep_research=True,
    )
    assert route_on.task_type == "external_lookup"
    assert route_on.preferred_tools[0] == "web_research"


def test_deep_research_toggle_overrides_chat_classification_with_no_keywords_at_all() -> None:
    text = "帮我看看这两个东西哪个更好用，给我点依据。"

    route_off = build_task_route(
        [{"role": "user", "content": text}],
        llm_backend="openai",
        llm_base_url="https://api.openai.com/v1",
        llm_model="gpt-4o-mini",
        deep_research=False,
    )
    assert route_off.task_type == "chat"
    assert route_off.preferred_tools == []

    route_on = build_task_route(
        [{"role": "user", "content": text}],
        llm_backend="openai",
        llm_base_url="https://api.openai.com/v1",
        llm_model="gpt-4o-mini",
        deep_research=True,
    )
    assert route_on.task_type == "external_lookup"
    assert "web_research" in route_on.preferred_tools


def test_deep_research_toggle_does_not_hijack_a_code_task() -> None:
    route = build_task_route(
        [{"role": "user", "content": "帮我修一下 backend 里这个文件的代码 bug"}],
        llm_backend="openai",
        llm_base_url="https://api.openai.com/v1",
        llm_model="gpt-4o-mini",
        deep_research=True,
    )

    assert route.task_type == "code"
    assert "web_research" not in route.preferred_tools


def test_router_uses_background_artifact_workflow_for_report_code_tasks() -> None:
    route = build_task_route(
        [
            {
                "role": "user",
                "content": (
                    "你可以看到我的电脑屏幕吗？WPS 和 PyCharm 我都打开了。"
                    "请根据实验报告要求写代码、运行结果、生成图表并完成 Word 作业。"
                ),
            }
        ],
        llm_backend="deepseek",
        llm_base_url="https://api.deepseek.com",
        llm_model="deepseek-v4-flash",
    )

    assert route.task_type == "artifact_workflow"
    assert route.model_role == "code"
    assert route.execution_boundary == "metis_runtime"
    assert route.runtime_policy["sandbox_required"] is True
    assert route.runtime_policy["desktop_control_allowed"] is False
    assert route.preferred_tools[:4] == ["metis_runtime_job", "metis_runtime_job_status", "load_skill", "read_file"]
    assert route.preferred_tools.index("office_report_from_code_run") < route.preferred_tools.index("docx_create")
    assert "docx_create" in route.preferred_tools
    assert "docx_render_pages" in route.preferred_tools
    assert "pdf_create" in route.preferred_tools
    assert "pdf_render_pages" in route.preferred_tools
    assert "background artifact workflow" in route.tool_guidance
    assert "Do not use Computer Use" in route.tool_guidance
    assert "metis_runtime_job" in route.tool_guidance


def test_router_keeps_desktop_for_explicit_ui_control() -> None:
    route = build_task_route(
        [{"role": "user", "content": "帮我点击 WPS 里的提交按钮，然后滚动检查页面底部。"}],
        llm_backend="openai",
        llm_base_url="https://api.openai.com/v1",
        llm_model="gpt-4o",
    )

    assert route.task_type == "desktop"
    assert route.execution_boundary == "desktop"
    assert route.runtime_policy["desktop_control_allowed"] is True
    assert route.preferred_tools[:3] == [
        "desktop_win2_status",
        "desktop_win2_observe",
        "desktop_win2_action",
    ]


def test_runtime_policy_matches_claude_style_boundaries() -> None:
    artifact_policy = runtime_policy_for_task("artifact_workflow", "帮我完成实验报告，跑代码，生成图表")
    code_policy = runtime_policy_for_task("code", "跑 pytest 并修复失败")
    strict_policy = runtime_policy_for_task("code", "严格沙箱运行 pytest，不要回退到本机")
    desktop_policy = runtime_policy_for_task("desktop", "点击 WPS 提交按钮")

    assert artifact_policy["execution_boundary"] == "metis_runtime"
    assert artifact_policy["recommended_tool"] == "metis_runtime_job"
    assert artifact_policy["fallback_order"] == ["metis_wsl", "wsl", "docker", "local-copy"]
    assert artifact_policy["sandbox_mode"] == "copy"
    assert artifact_policy["desktop_control_allowed"] is False

    assert code_policy["execution_boundary"] == "repo_plus_runtime"
    assert "test" in code_policy["sandbox_for"]
    assert strict_policy["strict_sandbox"] is True
    assert strict_policy["fallback_mode"] == "strict"
    assert strict_policy["fallback_order"] == ["metis_wsl", "wsl", "docker"]
    assert "local-copy" not in strict_policy["fallback_order"]
    assert desktop_policy["execution_boundary"] == "desktop"


def test_route_hint_is_appended_after_user_message_not_system_prefix(monkeypatch: Any) -> None:
    monkeypatch.setattr(agent_loop, "_build_environment_context", lambda: "")
    config = AgentConfig(
        llm_backend="deepseek",
        llm_model="deepseek-v4-pro",
        system_prompt="Base prompt.",
        routing_task_type="code",
        routing_model_role="code",
        routing_reason="test",
        routing_tool_guidance="Use repo tools first.",
        routing_preferred_tools=["grep_search", "read_file"],
        model_fallbacks=["deepseek-v4-pro", "deepseek-v4-flash"],
    )

    working = agent_loop._prepare_working_messages([{"role": "user", "content": "修复代码"}], config)

    assert working[0]["role"] == "system"
    assert ROUTE_MARKER not in str(working[0]["content"])
    assert working[-1]["role"] == "user"
    assert ROUTE_MARKER in str(working[-1]["content"])


def test_lean_tool_profile_exposes_preview_and_win2_tools() -> None:
    registry = ToolRegistry()
    register_desktop_tools(registry)
    config = AgentConfig(
        llm_backend="deepseek",
        llm_model="deepseek-v4-flash",
        routing_preferred_tools=["preview_browser_observe", "desktop_win2_status"],
    )

    schemas = agent_loop._tool_schemas_for_config(registry, config)
    names = [schema["function"]["name"] for schema in schemas]

    assert "preview_browser_observe" in names
    assert "desktop_win2_status" in names
    assert names.index("preview_browser_observe") < names.index("desktop_win2_status")


def test_artifact_workflow_blocks_desktop_control_tools() -> None:
    registry = ToolRegistry()
    from backend.runtime.tool_registry import register_builtin_tools

    register_builtin_tools(registry)
    register_desktop_tools(registry)
    config = AgentConfig(
        llm_backend="deepseek",
        llm_model="deepseek-v4-pro",
        routing_task_type="artifact_workflow",
        routing_preferred_tools=[
            "desktop_win2_status",
            "desktop_win2_observe",
            "desktop_win2_action",
            "desktop_win2_task",
        ],
    )

    schemas = agent_loop._tool_schemas_for_config(registry, config)
    names = {schema["function"]["name"] for schema in schemas}

    assert "desktop_win2_status" in names
    assert "desktop_win2_observe" in names
    assert "office_report_from_code_run" in names
    assert "docx_create" in names
    assert "pdf_render_pages" in names
    assert "desktop_win2_action" not in names
    assert "desktop_win2_task" not in names
    assert "desktop_action" not in names


def test_runtime_job_tool_is_exposed_and_prioritized_for_artifact_workflow() -> None:
    registry = ToolRegistry()
    from backend.runtime.tool_registry import register_builtin_tools

    register_builtin_tools(registry)
    config = AgentConfig(
        llm_backend="deepseek",
        llm_model="deepseek-v4-pro",
        routing_task_type="artifact_workflow",
        routing_preferred_tools=["metis_runtime_job", "metis_runtime_job_status"],
    )

    schemas = agent_loop._tool_schemas_for_config(registry, config)
    names = [schema["function"]["name"] for schema in schemas]

    assert "metis_runtime_job" in names
    assert "metis_runtime_job_status" in names
    # The high-level composite is exposed; the low-level runtime primitives it
    # composes are hidden as internal tools (no step-multiplying duplicates).
    assert "metis_runtime_create" not in names
    assert "metis_runtime_run" not in names


class _FailOnceBackend(LLMBackend):
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
        raise TimeoutError("primary model timed out")

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
        raise TimeoutError("primary model timed out")
        yield ""


class _GoodBackend(LLMBackend):
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
        return LLMResponse(content="fallback ok")

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
        yield "fallback ok"
        return LLMResponse(content="fallback ok")


def test_model_failure_switches_to_fallback_without_raw_error(monkeypatch: Any) -> None:
    monkeypatch.setattr(agent_loop, "_build_environment_context", lambda: "")
    monkeypatch.setattr(agent_loop, "_create_backend", lambda config: _GoodBackend())
    config = AgentConfig(
        llm_backend="deepseek",
        llm_model="bad-model",
        model_fallbacks=["bad-model", "good-model"],
        max_turns=2,
    )

    events = list(
        agent_loop.run(
            [{"role": "user", "content": "你好"}],
            config,
            registry=ToolRegistry(),
            backend=_FailOnceBackend(),
        )
    )

    assert any(isinstance(event, RuntimeStatusEvent) and event.phase == "model_fallback" for event in events)
    assert any(isinstance(event, ContentEvent) and event.text == "fallback ok" for event in events)
    assert not any(getattr(event, "type", "") == "error" for event in events)

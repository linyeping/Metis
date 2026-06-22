from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from backend.evals.metrics import MetricsCollector
from backend.evals.runner import _build_eval_config, run_checker
from backend.evals.tasks.task_spec import EvalTask
from backend.runtime.agent_loop import (
    AgentConfig,
    ErrorEvent,
    _turn_budget_warn_threshold,
    run_stream,
)
from backend.runtime.context_budget import IMAGE_BLOCK_TOKEN_ESTIMATE, estimate_tokens
from backend.runtime.context_eviction import evict_tool_results
from backend.runtime.llm_backends import LLMBackend, LLMResponse, ToolCall
from backend.runtime.tool_registry import ToolDefinition, ToolRegistry
from backend.tools.coding.workflow_features.agent_state.state_paths import AGENT_TODO_FILE


class CaptureMessagesBackend(LLMBackend):
    def __init__(self, *, use_tool_first: bool = False) -> None:
        self.calls = 0
        self.messages_by_call: List[List[Dict[str, Any]]] = []
        self.use_tool_first = use_tool_first

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
        self.messages_by_call.append([dict(message) for message in messages])
        if self.use_tool_first and self.calls == 1:
            return LLMResponse(tool_calls=[ToolCall("call_echo", "echo", {"value": "ok"})])
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


def test_eval_checker_gets_workspace_pythonpath(tmp_path: Path) -> None:
    package = tmp_path / "samplepkg"
    package.mkdir()
    (package / "__init__.py").write_text("VALUE = 42\n", encoding="utf-8")
    checks = tmp_path / "checks"
    checks.mkdir()
    (checks / "check_import.py").write_text(
        "import samplepkg\nassert samplepkg.VALUE == 42\n",
        encoding="utf-8",
    )

    result = run_checker(tmp_path, EvalTask("import-check", "", ".", "checks/check_import.py"))

    assert result["ok"] is True


def test_context_budget_counts_cjk_and_images_without_base64_explosion() -> None:
    assert estimate_tokens("你好世界") == 4
    assert estimate_tokens("hello world") < estimate_tokens("你好世界hello world")

    image_block = {"type": "image_url", "image_url": {"url": "data:image/png;base64," + ("x" * 100_000)}}

    assert estimate_tokens(image_block) == IMAGE_BLOCK_TOKEN_ESTIMATE


def test_evict_tool_results_archives_old_image_blocks_but_keeps_recent() -> None:
    old_image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,old"}}
    recent_image = {"type": "image_url", "image_url": {"url": "data:image/png;base64,recent"}}
    messages = [
        {"role": "user", "content": [{"type": "text", "text": "old"}, old_image]},
        {"role": "assistant", "content": "between"},
        {"role": "user", "content": [{"type": "text", "text": "recent"}, recent_image]},
    ]

    evicted, count = evict_tool_results(messages, force=True, protect_recent=1)

    assert count == 1
    assert "历史截图已移除" in evicted[0]["content"][1]["text"]
    assert evicted[2]["content"][1] == recent_image


def test_run_stream_clears_stale_todos_before_first_llm_call(tmp_path: Path) -> None:
    (tmp_path / AGENT_TODO_FILE).write_text(
        '{"todos":[{"id":"old","content":"rewrite hello.py again","status":"pending"}]}',
        encoding="utf-8",
    )
    backend = CaptureMessagesBackend()

    list(
        run_stream(
            [{"role": "user", "content": "take a screenshot"}],
            AgentConfig(system_prompt="Base", workspace_root=str(tmp_path), llm_model="fake", max_turns=1),
            registry=ToolRegistry(),
            backend=backend,
        )
    )

    assert not (tmp_path / AGENT_TODO_FILE).exists()
    assert "rewrite hello.py again" not in str(backend.messages_by_call[0])


def test_run_stream_injects_turn_budget_hint_when_near_limit(tmp_path: Path) -> None:
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="echo",
            description="echo",
            parameters={"type": "object", "properties": {"value": {"type": "string"}}, "required": ["value"]},
            execute_fn=lambda value: f"echo:{value}",
            requires_approval=False,
        )
    )
    backend = CaptureMessagesBackend(use_tool_first=True)

    list(
        run_stream(
            [{"role": "user", "content": "do work"}],
            AgentConfig(system_prompt="Base", workspace_root=str(tmp_path), llm_model="fake", max_turns=4),
            registry=registry,
            backend=backend,
        )
    )

    assert len(backend.messages_by_call) >= 2
    assert "[轮次预算] remaining=3" in str(backend.messages_by_call[1])


class TodoChurnBackend(LLMBackend):
    """前 N 轮反复发 todo_write（参数每次不同），之后给文本，模拟规划空转。"""

    def __init__(self, churn_turns: int = 3) -> None:
        self.calls = 0
        self.messages_by_call: List[List[Dict[str, Any]]] = []
        self._churn_turns = churn_turns

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
        self.messages_by_call.append([dict(message) for message in messages])
        if self.calls <= self._churn_turns:
            return LLMResponse(
                tool_calls=[ToolCall(f"call_{self.calls}", "todo_write", {"todos": [{"step": self.calls}]})]
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


def test_run_stream_injects_todo_churn_hint(tmp_path: Path) -> None:
    # 关键回归点：循环里每次 todo_write 成功都会 tracker.reset()，churn 计数必须扛过
    # reset()，否则反复写待办的空转永远检测不到（DeepSeek eval 里实测连刷 12~20 次）。
    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="todo_write",
            description="update the todo list",
            parameters={"type": "object", "properties": {"todos": {"type": "array"}}, "required": ["todos"]},
            execute_fn=lambda todos: "todos updated",
            requires_approval=False,
        )
    )
    backend = TodoChurnBackend(churn_turns=3)

    list(
        run_stream(
            [{"role": "user", "content": "plan and do work"}],
            AgentConfig(system_prompt="Base", workspace_root=str(tmp_path), llm_model="fake", max_turns=10),
            registry=registry,
            backend=backend,
        )
    )

    # 第 3 次 todo_write 处理完后，churn 提示应注入，出现在第 4 次请求的消息里。
    assert backend.calls >= 4
    fourth_request = str(backend.messages_by_call[3])
    assert "[System hint]" in fourth_request
    assert "连续" in fourth_request and "todo_write" in fourth_request


def test_turn_budget_warn_threshold_scales_with_budget() -> None:
    # 小预算（capability=12）要更早预警，原来固定 3 太晚。
    assert _turn_budget_warn_threshold(4) == 3
    assert _turn_budget_warn_threshold(12) == 4
    assert _turn_budget_warn_threshold(20) == 6
    # 大预算封顶 8，避免从太早就反复刷提示。
    assert _turn_budget_warn_threshold(35) == 8
    assert _turn_budget_warn_threshold(64) == 8
    assert _turn_budget_warn_threshold(0) == 3


def test_metrics_collector_marks_max_turn_death() -> None:
    collector = MetricsCollector()

    collector.observe(ErrorEvent(code="RUNTIME_MAX_TURNS"))
    metrics = collector.finish(success=False)

    assert metrics.died_at_max_turns is True


def test_eval_prod_system_prompt_mode_compiles_runtime_prompt(tmp_path: Path) -> None:
    config = _build_eval_config(
        tmp_path,
        backend_name="fake",
        model="fake-eval",
        base_url="",
        api_key="",
        max_turns=3,
        system_prompt_mode="prod",
    )

    assert config.system_prompt
    assert "[Loop Discipline]" in config.system_prompt
    assert "[Metis workspace]" in config.system_prompt
    assert config.workspace_root == str(tmp_path)

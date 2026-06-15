# -*- coding: utf-8 -*-
"""FABLEADV-25: DeepSeek 缓存优化 = 稳定前缀回归守卫。

DeepSeek 上下文缓存是服务端自动的、基于**前缀**（从第 0 个 token 起逐块匹配）。
最大化命中率的唯一杠杆是：系统提示 + 工具定义跨请求字节稳定，易变内容（todo
状态、日期、会话 id）放到**末尾**。中间任何早期变动都会让缓存从那点之后全失效。

这些测试把"前缀稳定"锁成不变量——以后谁往前缀塞易变内容打破缓存（负优化），
这里就会红。
"""
from __future__ import annotations

from backend.runtime.agent_loop import (
    AgentConfig,
    _messages_for_llm_request,
    _prepare_working_messages,
    _refresh_todo_context_message,
    _TODO_CONTEXT_MARKER,
)
from backend.runtime.context_budget import context_ledger
from backend.runtime.llm_backends import Usage


def _config() -> AgentConfig:
    return AgentConfig(
        llm_backend="deepseek",
        llm_model="deepseek-chat",
        system_prompt="You are Metis. Follow the rules.",
        execution_mode="execute",
    )


def _leading_system(messages):
    out = []
    for m in messages:
        if m.get("role") == "system":
            out.append(m.get("content"))
        else:
            break
    return out


def test_system_prefix_is_byte_stable_across_calls(monkeypatch):
    import backend.runtime.agent_loop as al

    # 重置记忆化 + 给足探测超时，确保环境上下文确定地完成并缓存（否则首次冷启动超时会空）
    monkeypatch.setattr(al, "_ENV_CONTEXT_CACHE", None, raising=False)
    monkeypatch.setenv("METIS_ENV_CONTEXT_TIMEOUT", "5")
    msgs = [{"role": "user", "content": "hello"}]
    a = _prepare_working_messages(list(msgs), _config())
    b = _prepare_working_messages(list(msgs), _config())
    # 前导系统消息（前缀的核心）必须逐字节一致，否则缓存从这里就断
    assert _leading_system(a) == _leading_system(b)
    assert a[0]["role"] == "system"


def test_messages_for_llm_request_preserves_system_prefix():
    working = _prepare_working_messages([{"role": "user", "content": "hi"}], _config())
    req = _messages_for_llm_request(working, _config())
    # 请求构造不得改动首个系统消息（前缀起点）
    assert req[0]["role"] == "system"
    assert req[0]["content"] == working[0]["content"]


def test_volatile_todo_lives_at_the_end_not_in_prefix(tmp_path, monkeypatch):
    # 造一个有 todo 的工作区
    import json

    todos = {"todos": [{"content": "step one", "status": "in_progress"}]}
    (tmp_path / ".agent_todos.json").write_text(json.dumps(todos), encoding="utf-8")

    base = _prepare_working_messages([{"role": "user", "content": "go"}], _config())
    refreshed = _refresh_todo_context_message(base, str(tmp_path))
    # todo 块（易变）必须是最后一条，绝不能混进前导系统前缀
    assert _TODO_CONTEXT_MARKER in str(refreshed[-1].get("content"))
    for content in _leading_system(refreshed):
        assert _TODO_CONTEXT_MARKER not in str(content)


def test_todo_refresh_replaces_not_accumulates(tmp_path):
    import json

    (tmp_path / ".agent_todos.json").write_text(
        json.dumps({"todos": [{"content": "a", "status": "pending"}]}), encoding="utf-8"
    )
    base = _prepare_working_messages([{"role": "user", "content": "go"}], _config())
    once = _refresh_todo_context_message(base, str(tmp_path))
    twice = _refresh_todo_context_message(once, str(tmp_path))
    # 反复刷新不得累积多条 todo 消息（否则前缀长度漂移）
    markers = [m for m in twice if _TODO_CONTEXT_MARKER in str(m.get("content"))]
    assert len(markers) == 1


def test_request_volatile_layers_excluded_when_disabled(tmp_path):
    """显式关掉 agent_state/open_files/terminal 后，前缀里不得出现任何 request(易变)层。"""
    from backend.core.engine.prompt_runtime import compile_prompt_runtime

    snap = compile_prompt_runtime(
        "BASE",
        workspace_root=str(tmp_path),
        include_agent_state_hint=False,
        include_open_files_hint=False,
        include_terminal_hint=False,
    )
    assert all(layer.stability != "request" for layer in snap.layers)
    for name in ("agent_state_hint", "open_files_hint", "terminal_hint"):
        assert name not in snap.layer_names()


def test_production_config_builder_excludes_volatile_layers_from_prefix(monkeypatch):
    """build_agent_config(生产前缀构建器)必须把易变 request 层排除出前缀——锁死缓存纪律。"""
    import backend.web.llm_state as llm_state

    captured = {}

    def fake_compile(base, **kwargs):
        captured.update(kwargs)
        from backend.core.engine.prompt_runtime import PromptRuntimeSnapshot

        return PromptRuntimeSnapshot(base, [], base, kwargs.get("workspace_root"))

    monkeypatch.setattr(llm_state, "compile_prompt_runtime", fake_compile)
    llm_state.build_agent_config(system_prompt="BASE", execution_mode="execute")
    assert captured.get("include_agent_state_hint") is False
    assert captured.get("include_open_files_hint") is False
    assert captured.get("include_terminal_hint") is False


def test_context_ledger_exposes_cache_hit_rate():
    usage = Usage(prompt_tokens=1000, completion_tokens=50, total_tokens=1050)
    usage.prompt_cache_hit_tokens = 710
    usage.prompt_cache_miss_tokens = 290
    payload = context_ledger([{"role": "system", "content": "x"}], [], usage=usage, model="deepseek-chat")
    assert payload["cache_hit_rate"] == round(710 / 1000, 4)

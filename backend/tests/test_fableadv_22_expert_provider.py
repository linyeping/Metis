# -*- coding: utf-8 -*-
"""FABLEADV-22: 子智能体（expert）必须继承主聊天的 base_url/api_key。

真机现象：`desktop_expert` 第一步就失败——
"Provider custom-openai requires base_url for OpenAI-compatible chat completions"，
逼主智能体退回最费步数的"截图+裸点击"循环（一句话指令烧 26 步）。
根因：expert_execute 只传了 llm_backend/llm_model，漏了 base_url/api_key。
"""
from __future__ import annotations


from backend.runtime import agent_loop, expert_tools


def test_get_current_base_url_priority(monkeypatch):
    for key in ("METIS_LLM_BASE_URL", "MIRO_LLM_BASE_URL", "DEEPSEEK_BASE_URL", "OPENAI_BASE_URL"):
        monkeypatch.delenv(key, raising=False)
    assert expert_tools._get_current_base_url() == ""
    monkeypatch.setenv("OPENAI_BASE_URL", "https://openai.example/v1")
    assert expert_tools._get_current_base_url() == "https://openai.example/v1"
    monkeypatch.setenv("METIS_LLM_BASE_URL", "https://relay.example/v1")
    assert expert_tools._get_current_base_url() == "https://relay.example/v1"  # 优先 METIS


def test_get_current_api_key_priority(monkeypatch):
    for key in ("METIS_LLM_API_KEY", "MIRO_LLM_API_KEY", "DEEPSEEK_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    assert expert_tools._get_current_api_key() == ""
    monkeypatch.setenv("METIS_LLM_API_KEY", "sk-relay")
    assert expert_tools._get_current_api_key() == "sk-relay"


def test_expert_config_inherits_provider_runtime(monkeypatch):
    monkeypatch.setenv("METIS_LLM_BACKEND", "custom-openai")
    monkeypatch.setenv("METIS_LLM_BASE_URL", "https://relay.example.com/v1")
    monkeypatch.setenv("METIS_LLM_API_KEY", "sk-test-123")
    monkeypatch.setenv("METIS_LLM_MODEL", "gpt-5.5")

    captured: dict = {}

    def fake_run(messages, config, registry=None, backend=None):
        captured["config"] = config
        yield agent_loop.DoneEvent(
            total_turns=0,
            total_tool_calls=0,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            prompt_cache_hit_tokens=0,
            prompt_cache_miss_tokens=0,
        )

    monkeypatch.setattr(agent_loop, "run", fake_run)

    executor = expert_tools._create_expert_executor(
        "desktop_expert", "system", {"desktop_vision_task"}
    )
    executor(goal="open bilibili and search lemon tree")

    cfg = captured["config"]
    assert cfg.llm_backend == "custom-openai"
    assert cfg.llm_base_url == "https://relay.example.com/v1"
    assert cfg.llm_api_key == "sk-test-123"
    assert cfg.llm_model == "gpt-5.5"

# -*- coding: utf-8 -*-
"""FABLEADV-42: DeepSeek 默认走 /beta chat completions 端点。

/beta 开启 strict tool mode + chat prefix completion，提升工具调用可靠性，并配合稳定前缀
利于上下文缓存。仅 chat completions 受影响；用户显式带 /v1 或 /beta 视为自主选择。
"""
from __future__ import annotations

from backend.runtime.llm_backends.openai_compat import OpenAICompatBackend


def _url(base: str) -> str:
    return OpenAICompatBackend(base, "test-key", "deepseek-v4-flash").chat_completions_url


def test_plain_deepseek_host_uses_beta():
    assert _url("https://api.deepseek.com") == "https://api.deepseek.com/beta/chat/completions"
    assert _url("https://api.deepseek.com/") == "https://api.deepseek.com/beta/chat/completions"


def test_explicit_v1_opts_out_of_beta():
    assert _url("https://api.deepseek.com/v1") == "https://api.deepseek.com/v1/chat/completions"


def test_explicit_beta_not_doubled():
    assert _url("https://api.deepseek.com/beta") == "https://api.deepseek.com/beta/chat/completions"


def test_non_deepseek_provider_unaffected():
    # 中转/其它 OpenAI 兼容站点不应被加 /beta
    assert _url("https://api.openai.com/v1") == "https://api.openai.com/v1/chat/completions"
    assert "/beta" not in _url("https://example.com/v1")

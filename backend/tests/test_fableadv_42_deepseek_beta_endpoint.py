# -*- coding: utf-8 -*-
"""FABLEADV-42: DeepSeek chat completions 端点选择。

默认走普通 /chat/completions（/beta strict 模式会 400 掉常见工具 schema，破坏每次工具
调用）。METIS_DEEPSEEK_STRICT=1 时才走 /beta strict。仅 chat completions 受影响；用户
显式带 /v1 或 /beta 视为自主选择。
"""
from __future__ import annotations

import pytest

from backend.runtime.llm_backends.openai_compat import OpenAICompatBackend


def _url(base: str) -> str:
    return OpenAICompatBackend(base, "test-key", "deepseek-v4-flash").chat_completions_url


def test_plain_deepseek_host_defaults_to_plain_endpoint():
    # Strict mode is OFF by default so tool calls don't 400.
    assert _url("https://api.deepseek.com") == "https://api.deepseek.com/chat/completions"
    assert _url("https://api.deepseek.com/") == "https://api.deepseek.com/chat/completions"


def test_strict_opt_in_uses_beta(monkeypatch):
    monkeypatch.setenv("METIS_DEEPSEEK_STRICT", "1")
    assert _url("https://api.deepseek.com") == "https://api.deepseek.com/beta/chat/completions"


def test_explicit_v1_opts_out_of_beta():
    assert _url("https://api.deepseek.com/v1") == "https://api.deepseek.com/v1/chat/completions"


def test_explicit_beta_not_doubled():
    assert _url("https://api.deepseek.com/beta") == "https://api.deepseek.com/beta/chat/completions"


def test_non_deepseek_provider_unaffected():
    # 中转/其它 OpenAI 兼容站点不应被加 /beta
    assert _url("https://api.openai.com/v1") == "https://api.openai.com/v1/chat/completions"
    assert "/beta" not in _url("https://example.com/v1")

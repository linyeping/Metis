# -*- coding: utf-8 -*-
"""FABLEADV-43: 推理强度 opt-in 开关（+ 按模型档位，见 reasoning_tiers）。

默认关 = 零行为变化；开启后对推理能力模型（DeepSeek v4+/reasoner、GPT-5.x/o系列、
Claude、Gemini…）注入其支持的真实档位；非推理模型（gpt-4o、deepseek-chat…）一律不注入，
避免不支持的参数报错。
"""
from __future__ import annotations

from typing import Any, Dict

import backend.runtime.llm_backends.openai_compat as openai_compat
from backend.runtime.llm_backends.openai_compat import OpenAICompatBackend


class _JsonResponse:
    def __init__(self, payload: Dict[str, Any]) -> None:
        self._payload = payload
        self.headers: Dict[str, str] = {}

    def json(self) -> Dict[str, Any]:
        return self._payload


def _capture_payload(monkeypatch) -> Dict[str, Any]:
    captured: Dict[str, Any] = {}

    def fake_post(url, *, headers, payload, **kwargs):
        captured.update(payload)
        return _JsonResponse({"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}]})

    monkeypatch.setattr(openai_compat, "post_with_retries", fake_post)
    monkeypatch.setattr(openai_compat, "detect_and_cache_model", lambda *a, **k: "")
    return captured


def _run(monkeypatch, *, base_url: str, model: str, effort: str) -> Dict[str, Any]:
    captured = _capture_payload(monkeypatch)
    backend = OpenAICompatBackend(base_url, "test-key", model)
    backend.reasoning_effort = effort
    backend.chat([{"role": "user", "content": "hi"}])
    return captured


def test_default_off_sends_no_reasoning(monkeypatch):
    p = _run(monkeypatch, base_url="https://api.deepseek.com", model="deepseek-v4-flash", effort="")
    assert "reasoning_effort" not in p and "thinking" not in p


def test_high_on_deepseek_v4_injects(monkeypatch):
    p = _run(monkeypatch, base_url="https://api.deepseek.com", model="deepseek-v4-pro", effort="high")
    assert p["reasoning_effort"] == "high"
    assert p["thinking"] == {"type": "enabled"}


def test_max_maps_to_max(monkeypatch):
    p = _run(monkeypatch, base_url="https://api.deepseek.com", model="deepseek-v4-flash", effort="max")
    assert p["reasoning_effort"] == "max"


def test_legacy_deepseek_chat_not_injected(monkeypatch):
    p = _run(monkeypatch, base_url="https://api.deepseek.com", model="deepseek-chat", effort="high")
    assert "reasoning_effort" not in p and "thinking" not in p


def test_non_deepseek_not_injected(monkeypatch):
    # gpt-4o is NOT a reasoning model -> never inject (would error).
    p = _run(monkeypatch, base_url="https://api.openai.com/v1", model="gpt-4o", effort="high")
    assert "reasoning_effort" not in p and "thinking" not in p


def test_gpt5_passes_real_level_through(monkeypatch):
    # GPT-5.5 supports low/medium/high/xhigh -> the real chosen level is sent
    # (not collapsed to "high"), and no DeepSeek-only "thinking" flag.
    p = _run(monkeypatch, base_url="https://api.openai.com/v1", model="gpt-5.5", effort="xhigh")
    assert p["reasoning_effort"] == "xhigh"
    assert "thinking" not in p


def test_effort_clamped_to_model_max(monkeypatch):
    # 'max' is above GPT-5.5's ceiling (xhigh) -> clamp down to xhigh.
    p = _run(monkeypatch, base_url="https://api.openai.com/v1", model="gpt-5.5", effort="max")
    assert p["reasoning_effort"] == "xhigh"


def test_off_value_not_injected(monkeypatch):
    p = _run(monkeypatch, base_url="https://api.deepseek.com", model="deepseek-v4-flash", effort="off")
    assert "reasoning_effort" not in p and "thinking" not in p

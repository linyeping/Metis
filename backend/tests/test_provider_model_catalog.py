from __future__ import annotations

from typing import Any

import pytest

from backend.web import llm_state


@pytest.fixture(autouse=True)
def isolate_runtime_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_state, "load_persistent_config", lambda: None)
    monkeypatch.setattr(llm_state, "_env_file_values", lambda: {})
    monkeypatch.setattr(
        llm_state,
        "_runtime_value",
        lambda kind, backend, file_values, default="": "" if kind == "api_key" else default,
    )


def test_provider_models_returns_deepseek_presets_without_api_key() -> None:
    catalog = llm_state.get_provider_models(
        {
            "backend": "deepseek",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "api_key": "",
        }
    )

    assert catalog["ok"] is True
    assert catalog["status"] == "preset"
    assert catalog["models"][0]["id"] == "deepseek-v4-flash"
    assert catalog["models"][0]["context_limit"] == 1_000_000


def test_provider_models_falls_back_to_presets_when_remote_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_fetch(_urls: list[str], _api_key: str, _timeout: float = 12.0) -> dict[str, Any]:
        raise RuntimeError("simulated remote catalog failure")

    monkeypatch.setattr(llm_state, "_provider_get_first_json", fail_fetch)

    catalog = llm_state.get_provider_models(
        {
            "backend": "bailian",
            "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
            "model": "qwen3-coder-plus",
            "api_key": "fake-test-key",
        }
    )

    assert catalog["ok"] is True
    assert catalog["status"] == "fallback"
    assert [item["id"] for item in catalog["models"]] == ["qwen3-coder-plus", "qwen3-max"]


def test_models_url_candidates_preserve_version_segments() -> None:
    assert llm_state._models_url_candidates(
        {
            "provider_id": "zhipu-glm",
            "base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
            "api_base_url": "https://open.bigmodel.cn/api/coding/paas/v4",
        }
    )[:2] == [
        "https://open.bigmodel.cn/api/coding/paas/v4/models",
        "https://open.bigmodel.cn/api/coding/paas/v4/v1/models",
    ]


def test_models_url_candidates_include_deepseek_root_models() -> None:
    assert llm_state._models_url_candidates(
        {
            "provider_id": "deepseek",
            "base_url": "https://api.deepseek.com",
            "api_base_url": "https://api.deepseek.com",
        }
    )[0] == "https://api.deepseek.com/models"


def test_deepseek_model_fallback_does_not_read_openai_model() -> None:
    assert "OPENAI_MODEL" not in llm_state._runtime_keys("model", "deepseek")


def test_runtime_values_repair_deepseek_gpt_model() -> None:
    resolved = llm_state._resolved_provider_runtime_values(
        "deepseek",
        base_url="https://api.deepseek.com",
        model="gpt-4o",
    )

    assert resolved["backend"] == "deepseek"
    assert resolved["model"] == "deepseek-v4-flash"
    assert "gpt-4o" in resolved["model_warning"]


def test_provider_model_catalog_success_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    llm_state._clear_provider_probe_caches_for_tests()
    calls = 0

    def fake_get_json(_url: str, _api_key: str, _timeout: float = 12.0) -> dict[str, Any]:
        nonlocal calls
        calls += 1
        return {"data": [{"id": "gpt-5.5", "owned_by": "relay"}]}

    monkeypatch.setattr(llm_state, "_provider_get_json", fake_get_json)
    payload = {
        "backend": "custom-openai",
        "base_url": "https://relay.example.com/v1",
        "model": "gpt-5.5",
        "api_key": "test-cache-key",
    }

    first = llm_state.get_provider_models(payload)
    second = llm_state.get_provider_models(payload)

    assert calls == 1
    assert first["status"] == "ok"
    assert second["models"][0]["id"] == "gpt-5.5"


def test_ollama_models_use_local_tags_without_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(llm_state, "list_ollama_models", lambda _base_url: [{"name": "llama3.1:8b"}])
    monkeypatch.setattr(llm_state, "check_ollama_running", lambda _base_url: True)

    catalog = llm_state.get_provider_models(
        {
            "backend": "ollama",
            "base_url": "http://localhost:11434/v1",
            "model": "llama3.1:8b",
            "api_key": "",
        }
    )

    assert catalog["ok"] is True
    assert catalog["status"] == "ok"
    assert catalog["provider_id"] == "ollama"
    assert catalog["models_url"] == "http://localhost:11434/api/tags"
    assert catalog["models"][0]["id"] == "llama3.1:8b"
    assert "API Key" in catalog["hint"]


def test_provider_probe_cache_key_redacts_api_key() -> None:
    key = llm_state._provider_probe_cache_key(
        "models",
        "https://relay.example.com/v1/models",
        "secret-token-this-must-not-appear",
    )

    assert "secret-token-this-must-not-appear" not in repr(key)
    assert key[2] != "secret-token-this-must-not-appear"

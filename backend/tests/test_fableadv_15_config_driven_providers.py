from __future__ import annotations

import json

import pytest
from flask import Flask

from backend.bridges import provider_registry
from backend.bridges import provider_user_config
from backend.web import settings_routes


@pytest.fixture
def metis_home(tmp_path, monkeypatch):
    monkeypatch.setenv("METIS_HOME", str(tmp_path))
    from backend.core import paths as metis_paths

    metis_paths.clear_metis_home_cache()
    yield tmp_path
    # restore builtin-only registry for other tests
    provider_registry.reload_provider_registry("")
    metis_paths.clear_metis_home_cache()


def _write_global(tmp_path, providers):
    (tmp_path / "providers.json").write_text(json.dumps({"providers": providers}, ensure_ascii=False), encoding="utf-8")


def test_user_provider_merges_and_overrides(metis_home):
    _write_global(metis_home, [
        {
            "id": "my-relay",
            "display_name": "内网中转",
            "backend_type": "openai",
            "base_url": "https://llm.internal.corp/v1",
            "api_key_env": "CORP_LLM_KEY",
            "default_model": "gpt-5.5",
            "models": ["gpt-5.5", "deepseek-v4-pro"],
            "supports_vision": True,
            "parallel_tool_calls": True,
        }
    ])
    count = provider_registry.reload_provider_registry()
    assert count >= 1
    profile = provider_registry.get_provider_profile("my-relay")
    assert profile.display_name == "内网中转"
    assert profile.supports_vision is True
    assert profile.api_key_env == "CORP_LLM_KEY"
    assert profile.source == "user"
    # builtin still present (fallback layer intact)
    assert provider_registry.get_provider_profile("deepseek") is not None


def test_plaintext_api_key_is_ignored(metis_home):
    profile = provider_user_config.save_user_provider({
        "id": "leaky",
        "backend_type": "openai",
        "base_url": "https://x.example/v1",
        "api_key": "sk-should-not-be-stored",
        "api_key_env": "X_KEY",
    })
    assert profile.api_key_env == "X_KEY"
    raw = json.loads((metis_home / "providers.json").read_text(encoding="utf-8"))
    stored = raw["providers"][0]
    assert "api_key" not in stored  # plaintext key stripped before persisting


def test_invalid_provider_rejected(metis_home):
    with pytest.raises(ValueError):
        provider_user_config.save_user_provider({"id": "bad id!", "backend_type": "openai"})
    with pytest.raises(ValueError):
        provider_user_config.save_user_provider({"id": "ok", "backend_type": "nonsense"})


def test_corrupt_providers_json_falls_back_to_builtin(metis_home):
    (metis_home / "providers.json").write_text("{ not valid json", encoding="utf-8")
    count = provider_registry.reload_provider_registry()
    assert count >= 1  # builtin defaults still load
    assert provider_registry.get_provider_profile("deepseek") is not None


def test_delete_user_provider(metis_home):
    provider_user_config.save_user_provider({
        "id": "tmp-relay",
        "backend_type": "openai",
        "base_url": "https://x.example/v1",
    })
    provider_registry.reload_provider_registry()
    assert provider_registry.get_provider_profile("tmp-relay").source == "user"
    assert provider_user_config.delete_user_provider("tmp-relay") is True
    provider_registry.reload_provider_registry()
    with pytest.raises(Exception):
        provider_registry.get_provider_profile("tmp-relay")


def test_provider_probe_route_updates_models_and_capabilities(metis_home, monkeypatch):
    provider_user_config.save_user_provider({
        "id": "probe-relay",
        "display_name": "Probe Relay",
        "backend_type": "openai",
        "base_url": "https://x.example/v1",
        "default_model": "old-model",
        "models": ["old-model"],
        "supports_vision": False,
        "parallel_tool_calls": False,
        "openai_compatible": True,
    })
    provider_registry.reload_provider_registry()
    monkeypatch.setattr(
        settings_routes,
        "get_provider_models",
        lambda data: {"ok": True, "models": [{"id": "gpt-4o"}], "message": ""},
    )
    app = Flask(__name__)

    with app.test_request_context(json={"base_url": "https://x.example/v1"}):
        response = settings_routes.providers_registry_probe("probe-relay")

    data = response.get_json()
    assert data["ok"] is True
    assert data["models"] == ["gpt-4o"]
    assert data["supports_vision"] is True
    profile = provider_registry.get_provider_profile("probe-relay")
    assert profile.default_model == "gpt-4o"
    assert profile.fallback_models == ("gpt-4o",)
    assert profile.supports_vision is True

import json

import pytest
from backend.core.paths import clear_metis_home_cache
from backend.web import llm_state


@pytest.fixture
def isolated_metis_home(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("METIS_HOME", str(tmp_path / "metis-home"))
    for key in (
        "METIS_LLM_BACKEND",
        "METIS_LLM_BASE_URL",
        "METIS_LLM_API_KEY",
        "METIS_LLM_MODEL",
        "METIS_PROXY_MODE",
        "METIS_PROXY_SCHEME",
        "METIS_PROXY_HOST",
        "METIS_PROXY_PORT",
        "METIS_PROXY_BYPASS",
        "METIS_LLM_PROXY",
        "METIS_LLM_API_KEY_SOURCE",
    ):
        monkeypatch.delenv(key, raising=False)
    # Never let settings tests touch the developer's real Credential Manager.
    monkeypatch.setattr(llm_state, "read_api_key", lambda: None)
    monkeypatch.setattr(llm_state, "write_api_key", lambda _value: False)
    clear_metis_home_cache()
    yield tmp_path / "metis-home"
    clear_metis_home_cache()


def test_network_check_uses_saved_api_key_when_request_omits_key(
    isolated_metis_home,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_metis_home.mkdir(parents=True, exist_ok=True)
    (isolated_metis_home / "config.json").write_text(
        json.dumps(
            {
                "backend": "custom-openai",
                "provider_id": "custom-openai",
                "base_url": "https://relay.example/v1",
                "api_key": "sk-saved",
                "model": "gpt-5.5",
                "proxy_mode": "off",
            }
        ),
        encoding="utf-8",
    )
    captured = {}

    def fake_verify(data):
        captured["api_key"] = data.get("api_key")
        return {
            "ok": True,
            "provider_id": data["provider_id"],
            "base_url": data["base_url"],
            "model": data["model"],
            "chat_url": f"{data['base_url']}/chat/completions",
        }

    monkeypatch.setattr(llm_state, "verify_provider_settings", fake_verify)
    def fail_conformance_probe(**_kwargs):
        pytest.fail("settings network check should not run the heavyweight conformance probe")

    monkeypatch.setattr(llm_state, "run_provider_conformance_probe", fail_conformance_probe)
    monkeypatch.setattr(
        llm_state,
        "_provider_get_first_json_uncached",
        lambda *_args, **_kwargs: {"data": [{"id": "gpt-5.5"}]},
    )

    result = llm_state.check_network_settings(
        {
            "backend": "custom-openai",
            "base_url": "https://relay.example/v1",
            "model": "gpt-5.5",
            "proxy_mode": "off",
        }
    )

    assert captured["api_key"] == "sk-saved"
    assert result["ok"] is True


def test_update_runtime_settings_strips_endpoint_whitespace(isolated_metis_home) -> None:
    isolated_metis_home.mkdir(parents=True, exist_ok=True)

    updated = llm_state.update_runtime_settings(
        {
            "backend": "custom-openai",
            "provider_id": "custom-openai",
            "base_url": " https://relay.example /v1 \n",
            "api_key": " sk-test 123\t",
            "model": "gpt-5.5",
        }
    )

    config = json.loads((isolated_metis_home / "config.json").read_text(encoding="utf-8"))
    assert "base_url" in updated
    assert config["base_url"] == "https://relay.example/v1"
    assert config["api_key"] == "sk-test123"


def test_secure_settings_write_omits_plaintext_api_key(
    isolated_metis_home,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored = {}

    def store(value: str) -> bool:
        stored["api_key"] = value
        return True

    monkeypatch.setattr(llm_state, "write_api_key", store)
    isolated_metis_home.mkdir(parents=True, exist_ok=True)

    llm_state.update_runtime_settings(
        {
            "backend": "custom-openai",
            "base_url": "https://relay.example/v1",
            "api_key": " sk-secure 123 ",
            "model": "gpt-5.5",
        }
    )

    config = json.loads((isolated_metis_home / "config.json").read_text(encoding="utf-8"))
    assert stored["api_key"] == "sk-secure123"
    assert "api_key" not in config
    assert "api_key_encrypted" not in config


def test_plaintext_config_migrates_to_credential_manager(
    isolated_metis_home,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_metis_home.mkdir(parents=True, exist_ok=True)
    config_path = isolated_metis_home / "config.json"
    config_path.write_text(
        json.dumps({"backend": "deepseek", "api_key": "sk-legacy", "model": "deepseek-chat"}),
        encoding="utf-8",
    )
    stored = {}

    def store(value: str) -> bool:
        stored["api_key"] = value
        return True

    monkeypatch.setattr(llm_state, "write_api_key", store)
    monkeypatch.setattr(llm_state, "read_api_key", lambda: stored.get("api_key"))

    llm_state.load_persistent_config()

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert stored["api_key"] == "sk-legacy"
    assert "api_key" not in config
    assert "api_key_encrypted" not in config
    assert llm_state.os.environ["METIS_LLM_API_KEY"] == "sk-legacy"


def test_electron_safe_storage_key_migrates_and_cleans_legacy_blob(
    isolated_metis_home,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_metis_home.mkdir(parents=True, exist_ok=True)
    config_path = isolated_metis_home / "config.json"
    config_path.write_text(
        json.dumps({"backend": "deepseek", "api_key_encrypted": "v10-old-blob"}),
        encoding="utf-8",
    )
    stored = {}
    monkeypatch.setenv("METIS_LLM_API_KEY", "sk-from-electron")
    monkeypatch.setenv("METIS_LLM_API_KEY_SOURCE", "electron-safe-storage")

    def store(value: str) -> bool:
        stored["api_key"] = value
        return True

    monkeypatch.setattr(llm_state, "write_api_key", store)

    llm_state.load_persistent_config()

    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert stored["api_key"] == "sk-from-electron"
    assert "api_key_encrypted" not in config
    assert "api_key" not in config
    assert "METIS_LLM_API_KEY_SOURCE" not in llm_state.os.environ


def test_credential_write_failure_preserves_plaintext_fallback(
    isolated_metis_home,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_write(_value: str) -> bool:
        raise llm_state.CredentialStoreError("write failed")

    monkeypatch.setattr(llm_state, "write_api_key", fail_write)
    isolated_metis_home.mkdir(parents=True, exist_ok=True)

    llm_state.update_runtime_settings({"api_key": "sk-preserved"})

    config = json.loads((isolated_metis_home / "config.json").read_text(encoding="utf-8"))
    assert config["api_key"] == "sk-preserved"


def test_runtime_loads_shared_credential_when_config_has_no_key(
    isolated_metis_home,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_metis_home.mkdir(parents=True, exist_ok=True)
    (isolated_metis_home / "config.json").write_text(
        json.dumps({"backend": "deepseek", "model": "deepseek-chat"}),
        encoding="utf-8",
    )
    monkeypatch.setattr(llm_state, "read_api_key", lambda: "sk-shared")

    llm_state.load_persistent_config()

    assert llm_state.os.environ["METIS_LLM_API_KEY"] == "sk-shared"


def test_provider_models_remote_only_does_not_use_local_presets(
    isolated_metis_home,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    isolated_metis_home.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(llm_state, "_provider_get_first_json", lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")))

    result = llm_state.get_provider_models(
        {
            "backend": "deepseek",
            "base_url": "https://api.deepseek.com",
            "model": "deepseek-v4-flash",
            "api_key": "sk-test",
            "remote_only": True,
        }
    )

    assert result["ok"] is False
    assert result["status"] == "error"
    assert result["models"] == []

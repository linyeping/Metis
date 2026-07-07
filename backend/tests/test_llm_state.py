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
    ):
        monkeypatch.delenv(key, raising=False)
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

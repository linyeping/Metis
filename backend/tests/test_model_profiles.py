from __future__ import annotations

from pathlib import Path
from typing import Iterator

import pytest
from backend.bridges.model_profiles import (
    delete_user_model_profile,
    load_user_model_profiles,
    resolve_model_profile,
    save_user_model_profile,
)
from backend.core.paths import clear_metis_home_cache
from backend.runtime import agent_loop
from backend.runtime.context_budget import context_ledger
from backend.web import llm_state
from backend.web.settings_routes import settings_bp
from flask import Flask


@pytest.fixture(autouse=True)
def isolated_metis_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    monkeypatch.setenv("METIS_HOME", str(tmp_path / "metis-home"))
    clear_metis_home_cache()
    yield tmp_path
    clear_metis_home_cache()


def test_user_exact_and_wildcard_profiles_override_builtins() -> None:
    save_user_model_profile(
        {
            "model": "gpt-4o",
            "context_window": 222_000,
            "max_output_tokens": 12_000,
            "compact_thresholds": [0.55, 0.75, 0.9],
        }
    )
    save_user_model_profile(
        {
            "model": "vendor-pro-*",
            "context_window": 456_000,
            "max_output_tokens": 24_000,
            "compact_thresholds": [0.5, 0.7, 0.88],
        }
    )

    exact = resolve_model_profile("GPT-4O", tier=1)
    wildcard = resolve_model_profile("vendor-pro-2026", tier=2)

    assert (exact.source, exact.context_window, exact.max_output_tokens) == ("user", 222_000, 12_000)
    assert exact.compact_thresholds == (0.55, 0.75, 0.9)
    assert wildcard.source == "user"
    assert wildcard.matched_model == "vendor-pro-*"
    assert wildcard.context_window == 456_000


def test_delete_restores_builtin_and_unknown_models_are_marked_estimates() -> None:
    save_user_model_profile({"model": "deepseek-chat", "context_window": 256_000})
    assert resolve_model_profile("deepseek-chat").source == "user"

    assert delete_user_model_profile("deepseek-chat") is True
    restored = resolve_model_profile("deepseek-chat")
    unknown = resolve_model_profile("private-lab-model")

    assert (restored.source, restored.context_window, restored.is_estimate) == ("builtin", 128_000, False)
    assert (unknown.source, unknown.context_window, unknown.is_estimate) == ("default", 128_000, True)


def test_invalid_toml_and_invalid_profile_fail_safely(tmp_path: Path) -> None:
    home = tmp_path / "metis-home"
    home.mkdir(parents=True)
    (home / "models.toml").write_text('not = "valid', encoding="utf-8")
    assert load_user_model_profiles() == {}
    assert resolve_model_profile("gpt-4o").source == "builtin"

    with pytest.raises(ValueError, match="compact_thresholds"):
        save_user_model_profile(
            {
                "model": "bad-model",
                "context_window": 100_000,
                "compact_thresholds": [0.8, 0.7, 0.9],
            }
        )


def test_runtime_ledger_compaction_and_output_budget_use_user_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    save_user_model_profile(
        {
            "model": "custom-runtime",
            "context_window": 90_000,
            "max_output_tokens": 2_048,
            "compact_thresholds": [0.4, 0.5, 0.75],
        }
    )
    ledger = context_ledger([{"role": "user", "content": "hello"}], model="custom-runtime")
    assert ledger["context_limit"] == 90_000
    assert ledger["context_source"] == "user"
    assert ledger["max_output_tokens"] == 2_048
    assert agent_loop._auto_compact_ratio("custom-runtime") == 0.5
    assert llm_state.compaction_stage(44_000, "custom-runtime") == 1

    monkeypatch.setattr(llm_state, "load_persistent_config", lambda: None)
    monkeypatch.setattr(llm_state, "_env_file_values", lambda: {})
    monkeypatch.setenv("METIS_LLM_BACKEND", "openai-compatible")
    monkeypatch.setenv("METIS_LLM_BASE_URL", "https://relay.example.test/v1")
    monkeypatch.setenv("METIS_LLM_MODEL", "custom-runtime")
    monkeypatch.setenv("METIS_MAX_TOKENS", "99999")
    config = llm_state.build_agent_config(system_prompt="You are Metis.", execution_mode="auto")
    assert config.max_tokens == 2_048


def test_model_profile_settings_route_round_trip() -> None:
    app = Flask(__name__)
    app.register_blueprint(settings_bp)
    with app.test_client() as client:
        saved = client.post(
            "/settings/model-profiles",
            json={
                "model": "route-model",
                "context_window": 150_000,
                "max_output_tokens": 10_000,
                "compact_thresholds": [0.6, 0.78, 0.9],
            },
        )
        assert saved.status_code == 200
        assert saved.get_json()["resolved"]["source"] == "user"

        fetched = client.get("/settings/model-profiles?model=route-model")
        assert fetched.get_json()["resolved"]["context_window"] == 150_000

        reset = client.post("/settings/model-profiles", json={"action": "reset", "model": "route-model"})
        assert reset.status_code == 200
        assert reset.get_json()["resolved"]["source"] == "default"

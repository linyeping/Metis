from __future__ import annotations

from pathlib import Path

from backend.bridges.model_capability import detect_from_model_name
import pytest

from backend.tools.browser_automation.browser_agent import (
    BrowserLLMConfig,
    BrowserResult,
    BrowserTask,
    _active_provider_config,
    _build_browser_use_llm,
    _format_browser_failure,
)
from backend.tools.browser_automation.tools import browse_web
from backend.tools.desk_automation.orchestrator.screen_reader import (
    ActionType,
    _convert_anthropic_cua_action,
    _convert_openai_cua_actions,
    _native_cua_protocol,
    _should_use_native_cua,
)


def test_new_116_native_cua_protocol_detection_and_capabilities() -> None:
    assert _should_use_native_cua("gpt-5.4", "openai") == "openai_cua"
    assert _should_use_native_cua("gpt-5.5-mini", "openai") == "openai_cua"
    assert _should_use_native_cua("claude-sonnet-4-20250514", "anthropic") == "anthropic_cua"
    assert _should_use_native_cua("deepseek-chat", "openai") is None
    assert _native_cua_protocol({"backend_type": "anthropic", "anthropic_model": "claude-opus-4-20250514"}) == "anthropic_cua"

    assert detect_from_model_name("gpt-5.4").vision_protocol == "openai_cua"
    assert detect_from_model_name("claude-sonnet-4-20250514").vision_protocol == "anthropic_cua"
    assert detect_from_model_name("deepseek-v4-flash").vision_protocol == "none"


def test_fableadv_14_native_cua_requires_official_endpoint() -> None:
    # Official OpenAI endpoint keeps native CUA.
    assert _native_cua_protocol({
        "vision_protocol": "openai_cua",
        "openai_base_url": "https://api.openai.com/v1",
        "openai_model": "gpt-5.5",
    }) == "openai_cua"

    # Relay / OpenAI-compatible endpoint degrades to legacy vision (None),
    # because it only speaks Chat Completions, not the native computer_use API.
    assert _native_cua_protocol({
        "vision_protocol": "openai_cua",
        "openai_base_url": "https://api.example.com/v1",
        "openai_model": "gpt-5.5",
    }) is None

    # Same degradation when the protocol is inferred from the model name.
    assert _native_cua_protocol({
        "backend_type": "openai",
        "openai_base_url": "https://api.example.com/v1",
        "openai_model": "gpt-5.5",
    }) is None

    # No explicit base_url → vendor SDK default (official), native kept.
    assert _native_cua_protocol({
        "backend_type": "anthropic",
        "anthropic_model": "claude-opus-4-20250514",
    }) == "anthropic_cua"


def test_new_116_cua_action_conversion() -> None:
    openai_actions = _convert_openai_cua_actions({"type": "click", "x": 100, "y": 200})
    assert openai_actions[0].action == ActionType.CLICK
    assert openai_actions[0].params == {"x": 100, "y": 200}

    anthropic_actions = _convert_anthropic_cua_action(
        "left_click",
        {"coordinate": [11, 22]},
    )
    assert anthropic_actions[0].action == ActionType.CLICK
    assert anthropic_actions[0].params == {"x": 11, "y": 22}


def test_new_116_browser_types_and_dependency_fallback(monkeypatch) -> None:
    task = BrowserTask(goal="check docs", max_steps=3)
    result = BrowserResult(ok=False, error="browser-use missing")

    assert task.max_steps == 3
    assert "browser-use" in str(result)
    assert "playwright install chromium" in str(result)

    monkeypatch.setattr(
        "backend.tools.browser_automation.tools.run_browser_task",
        lambda _task: BrowserResult(ok=False, error="browser-use missing"),
    )
    assert "browser-use" in browse_web("check docs")


def test_browser_result_labels_summary_vs_extracted_content() -> None:
    # The sub-agent's own narrative output and the actually-extracted page
    # text must be visibly distinct, otherwise a model relaying browse_web's
    # result has no way to know which parts were verified and which were
    # synthesized by the browsing sub-agent itself.
    with_extracted = str(BrowserResult(ok=True, output="GPT-5.5 released April 23", extracted_content="real page text"))
    assert "Sub-agent summary" in with_extracted
    assert "Extracted page content" in with_extracted
    assert "real page text" in with_extracted

    without_extracted = str(BrowserResult(ok=True, output="GPT-5.5 released April 23"))
    assert "Sub-agent summary" in without_extracted
    assert "Do not present its specific dates" in without_extracted


def test_browser_show_browser_runs_visible_without_user_profile(monkeypatch) -> None:
    captured: dict[str, BrowserTask] = {}

    def fake_run(task: BrowserTask) -> BrowserResult:
        captured["task"] = task
        return BrowserResult(ok=True, output="ok")

    monkeypatch.setattr("backend.tools.browser_automation.tools.run_browser_task", fake_run)

    assert "ok" in browse_web("play a song", show_browser=True)
    assert captured["task"].headless is False
    assert captured["task"].use_user_profile is False

    browse_web("open github", use_login=True)
    assert captured["task"].headless is False
    assert captured["task"].use_user_profile is True


def test_fableadv_50_browser_use_native_openai_compatible_llm() -> None:
    config = BrowserLLMConfig(
        provider_id="custom-openai",
        display_name="Custom Relay",
        backend_type="openai",
        model="gpt-5.5",
        base_url="https://relay.example/v1",
        api_key="test-secret",
        api_key_source="METIS_LLM_API_KEY",
        openai_compatible=True,
    )

    llm, resolved = _build_browser_use_llm(config)

    assert resolved.provider_id == "custom-openai"
    assert getattr(llm, "provider", "") == "openai"
    assert getattr(llm, "model", "") == "gpt-5.5"
    assert str(getattr(llm, "base_url", "")) == "https://relay.example/v1"


def test_fableadv_50_browser_use_missing_key_is_clear() -> None:
    config = BrowserLLMConfig(
        provider_id="custom-openai",
        display_name="Custom Relay",
        backend_type="openai",
        model="gpt-5.5",
        base_url="https://relay.example/v1",
        api_key="",
        openai_compatible=True,
        api_key_required=True,
    )

    with pytest.raises(ValueError) as exc:
        _build_browser_use_llm(config)

    message = str(exc.value)
    assert "API Key" in message
    assert "api_key_encrypted" in message


def test_fableadv_50_browser_use_reads_metis_runtime_provider(monkeypatch) -> None:
    from backend.web import llm_state

    monkeypatch.setattr(llm_state, "load_persistent_config", lambda: None)
    monkeypatch.setattr(llm_state, "_env_file_values", lambda: {})
    monkeypatch.setenv("METIS_LLM_BACKEND", "custom-openai")
    monkeypatch.setenv("METIS_LLM_BASE_URL", "https://relay.example/v1")
    monkeypatch.setenv("METIS_LLM_MODEL", "gpt-5.5")
    monkeypatch.setenv("METIS_LLM_API_KEY", "runtime-secret")

    config = _active_provider_config()

    assert config.provider_id == "custom-openai"
    assert config.model == "gpt-5.5"
    assert config.base_url == "https://relay.example/v1"
    assert config.api_key == "runtime-secret"
    assert config.api_key_source == "runtime"


def test_fableadv_50_browser_use_errors_redact_secrets() -> None:
    config = BrowserLLMConfig(
        provider_id="custom-openai",
        model="gpt-5.5",
        base_url="https://relay.example/v1",
        api_key="sk-very-secret-token-123456",
        api_key_source="METIS_LLM_API_KEY",
    )

    message = _format_browser_failure(
        RuntimeError("ChatOpenAI object has no attribute provider; Authorization: Bearer sk-very-secret-token-123456"),
        config=config,
        phase="browser-use run",
    )

    assert "has no attribute provider" in message
    assert "METIS_LLM_API_KEY" in message
    assert "sk-very-secret-token" not in message
    assert "Bearer sk-****" in message


def test_new_116_delegate_browser_uses_real_browser_wrapper() -> None:
    source = (
        Path(__file__).resolve().parents[1]
        / "tools"
        / "coding"
        / "workflow_features"
        / "subagents"
        / "delegate_browser.py"
    ).read_text(encoding="utf-8")

    assert "run_browser_task" in source
    assert "not yet available" not in source


def test_new_116_desktop_python_candidate_sources_are_expanded() -> None:
    source = (Path(__file__).resolve().parents[2] / "desktop" / "electron" / "backend.cjs").read_text(encoding="utf-8")

    assert "project venv" in source
    assert "CONDA_PREFIX" in source
    assert "CONDA_EXE" in source
    assert "LocalAppData Python" in source
    assert "Miniconda3" in source

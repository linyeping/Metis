from __future__ import annotations

from pathlib import Path

from backend.bridges.model_capability import detect_from_model_name
from backend.tools.browser_automation.browser_agent import BrowserResult, BrowserTask
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

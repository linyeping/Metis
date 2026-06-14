from __future__ import annotations

from pathlib import Path
from typing import Any

from backend.bridges.model_capability import detect_from_model_name, tier_compact_thresholds
from backend.core.engine.prompt_runtime import compile_prompt_runtime
from backend.core.memory import workspace_state
from backend.runtime import tool_registry as runtime_tool_registry
from backend.runtime.llm_backends import openai_compat
from backend.tools.coding.workflow_features.hooks.post_tool_hook import post_tool_hook
from backend.web import llm_state
from backend.web.runtime_state import RuntimeState


def test_new_114_system_prompt_loads_metis_principles_without_stale_branding() -> None:
    from backend.web.app import _load_system_prompt

    prompt = _load_system_prompt()

    assert "Metis Agent Principles" in prompt
    assert "Eight Principles" in prompt or "八荣八耻" in prompt
    assert "Read before write" in prompt
    assert "Kiro" not in prompt
    assert "Cursor (C)" not in prompt
    assert "From Cursor" not in prompt
    assert "From Kiro" not in prompt


def test_new_114_prompt_runtime_layers_memory_tier_and_recency(tmp_path: Path) -> None:
    snapshot = compile_prompt_runtime(
        "You are Metis.",
        user_memory_text="Project convention: run pytest before claiming done.",
        model_tier=3,
        workspace_root=str(tmp_path),
        include_agent_state_hint=False,
        include_open_files_hint=False,
        include_terminal_hint=False,
        include_mode_router_hint=False,
        include_workflow_hint=False,
    )

    assert "Metis execution workflow" in snapshot.final_system_prompt
    assert "[User METIS.md]" in snapshot.final_system_prompt
    assert "Project convention: run pytest" in snapshot.final_system_prompt
    assert "Remember: (1) Read before you write." in snapshot.final_system_prompt
    assert "workspace_hint" in snapshot.layer_names()


def test_new_114_build_agent_config_compiles_prompt_with_detected_tier(monkeypatch: Any, tmp_path: Path) -> None:
    monkeypatch.setattr(llm_state, "load_persistent_config", lambda: None)
    monkeypatch.setattr(llm_state, "_env_file_values", lambda: {})
    monkeypatch.setenv("METIS_LLM_BACKEND", "openai-compatible")
    monkeypatch.setenv("METIS_LLM_BASE_URL", "https://relay.example.test/v1")
    monkeypatch.setenv("METIS_LLM_MODEL", "gpt-4o-mini")

    config = llm_state.build_agent_config(
        system_prompt="You are Metis.",
        user_memory_text="Keep edits small.",
        execution_mode="auto",
        workspace_root=str(tmp_path),
    )

    assert config.llm_backend == "openai-compatible"
    assert config.llm_model == "gpt-4o-mini"
    assert "Metis execution workflow" in config.system_prompt
    assert "Keep edits small." in config.system_prompt
    assert "Remember: (1) Read before you write." in config.system_prompt


def test_new_114_compaction_stages_follow_model_capability_tiers() -> None:
    assert llm_state.compaction_stage(70_000, "gpt-4o") == 0
    assert llm_state.compaction_stage(80_000, "gpt-4o") == 1
    assert llm_state.compaction_stage(105_000, "gpt-4o") == 2
    assert llm_state.compaction_stage(120_000, "gpt-4o") == 3

    assert llm_state.compaction_stage(70_000, "gpt-4o-mini") == 1
    assert tier_compact_thresholds(1) == (0.65, 0.82, 0.93)
    assert tier_compact_thresholds(3) == (0.50, 0.70, 0.85)


def test_new_114_observation_masking_preserves_recent_messages() -> None:
    from backend.web.app import _mask_observations

    history = [
        {"role": "user", "content": "start"},
        {"role": "tool", "name": "read_file", "tool_call_id": "old", "content": "A" * 300},
        {"role": "assistant", "content": "I saw it."},
        {"role": "user", "content": "next"},
        {"role": "tool", "name": "grep_search", "tool_call_id": "recent", "content": "keep me"},
    ]

    masked = _mask_observations(history, keep_recent=2)

    assert masked[1]["content"].startswith("[Observation masked")
    assert "read_file" in masked[1]["content"]
    assert masked[-1]["content"] == "keep me"


def test_new_114_runtime_tool_schemas_include_when_to_use_hints() -> None:
    runtime_tool_registry._REGISTRY = None
    runtime_tool_registry._LOADED_MCP_CONFIGS.clear()
    registry = runtime_tool_registry.get_registry(include_desktop=False, include_mcp=False)

    schemas = registry.get_all_schemas(format="openai")
    descriptions = {
        (schema.get("function") or {}).get("name"): (schema.get("function") or {}).get("description", "")
        for schema in schemas
    }
    hinted = [text for text in descriptions.values() if "[When to use]" in text]

    assert len(hinted) >= 10
    assert descriptions["read_file"].startswith("[When to use]")
    assert "before modifying" in descriptions["read_file"]
    assert descriptions["write_file"].startswith("[When to use]")


def test_new_114_model_capability_detection_and_header_cache() -> None:
    assert detect_from_model_name("claude-opus-4").tier == 1
    assert detect_from_model_name("gpt-4o-mini").detected_family == "openai"
    assert detect_from_model_name("gpt-4o-mini").tier == 3
    assert detect_from_model_name("deepseek-v4-flash").tier == 2
    assert detect_from_model_name("some-random-relay-model").detection_method == "default"

    class HeaderResponse:
        headers = {"x-served-model": "claude-opus-4"}

        def json(self) -> dict[str, Any]:
            raise AssertionError("streaming model detection must not consume response body")

    openai_compat._detected_models.clear()
    detected = openai_compat.detect_and_cache_model(
        "https://relay.example.test/v1",
        "test-key-new-114",
        HeaderResponse(),
        allow_body=False,
    )

    assert detected == "claude-opus-4"


def test_new_114_read_before_write_guard_and_session_reset(tmp_path: Path) -> None:
    workspace_state.clear_read_tracking()
    target = tmp_path / "module.py"

    assert workspace_state.has_file_been_read(str(tmp_path), "module.py") is False
    assert workspace_state.has_file_been_read(str(tmp_path), "module.py") is True

    workspace_state.clear_read_tracking()
    workspace_state.record_file_read(str(tmp_path), str(target))
    assert workspace_state.has_file_been_read(str(tmp_path), "module.py") is True

    RuntimeState().activate_session("next-session")
    assert workspace_state.has_file_been_read(str(tmp_path), "module.py") is False


def test_new_114_file_modification_hook_appends_verification_reminder() -> None:
    modified = post_tool_hook("write_file", {"file_path": "x.py"}, "write ok")
    read_only = post_tool_hook("read_file", {"file_path": "x.py"}, "read ok")

    assert "Principle #5 reminder" in modified
    assert read_only == "read ok"

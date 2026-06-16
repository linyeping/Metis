from __future__ import annotations

from backend.runtime.context_control import (
    COMPACT_MARKER,
    build_compact_state_v2,
    compact_state_after_truncate,
    model_context_for_history,
    normalize_compact_state,
)


def test_v1_compact_state_renders_unchanged() -> None:
    history = [
        {"id": "m1", "role": "user", "content": "old"},
        {"id": "m2", "role": "assistant", "content": "tail"},
    ]
    state = {
        "summary": "[Context Summary]\nold facts",
        "boundary_message_id": "m2",
        "boundary_index": 1,
        "compact_count": 1,
    }

    normalized = normalize_compact_state(state)
    messages = model_context_for_history(history, normalized)

    assert normalized["version"] == 1
    assert messages[0]["content"] == state["summary"]
    assert messages[1]["id"] == "m2"


def test_build_compact_state_v2_extracts_sections_and_tool_evidence() -> None:
    history = [
        {"id": "m1", "role": "user", "content": "Please fix the login overflow and keep replies concise."},
        {"id": "m2", "role": "assistant", "content": "Plan: inspect the chat layout and patch CSS."},
        {
            "id": "m3",
            "role": "assistant",
            "content": "",
            "metis_kind": "tool",
            "metis_tool": {
                "name": "robust_replace_in_file",
                "arguments": {"path": "desktop/src/index.css"},
                "result": "Modified: desktop/src/index.css",
                "status": "success",
            },
        },
        {"id": "m4", "role": "assistant", "content": "Implemented the CSS fix."},
        {"id": "m5", "role": "user", "content": "Now compact context."},
        {"id": "m6", "role": "assistant", "content": "Ready."},
    ]

    state = build_compact_state_v2(history, summary="[Context Summary]\nlogin layout fixed", keep_recent=2)
    context = model_context_for_history(history, state)

    assert state["version"] == 2
    assert state["boundary_message_id"] == "m5"
    assert state["sections"]["changed_files"] == ["desktop/src/index.css"]
    assert any("robust_replace_in_file" in item for item in state["sections"]["tool_evidence"])
    assert state["preserved_message_ids"] == ["m5", "m6"]
    assert context[0]["content"].startswith(COMPACT_MARKER)
    assert "## Tool Evidence" in context[0]["content"]
    assert [message["id"] for message in context[1:]] == ["m5", "m6"]


def test_compact_state_after_truncate_preserves_v2_when_boundary_survives() -> None:
    history = [
        {"id": "m1", "role": "user", "content": "one"},
        {"id": "m2", "role": "assistant", "content": "two"},
        {"id": "m3", "role": "user", "content": "three"},
        {"id": "m4", "role": "assistant", "content": "four"},
    ]
    state = build_compact_state_v2(history, summary="[Context Summary]\nold", keep_recent=2)

    truncated = compact_state_after_truncate(history, history[:3], state)

    assert truncated["version"] == 2
    assert truncated["boundary_message_id"] == "m3"
    assert truncated["preserved_message_ids"] == ["m3"]

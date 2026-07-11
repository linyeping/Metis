from __future__ import annotations

from backend.runtime.context_control import (
    COMPACT_MARKER,
    USER_INTENT_LEDGER_MARKER,
    build_compact_state_v2,
    compact_state_after_truncate,
    model_context_for_history,
    normalize_compact_state,
    recent_turn_boundary,
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
        {"id": "m5", "role": "user", "content": "Do not change the color palette."},
        {"id": "m6", "role": "assistant", "content": "Understood."},
        {"id": "m7", "role": "user", "content": "Now compact context."},
        {"id": "m8", "role": "assistant", "content": "Ready."},
    ]

    state = build_compact_state_v2(history, summary="[Context Summary]\nlogin layout fixed", keep_recent=2)
    context = model_context_for_history(history, state)

    assert state["version"] == 2
    assert state["boundary_message_id"] == "m5"
    assert state["sections"]["changed_files"] == ["desktop/src/index.css"]
    assert any("robust_replace_in_file" in item for item in state["sections"]["tool_evidence"])
    assert state["preserved_message_ids"] == ["m5", "m6", "m7", "m8"]
    assert state["preserved_user_message_ids"] == ["m1"]
    assert context[0]["content"].startswith(COMPACT_MARKER)
    assert "## Tool Evidence" in context[0]["content"]
    assert context[1]["content"].startswith(USER_INTENT_LEDGER_MARKER)
    assert [message["id"] for message in context if message.get("id")] == ["m1", "m5", "m6", "m7", "m8"]
    assert all(message.get("id") != "m3" for message in context)


def test_compact_state_after_truncate_preserves_v2_when_boundary_survives() -> None:
    history = [
        {"id": "m1", "role": "user", "content": "one"},
        {"id": "m2", "role": "assistant", "content": "two"},
        {"id": "m3", "role": "user", "content": "three"},
        {"id": "m4", "role": "assistant", "content": "four"},
        {"id": "m5", "role": "user", "content": "five"},
        {"id": "m6", "role": "assistant", "content": "six"},
    ]
    state = build_compact_state_v2(history, summary="[Context Summary]\nold", keep_recent=2)

    truncated = compact_state_after_truncate(history, history[:5], state)

    assert truncated["version"] == 2
    assert truncated["boundary_message_id"] == "m3"
    assert truncated["preserved_message_ids"] == ["m3", "m4", "m5"]
    assert truncated["preserved_user_message_ids"] == ["m1"]


def test_recent_turn_boundary_ignores_transcript_tool_cards() -> None:
    history = [
        {"id": "m1", "role": "user", "content": "first"},
        {
            "id": "tool-1",
            "role": "assistant",
            "content": "",
            "metis_kind": "tool",
            "metis_tool": {"name": "read_file", "result": "large", "status": "success"},
        },
        {"id": "m2", "role": "assistant", "content": "done"},
        {"id": "m3", "role": "user", "content": "second"},
        {
            "id": "tool-2",
            "role": "assistant",
            "content": "",
            "metis_kind": "tool",
            "metis_tool": {"name": "run_tests", "result": "ok", "status": "success"},
        },
        {"id": "m4", "role": "assistant", "content": "done again"},
        {"id": "m5", "role": "user", "content": "third"},
    ]

    assert recent_turn_boundary(history, keep_recent_turns=2) == 3


def test_recent_turn_boundary_defaults_to_six_user_turns() -> None:
    history = [
        message
        for turn in range(8)
        for message in (
            {"id": f"u{turn + 1}", "role": "user", "content": f"user-{turn + 1}"},
            {"id": f"a{turn + 1}", "role": "assistant", "content": f"assistant-{turn + 1}"},
        )
    ]

    assert recent_turn_boundary(history) == 4
    state = build_compact_state_v2(history, summary="执行摘要")
    assert state["boundary_message_id"] == "u3"
    assert state["preserved_message_ids"] == [message["id"] for message in history[4:]]


def test_transcript_tool_card_becomes_compact_recoverable_evidence() -> None:
    history = [
        {"id": "u1", "role": "user", "content": "运行测试"},
        {
            "id": "tool-1",
            "role": "assistant",
            "content": "",
            "metis_kind": "tool",
            "metis_tool": {
                "name": "run_tests",
                "status": "success",
                "summary": "测试通过",
                "result": (
                    "short preview\n"
                    "[Full tool result archived: .metis/artifacts/tool-results/s/run_tests-a.txt | "
                    "sha256=abc | chars=9000. Read this file when exact omitted output is needed.]"
                ),
            },
        },
        {"id": "a1", "role": "assistant", "content": "测试完成"},
    ]

    context = model_context_for_history(history, {})

    assert len(context) == 3
    assert context[1]["role"] == "assistant"
    assert "Persisted tool evidence" in context[1]["content"]
    assert "tool-results/s/run_tests-a.txt" in context[1]["content"]
    assert "short preview" not in context[1]["content"]


def test_compaction_replays_intermediate_user_corrections_verbatim() -> None:
    history = [
        {"id": "u1", "role": "user", "content": "修改登录页。"},
        {"id": "a1", "role": "assistant", "content": "开始。"},
        {"id": "u2", "role": "user", "content": "不要改整体配色，保留淡入淡出。"},
        {"id": "a2", "role": "assistant", "content": "收到。"},
        {"id": "u3", "role": "user", "content": "窄窗口只显示聊天区。"},
        {"id": "a3", "role": "assistant", "content": "完成。"},
        {"id": "u4", "role": "user", "content": "继续下一步。"},
    ]
    state = build_compact_state_v2(history, summary="执行摘要", keep_recent=2)

    context = model_context_for_history(history, state)
    replayed = [message["content"] for message in context if message.get("role") == "user"]

    assert replayed == [
        "修改登录页。",
        "不要改整体配色，保留淡入淡出。",
        "窄窗口只显示聊天区。",
        "继续下一步。",
    ]

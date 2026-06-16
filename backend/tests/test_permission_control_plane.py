from __future__ import annotations

from backend.runtime.permission_control import (
    evaluate_permission,
    evaluate_rule_layer,
    is_dangerous_allow_rule,
    permission_control_payload,
)


def test_hard_deny_beats_saved_allow_rule() -> None:
    decision = evaluate_rule_layer(
        tool_name="write_file",
        arguments={"path": "E:/outside.txt"},
        rules=[{"tool": "*", "action": "allow"}],
        hard_deny_reason="path outside workspace",
    )

    assert decision.action == "deny"
    assert decision.source == "hard_deny"
    assert decision.hard_denied is True


def test_deny_rules_take_precedence_over_broad_allow() -> None:
    decision = evaluate_rule_layer(
        tool_name="write_file",
        arguments={"path": "notes.md"},
        rules=[
            {"id": "allow-all", "tool": "*", "action": "allow", "updated_at": 20},
            {"id": "deny-write", "tool": "write_file", "action": "deny", "updated_at": 10},
        ],
    )

    assert decision.action == "deny"
    assert decision.rule_id == "deny-write"


def test_autoguard_skips_dangerous_broad_allow_rules() -> None:
    rule = {"id": "shell-all", "tool": "execute_bash_command", "action": "allow"}

    assert is_dangerous_allow_rule(rule) is True
    decision = evaluate_permission(
        mode="auto_guard",
        tool_name="execute_bash_command",
        arguments={"command": "Remove-Item important.txt"},
        rules=[rule],
        registry_requires_approval=False,
    )

    assert decision.action == "ask"
    assert decision.source == "auto_guard"
    assert decision.skipped_rule_ids == ["shell-all"]


def test_control_plane_payload_reports_dangerous_allow_rules() -> None:
    payload = permission_control_payload(
        mode="auto_guard",
        rules=[
            {"id": "read-ok", "tool": "read_file", "action": "allow", "args_match": {"path": "*.md"}},
            {"id": "shell-all", "tool": "execute_bash_command", "action": "allow"},
        ],
    )

    assert payload["version"] == "v2"
    assert payload["mode"] == "auto_guard"
    assert payload["dangerous_allow_count"] == 1
    assert payload["dangerous_allow_rules"][0]["id"] == "shell-all"

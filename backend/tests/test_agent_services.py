from __future__ import annotations

from backend.runtime.agent_services import (
    build_agent_runtime_profile,
    build_coordinator_board,
    build_tool_contract,
    build_worker_prompt,
    build_verification_agent_report,
    classify_autoguard,
    explain_permission,
    generate_away_summary,
    generate_prompt_suggestions,
    generate_session_title,
    generate_tool_label,
    should_auto_title,
    verification_command_policy,
)
from backend.runtime.context_control import build_compact_state_v2, model_context_for_history


def _history(count: int = 8):
    roles = ["user", "assistant"] * ((count + 1) // 2)
    return [
        {"id": f"m{index + 1}", "role": roles[index], "content": f"message {index + 1}"}
        for index in range(count)
    ]


def test_permission_explainer_includes_risk_and_autoguard() -> None:
    payload = explain_permission(
        tool_name="write_file",
        arguments={"path": "C:/Users/me/Desktop/report.docx"},
        decision={"reason": "Tool registry approval metadata was applied.", "mode": "auto_guard"},
        path_safety={
            "allowed": False,
            "code": "PATH_OUTSIDE_WORKSPACE",
            "path": "C:/Users/me/Desktop/report.docx",
            "suggested_root": "C:/Users/me/Desktop",
            "outside_workspace": True,
        },
        workspace_root="D:/project",
    )

    assert payload["riskLevel"] == "HIGH"
    assert payload["autoguard"]["shouldBlock"] is True
    assert "outside" in payload["autoguard"]["reason"]


def test_autoguard_blocks_destructive_shell_only_in_guarded_mode() -> None:
    guarded = classify_autoguard(
        tool_name="execute_bash_command",
        arguments={"command": "Remove-Item -Recurse C:/tmp/demo"},
        mode="auto_guard",
    )
    auto = classify_autoguard(
        tool_name="execute_bash_command",
        arguments={"command": "Remove-Item -Recurse C:/tmp/demo"},
        mode="auto",
    )

    assert guarded["shouldBlock"] is True
    assert guarded["riskLevel"] == "HIGH"
    assert auto["shouldBlock"] is False


def test_tool_label_title_summary_suggestions_and_verifier_report() -> None:
    history = [
        {"role": "user", "content": "帮我修复 Preview Browser 的登录按钮"},
        {"role": "assistant", "content": "已修改代码。下一步运行测试。"},
    ]

    assert generate_tool_label("read_file", {"path": "backend/web/app.py"}) == "Read app.py"
    assert generate_session_title(history).startswith("修复 Preview")
    assert "下一步" in generate_away_summary(history)
    assert generate_prompt_suggestions(history)[:2] == ["跑完整测试", "看一下 diff"]

    report = build_verification_agent_report(
        task="Fix login",
        changed_files=["desktop/src/App.tsx"],
        checks=[{"name": "typecheck", "result": "pass"}],
    )
    assert report["mode"] == "verification_only"
    assert report["verdict"] == "PASS"


def test_session_title_placeholders_are_auto_titled() -> None:
    assert should_auto_title("新会话") is True
    assert should_auto_title("Chat 2026-06-30 01:23") is True
    assert should_auto_title("Metis Chat") is True
    assert should_auto_title("修复登录按钮") is False


def test_verification_command_policy_blocks_write_like_commands() -> None:
    assert verification_command_policy("python -m pytest backend/tests/test_agent_services.py")["allowed"] is True
    assert verification_command_policy("git commit -m test")["allowed"] is False
    assert verification_command_policy("python -m pytest && git add .")["allowed"] is False


def test_compact_v2_modes_shape_model_context() -> None:
    history = _history(8)

    older_state = build_compact_state_v2(history, summary="older summary", keep_recent=3, mode="partial_older")
    older_context = model_context_for_history(history, older_state)
    assert older_context[0]["role"] == "system"
    assert [message["id"] for message in older_context[1:]] == ["m6", "m7", "m8"]

    recent_state = build_compact_state_v2(
        history,
        summary="recent summary",
        mode="partial_recent",
        boundary_index=5,
    )
    recent_context = model_context_for_history(history, recent_state)
    assert [message["id"] for message in recent_context[:-1]] == ["m1", "m2", "m3", "m4", "m5"]
    assert recent_context[-1]["role"] == "system"

    full_state = build_compact_state_v2(history, summary="full summary", mode="full")
    full_context = model_context_for_history(history, full_state)
    assert len(full_context) == 1
    assert full_context[0]["role"] == "system"


def test_prompt_runtime_profile_tool_contract_and_workers(tmp_path) -> None:
    history = [
        {"role": "user", "content": "请修一下登录按钮，后台跑完测试"},
        {
            "role": "assistant",
            "content": "",
            "metis_kind": "tool",
            "metis_tool": {
                "name": "read_file",
                "arguments": {"path": "desktop/src/App.tsx"},
                "result": "ok",
                "status": "success",
            },
        },
        {
            "role": "assistant",
            "content": "",
            "metis_kind": "tool",
            "metis_tool": {
                "name": "robust_replace_in_file",
                "arguments": {"path": "desktop/src/App.tsx"},
                "result": "Modified: desktop/src/App.tsx",
                "status": "success",
            },
        },
    ]

    profile = build_agent_runtime_profile(
        history=history,
        workspace_root=str(tmp_path),
        session_id="s1",
        mode="auto_guard",
        model="deepseek-chat",
        compact_state={"mode": "partial_older", "compact_count": 2, "summary": "old"},
    )

    assert profile["promptRuntime"]["cachePolicy"] == "stable-prefix-plus-session-suffix"
    assert "scratch" in profile["promptRuntime"]["scratchpadPath"]
    assert profile["promptRuntime"]["compactCount"] == 2
    assert profile["toolContracts"]["version"] == "tool-contract-v3"
    assert profile["coordinator"]["freshVerifier"]["enabled"] is True
    assert profile["proactive"]["enabled"] is True

    contract = build_tool_contract("execute_bash_command", {"command": "python -m pytest"}, mode="auto_guard")
    assert contract["category"] == "shell"
    assert contract["riskLevel"] == "MEDIUM"
    assert contract["readBeforeEdit"] is True
    assert "dedicated" in contract["saferAlternative"]

    prompt = build_worker_prompt(
        goal="Fix login button",
        worker_type="verification",
        workspace_root=str(tmp_path),
        files=["desktop/src/App.tsx"],
    )
    assert "Worker type: verification" in prompt
    assert "desktop/src/App.tsx" in prompt
    assert "self-contained" in prompt


def test_coordinator_board_detects_research_implementation_and_verification() -> None:
    history = [
        {
            "role": "assistant",
            "metis_kind": "tool",
            "metis_tool": {"name": "grep_search", "status": "success", "result": "ok"},
        },
        {
            "role": "assistant",
            "metis_kind": "tool",
            "metis_tool": {"name": "write_file", "status": "success", "result": "ok"},
        },
        {
            "role": "assistant",
            "metis_kind": "tool",
            "metis_tool": {"name": "run_tests", "status": "error", "result": "failed"},
        },
    ]

    board = build_coordinator_board(history)
    workers = {item["id"]: item for item in board["workers"]}
    assert workers["research"]["status"] == "done"
    assert workers["implementation"]["status"] == "done"
    assert workers["verification"]["status"] == "error"
    assert "retry" in board["nextAction"].lower()

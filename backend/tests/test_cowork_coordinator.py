from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, List

import pytest
from backend.core.paths import clear_metis_home_cache
from backend.runtime import cowork_coordinator
from backend.runtime.agent_loop import AgentConfig, ContentEvent, DoneEvent, ToolResultEvent
from backend.runtime.artifact_registry import ArtifactFilters, list_artifacts
from backend.runtime.cancellation import OperationCancelled
from backend.runtime.cowork_coordinator import iter_local_cowork_events
from backend.runtime.worktree_manager import WorktreeRecord


@pytest.fixture(autouse=True)
def isolated_metis_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("METIS_HOME", str(tmp_path / "metis-home"))
    clear_metis_home_cache()
    yield
    clear_metis_home_cache()


def test_local_cowork_propagates_cancel_before_subrun(tmp_path: Path) -> None:
    events = iter_local_cowork_events(
        "Inspect implementation",
        workspace_root=str(tmp_path),
        run_id="run_cancel",
        session_id="session_cancel",
        cancelled=lambda: True,
    )

    first = next(events)

    assert first["kind"] == "runtime_status"
    with pytest.raises(OperationCancelled):
        next(events)


def test_bounded_llm_planner_outputs_complete_subrun_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    def fake_planner(**_: Any) -> str:
        return json.dumps(
            {
                "subruns": [
                    {
                        "title": "Inspect API shape",
                        "objective": "Map current run and artifact APIs.",
                        "inputs": ["backend/web/app.py", "backend/runtime/artifact_registry.py"],
                        "expected_artifacts": ["API gap report"],
                        "acceptance_criteria": ["Report lists routes and missing lifecycle fields."],
                        "execution_profile": "local_worktree",
                        "dependencies": [],
                    },
                    {
                        "title": "Run validation",
                        "objective": "Execute focused smoke validation in the runtime.",
                        "inputs": ["Subrun 1 report"],
                        "expected_artifacts": ["stdout evidence", "diff summary"],
                        "acceptance_criteria": ["Validation command output is attached."],
                        "execution_profile": "local_vm",
                        "dependencies": ["1"],
                    },
                ]
            }
        )

    monkeypatch.setattr(cowork_coordinator, "_call_bounded_planner_model", fake_planner)

    plan = cowork_coordinator.build_cowork_plan(
        "Review APIs and validate",
        run_id="run_plan",
        session_id="session_plan",
        max_subruns=3,
        execution_profile="local_vm",
        base_config=AgentConfig(llm_backend="fake", llm_model="planner-test"),
        workspace_root=str(tmp_path),
    )

    assert plan["schema"] == cowork_coordinator.COWORK_PLAN_SCHEMA
    assert plan["version"] == cowork_coordinator.COWORK_PLAN_VERSION
    assert plan["planner"]["mode"] == "llm_bounded"
    assert len(plan["subruns"]) == 2
    first, second = plan["subruns"]
    for subrun in plan["subruns"]:
        assert subrun["subrun_id"].startswith("subrun_")
        assert subrun["objective"]
        assert subrun["inputs"]
        assert subrun["expected_artifacts"]
        assert subrun["acceptance_criteria"]
        assert "Objective:" in subrun["prompt"]
        assert "Acceptance criteria:" in subrun["prompt"]
    assert first["dependencies"] == []
    assert second["dependencies"] == [first["subrun_id"]]
    assert second["execution_profile"] == "local_vm"


def test_bounded_llm_planner_falls_back_with_complete_fields(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(cowork_coordinator, "_call_bounded_planner_model", lambda **_: "not json")

    plan = cowork_coordinator.build_cowork_plan(
        "Create a docx report\nValidate output",
        run_id="run_fallback",
        session_id="session_fallback",
        max_subruns=2,
        execution_profile="local_worktree",
        base_config=AgentConfig(llm_backend="fake", llm_model="planner-test"),
        workspace_root=str(tmp_path),
    )

    assert plan["planner"]["mode"] == "deterministic_fallback"
    assert "fallback_reason" in plan["planner"]
    assert len(plan["subruns"]) == 2
    assert all(subrun["execution_profile"] == "local_worktree" for subrun in plan["subruns"])
    assert all(subrun["objective"] for subrun in plan["subruns"])
    assert all(subrun["inputs"] for subrun in plan["subruns"])
    assert all(subrun["expected_artifacts"] for subrun in plan["subruns"])
    assert all(subrun["acceptance_criteria"] for subrun in plan["subruns"])


def test_deterministic_fallback_uses_numbered_subruns_and_profile_hints(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(cowork_coordinator, "_call_bounded_planner_model", lambda **_: (_ for _ in ()).throw(TimeoutError("planner timeout")))

    goal = """用 Cowork 做一个本地并行验证任务，必须拆成 3 个 subrun：

1. fast-artifact：在 workspace 里创建 reports/cowork-smoke-fast.md，内容包含 FAST_ARTIFACT_OK，并输出 FAST_DONE。
2. slow-local-vm：使用 local_vm 执行一个长任务：先输出 SLOW_STARTED，然后 sleep 120 秒，最后创建 reports/cowork-smoke-slow.md，内容包含 SLOW_ARTIFACT_OK，并输出 SLOW_DONE。
3. dependent-summary：依赖 fast-artifact 和 slow-local-vm，读取两个报告文件，生成 reports/cowork-smoke-summary.md，内容必须包含 FAST_ARTIFACT_OK、SLOW_ARTIFACT_OK、SUMMARY_OK。

要求：
- 每个 subrun 都必须有 evidence：stdout/test、diff、artifact 或失败原因。
- slow-local-vm 必须走 local_vm，不要走 local_direct。
- fast-artifact 和 dependent-summary 可以走 local_worktree。
"""

    plan = cowork_coordinator.build_cowork_plan(
        goal,
        run_id="run_numbered_fallback",
        session_id="session_numbered_fallback",
        max_subruns=3,
        execution_profile="local_worktree",
        base_config=AgentConfig(llm_backend="fake", llm_model="planner-test"),
        workspace_root=str(tmp_path),
    )

    assert plan["planner"]["mode"] == "deterministic_fallback"
    titles = [subrun["title"] for subrun in plan["subruns"]]
    assert len(titles) == 3
    assert "必须拆成" not in titles[0]
    assert "fast-artifact" in titles[0]
    assert "slow-local-vm" in titles[1]
    assert "dependent-summary" in titles[2]
    assert [subrun["execution_profile"] for subrun in plan["subruns"]] == [
        "local_worktree",
        "local_vm",
        "local_worktree",
    ]
    assert plan["subruns"][2]["dependencies"] == [
        plan["subruns"][0]["subrun_id"],
        plan["subruns"][1]["subrun_id"],
    ]
    vm_tasks = plan["subruns"][1]["vm_tasks"]
    assert vm_tasks
    assert "SLOW_STARTED" in vm_tasks[0]["command"]
    assert "SLOW_DONE" in vm_tasks[0]["command"]
    assert vm_tasks[0]["artifact_patterns"] == ["reports/cowork-smoke-slow.md"]


def test_cowork_summary_text_returns_user_facing_answer_from_report(tmp_path: Path) -> None:
    worktree = tmp_path / "cowork-1"
    report = worktree / "output" / "cowork" / "answer.md"
    report.parent.mkdir(parents=True)
    report.write_text(
        "# 任务意义说明报告\n\n"
        "## 结论摘要\n\n"
        "这段不应该优先于最终回答。\n\n"
        "## 最终回答\n\n"
        "这个任务的意义在于：把分散上下文整理成可执行判断，让用户知道为什么继续做、做完有什么价值。\n",
        encoding="utf-8",
    )
    summary = {
        "goal": "这个任务的意义是什么",
        "subruns": [
            {
                "title": "说明任务意义",
                "status": "succeeded",
                "worktree_workspace_root": str(worktree),
                "diff": {"status": " M .agent_todos.json\n?? output/cowork/answer.md"},
                "agent": {"final_text": "已完成该子任务的中文说明报告。"},
            }
        ],
        "artifact": {"artifact_id": "art_summary"},
    }

    text = cowork_coordinator._cowork_summary_text(summary)

    assert "这个任务的意义在于" in text
    assert "为什么继续做" in text
    assert "Cowork local run complete" not in text
    assert "art_summary" not in text


def test_simple_question_fallback_uses_direct_answer_contract(tmp_path: Path) -> None:
    plan = cowork_coordinator.build_cowork_plan(
        "这个任务的意义是什么",
        run_id="run_question",
        session_id="session_question",
        max_subruns=3,
        execution_profile="local_worktree",
        workspace_root=str(tmp_path),
    )

    assert len(plan["subruns"]) == 1
    subrun = plan["subruns"][0]
    assert subrun["title"] == "直接回答用户问题"
    assert subrun["expected_artifacts"] == ["Natural final answer"]
    assert "Do not create a report" in subrun["prompt"]
    assert "Inspect current implementation" not in subrun["title"]


def test_cowork_start_direct_answer_skips_subruns(tmp_path: Path) -> None:
    events = list(
        iter_local_cowork_events(
            "这个任务的意义是什么",
            workspace_root=str(tmp_path),
            run_id="run_direct",
            session_id="session_direct",
            max_subruns=3,
        )
    )

    kinds = [event["kind"] for event in events]
    status = events[0]["payload"]
    content = next(event for event in events if event["kind"] == "content")["payload"]["text"]

    assert status["details"]["schema"] == cowork_coordinator.COWORK_START_DECISION_SCHEMA
    assert status["details"]["decision"]["mode"] == "direct_answer"
    assert "subrun_planned" not in kinds
    assert "subrun_running" not in kinds
    assert "artifact_created" not in kinds
    assert "意义" in content
    assert events[-1]["payload"]["tool_calls"] == 0


def test_cowork_start_clarifies_underspecified_goal(tmp_path: Path) -> None:
    events = list(
        iter_local_cowork_events(
            "做一下",
            workspace_root=str(tmp_path),
            run_id="run_clarify",
            session_id="session_clarify",
            max_subruns=3,
        )
    )

    kinds = [event["kind"] for event in events]
    status = events[0]["payload"]
    content = next(event for event in events if event["kind"] == "content")["payload"]["text"]

    assert status["details"]["decision"]["mode"] == "clarify"
    assert "subrun_planned" not in kinds
    assert "你想让我具体处理什么" in content
    assert events[-1]["payload"]["tool_calls"] == 0


def test_cowork_start_routes_project_questions_to_subruns() -> None:
    project_decision = cowork_coordinator.decide_cowork_start("分析这个项目的架构是什么")
    definition_decision = cowork_coordinator.decide_cowork_start("什么是单元测试？")

    assert project_decision["mode"] == "cowork_plan"
    assert project_decision["reason"] == "project_analysis_or_workspace_action_required"
    assert definition_decision["mode"] == "direct_answer"


def test_cowork_summary_text_does_not_dump_report_without_final_answer(tmp_path: Path) -> None:
    worktree = tmp_path / "cowork-report"
    report = worktree / "output" / "cowork" / "report.md"
    report.parent.mkdir(parents=True)
    report.write_text(
        "# 任务说明报告\n\n"
        "## 变更摘要\n\n"
        "- changed backend/runtime/cowork_coordinator.py\n\n"
        "## 验证情况\n\n"
        "- pytest passed\n",
        encoding="utf-8",
    )
    summary = {
        "goal": "这个任务的意义是什么",
        "subruns": [
            {
                "title": "说明任务意义",
                "status": "succeeded",
                "worktree_workspace_root": str(worktree),
                "diff": {"status": "?? output/cowork/report.md"},
                "agent": {"final_text": "已完成该子任务的中文说明报告。"},
            }
        ],
        "artifact": {"artifact_id": "art_summary"},
    }

    text = cowork_coordinator._cowork_summary_text(summary)

    assert "changed backend/runtime/cowork_coordinator.py" not in text
    assert "pytest passed" not in text
    assert "没有形成可直接展示的自然回答" in text
    assert "art_summary" not in text


def test_answer_focused_subrun_succeeds_without_diff_or_artifact(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    worktree = tmp_path / "worktree"
    source.mkdir()
    worktree.mkdir()
    plan = {
        "schema": cowork_coordinator.COWORK_PLAN_SCHEMA,
        "version": cowork_coordinator.COWORK_PLAN_VERSION,
        "run_id": "run_answer",
        "session_id": "session_answer",
        "goal": "这个任务的意义是什么",
        "status": "planned",
        "created_at": time.time(),
        "subruns": [
            {
                "subrun_id": "subrun_answer",
                "title": "直接回答用户问题",
                "objective": "Answer the user's question.",
                "inputs": ["Parent Cowork goal"],
                "expected_artifacts": ["Natural final answer"],
                "acceptance_criteria": ["Final answer directly addresses the user's question."],
                "dependencies": [],
                "prompt": "Finish with 最终回答 only.",
                "execution_profile": "local_worktree",
            }
        ],
        "planner": {"mode": "test"},
    }
    record = WorktreeRecord(
        worktree_id="wt_answer",
        workspace_root=str(source),
        repo_root=str(source),
        worktree_path=str(worktree),
        worktree_workspace_root=str(worktree),
        branch="metis/run/cowork-answer",
        base_ref="HEAD",
        run_id="run_answer",
        session_id="session_answer",
        label="cowork-1",
    )

    def fake_create_worktree(workspace_root: str, *, run_id: str = "", session_id: str = "", label: str = "") -> WorktreeRecord:
        assert workspace_root == str(source)
        return record

    def fake_diff_worktree(workspace_root: str, worktree_id: str, *, max_chars: int = 200000) -> Dict[str, Any]:
        return {
            "schema": "metis.worktree_diff.v1",
            "worktree": record.to_dict(),
            "status": "",
            "stat": "",
            "patch": "",
            "truncated": False,
            "base_ref": "HEAD",
        }

    def fake_run_agent_loop(messages: List[Dict[str, Any]], config: AgentConfig):
        assert config.enabled_tools == [
            "read_file",
            "read_multiple_files",
            "list_directory",
            "glob_search",
            "grep_search",
            "semantic_search",
        ]
        yield ContentEvent(text="## 最终回答\n\n这个任务的意义在于帮助用户把目标、价值和下一步判断说清楚。")
        yield DoneEvent(total_turns=1, total_tool_calls=0, total_tokens=8)

    monkeypatch.setattr(cowork_coordinator, "build_cowork_plan", lambda *_args, **_kwargs: plan)
    monkeypatch.setattr(cowork_coordinator, "create_worktree", fake_create_worktree)
    monkeypatch.setattr(cowork_coordinator, "diff_worktree", fake_diff_worktree)
    monkeypatch.setattr(cowork_coordinator, "run_agent_loop", fake_run_agent_loop)

    events = list(
        iter_local_cowork_events(
            "Run answer focused subrun with the supplied plan",
            workspace_root=str(source),
            run_id="run_answer",
            session_id="session_answer",
            max_subruns=1,
            base_config=AgentConfig(llm_backend="fake", llm_model="fake", max_turns=4),
        )
    )

    done = next(event for event in events if event["kind"] == "subrun_succeeded")
    evidence = done["payload"]["result"]["evidence"]
    content = next(event for event in events if event["kind"] == "content")["payload"]["text"]

    assert evidence["success_evidence"] is True
    assert evidence["counts"]["answer"] == 1
    assert evidence["counts"]["diff"] == 0
    assert "这个任务的意义在于" in content
    assert "subrun" not in content.lower()
    assert "artifact" not in content.lower()


def test_local_cowork_runs_restricted_agent_inside_subrun_worktree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    worktree = tmp_path / "worktree"
    source.mkdir()
    worktree.mkdir()
    seen: Dict[str, Any] = {}

    record = WorktreeRecord(
        worktree_id="wt_agent",
        workspace_root=str(source),
        repo_root=str(source),
        worktree_path=str(worktree),
        worktree_workspace_root=str(worktree),
        branch="metis/run/cowork-agent",
        base_ref="HEAD",
        run_id="run_agent",
        session_id="session_agent",
        label="cowork-1",
    )

    def fake_create_worktree(workspace_root: str, *, run_id: str = "", session_id: str = "", label: str = "") -> WorktreeRecord:
        assert workspace_root == str(source)
        return record

    def fake_diff_worktree(workspace_root: str, worktree_id: str, *, max_chars: int = 200000) -> Dict[str, Any]:
        assert workspace_root == str(source)
        assert worktree_id == "wt_agent"
        return {
            "schema": "metis.worktree_diff.v1",
            "worktree": record.to_dict(),
            "status": " M report.md\n",
            "stat": "report.md | 1 +\n",
            "patch": "diff --git a/report.md b/report.md\n+agent subrun complete\n",
            "truncated": False,
            "base_ref": "HEAD",
        }

    def fake_run_agent_loop(messages: List[Dict[str, Any]], config: AgentConfig):
        seen["messages"] = messages
        seen["workspace_root"] = config.workspace_root
        seen["source_workspace_root"] = config.source_workspace_root
        seen["worktree_id"] = config.worktree_id
        seen["enabled_tools"] = list(config.enabled_tools)
        seen["execution_mode"] = config.execution_mode
        seen["surface_mode"] = config.surface_mode
        yield ContentEvent(text="agent subrun complete")
        yield DoneEvent(total_turns=1, total_tool_calls=0, total_tokens=3)

    monkeypatch.setattr(cowork_coordinator, "create_worktree", fake_create_worktree)
    monkeypatch.setattr(cowork_coordinator, "diff_worktree", fake_diff_worktree)
    monkeypatch.setattr(cowork_coordinator, "run_agent_loop", fake_run_agent_loop)

    events = list(
        iter_local_cowork_events(
            "Create a docx report",
            workspace_root=str(source),
            run_id="run_agent",
            session_id="session_agent",
            max_subruns=1,
            base_config=AgentConfig(llm_backend="fake", llm_model="fake", max_turns=4, system_prompt="base"),
        )
    )

    assert seen["workspace_root"] == str(worktree)
    assert seen["source_workspace_root"] == str(source)
    assert seen["worktree_id"] == "wt_agent"
    assert seen["execution_mode"] == "auto"
    assert seen["surface_mode"] == "cowork"
    assert "write_file" in seen["enabled_tools"]
    assert "docx_create" in seen["enabled_tools"]
    assert "browse_web" not in seen["enabled_tools"]
    done = next(event for event in events if event["kind"] == "subrun_succeeded")
    assert done["payload"]["schema"] == cowork_coordinator.COWORK_SUBRUN_EVENT_SCHEMA
    assert done["payload"]["subrun_id"]
    assert done["payload"]["status"] == "succeeded"
    evidence = done["payload"]["evidence"]
    assert evidence["schema"] == cowork_coordinator.COWORK_SUBRUN_EVIDENCE_SCHEMA
    assert evidence["success_evidence"] is True
    assert evidence["counts"]["diff"] == 1
    agent = done["payload"]["result"]["agent"]
    assert agent["ok"] is True
    assert agent["final_text"] == "agent subrun complete"
    assert agent["workspace_root"] == str(worktree)
    assert "xlsx_create" in seen["enabled_tools"]
    assert "pptx_create" in seen["enabled_tools"]


def test_local_cowork_fails_subrun_without_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    worktree = tmp_path / "worktree"
    source.mkdir()
    worktree.mkdir()
    record = WorktreeRecord(
        worktree_id="wt_empty",
        workspace_root=str(source),
        repo_root=str(source),
        worktree_path=str(worktree),
        worktree_workspace_root=str(worktree),
        branch="metis/run/cowork-empty",
        base_ref="HEAD",
        run_id="run_empty",
        session_id="session_empty",
        label="cowork-1",
    )

    def fake_create_worktree(workspace_root: str, *, run_id: str = "", session_id: str = "", label: str = "") -> WorktreeRecord:
        assert workspace_root == str(source)
        return record

    def fake_diff_worktree(workspace_root: str, worktree_id: str, *, max_chars: int = 200000) -> Dict[str, Any]:
        return {
            "schema": "metis.worktree_diff.v1",
            "worktree": record.to_dict(),
            "status": "",
            "stat": "",
            "patch": "",
            "truncated": False,
            "base_ref": "HEAD",
        }

    monkeypatch.setattr(cowork_coordinator, "create_worktree", fake_create_worktree)
    monkeypatch.setattr(cowork_coordinator, "diff_worktree", fake_diff_worktree)

    events = list(
        iter_local_cowork_events(
            "Inspect implementation",
            workspace_root=str(source),
            run_id="run_empty",
            session_id="session_empty",
            max_subruns=1,
        )
    )

    assert not [event for event in events if event["kind"] == "subrun_succeeded"]
    failed = next(event for event in events if event["kind"] == "subrun_failed")
    evidence = failed["payload"]["result"]["evidence"]
    assert failed["payload"]["status"] == "failed"
    assert evidence["schema"] == cowork_coordinator.COWORK_SUBRUN_EVIDENCE_SCHEMA
    assert evidence["success_evidence"] is False
    assert evidence["missing_evidence"] is False
    assert evidence["counts"]["artifacts"] == 0
    assert evidence["counts"]["stdout_test"] == 0
    assert evidence["failure_reasons"][0]["code"] == "SUBRUN_MISSING_EVIDENCE"


def test_local_cowork_accepts_local_vm_stdout_as_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    worktree = tmp_path / "worktree"
    source.mkdir()
    worktree.mkdir()
    record = WorktreeRecord(
        worktree_id="wt_vm_stdout",
        workspace_root=str(source),
        repo_root=str(source),
        worktree_path=str(worktree),
        worktree_workspace_root=str(worktree),
        branch="metis/run/cowork-vm-stdout",
        base_ref="HEAD",
        run_id="run_vm_stdout",
        session_id="session_vm_stdout",
        label="cowork-1",
    )

    def fake_create_worktree(workspace_root: str, *, run_id: str = "", session_id: str = "", label: str = "") -> WorktreeRecord:
        assert workspace_root == str(source)
        return record

    def fake_diff_worktree(workspace_root: str, worktree_id: str, *, max_chars: int = 200000) -> Dict[str, Any]:
        return {
            "schema": "metis.worktree_diff.v1",
            "worktree": record.to_dict(),
            "status": "",
            "stat": "",
            "patch": "",
            "truncated": False,
            "base_ref": "HEAD",
        }

    def fake_run_local_vm_command(request: Any) -> Dict[str, Any]:
        assert str(request.workspace_root) == str(worktree)
        assert request.command
        assert "METIS_COWORK_VM_OK" not in request.command
        return {
            "ok": True,
            "runner": "local_vm",
            "backend": "metis_wsl",
            "run_id": "vm_stdout",
            "job": {
                "job_id": "job_stdout",
                "status": "done",
                "returncode": 0,
                "timed_out": False,
                "stdout": "VALIDATION_OK\n",
                "stderr": "",
                "artifacts_dir": "",
                "patch_path": "",
                "changed_files": [],
                "artifacts": [],
            },
        }

    monkeypatch.setattr(cowork_coordinator, "create_worktree", fake_create_worktree)
    monkeypatch.setattr(cowork_coordinator, "diff_worktree", fake_diff_worktree)
    monkeypatch.setattr(cowork_coordinator, "run_local_vm_command", fake_run_local_vm_command)

    events = list(
        iter_local_cowork_events(
            "Run VM check",
            workspace_root=str(source),
            run_id="run_vm_stdout",
            session_id="session_vm_stdout",
            execution_profile="local_vm",
            max_subruns=1,
        )
    )

    done = next(event for event in events if event["kind"] == "subrun_succeeded")
    evidence = done["payload"]["result"]["evidence"]
    assert evidence["success_evidence"] is True
    assert evidence["counts"]["stdout_test"] == 1
    assert evidence["stdout_test"][0]["source"] == "local_vm"
    assert "VALIDATION_OK" in evidence["stdout_test"][0]["stdout"]


def test_local_cowork_dag_scheduler_runs_independent_subruns_in_parallel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    worktrees = [tmp_path / f"worktree-{index}" for index in range(1, 4)]
    for worktree in worktrees:
        worktree.mkdir()

    plan = {
        "schema": cowork_coordinator.COWORK_PLAN_SCHEMA,
        "version": cowork_coordinator.COWORK_PLAN_VERSION,
        "run_id": "run_dag",
        "session_id": "session_dag",
        "goal": "Run independent validation branches before aggregate",
        "status": "planned",
        "created_at": time.time(),
        "subruns": [
            {
                "subrun_id": "subrun_a",
                "title": "A",
                "objective": "A",
                "inputs": ["source"],
                "expected_artifacts": ["diff"],
                "acceptance_criteria": ["diff exists"],
                "dependencies": [],
                "prompt": "A",
                "execution_profile": "local_worktree",
            },
            {
                "subrun_id": "subrun_b",
                "title": "B",
                "objective": "B",
                "inputs": ["source"],
                "expected_artifacts": ["diff"],
                "acceptance_criteria": ["diff exists"],
                "dependencies": [],
                "prompt": "B",
                "execution_profile": "local_worktree",
            },
            {
                "subrun_id": "subrun_c",
                "title": "C",
                "objective": "C",
                "inputs": ["A", "B"],
                "expected_artifacts": ["diff"],
                "acceptance_criteria": ["A and B succeeded first"],
                "dependencies": ["subrun_a", "subrun_b"],
                "prompt": "C",
                "execution_profile": "local_worktree",
            },
        ],
        "planner": {"mode": "test"},
    }
    records: Dict[str, WorktreeRecord] = {}
    lock = threading.Lock()
    active = 0
    max_active = 0
    started: Dict[str, float] = {}
    finished: Dict[str, float] = {}

    def fake_build_cowork_plan(*_args: Any, **_kwargs: Any) -> Dict[str, Any]:
        return plan

    def fake_create_worktree(workspace_root: str, *, run_id: str = "", session_id: str = "", label: str = "") -> WorktreeRecord:
        nonlocal active, max_active
        index = int(label.rsplit("-", 1)[-1])
        subrun_id = f"subrun_{chr(96 + index)}"
        with lock:
            active += 1
            max_active = max(max_active, active)
            started[subrun_id] = time.time()
        time.sleep(0.08 if index in {1, 2} else 0.01)
        record = WorktreeRecord(
            worktree_id=f"wt_{index}",
            workspace_root=str(source),
            repo_root=str(source),
            worktree_path=str(worktrees[index - 1]),
            worktree_workspace_root=str(worktrees[index - 1]),
            branch=f"metis/run/cowork-{index}",
            base_ref="HEAD",
            run_id=run_id,
            session_id=session_id,
            label=label,
        )
        records[record.worktree_id] = record
        with lock:
            active -= 1
            finished[subrun_id] = time.time()
        return record

    def fake_diff_worktree(workspace_root: str, worktree_id: str, *, max_chars: int = 200000) -> Dict[str, Any]:
        record = records[worktree_id]
        return {
            "schema": "metis.worktree_diff.v1",
            "worktree": record.to_dict(),
            "status": f" M {worktree_id}.txt\n",
            "stat": f"{worktree_id}.txt | 1 +\n",
            "patch": f"diff --git a/{worktree_id}.txt b/{worktree_id}.txt\n+done\n",
            "truncated": False,
            "base_ref": "HEAD",
        }

    monkeypatch.setattr(cowork_coordinator, "build_cowork_plan", fake_build_cowork_plan)
    monkeypatch.setattr(cowork_coordinator, "create_worktree", fake_create_worktree)
    monkeypatch.setattr(cowork_coordinator, "diff_worktree", fake_diff_worktree)

    events = list(
        iter_local_cowork_events(
            "Run DAG",
            workspace_root=str(source),
            run_id="run_dag",
            session_id="session_dag",
            max_subruns=3,
            max_parallel_subruns=2,
        )
    )

    succeeded = [event for event in events if event["kind"] == "subrun_succeeded"]
    succeeded_ids = [event["payload"]["subrun_id"] for event in succeeded]
    assert set(succeeded_ids) == {"subrun_a", "subrun_b", "subrun_c"}
    assert succeeded_ids[-1] == "subrun_c"
    assert max_active >= 2
    assert started["subrun_c"] >= finished["subrun_a"]
    assert started["subrun_c"] >= finished["subrun_b"]


def test_local_cowork_registers_artifact_tool_outputs_from_agent_subrun(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    worktree = tmp_path / "worktree"
    source.mkdir()
    worktree.mkdir()
    output = worktree / "output" / "office" / "results.xlsx"
    output.parent.mkdir(parents=True)
    try:
        from openpyxl import Workbook  # type: ignore
    except Exception:
        pytest.skip("openpyxl is not installed in this environment")
    wb = Workbook()
    ws = wb.active
    ws.append(["metric", "value"])
    ws.append(["period", 12.5])
    wb.save(str(output))

    record = WorktreeRecord(
        worktree_id="wt_artifact",
        workspace_root=str(source),
        repo_root=str(source),
        worktree_path=str(worktree),
        worktree_workspace_root=str(worktree),
        branch="metis/run/cowork-artifact",
        base_ref="HEAD",
        run_id="run_artifact",
        session_id="session_artifact",
        label="cowork-1",
    )

    def fake_create_worktree(workspace_root: str, *, run_id: str = "", session_id: str = "", label: str = "") -> WorktreeRecord:
        assert workspace_root == str(source)
        return record

    def fake_diff_worktree(workspace_root: str, worktree_id: str, *, max_chars: int = 200000) -> Dict[str, Any]:
        return {
            "schema": "metis.worktree_diff.v1",
            "worktree": record.to_dict(),
            "status": "?? output/office/results.xlsx\n",
            "stat": " output/office/results.xlsx | Bin 0 -> 17 bytes\n",
            "patch": "",
            "truncated": False,
            "base_ref": "HEAD",
        }

    def fake_run_agent_loop(messages: List[Dict[str, Any]], config: AgentConfig):
        yield ToolResultEvent(
            tool_name="xlsx_create",
            call_id="call_xlsx",
            result='{"ok": true, "output_path": "output/office/results.xlsx", "title": "Results"}',
        )
        yield DoneEvent(total_turns=1, total_tool_calls=1, total_tokens=3)

    monkeypatch.setattr(cowork_coordinator, "create_worktree", fake_create_worktree)
    monkeypatch.setattr(cowork_coordinator, "diff_worktree", fake_diff_worktree)
    monkeypatch.setattr(cowork_coordinator, "run_agent_loop", fake_run_agent_loop)

    events = list(
        iter_local_cowork_events(
            "Create an xlsx report",
            workspace_root=str(source),
            run_id="run_artifact",
            session_id="session_artifact",
            max_subruns=1,
            base_config=AgentConfig(llm_backend="fake", llm_model="fake", max_turns=4),
        )
    )

    done = next(event for event in events if event["kind"] == "subrun_succeeded")
    artifacts = done["payload"]["result"]["artifacts"]
    registered = [item for item in artifacts if item.get("source_tool_call_id") == "call_xlsx"]
    assert registered
    assert registered[0]["kind"] == "document"
    assert registered[0]["metadata"]["source"] == "cowork_tool_result"
    assert registered[0]["metadata"]["relative_path"] == "output/office/results.xlsx"
    listed = list_artifacts(ArtifactFilters(run_id="run_artifact", session_id="session_artifact"))
    assert any(item.get("source_tool_call_id") == "call_xlsx" for item in listed)

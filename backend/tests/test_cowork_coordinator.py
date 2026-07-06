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

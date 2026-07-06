from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List

import pytest

from backend.runtime.cancellation import OperationCancelled
from backend.runtime import cowork_coordinator
from backend.runtime.agent_loop import AgentConfig, ContentEvent, DoneEvent
from backend.runtime.cowork_coordinator import iter_local_cowork_events
from backend.runtime.worktree_manager import WorktreeRecord


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
            "status": "",
            "stat": "",
            "patch": "",
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
    done = next(event for event in events if event["kind"] == "subagent_done")
    agent = done["payload"]["result"]["agent"]
    assert agent["ok"] is True
    assert agent["final_text"] == "agent subrun complete"
    assert agent["workspace_root"] == str(worktree)

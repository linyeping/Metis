from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Dict, List

from backend.runtime import subagent_worktree as swt
from backend.runtime.mini_agent import MiniAgentResult
from backend.tools.coding.foundation.core_mechanisms.execution_boundary_context import (
    get_context_workspace_root,
)
from backend.tools.coding.workflow_features.subagents import delegate_best_of_n as bon
from backend.tools.coding.workflow_features.subagents import task_dispatch
from backend.tools.coding.workflow_features.subagents import task_subprocess_worker


def _completed(
    args: List[str],
    returncode: int = 0,
    stdout: str = "",
    stderr: str = "",
) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args, returncode, stdout, stderr)


def test_subagent_worktree_slug_is_git_safe() -> None:
    assert swt._slug("Best of N: Redis!!") == "best-of-n-redis"
    assert swt._slug("中文任务") == "subagent"


def test_create_subagent_worktree_uses_sibling_base_and_unique_branch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    repo = tmp_path / "repo"
    workspace = repo / "src"
    workspace.mkdir(parents=True)
    calls: List[tuple[List[str], Path, int]] = []

    class FakeUuid:
        hex = "abcdef1234567890"

    def fake_run_git(args: List[str], *, cwd: Path, timeout: int = 20):
        calls.append((args, cwd, timeout))
        if args == ["rev-parse", "--show-toplevel"]:
            return _completed(args, stdout=f"{repo}\n")
        if args == ["rev-parse", "--verify", "HEAD"]:
            return _completed(args, stdout="abc123\n")
        if args == ["status", "--porcelain"]:
            return _completed(args, stdout="")
        if args[:3] == ["worktree", "add", "-b"]:
            return _completed(args)
        return _completed(args, returncode=1, stderr="unexpected git call")

    monkeypatch.delenv("METIS_SUBAGENT_WORKTREE_BASE", raising=False)
    monkeypatch.setattr(swt.uuid, "uuid4", lambda: FakeUuid())
    monkeypatch.setattr(swt, "_run_git", fake_run_git)

    plan = swt.create_subagent_worktree(
        str(workspace),
        label="Best of N",
        attempt_index=2,
    )

    assert plan.ok is True
    assert plan.mode == "git_worktree"
    assert plan.branch == "metis/subagent/best-of-n-2-abcdef12"
    assert Path(plan.worktree_path).name == "best-of-n-2-abcdef12"
    assert ".metis-subagent-worktrees" in Path(plan.worktree_path).parts
    assert not swt._is_within(Path(plan.worktree_path), repo)
    assert any(call[0][:3] == ["worktree", "add", "-b"] for call in calls)


def test_delegate_best_of_n_refuses_to_run_without_all_worktrees(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "repo"
    source.mkdir()

    def fake_create(*args, **kwargs):
        return swt.SubagentWorktreePlan(
            ok=False,
            mode="unavailable",
            workspace_root=str(source),
            reason="git unavailable",
        )

    def fail_run(*args, **kwargs):
        raise AssertionError("mini agent must not run without worktree isolation")

    monkeypatch.setattr(swt, "create_subagent_worktree", fake_create)
    monkeypatch.setattr("backend.runtime.mini_agent.run_mini_agent", fail_run)

    out = bon.delegate_best_of_n("try something", n=2, workspace_root=str(source))

    assert "Subagent Worktree Isolation v2 refused" in out
    assert "No attempt was started in the main working tree" in out
    assert swt.SUBAGENT_WORKTREE_SCHEMA in out
    assert "git unavailable" in out


def test_delegate_best_of_n_runs_attempts_inside_distinct_worktrees(
    tmp_path: Path,
    monkeypatch,
) -> None:
    source = tmp_path / "repo"
    source.mkdir()
    worktrees = [tmp_path / "wt1", tmp_path / "wt2"]
    plans = [
        swt.SubagentWorktreePlan(
            ok=True,
            mode="git_worktree",
            workspace_root=str(source),
            repo_root=str(source),
            worktree_path=str(worktrees[0]),
            branch="metis/subagent/best-of-n-1-a",
            reason="created",
        ),
        swt.SubagentWorktreePlan(
            ok=True,
            mode="git_worktree",
            workspace_root=str(source),
            repo_root=str(source),
            worktree_path=str(worktrees[1]),
            branch="metis/subagent/best-of-n-2-b",
            reason="created",
        ),
    ]
    create_calls: List[Dict[str, Any]] = []
    seen_roots: List[str] = []
    seen_context_roots: List[str] = []

    def fake_create(*, workspace_root: str, label: str, attempt_index: int):
        create_calls.append(
            {"workspace_root": workspace_root, "label": label, "attempt_index": attempt_index}
        )
        return plans[attempt_index - 1]

    def fake_run_mini_agent(task, config, registry, backend):
        del task, registry, backend
        seen_roots.append(config.workspace_root)
        seen_context_roots.append(str(get_context_workspace_root()))
        return MiniAgentResult(ok=True, output=f"done {len(seen_roots)}", turns_used=1)

    monkeypatch.setattr(swt, "create_subagent_worktree", fake_create)
    monkeypatch.setattr("backend.web.app._load_config_for_workspace", lambda root: object())
    monkeypatch.setattr("backend.runtime.agent_loop._create_backend", lambda config: object())
    monkeypatch.setattr("backend.runtime.tool_registry.get_registry", lambda: object())
    monkeypatch.setattr("backend.runtime.mini_agent.run_mini_agent", fake_run_mini_agent)

    out = bon.delegate_best_of_n("try two approaches", n=2, workspace_root=str(source))

    assert [call["attempt_index"] for call in create_calls] == [1, 2]
    assert {call["workspace_root"] for call in create_calls} == {str(source)}
    assert seen_roots == [str(worktrees[0]), str(worktrees[1])]
    assert seen_context_roots == [str(worktrees[0]), str(worktrees[1])]
    assert "Subagent Worktree Isolation v2" in out
    assert str(worktrees[0]) in out and str(worktrees[1]) in out
    assert "done 1" in out and "done 2" in out


def test_task_best_of_n_routes_workspace_root(monkeypatch) -> None:
    seen: Dict[str, str] = {}

    def fake_delegate_best_of_n(task: str, n: int = 3, workspace_root: str = ".") -> str:
        seen["task"] = task
        seen["workspace_root"] = workspace_root
        seen["n"] = str(n)
        return "ok"

    monkeypatch.setattr(bon, "delegate_best_of_n", fake_delegate_best_of_n)

    assert task_dispatch._dispatch_in_process("prompt", "best_of_n", "D:/repo") == "ok"
    assert seen == {"task": "prompt", "workspace_root": "D:/repo", "n": "3"}


def test_task_subprocess_worker_best_of_n_routes_workspace_root(monkeypatch) -> None:
    seen: Dict[str, str] = {}

    def fake_delegate_best_of_n(task: str, n: int = 3, workspace_root: str = ".") -> str:
        seen["task"] = task
        seen["workspace_root"] = workspace_root
        seen["n"] = str(n)
        return "ok"

    monkeypatch.setattr(bon, "delegate_best_of_n", fake_delegate_best_of_n)

    assert task_subprocess_worker._route("prompt", "bestofn", "D:/repo") == "ok"
    assert seen == {"task": "prompt", "workspace_root": "D:/repo", "n": "3"}

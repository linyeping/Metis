"""Git worktree isolation helpers for write-capable sub-agents."""
from __future__ import annotations

import hashlib
import os
import re
import subprocess
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional


SUBAGENT_WORKTREE_SCHEMA = "metis.subagent.worktree.v2"


@dataclass(frozen=True)
class SubagentWorktreePlan:
    ok: bool
    mode: str
    workspace_root: str
    repo_root: str = ""
    worktree_path: str = ""
    branch: str = ""
    base_ref: str = "HEAD"
    dirty: bool = False
    reason: str = ""
    schema: str = SUBAGENT_WORKTREE_SCHEMA

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "ok": self.ok,
            "mode": self.mode,
            "workspace_root": self.workspace_root,
            "repo_root": self.repo_root,
            "worktree_path": self.worktree_path,
            "branch": self.branch,
            "base_ref": self.base_ref,
            "dirty": self.dirty,
            "reason": self.reason,
        }


def create_subagent_worktree(
    workspace_root: str = ".",
    *,
    label: str = "subagent",
    attempt_index: Optional[int] = None,
) -> SubagentWorktreePlan:
    """Create an isolated git worktree for a write-capable sub-agent.

    This helper never falls back to the caller's working tree. Callers that
    expose write tools should treat an unavailable plan as a hard stop.
    """
    workspace = _resolve_existing_dir(workspace_root)
    if workspace is None:
        return SubagentWorktreePlan(
            ok=False,
            mode="unavailable",
            workspace_root=str(Path(workspace_root or ".").expanduser()),
            reason=f"workspace_root is not an existing directory: {workspace_root!r}",
        )

    repo_root, repo_reason = _git_repo_root(workspace)
    if repo_root is None:
        return SubagentWorktreePlan(
            ok=False,
            mode="unavailable",
            workspace_root=str(workspace),
            reason=repo_reason,
        )

    head = _run_git(["rev-parse", "--verify", "HEAD"], cwd=repo_root)
    if head.returncode != 0:
        return SubagentWorktreePlan(
            ok=False,
            mode="unavailable",
            workspace_root=str(workspace),
            repo_root=str(repo_root),
            reason=_git_error("git repository has no HEAD commit", head),
        )

    base_dir = _worktree_base_dir(repo_root)
    if _is_within(base_dir, repo_root):
        return SubagentWorktreePlan(
            ok=False,
            mode="unavailable",
            workspace_root=str(workspace),
            repo_root=str(repo_root),
            reason=(
                "subagent worktree base is inside the source workspace; "
                f"refusing nested worktrees: {base_dir}"
            ),
        )

    try:
        base_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return SubagentWorktreePlan(
            ok=False,
            mode="unavailable",
            workspace_root=str(workspace),
            repo_root=str(repo_root),
            reason=f"failed to create subagent worktree base {base_dir}: {exc}",
        )

    slug = _slug(label)
    suffix = uuid.uuid4().hex[:8]
    attempt = f"-{int(attempt_index)}" if attempt_index is not None else ""
    branch = f"metis/subagent/{slug}{attempt}-{suffix}"
    worktree_path = base_dir / f"{slug}{attempt}-{suffix}"

    created = _run_git(
        ["worktree", "add", "-b", branch, str(worktree_path), "HEAD"],
        cwd=repo_root,
        timeout=60,
    )
    if created.returncode != 0:
        return SubagentWorktreePlan(
            ok=False,
            mode="unavailable",
            workspace_root=str(workspace),
            repo_root=str(repo_root),
            branch=branch,
            worktree_path=str(worktree_path),
            dirty=_is_dirty(repo_root),
            reason=_git_error("failed to create git worktree", created),
        )

    return SubagentWorktreePlan(
        ok=True,
        mode="git_worktree",
        workspace_root=str(workspace),
        repo_root=str(repo_root),
        worktree_path=str(worktree_path),
        branch=branch,
        base_ref="HEAD",
        dirty=_is_dirty(repo_root),
        reason="created isolated git worktree",
    )


def worktree_plans_payload(plans: List[SubagentWorktreePlan]) -> Dict[str, Any]:
    return {
        "schema": SUBAGENT_WORKTREE_SCHEMA,
        "ok": all(plan.ok for plan in plans),
        "plans": [plan.to_dict() for plan in plans],
    }


def _resolve_existing_dir(path: str) -> Optional[Path]:
    try:
        resolved = Path(path or ".").expanduser().resolve()
    except OSError:
        return None
    return resolved if resolved.is_dir() else None


def _git_repo_root(cwd: Path) -> tuple[Optional[Path], str]:
    result = _run_git(["rev-parse", "--show-toplevel"], cwd=cwd)
    if result.returncode != 0:
        return None, _git_error("workspace is not inside a git repository", result)
    raw = (result.stdout or "").strip().splitlines()
    if not raw:
        return None, "git did not return a repository root"
    return Path(raw[-1]).resolve(), ""


def _worktree_base_dir(repo_root: Path) -> Path:
    configured = os.environ.get("METIS_SUBAGENT_WORKTREE_BASE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    digest = hashlib.sha1(str(repo_root).lower().encode("utf-8")).hexdigest()[:8]
    return (repo_root.parent / ".metis-subagent-worktrees" / f"{repo_root.name}-{digest}").resolve()


def _slug(text: str, *, default: str = "subagent", limit: int = 42) -> str:
    lowered = str(text or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    if not slug:
        slug = default
    return slug[:limit].strip("-") or default


def _is_dirty(repo_root: Path) -> bool:
    result = _run_git(["status", "--porcelain"], cwd=repo_root)
    return result.returncode == 0 and bool((result.stdout or "").strip())


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _git_error(prefix: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout or "").strip()
    if not detail:
        return prefix
    return f"{prefix}: {detail[:1200]}"


def _run_git(
    args: List[str],
    *,
    cwd: Path,
    timeout: int = 20,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(["git", *args], 1, "", str(exc))


__all__ = [
    "SUBAGENT_WORKTREE_SCHEMA",
    "SubagentWorktreePlan",
    "create_subagent_worktree",
    "worktree_plans_payload",
]

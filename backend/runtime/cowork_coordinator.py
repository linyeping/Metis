from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List

from backend.runtime.artifact_registry import register_artifact

COWORK_COORDINATOR_SCHEMA = "metis.cowork_coordinator.v1"
COWORK_PLAN_SCHEMA = "metis.cowork_plan.v1"
COWORK_SUMMARY_SCHEMA = "metis.cowork_summary.v1"


@dataclass(frozen=True)
class CoworkSubrunPlan:
    subrun_id: str
    title: str
    prompt: str
    execution_profile: str = "local_worktree"
    status: str = "planned"
    run_id: str = ""
    worktree_id: str = ""
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    diff: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subrun_id": self.subrun_id,
            "title": self.title,
            "prompt": self.prompt,
            "execution_profile": self.execution_profile,
            "status": self.status,
            "run_id": self.run_id,
            "worktree_id": self.worktree_id,
            "artifacts": list(self.artifacts),
            "diff": dict(self.diff),
        }


def build_cowork_plan(goal: str, *, run_id: str = "", session_id: str = "", max_subruns: int = 3) -> Dict[str, Any]:
    goal_text = str(goal or "").strip()
    tasks = _task_candidates(goal_text, max_subruns=max_subruns)
    subruns = [
        CoworkSubrunPlan(
            subrun_id=f"subrun_{uuid.uuid4().hex[:10]}",
            title=title,
            prompt=_subrun_prompt(goal_text, title),
        ).to_dict()
        for title in tasks
    ]
    return {
        "schema": COWORK_PLAN_SCHEMA,
        "coordinator_schema": COWORK_COORDINATOR_SCHEMA,
        "run_id": run_id,
        "session_id": session_id,
        "goal": goal_text,
        "status": "planned",
        "created_at": time.time(),
        "subruns": subruns,
        "merge_policy": {
            "diffs": "review_then_promote",
            "artifacts": "register_all",
            "conflicts": "main_run_decides",
        },
    }


def summarize_cowork_results(
    *,
    plan: Dict[str, Any],
    workspace_root: str,
    run_id: str = "",
    session_id: str = "",
) -> Dict[str, Any]:
    """Persist a stable summary artifact for a coordinator plan/subrun result set."""
    root = Path(workspace_root or ".").expanduser().resolve()
    out_dir = root / ".metis" / "cowork"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_id = f"cowork_{uuid.uuid4().hex[:12]}"
    subruns = plan.get("subruns") if isinstance(plan.get("subruns"), list) else []
    payload = {
        "schema": COWORK_SUMMARY_SCHEMA,
        "summary_id": summary_id,
        "run_id": run_id or str(plan.get("run_id") or ""),
        "session_id": session_id or str(plan.get("session_id") or ""),
        "goal": str(plan.get("goal") or ""),
        "created_at": time.time(),
        "subrun_count": len(subruns),
        "subruns": subruns,
        "artifacts": _collect_subrun_artifacts(subruns),
        "diffs": _collect_subrun_diffs(subruns),
    }
    path = out_dir / f"{summary_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    artifact = register_artifact(
        kind="report",
        title="Cowork coordinator summary",
        path=str(path),
        mime="application/json",
        run_id=payload["run_id"],
        session_id=payload["session_id"],
        metadata={"summary_id": summary_id, "source": "cowork_coordinator"},
        workspace_root=str(root),
    )
    return {**payload, "artifact": artifact}


def _task_candidates(goal: str, *, max_subruns: int) -> List[str]:
    limit = max(1, min(int(max_subruns or 3), 6))
    lines = [line.strip(" -\t") for line in goal.splitlines() if line.strip(" -\t")]
    if len(lines) >= 2:
        return [_compact_title(line, index) for index, line in enumerate(lines[:limit], 1)]
    return [
        "Inspect current implementation",
        "Draft isolated change plan",
        "Validate and summarize diffs",
    ][:limit]


def _compact_title(text: str, index: int) -> str:
    title = " ".join(text.split())
    if len(title) > 80:
        title = title[:77].rstrip() + "..."
    return title or f"Subtask {index}"


def _subrun_prompt(goal: str, title: str) -> str:
    return (
        f"Parent cowork goal:\n{goal}\n\n"
        f"Subtask:\n{title}\n\n"
        "Work in an isolated local worktree. Return the artifacts produced, changed files, "
        "and a concise diff summary. Do not promote changes to the source workspace."
    )


def _collect_subrun_artifacts(subruns: List[Any]) -> List[Dict[str, Any]]:
    artifacts: List[Dict[str, Any]] = []
    for item in subruns:
        if not isinstance(item, dict):
            continue
        raw = item.get("artifacts")
        if isinstance(raw, list):
            artifacts.extend([artifact for artifact in raw if isinstance(artifact, dict)])
    return artifacts


def _collect_subrun_diffs(subruns: List[Any]) -> List[Dict[str, Any]]:
    diffs: List[Dict[str, Any]] = []
    for item in subruns:
        if not isinstance(item, dict):
            continue
        diff = item.get("diff")
        if isinstance(diff, dict) and diff:
            diffs.append(diff)
    return diffs


__all__ = [
    "COWORK_COORDINATOR_SCHEMA",
    "COWORK_PLAN_SCHEMA",
    "COWORK_SUMMARY_SCHEMA",
    "build_cowork_plan",
    "summarize_cowork_results",
]

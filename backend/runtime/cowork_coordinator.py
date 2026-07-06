from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List

from backend.runtime.artifact_registry import register_artifact
from backend.runtime.cancellation import OperationCancelled
from backend.runtime.execution_profile import LOCAL_DIRECT, LOCAL_VM, LOCAL_WORKTREE
from backend.runtime.local_vm_runner import LocalVmCommand, run_local_vm_command
from backend.runtime.worktree_manager import WorktreeRecord, create_worktree, diff_worktree

COWORK_COORDINATOR_SCHEMA = "metis.cowork_coordinator.v1"
COWORK_PLAN_SCHEMA = "metis.cowork_plan.v1"
COWORK_SUMMARY_SCHEMA = "metis.cowork_summary.v1"
COWORK_EXECUTION_SCHEMA = "metis.cowork_execution.v1"


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


def iter_local_cowork_events(
    goal: str,
    *,
    workspace_root: str,
    source_workspace_root: str = "",
    run_id: str = "",
    session_id: str = "",
    execution_profile: str = LOCAL_DIRECT,
    max_subruns: int = 3,
    cancelled: Callable[[], bool] | None = None,
) -> Iterator[Dict[str, Any]]:
    """Execute the first local Cowork path and yield desktop stream events.

    This is intentionally local-only and sequential for the first stable
    protocol. Each subrun gets a managed worktree; local_vm is only used as the
    command runner inside that worktree.
    """
    source_root = Path(source_workspace_root or workspace_root or ".").expanduser().resolve()
    subrun_profile = _subrun_execution_profile(execution_profile)
    plan = build_cowork_plan(
        goal,
        run_id=run_id,
        session_id=session_id,
        max_subruns=max_subruns,
    )
    for item in plan.get("subruns", []):
        if isinstance(item, dict):
            item["execution_profile"] = subrun_profile

    yield _runtime_status(
        "planning",
        "Cowork plan created.",
        details={
            "schema": COWORK_EXECUTION_SCHEMA,
            "plan": plan,
            "execution_profile": subrun_profile,
        },
    )

    subruns = plan.get("subruns") if isinstance(plan.get("subruns"), list) else []
    for index, subrun in enumerate(subruns, 1):
        if not isinstance(subrun, dict):
            continue
        _raise_if_cancelled(cancelled)
        title = str(subrun.get("title") or f"Subrun {index}")
        task_id = str(subrun.get("subrun_id") or f"subrun_{index}")
        yield _subagent_event(
            "subagent_start",
            task_id=task_id,
            name=title,
            progress=0,
            status="running",
            result={"execution_profile": subrun_profile},
        )
        try:
            record = create_worktree(
                str(source_root),
                run_id=f"{run_id}-{task_id}" if run_id else task_id,
                session_id=session_id,
                label=f"cowork-{index}",
            )
            subrun.update(
                {
                    "status": "running",
                    "worktree_id": record.worktree_id,
                    "worktree": record.to_dict(),
                    "worktree_workspace_root": record.worktree_workspace_root,
                }
            )
            yield _subagent_event(
                "subagent_progress",
                task_id=task_id,
                name=title,
                progress=25,
                status="running",
                result={
                    "worktree_id": record.worktree_id,
                    "worktree_workspace_root": record.worktree_workspace_root,
                },
            )

            vm_result: Dict[str, Any] = {}
            if subrun_profile == LOCAL_VM:
                _raise_if_cancelled(cancelled)
                yield _subagent_event(
                    "subagent_progress",
                    task_id=task_id,
                    name=title,
                    progress=45,
                    status="running",
                    result={"runner": "local_vm", "backend": "metis_wsl"},
                )
                vm_result = _run_subrun_local_vm(subrun, record)
                subrun["local_vm"] = vm_result

            _raise_if_cancelled(cancelled)
            artifact = _write_subrun_artifact(
                subrun=subrun,
                record=record,
                run_id=run_id,
                session_id=session_id,
                goal=str(plan.get("goal") or ""),
                vm_result=vm_result,
            )
            subrun["artifacts"] = [artifact]
            diff = _safe_diff(source_root, record)
            subrun["diff"] = diff
            failed = bool(vm_result) and not bool(vm_result.get("ok"))
            subrun["status"] = "failed" if failed else "done"
            result = {
                "execution_profile": subrun_profile,
                "worktree_id": record.worktree_id,
                "worktree_workspace_root": record.worktree_workspace_root,
                "artifacts": subrun["artifacts"],
                "diff": diff,
            }
            if vm_result:
                result["local_vm"] = vm_result
            yield _subagent_event(
                "subagent_done",
                task_id=task_id,
                name=title,
                progress=100,
                status="error" if failed else "done",
                result=result,
            )
        except OperationCancelled:
            raise
        except Exception as exc:
            subrun["status"] = "failed"
            subrun["error"] = f"{type(exc).__name__}: {exc}"
            yield _subagent_event(
                "subagent_done",
                task_id=task_id,
                name=title,
                progress=100,
                status="error",
                result={
                    "execution_profile": subrun_profile,
                    "error": subrun["error"],
                },
            )

    _raise_if_cancelled(cancelled)
    yield _runtime_status(
        "summarizing",
        "Cowork subruns complete; collecting artifacts and diffs.",
        details={"schema": COWORK_EXECUTION_SCHEMA, "subrun_count": len(subruns)},
    )
    summary = summarize_cowork_results(
        plan=plan,
        workspace_root=str(source_root),
        run_id=run_id,
        session_id=session_id,
    )
    yield {
        "type": "artifact_created",
        "kind": "artifact_created",
        "payload": {
            "artifact": summary.get("artifact", {}),
            "source": "cowork_coordinator",
            "summary_id": summary.get("summary_id", ""),
        },
    }
    text = _cowork_summary_text(summary)
    yield {"type": "content", "kind": "content", "payload": {"text": text}}
    yield {
        "type": "done",
        "kind": "done",
        "payload": {
            "turns": 1,
            "tool_calls": len(subruns),
            "context_ledger": {
                "cowork_summary_id": summary.get("summary_id", ""),
                "artifact_id": (summary.get("artifact") or {}).get("artifact_id", ""),
            },
        },
    }


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


def _subrun_execution_profile(value: str) -> str:
    profile = str(value or "").strip().lower().replace("-", "_")
    if profile == LOCAL_VM:
        return LOCAL_VM
    if profile in {LOCAL_WORKTREE, LOCAL_DIRECT, ""}:
        return LOCAL_WORKTREE
    return LOCAL_WORKTREE


def _runtime_status(phase: str, message: str, *, details: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "type": "runtime_status",
        "kind": "runtime_status",
        "payload": {
            "phase": phase,
            "message": message,
            "recoverable": True,
            "details": details or {},
        },
    }


def _subagent_event(
    kind: str,
    *,
    task_id: str,
    name: str,
    progress: int,
    status: str,
    result: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "task_id": task_id,
        "name": name,
        "progress": max(0, min(int(progress or 0), 100)),
        "status": status,
    }
    if result is not None:
        payload["result"] = result
    return {"type": kind, "kind": kind, "payload": payload}


def _run_subrun_local_vm(subrun: Dict[str, Any], record: WorktreeRecord) -> Dict[str, Any]:
    result = run_local_vm_command(
        LocalVmCommand(
            command=_local_vm_subrun_command(str(subrun.get("subrun_id") or "")),
            workspace_root=record.worktree_workspace_root,
            timeout=60,
            allow_network=False,
            collect_artifacts=False,
            export_patch=True,
            export_diagnostics="on_failure",
        )
    )
    return _compact_local_vm_result(result)


def _local_vm_subrun_command(subrun_id: str) -> str:
    safe_id = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in subrun_id)[:80] or "subrun"
    return (
        "python3 - <<'PY'\n"
        "import json, os\n"
        "from pathlib import Path\n"
        f"subrun_id = {safe_id!r}\n"
        "out = Path(os.environ.get('METIS_RUNTIME_ARTIFACTS_DIR', '.'))\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "payload = {'schema': 'metis.cowork_local_vm_smoke.v1', 'subrun_id': subrun_id, 'ok': True}\n"
        "(out / f'{subrun_id}-local-vm.json').write_text(json.dumps(payload, sort_keys=True) + '\\n', encoding='utf-8')\n"
        "print('METIS_COWORK_VM_OK')\n"
        "PY"
    )


def _compact_local_vm_result(result: Dict[str, Any]) -> Dict[str, Any]:
    job = result.get("job") if isinstance(result.get("job"), dict) else {}
    payload: Dict[str, Any] = {
        "ok": bool(result.get("ok")),
        "runner": str(result.get("runner") or "local_vm"),
        "backend": str(result.get("backend") or "metis_wsl"),
        "run_id": str(result.get("run_id") or ""),
    }
    for key in ("code", "error"):
        if result.get(key):
            payload[key] = str(result.get(key) or "")
    if job:
        payload.update(
            {
                "job_id": str(job.get("job_id") or ""),
                "status": str(job.get("status") or ""),
                "returncode": job.get("returncode"),
                "timed_out": bool(job.get("timed_out")),
                "stdout": _truncate(str(job.get("stdout") or ""), 2000),
                "stderr": _truncate(str(job.get("stderr") or ""), 2000),
                "artifacts_dir": str(job.get("artifacts_dir") or ""),
                "patch_path": str(job.get("patch_path") or ""),
                "changed_files": job.get("changed_files") if isinstance(job.get("changed_files"), list) else [],
                "artifacts": _compact_artifacts(job.get("artifacts")),
            }
        )
    return payload


def _write_subrun_artifact(
    *,
    subrun: Dict[str, Any],
    record: WorktreeRecord,
    run_id: str,
    session_id: str,
    goal: str,
    vm_result: Dict[str, Any],
) -> Dict[str, Any]:
    root = Path(record.worktree_workspace_root).expanduser().resolve()
    out_dir = root / ".metis" / "cowork"
    out_dir.mkdir(parents=True, exist_ok=True)
    subrun_id = str(subrun.get("subrun_id") or record.worktree_id)
    path = out_dir / f"{subrun_id}.json"
    payload = {
        "schema": "metis.cowork_subrun_report.v1",
        "subrun_id": subrun_id,
        "run_id": run_id,
        "session_id": session_id,
        "goal": goal,
        "title": str(subrun.get("title") or ""),
        "prompt": str(subrun.get("prompt") or ""),
        "execution_profile": str(subrun.get("execution_profile") or ""),
        "worktree": record.to_dict(),
        "local_vm": vm_result,
        "created_at": time.time(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return register_artifact(
        kind="report",
        title=f"Cowork subrun - {payload['title'] or subrun_id}",
        path=str(path),
        mime="application/json",
        run_id=run_id,
        session_id=session_id,
        metadata={"source": "cowork_subrun", "subrun_id": subrun_id, "worktree_id": record.worktree_id},
        workspace_root=str(root),
    )


def _safe_diff(source_root: Path, record: WorktreeRecord) -> Dict[str, Any]:
    try:
        return _compact_diff(diff_worktree(str(source_root), record.worktree_id))
    except Exception as exc:
        return {
            "schema": "metis.worktree_diff_summary.v1",
            "ok": False,
            "worktree_id": record.worktree_id,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _compact_diff(diff: Dict[str, Any]) -> Dict[str, Any]:
    worktree = diff.get("worktree") if isinstance(diff.get("worktree"), dict) else {}
    patch = str(diff.get("patch") or "")
    return {
        "schema": "metis.worktree_diff_summary.v1",
        "ok": True,
        "worktree_id": str(worktree.get("worktree_id") or ""),
        "worktree_workspace_root": str(worktree.get("worktree_workspace_root") or ""),
        "base_ref": str(diff.get("base_ref") or ""),
        "status": _truncate(str(diff.get("status") or ""), 2000),
        "stat": _truncate(str(diff.get("stat") or ""), 2000),
        "patch_preview": _truncate(patch, 4000),
        "truncated": bool(diff.get("truncated")) or len(patch) > 4000,
    }


def _compact_artifacts(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in value[:50]:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "path": str(item.get("path") or ""),
                "relative_path": str(item.get("relative_path") or item.get("relativePath") or ""),
                "size": item.get("size", 0),
            }
        )
    return out


def _cowork_summary_text(summary: Dict[str, Any]) -> str:
    subruns = summary.get("subruns") if isinstance(summary.get("subruns"), list) else []
    done = sum(1 for item in subruns if isinstance(item, dict) and item.get("status") == "done")
    failed = sum(1 for item in subruns if isinstance(item, dict) and item.get("status") == "failed")
    artifact = summary.get("artifact") if isinstance(summary.get("artifact"), dict) else {}
    lines = [
        "Cowork local run complete.",
        f"Subruns: {len(subruns)} total, {done} done, {failed} failed.",
    ]
    if artifact.get("artifact_id"):
        lines.append(f"Summary artifact: {artifact.get('artifact_id')}.")
    return "\n".join(lines)


def _raise_if_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if callable(cancelled) and cancelled():
        raise OperationCancelled("Cowork run cancelled")


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


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
    "COWORK_EXECUTION_SCHEMA",
    "COWORK_PLAN_SCHEMA",
    "COWORK_SUMMARY_SCHEMA",
    "build_cowork_plan",
    "iter_local_cowork_events",
    "summarize_cowork_results",
]

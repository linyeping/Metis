from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass
from typing import Any, Dict

from backend.runtime.isolated_runtime import metis_wsl_runtime_import, metis_wsl_runtime_status
from backend.runtime.runtime_job import metis_runtime_job

LOCAL_VM_RUNNER_SCHEMA = "metis.local_vm_runner.v1"


@dataclass(frozen=True)
class LocalVmCommand:
    command: str
    workspace_root: str
    cwd: str = ""
    timeout: int = 120
    allow_network: bool = False
    collect_artifacts: bool = False
    export_patch: bool = True


def run_local_vm_command(request: LocalVmCommand) -> Dict[str, Any]:
    """Run one command through the local VM-capable runtime job boundary.

    This is intentionally command-only. Chat, Code, Preview, permissions, and
    artifacts keep their normal desktop control path; local_vm is a runner that
    execution tools may opt into later.
    """
    started = time.time()
    run_id = f"vmcmd_{uuid.uuid4().hex[:10]}"
    command = str(request.command or "").strip()
    if not command:
        return {
            "schema": LOCAL_VM_RUNNER_SCHEMA,
            "ok": False,
            "runner": "local_vm",
            "run_id": run_id,
            "started_at": started,
            "finished_at": time.time(),
            "error": "command is required",
        }
    readiness = _metis_wsl_readiness(request.workspace_root)
    if readiness is not None:
        return {
            "schema": LOCAL_VM_RUNNER_SCHEMA,
            "ok": False,
            "runner": "local_vm",
            "backend": "metis_wsl",
            "run_id": run_id,
            "started_at": started,
            "finished_at": time.time(),
            **readiness,
        }
    try:
        raw = metis_runtime_job(
            task=f"Local VM command: {command[:120]}",
            command=command,
            root=request.workspace_root,
            cwd=request.cwd,
            backend="metis_wsl",
            mode="copy",
            timeout=max(1, int(request.timeout or 120)),
            allow_network=bool(request.allow_network),
            collect_artifacts=bool(request.collect_artifacts),
            export_patch=bool(request.export_patch),
            export_diagnostics="on_failure",
            strict_sandbox=True,
        )
        payload = json.loads(raw) if raw else {}
    except Exception as exc:
        return {
            "schema": LOCAL_VM_RUNNER_SCHEMA,
            "ok": False,
            "runner": "local_vm",
            "backend": "metis_wsl",
            "run_id": run_id,
            "started_at": started,
            "finished_at": time.time(),
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "schema": LOCAL_VM_RUNNER_SCHEMA,
        "ok": bool(payload.get("ok") or payload.get("status") == "done"),
        "runner": "local_vm",
        "backend": "metis_wsl",
        "run_id": run_id,
        "started_at": started,
        "finished_at": time.time(),
        "job": payload,
    }


def _metis_wsl_readiness(root: str) -> Dict[str, Any] | None:
    """Return an actionable error payload when MetisRuntime is not installed."""
    try:
        status = json.loads(metis_wsl_runtime_status(root=root))
    except Exception as exc:
        return {
            "code": "METIS_WSL_STATUS_FAILED",
            "error": f"{type(exc).__name__}: {exc}",
        }
    if status.get("available"):
        return None
    import_plan: Dict[str, Any] = {}
    if status.get("ready_to_import"):
        try:
            import_plan = json.loads(metis_wsl_runtime_import(root=root, dry_run=True))
        except Exception as exc:
            import_plan = {
                "ok": False,
                "code": "METIS_WSL_IMPORT_PLAN_FAILED",
                "error": f"{type(exc).__name__}: {exc}",
            }
    return {
        "code": "METIS_WSL_RUNTIME_UNAVAILABLE",
        "error": status.get("reason") or "MetisRuntime WSL runtime is not available",
        "wsl_runtime": status,
        "import_plan": import_plan,
        "next_steps": [
            "Import the verified MetisRuntime WSL rootfs before using local_vm.",
            "Use the import_plan command only after confirming the rootfs asset is trusted.",
        ],
    }


__all__ = ["LOCAL_VM_RUNNER_SCHEMA", "LocalVmCommand", "run_local_vm_command"]

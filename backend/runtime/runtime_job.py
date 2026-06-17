from __future__ import annotations

import json
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.runtime.isolated_runtime import (
    metis_runtime_collect_artifacts,
    metis_runtime_create,
    metis_runtime_export_diagnostics,
    metis_runtime_export_patch,
    metis_runtime_run,
)
from backend.tools.coding.foundation.core_mechanisms.execution_boundary_context import get_effective_sub_allow
from backend.tools.coding.foundation.core_mechanisms.path_security import safe_path_for_read


RUNTIME_JOB_SCHEMA = "metis.runtime_job.v1"
RUNTIME_JOB_STATUS_SCHEMA = "metis.runtime_job.status.v1"


def metis_runtime_job(
    task: str,
    command: str,
    root: str = ".",
    cwd: str = "",
    backend: str = "auto",
    mode: str = "copy",
    timeout: int = 120,
    allow_network: bool = False,
    collect_artifacts: bool = False,
    artifact_patterns: Optional[List[str]] = None,
    export_patch: bool = True,
    export_diagnostics: str = "on_failure",
    require_artifacts: bool = False,
    expected_stdout_contains: str = "",
    strict_sandbox: bool = False,
    max_files: int = 2000,
    max_bytes: int = 80 * 1024 * 1024,
) -> str:
    """Run one Claude-style isolated runtime job and return a stable result contract."""
    started = time.time()
    job_id = f"job_{int(started * 1000)}_{uuid.uuid4().hex[:8]}"
    task_text = str(task or "").strip() or str(command or "").strip()[:120] or "Metis Runtime Job"
    command_text = str(command or "").strip()
    if not command_text:
        return _json_error("command is required", code="COMMAND_REQUIRED", job_id=job_id)

    created: Dict[str, Any] = {}
    run: Dict[str, Any] = {}
    collected: Dict[str, Any] = {}
    patch: Dict[str, Any] = {}
    diagnostics: Dict[str, Any] = {}
    status = "failed"
    session_id = ""

    try:
        created = _loads(
            metis_runtime_create(
                task=task_text,
                root=root,
                mode=mode,
                backend=backend,
                max_files=max(1, int(max_files or 2000)),
                max_bytes=max(1024, int(max_bytes or 1)),
                allow_network=bool(allow_network),
                allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
                allow_project_write=False,
                allow_desktop_write=False,
                strict_sandbox=bool(strict_sandbox),
            )
        )
        session_id = str(created.get("session_id") or "")
        if not created.get("ok") or not session_id:
            payload = _job_payload(
                job_id=job_id,
                task=task_text,
                command=command_text,
                started=started,
                status="failed",
                created=created,
                run=run,
                collected=collected,
                patch=patch,
                diagnostics=diagnostics,
                verifier=_verifier_payload(
                    run=run,
                    artifacts=[],
                    require_artifacts=require_artifacts,
                    expected_stdout_contains=expected_stdout_contains,
                    create_failed=True,
                ),
            )
            _write_job(root, payload)
            return _json(payload)

        status = "running"
        _write_job(
            root,
            _job_payload(
                job_id=job_id,
                task=task_text,
                command=command_text,
                started=started,
                status=status,
                created=created,
                run=run,
                collected=collected,
                patch=patch,
                diagnostics=diagnostics,
                verifier={"ok": False, "checks": []},
            ),
        )

        run = _loads(
            metis_runtime_run(
                session_id=session_id,
                command=command_text,
                cwd=cwd,
                timeout=max(1, int(timeout or 120)),
                allow_network=bool(allow_network),
            )
        )
        if (collect_artifacts or bool(artifact_patterns)) and session_id:
            collected = _loads(
                metis_runtime_collect_artifacts(
                    session_id=session_id,
                    patterns=artifact_patterns or None,
                )
            )
        if export_patch and session_id:
            patch = _loads(metis_runtime_export_patch(session_id=session_id))

        artifacts = _combined_artifacts(run, collected, patch)
        verifier = _verifier_payload(
            run=run,
            artifacts=artifacts,
            require_artifacts=require_artifacts,
            expected_stdout_contains=expected_stdout_contains,
            create_failed=False,
        )
        status = "done" if verifier.get("ok") else "failed"

        diagnostics_mode = str(export_diagnostics or "on_failure").strip().lower()
        should_export_diagnostics = diagnostics_mode in {"always", "true", "1", "yes"} or (
            diagnostics_mode in {"on_failure", "failure", "failed"} and status != "done"
        )
        if should_export_diagnostics and session_id:
            diagnostics = _loads(metis_runtime_export_diagnostics(session_id=session_id))

        payload = _job_payload(
            job_id=job_id,
            task=task_text,
            command=command_text,
            started=started,
            status=status,
            created=created,
            run=run,
            collected=collected,
            patch=patch,
            diagnostics=diagnostics,
            verifier=verifier,
        )
        payload["message"] = (
            "Runtime job completed and verified."
            if status == "done"
            else "Runtime job finished but verification failed; inspect diagnostics and stderr."
        )
        _write_job(root, payload)
        return _json(payload)
    except Exception as exc:
        if session_id:
            try:
                diagnostics = _loads(metis_runtime_export_diagnostics(session_id=session_id))
            except Exception:
                diagnostics = {}
        payload = _job_payload(
            job_id=job_id,
            task=task_text,
            command=command_text,
            started=started,
            status="failed",
            created=created,
            run=run,
            collected=collected,
            patch=patch,
            diagnostics=diagnostics,
            verifier={"ok": False, "checks": [], "error": f"{type(exc).__name__}: {exc}"},
        )
        payload["ok"] = False
        payload["error"] = f"{type(exc).__name__}: {exc}"
        _write_job(root, payload)
        return _json(payload)


def metis_runtime_job_status(job_id: str = "", root: str = ".") -> str:
    """Return one runtime job status or list recent job manifests."""
    try:
        root_path = _job_root(root)
        jobs_dir = root_path / ".metis" / "runtime-jobs"
        target = str(job_id or "").strip()
        if target:
            path = jobs_dir / f"{target}.json"
            if not path.is_file():
                return _json_error(f"runtime job not found: {target}", code="JOB_NOT_FOUND", job_id=target)
            data = json.loads(path.read_text(encoding="utf-8"))
            data["ok"] = bool(data.get("ok", True))
            return _json(data)
        jobs = []
        if jobs_dir.is_dir():
            for path in sorted(jobs_dir.glob("job_*.json"), key=lambda item: item.stat().st_mtime, reverse=True):
                try:
                    data = json.loads(path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                jobs.append(
                    {
                        "job_id": data.get("job_id", path.stem),
                        "session_id": data.get("session_id", ""),
                        "task": data.get("task", ""),
                        "status": data.get("status", ""),
                        "backend": data.get("backend", ""),
                        "created_at": data.get("created_at", 0),
                        "updated_at": data.get("updated_at", 0),
                        "artifacts_dir": data.get("artifacts_dir", ""),
                        "diagnostics_zip": data.get("diagnostics_zip", ""),
                    }
                )
        return _json({"ok": True, "schema": RUNTIME_JOB_STATUS_SCHEMA, "root": str(root_path), "jobs": jobs[:30]})
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="JOB_STATUS_FAILED", job_id=job_id)


def _job_payload(
    *,
    job_id: str,
    task: str,
    command: str,
    started: float,
    status: str,
    created: Dict[str, Any],
    run: Dict[str, Any],
    collected: Dict[str, Any],
    patch: Dict[str, Any],
    diagnostics: Dict[str, Any],
    verifier: Dict[str, Any],
) -> Dict[str, Any]:
    artifacts = _combined_artifacts(run, collected, patch, diagnostics)
    session_id = str(created.get("session_id") or run.get("session_id") or diagnostics.get("session_id") or "")
    return {
        "ok": status == "done",
        "schema": RUNTIME_JOB_SCHEMA,
        "job_id": job_id,
        "session_id": session_id,
        "task": task,
        "status": status,
        "runtime_boundary": "metis_runtime",
        "sandbox_mode": str(created.get("mode") or "copy"),
        "backend": str(run.get("backend") or created.get("backend") or ""),
        "boundary": run.get("boundary") if isinstance(run.get("boundary"), dict) else created.get("boundary", {}),
        "fallback_reason": str(run.get("fallback_reason") or ""),
        "command": command,
        "created_at": started,
        "updated_at": time.time(),
        "duration_ms": int((time.time() - started) * 1000),
        "returncode": run.get("returncode"),
        "timed_out": bool(run.get("timed_out")),
        "stdout": str(run.get("stdout") or ""),
        "stderr": str(run.get("stderr") or ""),
        "stdout_path": str(run.get("stdout_path") or ""),
        "stderr_path": str(run.get("stderr_path") or ""),
        "workspace_dir": str(created.get("workspace_dir") or ""),
        "artifacts_dir": str(run.get("artifacts_dir") or created.get("artifacts_dir") or collected.get("artifacts_dir") or ""),
        "diagnostics_zip": str(diagnostics.get("diagnostics_zip") or run.get("diagnostics_zip") or ""),
        "patch_path": str(patch.get("patch_path") or diagnostics.get("patch_path") or ""),
        "summary_path": str(diagnostics.get("summary_path") or ""),
        "changed_files": patch.get("changed_files") if isinstance(patch.get("changed_files"), list) else diagnostics.get("changed_files", []),
        "artifacts": artifacts,
        "verifier": verifier,
        "created": created,
        "run": run,
        "collected": collected,
        "patch": patch,
        "diagnostics": diagnostics,
    }


def _verifier_payload(
    *,
    run: Dict[str, Any],
    artifacts: List[Dict[str, Any]],
    require_artifacts: bool,
    expected_stdout_contains: str,
    create_failed: bool,
) -> Dict[str, Any]:
    checks = []
    checks.append({"id": "session_created", "ok": not create_failed})
    checks.append({"id": "exit_code_zero", "ok": bool(run.get("ok")) and run.get("returncode") == 0})
    checks.append({"id": "not_timed_out", "ok": not bool(run.get("timed_out"))})
    if require_artifacts:
        checks.append({"id": "artifact_exists", "ok": any(_artifact_size(item) > 0 for item in artifacts)})
    expected = str(expected_stdout_contains or "").strip()
    if expected:
        checks.append({"id": "stdout_contains", "ok": expected in str(run.get("stdout") or ""), "expected": expected})
    return {
        "ok": all(bool(item.get("ok")) for item in checks),
        "checks": checks,
        "artifact_count": len(artifacts),
    }


def _combined_artifacts(*payloads: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen = set()
    for payload in payloads:
        raw = payload.get("artifacts") if isinstance(payload, dict) else None
        if not isinstance(raw, list):
            continue
        for item in raw:
            if not isinstance(item, dict):
                continue
            key = str(item.get("path") or item.get("relative_path") or item)
            if key in seen:
                continue
            seen.add(key)
            out.append(item)
    return out


def _artifact_size(item: Dict[str, Any]) -> int:
    value = item.get("size") or item.get("size_bytes") or item.get("bytes")
    try:
        return int(value)
    except Exception:
        return 0


def _write_job(root: str, payload: Dict[str, Any]) -> None:
    try:
        root_path = _job_root(root)
        jobs_dir = root_path / ".metis" / "runtime-jobs"
        jobs_dir.mkdir(parents=True, exist_ok=True)
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            return
        (jobs_dir / f"{job_id}.json").write_text(_json(payload), encoding="utf-8")
    except Exception:
        return


def _job_root(root: str) -> Path:
    raw = str(root or ".").strip() or "."
    try:
        path = safe_path_for_read(raw, allow_paths_outside_workspace=get_effective_sub_allow("allow_paths_outside_workspace"))
    except Exception:
        path = Path(raw).expanduser().resolve(strict=False)
    if path.is_file():
        path = path.parent
    return path


def _loads(text: str) -> Dict[str, Any]:
    try:
        data = json.loads(text or "{}")
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "raw": str(text or "")}
    return data if isinstance(data, dict) else {"ok": False, "error": "Expected JSON object", "raw": data}


def _json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _json_error(message: str, *, code: str = "RUNTIME_JOB_ERROR", job_id: str = "") -> str:
    return _json({"ok": False, "schema": RUNTIME_JOB_SCHEMA, "job_id": job_id, "code": code, "error": str(message)})


__all__ = ["metis_runtime_job", "metis_runtime_job_status", "RUNTIME_JOB_SCHEMA"]

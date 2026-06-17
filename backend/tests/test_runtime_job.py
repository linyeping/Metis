from __future__ import annotations

import json
from pathlib import Path

from backend.runtime import runtime_job


def _json(data: dict) -> str:
    return json.dumps(data)


def test_metis_runtime_job_wraps_create_run_collect_patch_and_verifier(tmp_path: Path, monkeypatch) -> None:
    calls = []

    monkeypatch.setattr(runtime_job, "_job_root", lambda root=".": tmp_path)
    monkeypatch.setattr(
        runtime_job,
        "metis_runtime_create",
        lambda **kwargs: calls.append(("create", kwargs))
        or _json(
            {
                "ok": True,
                "session_id": "rt_job",
                "backend": "metis_wsl",
                "mode": "copy",
                "workspace_dir": str(tmp_path / ".metis" / "runtime" / "rt_job" / "workspace"),
                "artifacts_dir": str(tmp_path / ".metis" / "artifacts" / "rt_job"),
            }
        ),
    )
    monkeypatch.setattr(
        runtime_job,
        "metis_runtime_run",
        lambda **kwargs: calls.append(("run", kwargs))
        or _json(
            {
                "ok": True,
                "session_id": "rt_job",
                "backend": "metis_wsl",
                "returncode": 0,
                "timed_out": False,
                "stdout": "REPORT_OK",
                "stderr": "",
                "artifacts_dir": str(tmp_path / ".metis" / "artifacts" / "rt_job"),
                "artifacts": [{"path": str(tmp_path / ".metis" / "artifacts" / "rt_job" / "report.md"), "relative_path": "report.md", "size": 12}],
            }
        ),
    )
    monkeypatch.setattr(
        runtime_job,
        "metis_runtime_collect_artifacts",
        lambda **kwargs: calls.append(("collect", kwargs))
        or _json(
            {
                "ok": True,
                "session_id": "rt_job",
                "artifacts_dir": str(tmp_path / ".metis" / "artifacts" / "rt_job"),
                "artifacts": [{"path": str(tmp_path / ".metis" / "artifacts" / "rt_job" / "report.md"), "relative_path": "report.md", "size": 12}],
            }
        ),
    )
    monkeypatch.setattr(
        runtime_job,
        "metis_runtime_export_patch",
        lambda **kwargs: calls.append(("patch", kwargs))
        or _json({"ok": True, "session_id": "rt_job", "patch_path": str(tmp_path / ".metis" / "artifacts" / "rt_job" / "rt_job.patch"), "changed_files": []}),
    )
    monkeypatch.setattr(runtime_job, "metis_runtime_export_diagnostics", lambda **kwargs: calls.append(("diagnostics", kwargs)) or _json({"ok": True}))

    result = json.loads(
        runtime_job.metis_runtime_job(
            task="run report",
            command="python report.py",
            root=str(tmp_path),
            collect_artifacts=True,
            require_artifacts=True,
            expected_stdout_contains="REPORT_OK",
        )
    )

    assert result["ok"] is True
    assert result["schema"] == runtime_job.RUNTIME_JOB_SCHEMA
    assert result["runtime_boundary"] == "metis_runtime"
    assert result["backend"] == "metis_wsl"
    assert result["status"] == "done"
    assert result["verifier"]["ok"] is True
    assert [name for name, _kwargs in calls] == ["create", "run", "collect", "patch"]
    create_kwargs = calls[0][1]
    assert create_kwargs["strict_sandbox"] is False
    assert (tmp_path / ".metis" / "runtime-jobs" / f"{result['job_id']}.json").is_file()

    listed = json.loads(runtime_job.metis_runtime_job_status(root=str(tmp_path)))
    assert listed["ok"] is True
    assert listed["jobs"][0]["job_id"] == result["job_id"]


def test_metis_runtime_job_exports_diagnostics_on_failure(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(runtime_job, "_job_root", lambda root=".": tmp_path)
    monkeypatch.setattr(runtime_job, "metis_runtime_create", lambda **kwargs: _json({"ok": True, "session_id": "rt_fail", "backend": "local", "mode": "copy"}))
    monkeypatch.setattr(
        runtime_job,
        "metis_runtime_run",
        lambda **kwargs: _json({"ok": False, "session_id": "rt_fail", "backend": "local", "returncode": 1, "timed_out": False, "stdout": "", "stderr": "boom"}),
    )
    monkeypatch.setattr(runtime_job, "metis_runtime_collect_artifacts", lambda **kwargs: _json({"ok": True, "artifacts": []}))
    monkeypatch.setattr(runtime_job, "metis_runtime_export_patch", lambda **kwargs: _json({"ok": True, "changed_files": [], "patch_path": ""}))
    monkeypatch.setattr(
        runtime_job,
        "metis_runtime_export_diagnostics",
        lambda **kwargs: _json({"ok": True, "session_id": "rt_fail", "diagnostics_zip": str(tmp_path / "diag.zip")}),
    )

    result = json.loads(runtime_job.metis_runtime_job(task="failing job", command="python fail.py", root=str(tmp_path)))

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert result["diagnostics_zip"].endswith("diag.zip")
    assert any(check["id"] == "exit_code_zero" and not check["ok"] for check in result["verifier"]["checks"])


def test_metis_runtime_job_can_require_strict_sandbox(tmp_path: Path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(runtime_job, "_job_root", lambda root=".": tmp_path)
    monkeypatch.setattr(
        runtime_job,
        "metis_runtime_create",
        lambda **kwargs: calls.append(("create", kwargs))
        or _json({"ok": False, "code": "STRICT_SANDBOX_UNAVAILABLE", "error": "strict"}),
    )

    result = json.loads(
        runtime_job.metis_runtime_job(
            task="strict job",
            command="python main.py",
            root=str(tmp_path),
            strict_sandbox=True,
        )
    )

    assert result["ok"] is False
    assert result["status"] == "failed"
    assert calls[0][1]["strict_sandbox"] is True
    assert any(check["id"] == "session_created" and not check["ok"] for check in result["verifier"]["checks"])

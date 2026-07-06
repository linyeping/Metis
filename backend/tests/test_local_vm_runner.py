from __future__ import annotations

import json

from backend.runtime import local_vm_runner
from backend.runtime.local_vm_runner import LocalVmCommand, run_local_vm_command


def test_local_vm_returns_import_plan_when_metis_wsl_is_not_installed(monkeypatch) -> None:
    status = {
        "ok": True,
        "available": False,
        "ready_to_import": True,
        "reason": "Metis managed WSL runtime can be imported",
        "distro_name": "MetisRuntime",
    }
    plan = {
        "ok": True,
        "dry_run": True,
        "command": ["wsl.exe", "--import", "MetisRuntime", "C:\\Metis", "rootfs.vhdx", "--version", "2", "--vhd"],
    }
    called = {"job": False}

    monkeypatch.setattr(local_vm_runner, "metis_wsl_runtime_status", lambda root=".": json.dumps(status))
    monkeypatch.setattr(local_vm_runner, "metis_wsl_runtime_import", lambda root=".", dry_run=True: json.dumps(plan))
    monkeypatch.setattr(local_vm_runner, "metis_runtime_job", lambda **_kwargs: called.__setitem__("job", True))

    result = run_local_vm_command(LocalVmCommand(command="echo hello", workspace_root="D:\\repo"))

    assert result["ok"] is False
    assert result["backend"] == "metis_wsl"
    assert result["code"] == "METIS_WSL_RUNTIME_UNAVAILABLE"
    assert result["wsl_runtime"] == status
    assert result["import_plan"] == plan
    assert called["job"] is False


def test_local_vm_runs_through_metis_wsl_backend(monkeypatch) -> None:
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        local_vm_runner,
        "metis_wsl_runtime_status",
        lambda root=".": json.dumps({"ok": True, "available": True, "distro_name": "MetisRuntime"}),
    )

    def fake_job(**kwargs):
        captured.update(kwargs)
        return json.dumps({"ok": True, "status": "done", "backend": "metis_wsl"})

    monkeypatch.setattr(local_vm_runner, "metis_runtime_job", fake_job)

    result = run_local_vm_command(
        LocalVmCommand(command="python -V", workspace_root="D:\\repo", cwd="src", timeout=7, allow_network=True)
    )

    assert result["ok"] is True
    assert result["backend"] == "metis_wsl"
    assert result["job"]["backend"] == "metis_wsl"
    assert captured["backend"] == "metis_wsl"
    assert captured["strict_sandbox"] is True
    assert captured["cwd"] == "src"
    assert captured["timeout"] == 7
    assert captured["allow_network"] is True

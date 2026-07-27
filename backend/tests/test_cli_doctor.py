from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import pytest
from backend.cli import doctor
from backend.cli.app import main
from backend.cli.args import DoctorCommandArgs, SandboxCommandArgs, parse_args

_REAL_SANDBOX_STATUS = doctor.sandbox_status_payload


@pytest.fixture(autouse=True)
def isolated_diagnostics(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("METIS_HOME", str(tmp_path / "metis-home"))
    monkeypatch.delenv("METIS_LLM_API_KEY", raising=False)
    monkeypatch.setattr(doctor, "is_available", lambda: True)
    monkeypatch.setattr(doctor, "read_api_key", lambda: "test-credential")
    monkeypatch.setattr(
        doctor,
        "sandbox_status_payload",
        lambda **_kwargs: {
            "schema": doctor.SANDBOX_STATUS_SCHEMA,
            "ok": True,
            "ready": True,
            "supported": True,
            "service": {"responding": True, "protocol": "metis.vm.svc.v2"},
            "needs": [],
            "message": "ready",
        },
    )


def test_parse_doctor_and_sandbox_commands() -> None:
    doctor_args = parse_args(["doctor", "--deep", "--output-format", "json"])
    assert isinstance(doctor_args, DoctorCommandArgs)
    assert doctor_args.deep is True
    assert doctor_args.output_format == "json"

    sandbox_args = parse_args(["sandbox", "repair", "--allow-download", "--force"])
    assert isinstance(sandbox_args, SandboxCommandArgs)
    assert sandbox_args.action == "repair"
    assert sandbox_args.allow_download is True
    assert sandbox_args.force is True


def test_doctor_is_read_only_and_never_emits_api_key(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "metis-home"
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    secret = "sk-do-not-print-this-value"
    monkeypatch.setenv("METIS_LLM_API_KEY", secret)
    monkeypatch.setenv("METIS_LLM_BACKEND", "deepseek")
    monkeypatch.setenv("METIS_LLM_MODEL", "deepseek-chat")

    payload = doctor.doctor_payload(workspace=workspace)
    rendered = json.dumps(payload)

    assert payload["schema"] == doctor.DOCTOR_SCHEMA
    assert payload["ok"] is True
    assert secret not in rendered
    assert not home.exists()
    assert secret not in doctor._safe_message(f"download failed with api_key={secret}")


def test_doctor_reports_invalid_settings_without_exposing_contents(tmp_path: Path) -> None:
    home = tmp_path / "metis-home"
    home.mkdir()
    (home / "settings.json").write_text('{"api_key":"sk-secret",', encoding="utf-8")
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    payload = doctor.doctor_payload(workspace=workspace)
    settings = next(item for item in payload["checks"] if item["id"] == "settings")

    assert payload["ok"] is False
    assert settings["status"] == "fail"
    assert "sk-secret" not in json.dumps(payload)


def test_session_database_check_uses_read_only_integrity_probe(tmp_path: Path) -> None:
    path = tmp_path / "session-state.db"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE schema_migrations(version INTEGER PRIMARY KEY)")
        connection.execute("INSERT INTO schema_migrations(version) VALUES (3)")
        connection.commit()
    before = path.stat().st_mtime_ns

    result = doctor._session_database_check(path)

    assert result["status"] == "pass"
    assert result["details"]["integrity"] == "ok"
    assert result["details"]["schema_version"] == 3
    assert path.stat().st_mtime_ns == before


def test_cli_doctor_json_contract_and_exit_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setenv("METIS_LLM_BACKEND", "fake")
    monkeypatch.setenv("METIS_LLM_MODEL", "fake-model")
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = main(
        ["doctor", "--workspace", str(workspace), "--output-format", "json"],
        stdout=stdout,
        stderr=stderr,
    )
    payload = json.loads(stdout.getvalue())

    assert code == 0
    assert stderr.getvalue() == ""
    assert payload["schema"] == doctor.DOCTOR_SCHEMA
    assert payload["summary"]["fail"] == 0


def test_sandbox_status_contract_compacts_service_and_protocol(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(doctor, "sandbox_status_payload", _REAL_SANDBOX_STATUS)
    monkeypatch.setattr(
        "backend.runtime.runtime_provision.provision_status",
        lambda deep=False: {
            "supported": True,
            "ready": False,
            "hcs_available": True,
            "hcs_reason": "service unavailable",
            "vm_platform_enabled": True,
            "service_installed": True,
            "service_running": True,
            "service_responding": False,
            "service_pipe_responding": False,
            "service_version": "0.2.0",
            "service_expected_version": "0.3.0",
            "service_protocol": "metis.vm.svc.v1",
            "service_upgrade_required": True,
            "bundle_installed": True,
            "bundle_path": str(tmp_path / "bundle"),
            "needs": ["upgrade_service"],
            "actions": [{"id": "upgrade_service"}],
            "ux_summary": "upgrade required",
        },
    )

    payload = doctor.sandbox_status_payload(workspace=workspace)

    assert payload["schema"] == doctor.SANDBOX_STATUS_SCHEMA
    assert payload["ready"] is False
    assert payload["service"]["upgrade_required"] is True
    assert payload["service"]["expected_protocol"] == "metis.vm.svc.v2"
    assert payload["needs"] == ["upgrade_service"]


def test_sandbox_repair_requires_explicit_download(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    before = {
        "schema": doctor.SANDBOX_STATUS_SCHEMA,
        "ok": True,
        "ready": False,
        "supported": True,
        "service": {"installed": True},
        "needs": ["install_pack"],
        "message": "pack missing",
    }
    calls: list[dict] = []
    monkeypatch.setattr(doctor, "sandbox_status_payload", lambda **_kwargs: before)

    def fake_repair(**kwargs):
        calls.append(kwargs)
        return {"ok": False, "code": "METIS_RUNTIME_PACK_SOURCE_MISSING", "message": "No source"}

    monkeypatch.setattr("backend.runtime.runtime_manager.runtime_manager_repair", fake_repair)
    monkeypatch.setattr("backend.runtime.runtime_provision.run_provision_elevated", lambda _actions: {"ok": True})

    payload = doctor.sandbox_repair_payload(workspace=workspace, allow_download=False)

    assert payload["schema"] == doctor.SANDBOX_REPAIR_SCHEMA
    assert payload["ok"] is False
    assert calls == [{"root": str(workspace), "source": "auto", "allow_download": False, "force": False}]
    assert "--allow-download" in payload["message"]


def test_sandbox_status_not_ready_exits_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.setattr(
        doctor,
        "sandbox_status_payload",
        lambda **_kwargs: {
            "schema": doctor.SANDBOX_STATUS_SCHEMA,
            "ok": True,
            "ready": False,
            "supported": True,
            "needs": ["install_pack"],
            "message": "pack missing",
        },
    )
    stdout = io.StringIO()

    code = main(["sandbox", "status", "--workspace", str(workspace)], stdout=stdout, stderr=io.StringIO())

    assert code == 3
    assert "install_pack" in stdout.getvalue()

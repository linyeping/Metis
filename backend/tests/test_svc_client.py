from __future__ import annotations

from types import SimpleNamespace

from backend.runtime import isolated_runtime, svc_client


def test_service_available_requires_exact_protocol(monkeypatch):
    monkeypatch.setattr(
        svc_client,
        "_rpc",
        lambda _messages: [{"ok": True, "result": {"protocol": "metis.vm.svc.v1"}}],
    )
    assert svc_client.service_available() is False

    monkeypatch.setattr(
        svc_client,
        "_rpc",
        lambda _messages: [{"ok": True, "result": {"protocol": svc_client.PROTOCOL}}],
    )
    assert svc_client.service_available() is True


def test_hcs_service_request_declares_boundary_and_not_session_paths(tmp_path, monkeypatch):
    source = tmp_path / "project"
    workspace = source / ".metis" / "runtime" / "rt_test" / "workspace"
    artifacts = source / ".metis" / "artifacts" / "rt_test"
    diagnostics = source / ".metis" / "diagnostics" / "rt_test"
    workspace.mkdir(parents=True)
    artifacts.mkdir(parents=True)
    diagnostics.mkdir(parents=True)
    manifest = SimpleNamespace(
        session_id="rt_test",
        paths=SimpleNamespace(
            source_root=source,
            workspace_dir=workspace,
            artifacts_dir=artifacts,
            diagnostics_dir=diagnostics,
        ),
    )
    captured = {}

    monkeypatch.setattr(svc_client, "service_available", lambda: True)

    def fake_run(params):
        captured.update(params)
        return {"returncode": 0, "stdout": "ok", "stderr": "", "timed_out": False}

    monkeypatch.setattr(svc_client, "run_job_via_service", fake_run)
    monkeypatch.setattr("backend.runtime.hcs_client.find_metis_bundle", lambda: None)

    result = isolated_runtime._run_hcs_command(
        manifest,
        "echo ok",
        work_dir=workspace,
        timeout=30,
        env_map={"TEST_ENV": "1"},
        network_allowed=False,
    )

    assert result.backend == "hcs"
    assert captured["source_root"] == str(source)
    assert captured["workspace_dir"] == str(workspace)
    assert captured["env"] == {"TEST_ENV": "1"}
    assert captured["request_id"].startswith("req_")
    assert "session_data_dir" not in captured
    assert "session_data_template" not in captured

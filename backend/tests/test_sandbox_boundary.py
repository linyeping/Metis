from __future__ import annotations

from pathlib import Path

from backend.runtime.sandbox_boundary import (
    SANDBOX_BOUNDARY_SCHEMA,
    boundary_policy_for_task,
    runtime_manifest_boundary,
    strict_sandbox_requested,
)


def test_boundary_policy_for_artifact_workflow_defaults_to_regular_fallback() -> None:
    policy = boundary_policy_for_task("artifact_workflow", "生成实验报告和图表").to_dict()

    assert policy["schema"] == SANDBOX_BOUNDARY_SCHEMA
    assert policy["execution_boundary"] == "metis_runtime"
    assert policy["recommended_tool"] == "metis_runtime_job"
    assert policy["fallback_mode"] == "regular"
    assert policy["fallback_order"] == ["metis_wsl", "wsl", "docker", "local-copy"]
    assert policy["desktop_control_allowed"] is False
    assert ".env" in policy["read_deny_patterns"]


def test_strict_sandbox_requested_removes_local_fallback() -> None:
    assert strict_sandbox_requested("严格沙箱运行，禁止回退") is True

    policy = boundary_policy_for_task("code", "strict sandbox run pytest").to_dict()

    assert policy["strict_sandbox"] is True
    assert policy["fallback_mode"] == "strict"
    assert policy["fallback_order"] == ["metis_wsl", "wsl", "docker"]


def test_runtime_manifest_boundary_lists_runtime_write_roots(tmp_path: Path) -> None:
    boundary = runtime_manifest_boundary(
        workspace_dir=tmp_path / "workspace",
        artifacts_dir=tmp_path / "artifacts",
        diagnostics_dir=tmp_path / "diagnostics",
        source_root=tmp_path / "source",
        backend="local",
        mode="copy",
        allow_network=False,
        strict_sandbox=False,
    )

    assert boundary["schema"] == SANDBOX_BOUNDARY_SCHEMA
    assert boundary["network"]["allow"] is False
    assert str(tmp_path / "workspace") in boundary["write_allow_roots"]
    assert str(tmp_path / "source") not in boundary["write_allow_roots"]
    assert boundary["local_copy_warning"]

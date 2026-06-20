from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import zipfile
from pathlib import Path

from backend.runtime import isolated_runtime
from backend.runtime.isolated_runtime import (
    metis_rootfs_asset_download,
    metis_rootfs_asset_register,
    metis_rootfs_asset_status,
    metis_rootfs_build,
    metis_rootfs_builder_status,
    metis_rootfs_image_build,
    metis_rootfs_image_builder_status,
    metis_rootfs_source_status,
    metis_runtime_bundle_package,
    metis_runtime_bundle_package_v2,
    metis_runtime_bundle_prepare,
    metis_vm_direct_assets_prepare,
    metis_vm_direct_runner_prepare,
    metis_vm_direct_runner_smoke,
    metis_vm_hcs_starter_prepare,
    metis_vm_hcs_starter_start,
    metis_vm_guest_handshake_prepare,
    metis_vm_guest_handshake_verify,
    metis_vm_rootfs_boot_verifier_prepare,
    metis_vm_rootfs_boot_verify,
    metis_vm_bundle_status,
    metis_vm_pack_adopt_reference,
    metis_vm_pack_scaffold,
    metis_wsl_runtime_import,
    metis_wsl_runtime_status,
    metis_sandbox_status,
    metis_runtime_collect_artifacts,
    metis_runtime_create,
    metis_runtime_export_diagnostics,
    metis_runtime_export_patch,
    metis_runtime_run,
    metis_runtime_status,
)
from backend.runtime.tool_registry import ToolRegistry, register_builtin_tools
from backend.tools.coding.foundation.core_mechanisms.execution_boundary_context import (
    workspace_root_override,
)
import pytest


@pytest.fixture(autouse=True)
def _no_hcs_autoselect(monkeypatch):
    """Legacy backend-selection tests target the vm/wsl/docker/local tiers.
    HCS is a newer top-preference tier probed from real host state (sandbox
    service / Hyper-V), so disable it here for deterministic results regardless
    of whether the host running the tests has the sandbox installed."""
    monkeypatch.setattr(isolated_runtime, "_hcs_backend_available", lambda: False)


def _json(text: str) -> dict:
    payload = json.loads(text)
    assert isinstance(payload, dict)
    return payload


def _mock_no_host_sandbox(monkeypatch) -> None:
    monkeypatch.setattr(isolated_runtime, "_reference_vm_bundle_candidates", lambda: [])
    monkeypatch.setattr(
        isolated_runtime,
        "_detect_metis_wsl_runtime",
        lambda *args, **kwargs: {
            "available": False,
            "installed": False,
            "ready_to_import": False,
            "reason": "mocked out for VM backend test",
        },
    )
    monkeypatch.setattr(
        isolated_runtime,
        "_detect_wsl",
        lambda *args, **kwargs: {"available": False, "distros": [], "reason": "mocked out"},
    )
    monkeypatch.setattr(
        isolated_runtime,
        "_detect_docker",
        lambda *args, **kwargs: {"available": False, "reason": "mocked out"},
    )


def _prepare_guest_protocol_vm_bundle(workspace: Path, bundle: Path) -> dict:
    bundle.mkdir(parents=True, exist_ok=True)
    for name in ("rootfs.vhdx", "vmlinuz", "initrd"):
        (bundle / name).write_bytes(f"fake {name}".encode("utf-8"))
    prepared = _json(
        metis_vm_direct_runner_prepare(
            root=str(workspace),
            bundle_path=str(bundle),
            version="vm-backend-test",
        )
    )
    assert prepared["ok"] is True
    return prepared


def test_isolated_runtime_runs_in_copy_and_exports_artifacts_and_patch(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)
    monkeypatch.setenv("METIS_PYTHON", sys.executable)

    (workspace / "src").mkdir()
    (workspace / "src" / "app.py").write_text("VALUE = 1\n", encoding="utf-8")
    (workspace / "node_modules").mkdir()
    (workspace / "node_modules" / "ignored.js").write_text("ignored\n", encoding="utf-8")
    (workspace / ".metis").mkdir()
    (workspace / ".metis" / "memory.json").write_text("{}", encoding="utf-8")

    with workspace_root_override(str(workspace)):
        created = _json(metis_runtime_create(task="runtime smoke"))
        assert created["ok"] is True
        session_id = created["session_id"]
        runtime_workspace = Path(created["workspace_dir"])
        assert created["boundary"]["strict_sandbox"] is False
        assert str(runtime_workspace) in created["boundary"]["write_allow_roots"]
        assert created["artifacts_dir"] in created["boundary"]["write_allow_roots"]
        assert (runtime_workspace / "src" / "app.py").is_file()
        assert not (runtime_workspace / "node_modules" / "ignored.js").exists()
        assert not (runtime_workspace / ".metis" / "memory.json").exists()

        script = runtime_workspace / "run_task.py"
        script.write_text(
            "import os\n"
            "from pathlib import Path\n"
            "Path('src/app.py').write_text('VALUE = 2\\n', encoding='utf-8')\n"
            "Path('workspace-output.txt').write_text('collected\\n', encoding='utf-8')\n"
            "out = Path(os.environ['METIS_RUNTIME_ARTIFACTS_DIR'])\n"
            "out.mkdir(parents=True, exist_ok=True)\n"
            "(out / 'result.txt').write_text('done\\n', encoding='utf-8')\n"
            "print('runtime ok')\n"
            ,
            encoding="utf-8",
        )
        command = f'"{sys.executable}" run_task.py'
        run = _json(metis_runtime_run(session_id=session_id, command=command, timeout=30))
        assert run["ok"] is True
        assert "runtime ok" in run["stdout"]
        assert any(str(item["relative_path"]).endswith("result.txt") for item in run["artifacts"])

        collected = _json(metis_runtime_collect_artifacts(session_id=session_id, patterns=["*.txt"]))
        assert collected["ok"] is True
        assert any(str(item["relative_path"]).endswith("workspace-output.txt") for item in collected["copied"])

        # Source project stays untouched until a patch is explicitly applied later.
        assert (workspace / "src" / "app.py").read_text(encoding="utf-8") == "VALUE = 1\n"

        patch = _json(metis_runtime_export_patch(session_id=session_id))
        assert patch["ok"] is True
        assert patch["changed_file_count"] >= 1
        patch_text = Path(patch["patch_path"]).read_text(encoding="utf-8")
        assert "VALUE = 2" in patch_text

        status = _json(metis_runtime_status(session_id=session_id))
        assert status["ok"] is True
        assert status["runs"]

        diagnostics = _json(metis_runtime_export_diagnostics(session_id=session_id))
        assert diagnostics["ok"] is True
        assert Path(diagnostics["diagnostics_zip"]).is_file()


def test_isolated_runtime_blocks_network_commands_by_default(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)

    with workspace_root_override(str(workspace)):
        created = _json(metis_runtime_create(task="network guard"))
        blocked = _json(metis_runtime_run(session_id=created["session_id"], command="curl https://example.com"))

    assert blocked["ok"] is False
    assert blocked["code"] == "NETWORK_BLOCKED"


def test_isolated_runtime_tools_are_registered_and_in_lean_profile() -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry)

    expected = {
        "metis_rootfs_asset_status",
        "metis_rootfs_asset_register",
        "metis_rootfs_source_status",
        "metis_rootfs_asset_download",
        "metis_rootfs_builder_status",
        "metis_rootfs_build",
        "metis_rootfs_image_builder_status",
        "metis_rootfs_image_build",
        "metis_runtime_bundle_package",
        "metis_runtime_bundle_package_v2",
        "metis_runtime_bundle_prepare",
        "metis_vm_direct_assets_prepare",
        "metis_vm_direct_runner_prepare",
        "metis_vm_direct_runner_smoke",
        "metis_vm_hcs_starter_prepare",
        "metis_vm_hcs_starter_start",
        "metis_vm_guest_handshake_prepare",
        "metis_vm_guest_handshake_verify",
        "metis_vm_rootfs_boot_verifier_prepare",
        "metis_vm_rootfs_boot_verify",
        "metis_vm_bundle_status",
        "metis_vm_pack_adopt_reference",
        "metis_vm_pack_scaffold",
        "metis_wsl_runtime_status",
        "metis_wsl_runtime_import",
        "metis_sandbox_status",
        "metis_runtime_create",
        "metis_runtime_run",
        "metis_runtime_collect_artifacts",
        "metis_runtime_export_patch",
        "metis_runtime_export_diagnostics",
        "metis_runtime_status",
    }
    assert expected.issubset(set(registry.tool_names))
    lean_names = {
        (schema.get("function") or {}).get("name")
        for schema in registry.get_schemas_for_profile("lean", format="openai", include_desktop=False)
    }
    assert expected.issubset(lean_names)
    assert registry.get_tool_profile("metis_rootfs_asset_status").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_rootfs_asset_status").approval == "never"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_rootfs_asset_register").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_rootfs_asset_register").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_rootfs_source_status").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_rootfs_source_status").approval == "never"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_rootfs_asset_download").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_rootfs_asset_download").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_rootfs_builder_status").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_rootfs_builder_status").approval == "never"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_rootfs_build").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_rootfs_build").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_rootfs_image_builder_status").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_rootfs_image_builder_status").approval == "never"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_rootfs_image_build").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_rootfs_image_build").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_runtime_bundle_package").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_runtime_bundle_package").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_runtime_bundle_package_v2").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_runtime_bundle_package_v2").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_runtime_bundle_prepare").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_runtime_bundle_prepare").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_direct_assets_prepare").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_direct_assets_prepare").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_direct_runner_prepare").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_direct_runner_prepare").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_direct_runner_smoke").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_direct_runner_smoke").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_hcs_starter_prepare").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_hcs_starter_prepare").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_hcs_starter_start").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_hcs_starter_start").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_guest_handshake_prepare").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_guest_handshake_prepare").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_guest_handshake_verify").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_guest_handshake_verify").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_rootfs_boot_verifier_prepare").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_rootfs_boot_verifier_prepare").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_rootfs_boot_verify").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_rootfs_boot_verify").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_bundle_status").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_bundle_status").approval == "never"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_pack_adopt_reference").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_pack_adopt_reference").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_pack_scaffold").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_vm_pack_scaffold").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_wsl_runtime_status").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_wsl_runtime_status").approval == "never"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_wsl_runtime_import").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_wsl_runtime_import").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_sandbox_status").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_sandbox_status").approval == "never"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_runtime_create").toolset == "runtime"  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_runtime_create").destructive is True  # type: ignore[union-attr]
    assert registry.get_tool_profile("metis_runtime_status").approval == "never"  # type: ignore[union-attr]


def test_sandbox_status_and_auto_backend_fallback(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)

    fake_status = {
        "preferred": "local",
        "wsl": {"available": False, "reason": "no distro"},
        "docker": {"available": False, "reason": "daemon unavailable"},
        "local": {"available": True, "kind": "local_copy"},
    }
    monkeypatch.setattr(isolated_runtime, "_detect_sandbox_backends", lambda **_kwargs: fake_status)

    with workspace_root_override(str(workspace)):
        status = _json(metis_sandbox_status())
        created = _json(metis_runtime_create(task="fallback", backend="auto"))

    assert status["ok"] is True
    assert status["preferred"] == "local"
    assert created["ok"] is True
    assert created["backend"] == "local"
    assert created["sandbox"]["requested"] == "auto"
    assert "auto selected" in created["sandbox"]["fallback_reason"]


def test_strict_sandbox_refuses_local_copy_fallback(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)

    fake_status = {
        "preferred": "local",
        "metis_wsl": {"available": False, "reason": "no metis runtime"},
        "vm_pack": {"runnable": False, "reason": "no vm pack"},
        "wsl": {"available": False, "reason": "no distro"},
        "docker": {"available": False, "reason": "daemon unavailable"},
        "local": {"available": True, "kind": "local_copy"},
    }
    monkeypatch.setattr(isolated_runtime, "_detect_sandbox_backends", lambda **_kwargs: fake_status)

    with workspace_root_override(str(workspace)):
        created = _json(metis_runtime_create(task="strict fallback", backend="auto", strict_sandbox=True))

    assert created["ok"] is False
    assert created["code"] == "STRICT_SANDBOX_UNAVAILABLE"


def test_vm_bundle_status_detects_bundle_but_does_not_select_unimplemented_runner(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    bundle = tmp_path / "metisvm.bundle"
    workspace.mkdir()
    backend_cwd.mkdir()
    bundle.mkdir()
    monkeypatch.chdir(backend_cwd)

    for name in ("rootfs.vhdx", "vmlinuz", "initrd"):
        (bundle / name).write_bytes(b"fake")

    monkeypatch.setattr(
        isolated_runtime,
        "_detect_vm_host_capabilities",
        lambda: {
            "platform": "windows",
            "windows": True,
            "hcsdiag": "hcsdiag.exe",
            "vmcompute_state": "RUNNING",
            "vmcompute_available": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(isolated_runtime, "_reference_vm_bundle_candidates", lambda: [])
    monkeypatch.setattr(
        isolated_runtime,
        "_detect_metis_wsl_runtime",
        lambda *args, **kwargs: {
            "available": False,
            "installed": False,
            "ready_to_import": False,
            "reason": "mocked out for VM pack fallback test",
        },
    )
    monkeypatch.setattr(
        isolated_runtime,
        "_detect_wsl",
        lambda *args, **kwargs: {"available": False, "distros": [], "reason": "mocked out"},
    )
    monkeypatch.setattr(
        isolated_runtime,
        "_detect_docker",
        lambda *args, **kwargs: {"available": False, "reason": "mocked out"},
    )

    with workspace_root_override(str(workspace)):
        status = _json(metis_vm_bundle_status(bundle_path=str(bundle)))
        sandbox = _json(metis_sandbox_status(vm_bundle_path=str(bundle)))
        created = _json(metis_runtime_create(task="vm fallback", backend="vm", vm_bundle_path=str(bundle)))

    assert status["ok"] is True
    assert status["bundle_detected"] is True
    assert status["selected_bundle"]["ready"] is True
    assert status["runner_available"] is False
    assert sandbox["vm_pack"]["bundle_detected"] is True
    assert sandbox["preferred"] == "local"
    assert created["ok"] is True
    assert created["backend"] == "local"
    assert created["sandbox"]["requested"] == "vm"
    assert "VM Pack unavailable" in created["sandbox"]["fallback_reason"]


def test_vm_pack_scaffold_creates_metis_blueprint_without_boot_assets(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)
    monkeypatch.setattr(isolated_runtime, "_reference_vm_bundle_candidates", lambda: [])

    with workspace_root_override(str(workspace)):
        scaffold = _json(metis_vm_pack_scaffold())
        status = _json(metis_vm_bundle_status())

    bundle = Path(scaffold["bundle_path"])
    assert scaffold["ok"] is True
    assert (bundle / "metis-vm-pack.json").is_file()
    assert (bundle / "guest" / "PROTOCOL.md").is_file()
    assert (bundle / "guest" / "metisd.py").is_file()
    assert status["ok"] is True
    assert status["bundle_detected"] is True
    assert status["blueprint_detected"] is True
    assert status["metis_owned_bundle_detected"] is False
    assert status["runner_available"] is False
    assert set(status["selected_bundle"]["missing_required"]) == {"rootfs.vhdx", "vmlinuz", "initrd"}


def test_vm_pack_adopt_reference_writes_plan_without_copying_assets(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    reference = tmp_path / "claudevm.bundle"
    workspace.mkdir()
    backend_cwd.mkdir()
    reference.mkdir()
    monkeypatch.chdir(backend_cwd)
    monkeypatch.setattr(isolated_runtime, "_reference_vm_bundle_candidates", lambda: [])

    for name in ("rootfs.vhdx", "vmlinuz", "initrd", "sessiondata.vhdx", "smol-bin.vhdx"):
        (reference / name).write_bytes(f"fake {name}".encode("utf-8"))
        (reference / f".{name}.origin").write_text("6d1538ba6fecc4e5c5583993c4b30bb1875f0f5a\n", encoding="utf-8")

    with workspace_root_override(str(workspace)):
        adopted = _json(metis_vm_pack_adopt_reference(reference_bundle_path=str(reference)))
        status = _json(metis_vm_bundle_status(bundle_path=adopted["bundle_path"]))

    bundle = Path(adopted["bundle_path"])
    manifest = json.loads((bundle / "metis-vm-pack.json").read_text(encoding="utf-8"))
    plan = json.loads((bundle / "reference-adoption-plan.json").read_text(encoding="utf-8"))
    rootfs_plan = next(item for item in plan["files"] if item["name"] == "rootfs.vhdx")

    assert adopted["ok"] is True
    assert adopted["copy_assets"] is False
    assert adopted["copied_assets"] == []
    assert plan["reference_only"] is True
    assert plan["ready_shape"] is True
    assert rootfs_plan["origin"] == "6d1538ba6fecc4e5c5583993c4b30bb1875f0f5a"
    assert not (bundle / "rootfs.vhdx").exists()
    assert manifest["reference_adoption"]["reference_only"] is True
    assert manifest["assets"]["rootfs"]["reference_only"] is True
    assert status["metis_owned_bundle_detected"] is False
    assert status["configured_candidates"][0]["metis_owned"] is False


def test_vm_pack_adopt_reference_can_copy_assets_when_explicit(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    reference = tmp_path / "claudevm.bundle"
    workspace.mkdir()
    backend_cwd.mkdir()
    reference.mkdir()
    monkeypatch.chdir(backend_cwd)
    monkeypatch.setattr(isolated_runtime, "_reference_vm_bundle_candidates", lambda: [])

    expected_digest = ""
    for name in ("rootfs.vhdx", "vmlinuz", "initrd"):
        payload = f"fake {name}".encode("utf-8")
        if name == "rootfs.vhdx":
            expected_digest = hashlib.sha256(payload).hexdigest()
        (reference / name).write_bytes(payload)
        (reference / f".{name}.origin").write_text("local-test-origin\n", encoding="utf-8")

    with workspace_root_override(str(workspace)):
        adopted = _json(
            metis_vm_pack_adopt_reference(
                reference_bundle_path=str(reference),
                copy_assets=True,
                hash_assets=True,
            )
        )
        status = _json(metis_vm_bundle_status(bundle_path=adopted["bundle_path"]))

    bundle = Path(adopted["bundle_path"])
    manifest = json.loads((bundle / "metis-vm-pack.json").read_text(encoding="utf-8"))

    assert adopted["ok"] is True
    assert adopted["copy_assets"] is True
    assert (bundle / "rootfs.vhdx").read_bytes() == b"fake rootfs.vhdx"
    assert (bundle / ".rootfs.vhdx.origin").read_text(encoding="utf-8").strip() == "local-test-origin"
    assert manifest["reference_adoption"]["reference_only"] is True
    assert manifest["reference_adoption"]["copied_asset_count"] == 3
    assert manifest["assets"]["rootfs"]["sha256"] == expected_digest
    assert status["selected_bundle"]["ready"] is True
    assert status["selected_bundle"]["metis_owned"] is False


def test_rootfs_asset_register_writes_manifest_and_verifies_sha256(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)
    source = workspace / "downloads" / "metis-rootfs.tar"
    source.parent.mkdir()
    source.write_text("fake rootfs", encoding="utf-8")

    with workspace_root_override(str(workspace)):
        scaffold = _json(metis_vm_pack_scaffold())
        registered = _json(metis_rootfs_asset_register(rootfs_path=str(source)))
        status = _json(metis_rootfs_asset_status())

    bundle = Path(scaffold["bundle_path"])
    rootfs = bundle / "rootfs.tar"
    manifest = json.loads((bundle / "metis-vm-pack.json").read_text(encoding="utf-8"))
    assert registered["ok"] is True
    assert registered["copied"] is True
    assert rootfs.is_file()
    assert manifest["assets"]["rootfs"]["path"] == "rootfs.tar"
    assert len(manifest["assets"]["rootfs"]["sha256"]) == 64
    assert status["ok"] is True
    assert status["selected_rootfs"]["path"] == str(rootfs)
    assert status["verification"]["checksum_verified"] is True
    assert status["verification"]["verified"] is True


def test_runtime_bundle_prepare_writes_metis_owned_bundle_files(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)
    source = workspace / "downloads" / "metis-rootfs.tar"
    source.parent.mkdir()
    source.write_bytes(b"metis owned rootfs")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    with workspace_root_override(str(workspace)):
        prepared = _json(
            metis_runtime_bundle_prepare(
                rootfs_path=str(source),
                version="26.6.17",
                channel="stable",
                expected_sha256=digest,
            )
        )
        rootfs_status = _json(metis_rootfs_asset_status(rootfs_path=prepared["rootfs_path"]))

    bundle = Path(prepared["bundle_path"])
    manifest = json.loads((bundle / "metis-runtime-bundle.json").read_text(encoding="utf-8"))
    latest = json.loads((bundle / "metis-runtime-latest.json").read_text(encoding="utf-8"))
    vm_manifest = json.loads((bundle / "metis-vm-pack.json").read_text(encoding="utf-8"))
    origin_detail = json.loads((bundle / "origins" / "rootfs.tar.origin.json").read_text(encoding="utf-8"))

    assert prepared["ok"] is True
    assert prepared["ready"] is True
    assert (bundle / "rootfs.tar").read_bytes() == b"metis owned rootfs"
    assert (bundle / ".rootfs.tar.origin").read_text(encoding="utf-8").strip() == digest
    assert (bundle / "install-metis-runtime.ps1").is_file()
    assert (bundle / "relocate-metis-runtime-pack.ps1").is_file()
    assert (bundle / "smoke-metis-runtime.ps1").is_file()
    assert manifest["owner"] == "metis"
    assert manifest["version"] == "26.6.17"
    assert manifest["channel"] == "stable"
    assert manifest["assets"]["rootfs"]["sha256"] == digest
    assert manifest["assets"]["rootfs"]["verified"] is True
    assert manifest["security"]["third_party_reference_assets"] is False
    assert latest["rootfs"]["sha256"] == digest
    assert latest["ready"] is True
    assert origin_detail["owner"] == "metis"
    assert origin_detail["reference_only"] is False
    assert vm_manifest["runtime_bundle"]["schema"] == "metis.runtime_bundle.manifest.v1"
    assert vm_manifest["runtime_bundle"]["ready"] is True
    assert rootfs_status["verification"]["verified"] is True


def test_runtime_bundle_prepare_dry_run_does_not_write_bundle(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)

    with workspace_root_override(str(workspace)):
        planned = _json(metis_runtime_bundle_prepare(version="preview", dry_run=True))

    bundle = workspace / ".metis" / "runtime-pack" / "metisvm.bundle"
    assert planned["ok"] is True
    assert planned["dry_run"] is True
    assert planned["plan"]["version"] == "preview"
    assert planned["plan"]["ready"] is False
    assert not bundle.exists()


def test_runtime_bundle_package_creates_release_zip_and_manifests(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)
    source = workspace / "downloads" / "metis-rootfs.tar"
    source.parent.mkdir()
    source.write_bytes(b"metis owned rootfs package")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    with workspace_root_override(str(workspace)):
        prepared = _json(
            metis_runtime_bundle_prepare(
                rootfs_path=str(source),
                version="26.6.17",
                channel="stable",
                expected_sha256=digest,
            )
        )
        packaged = _json(metis_runtime_bundle_package(version="26.6.17", channel="stable"))

    bundle = Path(prepared["bundle_path"])
    package_path = Path(packaged["package_path"])
    sha_path = Path(packaged["sha256_path"])
    release_manifest_path = Path(packaged["release_manifest_path"])
    latest_manifest_path = Path(packaged["latest_manifest_path"])
    release_manifest = json.loads(release_manifest_path.read_text(encoding="utf-8"))

    assert packaged["ok"] is True
    assert package_path.is_file()
    assert sha_path.is_file()
    assert release_manifest_path.is_file()
    assert latest_manifest_path.is_file()
    assert packaged["package_sha256"] == hashlib.sha256(package_path.read_bytes()).hexdigest()
    assert sha_path.read_text(encoding="utf-8").startswith(packaged["package_sha256"])
    assert release_manifest["schema"] == "metis.runtime_bundle.package.v1"
    assert release_manifest["package"]["include_rootfs"] is True
    assert release_manifest["rootfs"]["sha256"] == digest
    with zipfile.ZipFile(package_path, "r") as archive:
        names = set(archive.namelist())
    assert f"{bundle.name}/metis-runtime-bundle.json" in names
    assert f"{bundle.name}/metis-runtime-latest.json" in names
    assert f"{bundle.name}/rootfs.tar" in names
    assert f"{bundle.name}/install-metis-runtime.ps1" in names


def test_runtime_bundle_package_dry_run_does_not_write_release(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)
    source = workspace / "downloads" / "metis-rootfs.tar"
    source.parent.mkdir()
    source.write_bytes(b"metis owned rootfs package dry")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()

    with workspace_root_override(str(workspace)):
        metis_runtime_bundle_prepare(
            rootfs_path=str(source),
            version="preview",
            channel="beta",
            expected_sha256=digest,
        )
        planned = _json(metis_runtime_bundle_package(version="preview", channel="beta", dry_run=True))

    assert planned["ok"] is True
    assert planned["dry_run"] is True
    assert planned["file_count"] > 0
    assert not Path(planned["package_path"]).exists()


def test_runtime_bundle_package_v2_dry_run_reports_missing_assets(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)

    with workspace_root_override(str(workspace)):
        scaffold = _json(metis_vm_pack_scaffold())
        planned = _json(metis_runtime_bundle_package_v2(version="26.6.17", channel="stable", dry_run=True))

    assert scaffold["ok"] is True
    assert planned["ok"] is True
    assert planned["dry_run"] is True
    assert planned["schema"] == "metis.runtime_bundle.package.v2"
    assert set(planned["asset_status"]["missing_required"]) >= {"vmlinuz", "initrd", "rootfs.vhdx", "metis-bin.vhdx"}
    assert planned["package_path"].endswith("metis-runtime-bundle-v2-26.6.17-stable.zip")
    assert not (workspace / ".metis" / "runtime-pack" / "releases").exists()


def test_runtime_bundle_package_v2_creates_release_assets(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)

    def fake_compress(source: Path, target: Path, *, compression: dict, force: bool) -> dict:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(b"zst:" + source.read_bytes())
        return {
            "ok": True,
            "kind": compression["kind"],
            "source": str(source),
            "target": str(target),
            "source_size_bytes": source.stat().st_size,
            "target_size_bytes": target.stat().st_size,
            "duration_ms": 1,
        }

    monkeypatch.setattr(
        isolated_runtime,
        "_detect_zstd_compressor",
        lambda: {"available": True, "kind": "test-zstd", "executable": "", "reason": ""},
    )
    monkeypatch.setattr(isolated_runtime, "_compress_file_zst", fake_compress)

    with workspace_root_override(str(workspace)):
        scaffold = _json(metis_vm_pack_scaffold())
        bundle = Path(scaffold["bundle_path"])
        for name, data in {
            "vmlinuz": b"kernel",
            "initrd": b"initrd",
            "rootfs.vhdx": b"rootfs-vhdx",
            "metis-bin.vhdx": b"metis-bin",
            "sessiondata.vhdx": b"session",
        }.items():
            (bundle / name).write_bytes(data)
        packaged = _json(
            metis_runtime_bundle_package_v2(
                version="26.6.17",
                channel="stable",
                include_sessiondata=True,
                dry_run=False,
            )
        )

    package_path = Path(packaged["package_path"])
    sha_path = Path(packaged["sha256_path"])
    release_manifest_path = Path(packaged["release_manifest_path"])
    latest_manifest_path = Path(packaged["latest_manifest_path"])
    release_dir = Path(packaged["output_dir"])
    manifest = json.loads(release_manifest_path.read_text(encoding="utf-8"))

    assert packaged["ok"] is True
    assert package_path.is_file()
    assert sha_path.is_file()
    assert release_manifest_path.is_file()
    assert latest_manifest_path.is_file()
    assert (release_dir / "rootfs.vhdx.zst").read_bytes() == b"zst:rootfs-vhdx"
    assert (release_dir / "SHA256SUMS.txt").is_file()
    assert (release_dir / "runtime-bundle-v2-manifest.json").is_file()
    assert (release_dir / "install-metis-runtime-bundle-v2.ps1").is_file()
    assert (release_dir / "verify-metis-runtime-bundle-v2.ps1").is_file()
    assert packaged["package_sha256"] == hashlib.sha256(package_path.read_bytes()).hexdigest()
    assert sha_path.read_text(encoding="utf-8").startswith(packaged["package_sha256"])
    assert manifest["schema"] == "metis.runtime_bundle.package.v2"
    assert manifest["assets"]["rootfs.vhdx.zst"]["sha256"] == hashlib.sha256(b"zst:rootfs-vhdx").hexdigest()
    assert manifest["install"]["requires_docker"] is False
    assert manifest["install"]["requires_wsl_build"] is False
    with zipfile.ZipFile(package_path, "r") as archive:
        names = set(archive.namelist())
    assert "metis-runtime-bundle-v2/rootfs.vhdx.zst" in names
    assert "metis-runtime-bundle-v2/vmlinuz" in names
    assert "metis-runtime-bundle-v2/initrd" in names
    assert "metis-runtime-bundle-v2/metis-bin.vhdx" in names
    assert "metis-runtime-bundle-v2/sessiondata.vhdx" in names
    assert "metis-runtime-bundle-v2/metis-vm-pack.json" in names
    assert "metis-runtime-bundle-v2/SHA256SUMS.txt" in names
    assert "metis-runtime-bundle-v2/install-metis-runtime-bundle-v2.ps1" in names


def test_vm_direct_assets_prepare_writes_manifest_scripts_and_copies_assets(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    assets = workspace / "direct-assets"
    workspace.mkdir()
    backend_cwd.mkdir()
    assets.mkdir()
    monkeypatch.chdir(backend_cwd)

    sources = {
        "rootfs_vhdx_path": assets / "rootfs-source.vhdx",
        "kernel_path": assets / "vmlinuz-source",
        "initrd_path": assets / "initrd-source",
        "metis_bin_path": assets / "metis-bin-source.vhdx",
        "sessiondata_path": assets / "sessiondata-source.vhdx",
    }
    for key, path in sources.items():
        path.write_bytes(f"metis {key}".encode("utf-8"))

    with workspace_root_override(str(workspace)):
        prepared = _json(
            metis_vm_direct_assets_prepare(
                rootfs_vhdx_path=str(sources["rootfs_vhdx_path"]),
                kernel_path=str(sources["kernel_path"]),
                initrd_path=str(sources["initrd_path"]),
                metis_bin_path=str(sources["metis_bin_path"]),
                sessiondata_path=str(sources["sessiondata_path"]),
                version="direct-test",
            )
        )
        status = _json(metis_vm_bundle_status(bundle_path=prepared["bundle_path"]))

    bundle = Path(prepared["bundle_path"])
    manifest = json.loads((bundle / "metis-direct-vm-assets.json").read_text(encoding="utf-8"))
    vm_manifest = json.loads((bundle / "metis-vm-pack.json").read_text(encoding="utf-8"))

    assert prepared["ok"] is True
    assert prepared["assets_ready"] is True
    assert prepared["runner_ready"] is False
    assert (bundle / "rootfs.vhdx").is_file()
    assert (bundle / "vmlinuz").is_file()
    assert (bundle / "initrd").is_file()
    assert (bundle / "metis-bin.vhdx").is_file()
    assert (bundle / "sessiondata.vhdx").is_file()
    assert (bundle / ".rootfs.vhdx.origin").read_text(encoding="utf-8").strip()
    assert (bundle / "create-direct-vm-assets.ps1").is_file()
    assert (bundle / "host" / "hcs-runner.ps1").is_file()
    assert (bundle / "host" / "hcs-runner-plan.json").is_file()
    assert manifest["schema"] == "metis.vm_direct.assets.v1"
    assert manifest["owner"] == "metis"
    assert manifest["assets_ready"] is True
    assert manifest["runner"]["implemented"] is False
    assert manifest["missing_required"] == []
    assert vm_manifest["direct_vm"]["assets_ready"] is True
    assert vm_manifest["direct_vm"]["runner"]["implemented"] is False
    assert "metis-bin.vhdx" in vm_manifest["optional_assets"]
    assert status["selected_bundle"]["ready"] is True


def test_vm_direct_assets_prepare_dry_run_does_not_write_bundle(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)

    with workspace_root_override(str(workspace)):
        planned = _json(metis_vm_direct_assets_prepare(version="plan-only", dry_run=True))

    bundle = workspace / ".metis" / "runtime-pack" / "metisvm.bundle"
    assert planned["ok"] is True
    assert planned["dry_run"] is True
    assert planned["plan"]["version"] == "plan-only"
    assert planned["plan"]["status"]["assets_ready"] is False
    assert not bundle.exists()


def test_vm_direct_runner_prepare_writes_protocol_lifecycle_and_manifest(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)

    with workspace_root_override(str(workspace)):
        prepared = _json(metis_vm_direct_runner_prepare(version="runner-test"))
        status = _json(metis_vm_bundle_status(bundle_path=prepared["bundle_path"]))

    bundle = Path(prepared["bundle_path"])
    manifest = json.loads((bundle / "metis-direct-runner.json").read_text(encoding="utf-8"))
    vm_manifest = json.loads((bundle / "metis-vm-pack.json").read_text(encoding="utf-8"))

    assert prepared["ok"] is True
    assert prepared["runner_ready"] is False
    assert prepared["stdio_smoke_ready"] is True
    assert (bundle / "guest" / "metisd.py").is_file()
    assert (bundle / "guest" / "PROTOCOL.md").is_file()
    assert (bundle / "host" / "hcs-runner.ps1").is_file()
    assert (bundle / "host" / "hcs-runner-plan.json").is_file()
    assert (bundle / "host" / "artifact-sync.ps1").is_file()
    assert (bundle / "host" / "lifecycle-schema.json").is_file()
    assert manifest["schema"] == "metis.vm_direct.runner.v1"
    assert manifest["runner"]["implemented"] is False
    assert manifest["transport"]["stdio_smoke_ready"] is True
    assert "artifact.collect" in manifest["transport"]["methods"]
    assert "completed" in manifest["lifecycle"]["terminal_states"]
    assert vm_manifest["direct_runner"]["prepared"] is True
    assert vm_manifest["direct_runner"]["runner_ready"] is False
    assert status["selected_bundle"]["direct_runner"]["prepared"] is True
    assert status["runner_available"] is False


def test_vm_direct_runner_smoke_validates_jsonl_artifacts_and_diagnostics(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)
    monkeypatch.setenv("METIS_PYTHON", sys.executable)

    with workspace_root_override(str(workspace)):
        smoke = _json(metis_vm_direct_runner_smoke(timeout=30))

    assert smoke["ok"] is True
    assert smoke["run"]["ok"] is True
    assert "metis-vm-smoke-ok" in smoke["run"]["stdout"]
    assert any(str(item["relative_path"]).endswith("vm-smoke.txt") for item in smoke["artifacts"])
    assert Path(smoke["diagnostics_zip"]).is_file()
    assert Path(smoke["lifecycle_log"]).is_file()
    lifecycle_text = Path(smoke["lifecycle_log"]).read_text(encoding="utf-8")
    assert "handshake" in lifecycle_text
    assert "completed" in lifecycle_text


def test_vm_hcs_starter_prepare_writes_compute_document_and_bridge(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    assets = workspace / "assets"
    workspace.mkdir()
    backend_cwd.mkdir()
    assets.mkdir()
    monkeypatch.chdir(backend_cwd)

    sources = {
        "rootfs_vhdx_path": assets / "rootfs.vhdx",
        "kernel_path": assets / "vmlinuz",
        "initrd_path": assets / "initrd",
        "metis_bin_path": assets / "metis-bin.vhdx",
        "sessiondata_path": assets / "sessiondata.vhdx",
    }
    for key, path in sources.items():
        path.write_bytes(f"hcs {key}".encode("utf-8"))

    with workspace_root_override(str(workspace)):
        assets_prepared = _json(
            metis_vm_direct_assets_prepare(
                rootfs_vhdx_path=str(sources["rootfs_vhdx_path"]),
                kernel_path=str(sources["kernel_path"]),
                initrd_path=str(sources["initrd_path"]),
                metis_bin_path=str(sources["metis_bin_path"]),
                sessiondata_path=str(sources["sessiondata_path"]),
                version="hcs-test",
            )
        )
        prepared = _json(
            metis_vm_hcs_starter_prepare(
                bundle_path=assets_prepared["bundle_path"],
                version="hcs-test",
                memory_mb=1024,
                processor_count=1,
            )
        )
        status = _json(metis_vm_bundle_status(bundle_path=prepared["bundle_path"]))

    bundle = Path(prepared["bundle_path"])
    compute_doc = json.loads((bundle / "host" / "hcs-compute-system.json").read_text(encoding="utf-8"))
    manifest = json.loads((bundle / "metis-hcs-starter.json").read_text(encoding="utf-8"))
    vm_manifest = json.loads((bundle / "metis-vm-pack.json").read_text(encoding="utf-8"))

    assert prepared["ok"] is True
    assert prepared["assets_ready"] is True
    assert (bundle / "host" / "HcsApiBridge.cs").is_file()
    assert (bundle / "host" / "hcs-starter.ps1").is_file()
    assert (bundle / "host" / "hcs-start-plan.json").is_file()
    assert compute_doc["SchemaVersion"] == {"Major": 2, "Minor": 2}
    assert compute_doc["VirtualMachine"]["Chipset"]["LinuxKernelDirect"]["KernelFilePath"].endswith("vmlinuz")
    assert compute_doc["VirtualMachine"]["Chipset"]["LinuxKernelDirect"]["InitRdPath"].endswith("initrd")
    assert compute_doc["VirtualMachine"]["Devices"]["Scsi"]["0"]["Attachments"]["0"]["Path"].endswith("rootfs.vhdx")
    assert compute_doc["VirtualMachine"]["Devices"]["Scsi"]["0"]["Attachments"]["2"]["ReadOnly"] is True
    assert manifest["schema"] == "metis.vm_direct.hcs_starter.v1"
    assert manifest["starter_ready"] is True
    assert manifest["assets_ready"] is True
    assert manifest["safety"]["requires_enable_experimental_hcs"] is True
    assert vm_manifest["hcs_starter"]["starter_ready"] is True
    assert status["selected_bundle"]["hcs_starter"]["starter_ready"] is True


def test_vm_hcs_starter_start_dry_run_and_gate(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)

    with workspace_root_override(str(workspace)):
        dry = _json(metis_vm_hcs_starter_start(dry_run=True))
        blocked = _json(metis_vm_hcs_starter_start(dry_run=False, enable_experimental_hcs=False))

    assert dry["ok"] is True
    assert dry["dry_run"] is True
    assert "hcs-starter.ps1" in " ".join(dry["plan"]["command"])
    assert blocked["ok"] is False
    assert blocked["code"] == "METIS_HCS_EXPERIMENTAL_FLAG_REQUIRED"


def test_vm_rootfs_boot_verifier_prepare_and_dry_run_matrix(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    assets = workspace / "assets"
    workspace.mkdir()
    backend_cwd.mkdir()
    assets.mkdir()
    monkeypatch.chdir(backend_cwd)

    sources = {
        "rootfs_vhdx_path": assets / "rootfs.vhdx",
        "kernel_path": assets / "vmlinuz",
        "initrd_path": assets / "initrd",
        "metis_bin_path": assets / "metis-bin.vhdx",
        "sessiondata_path": assets / "sessiondata.vhdx",
    }
    for key, path in sources.items():
        path.write_bytes(f"boot {key}".encode("utf-8"))

    with workspace_root_override(str(workspace)):
        assets_prepared = _json(
            metis_vm_direct_assets_prepare(
                rootfs_vhdx_path=str(sources["rootfs_vhdx_path"]),
                kernel_path=str(sources["kernel_path"]),
                initrd_path=str(sources["initrd_path"]),
                metis_bin_path=str(sources["metis_bin_path"]),
                sessiondata_path=str(sources["sessiondata_path"]),
                version="boot-test",
            )
        )
        prepared = _json(
            metis_vm_rootfs_boot_verifier_prepare(
                bundle_path=assets_prepared["bundle_path"],
                version="boot-test",
                root_device_candidates=["/dev/sda", "/dev/sda1"],
                init_candidates=["/usr/local/bin/metisd"],
            )
        )
        dry = _json(
            metis_vm_rootfs_boot_verify(
                bundle_path=assets_prepared["bundle_path"],
                dry_run=True,
            )
        )
        blocked = _json(
            metis_vm_rootfs_boot_verify(
                bundle_path=assets_prepared["bundle_path"],
                dry_run=False,
                enable_experimental_hcs=False,
            )
        )
        status = _json(metis_vm_bundle_status(bundle_path=assets_prepared["bundle_path"]))

    bundle = Path(prepared["bundle_path"])
    manifest = json.loads((bundle / "metis-rootfs-boot-verifier.json").read_text(encoding="utf-8"))
    matrix = json.loads((bundle / "host" / "boot-cmdline-matrix.json").read_text(encoding="utf-8"))
    vm_manifest = json.loads((bundle / "metis-vm-pack.json").read_text(encoding="utf-8"))

    assert prepared["ok"] is True
    assert prepared["candidate_count"] == 2
    assert prepared["verifier_ready"] is True
    assert (bundle / "host" / "rootfs-inspect.ps1").is_file()
    assert (bundle / "host" / "rootfs-boot-verifier.ps1").is_file()
    assert manifest["schema"] == "metis.vm_direct.rootfs_boot_verifier.v1"
    assert manifest["runner_ready"] is False
    assert matrix["candidates"][0]["kernel_cmdline"].startswith("console=ttyS0")
    assert "root=/dev/sda" in matrix["candidates"][0]["kernel_cmdline"]
    assert dry["ok"] is True
    assert dry["dry_run"] is True
    assert len(dry["attempts"]) == 2
    assert dry["attempts"][0]["compute_document"].endswith(".hcs-compute-system.json")
    assert blocked["ok"] is False
    assert blocked["code"] == "METIS_ROOTFS_BOOT_EXPERIMENTAL_FLAG_REQUIRED"
    assert vm_manifest["rootfs_boot_verifier"]["verifier_ready"] is True
    assert vm_manifest["rootfs_boot_verifier"]["runner_ready"] is False
    assert status["selected_bundle"]["rootfs_boot_verifier"]["verifier_ready"] is True


def test_vm_guest_handshake_prepare_writes_gate_manifest(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)

    with workspace_root_override(str(workspace)):
        prepared = _json(metis_vm_guest_handshake_prepare(version="handshake-test"))
        dry = _json(metis_vm_guest_handshake_verify(bundle_path=prepared["bundle_path"], dry_run=True))
        status = _json(metis_vm_bundle_status(bundle_path=prepared["bundle_path"]))

    bundle = Path(prepared["bundle_path"])
    manifest = json.loads((bundle / "metis-guest-handshake.json").read_text(encoding="utf-8"))
    vm_manifest = json.loads((bundle / "metis-vm-pack.json").read_text(encoding="utf-8"))

    assert prepared["ok"] is True
    assert prepared["verifier_ready"] is True
    assert prepared["runner_ready"] is False
    assert (bundle / "host" / "guest-handshake.ps1").is_file()
    assert (bundle / "host" / "guest-handshake-plan.json").is_file()
    assert manifest["schema"] == "metis.vm_direct.guest_handshake.v1"
    assert manifest["transport"]["selected"] == "hcs-vsock-jsonl"
    assert manifest["handshake"]["method"] == "runtime.hello"
    assert manifest["runner_ready"] is False
    assert vm_manifest["guest_handshake"]["verifier_ready"] is True
    assert vm_manifest["guest_handshake"]["runner_ready"] is False
    assert dry["ok"] is True
    assert dry["plan"]["expected_method"] == "runtime.hello"
    assert dry["runner_ready"] is False
    assert status["selected_bundle"]["guest_handshake"]["verifier_ready"] is True
    assert status["selected_bundle"]["runner_ready"] is False


def test_vm_guest_handshake_stdio_writes_receipt_without_runner_ready(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)
    monkeypatch.setenv("METIS_PYTHON", sys.executable)

    with workspace_root_override(str(workspace)):
        prepared = _json(metis_vm_guest_handshake_prepare(version="stdio-handshake", transport="jsonl-stdio"))
        verified = _json(
            metis_vm_guest_handshake_verify(
                bundle_path=prepared["bundle_path"],
                transport="jsonl-stdio",
                dry_run=False,
            )
        )
        status = _json(metis_vm_bundle_status(bundle_path=prepared["bundle_path"]))

    bundle = Path(prepared["bundle_path"])
    manifest = json.loads((bundle / "metis-guest-handshake.json").read_text(encoding="utf-8"))
    receipt = verified["receipt"]

    assert verified["ok"] is True
    assert verified["handshake_verified"] is True
    assert verified["stdio_handshake_verified"] is True
    assert verified["hcs_handshake_verified"] is False
    assert verified["runner_ready"] is False
    assert receipt["hello_result"]["protocol"] == "metis.vm.guest.v1"
    assert receipt["receipt_relative_path"].startswith("host")
    assert Path(receipt["receipt_path"]).is_file()
    assert Path(receipt["lifecycle_log"]).is_file()
    assert manifest["stdio_handshake_verified"] is True
    assert manifest["hcs_handshake_verified"] is False
    assert manifest["runner_ready"] is False
    assert status["selected_bundle"]["guest_handshake"]["stdio_handshake_verified"] is True
    assert status["selected_bundle"]["runner_ready"] is False


def test_vm_guest_handshake_hcs_gate_and_transport_unavailable(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)

    with workspace_root_override(str(workspace)):
        dry = _json(metis_vm_guest_handshake_verify(transport="hcs-vsock-jsonl", dry_run=True))
        blocked = _json(
            metis_vm_guest_handshake_verify(
                bundle_path=dry["bundle_path"],
                transport="hcs-vsock-jsonl",
                dry_run=False,
                enable_experimental_hcs=False,
            )
        )
        unavailable = _json(
            metis_vm_guest_handshake_verify(
                bundle_path=dry["bundle_path"],
                transport="hcs-vsock-jsonl",
                dry_run=False,
                enable_experimental_hcs=True,
            )
        )

    assert dry["ok"] is True
    assert dry["plan"]["runner_ready_on_success"] is True
    assert dry["plan"]["transport_implemented"] is False
    assert blocked["ok"] is False
    assert blocked["code"] == "METIS_GUEST_HANDSHAKE_EXPERIMENTAL_FLAG_REQUIRED"
    assert unavailable["ok"] is False
    assert unavailable["code"] == "METIS_GUEST_HANDSHAKE_TRANSPORT_UNAVAILABLE"


def test_auto_selects_vm_when_guest_protocol_runner_ready(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    bundle = workspace / ".metis" / "runtime-pack" / "metisvm.bundle"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)
    monkeypatch.setenv("METIS_PYTHON", sys.executable)
    _mock_no_host_sandbox(monkeypatch)

    with workspace_root_override(str(workspace)):
        _prepare_guest_protocol_vm_bundle(workspace, bundle)
        status = _json(metis_vm_bundle_status(bundle_path=str(bundle)))
        created = _json(
            metis_runtime_create(
                task="vm auto",
                backend="auto",
                vm_bundle_path=str(bundle),
            )
        )

    assert status["runner_available"] is True
    assert status["selected_bundle"]["guest_protocol_ready"] is True
    assert status["selected_bundle"]["runner_transport"] == "jsonl-stdio"
    assert created["ok"] is True
    assert created["backend"] == "vm"
    assert created["sandbox"]["requested"] == "auto"
    assert created["sandbox"]["selected"] == "vm"
    assert created["sandbox"]["vm_bundle_path"] == str(bundle.resolve(strict=False))


def test_vm_runtime_run_uses_guest_protocol_and_collects_artifacts(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    bundle = workspace / ".metis" / "runtime-pack" / "metisvm.bundle"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)
    monkeypatch.setenv("METIS_PYTHON", sys.executable)
    _mock_no_host_sandbox(monkeypatch)
    (workspace / "main.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        "out = Path(os.environ['METIS_RUNTIME_ARTIFACTS_DIR'])\n"
        "out.mkdir(parents=True, exist_ok=True)\n"
        "(out / 'vm-result.txt').write_text('vm artifact ok\\n', encoding='utf-8')\n"
        "print('vm stdout ok')\n",
        encoding="utf-8",
    )

    with workspace_root_override(str(workspace)):
        _prepare_guest_protocol_vm_bundle(workspace, bundle)
        created = _json(
            metis_runtime_create(
                task="vm run",
                backend="auto",
                vm_bundle_path=str(bundle),
            )
        )
        run = _json(metis_runtime_run(created["session_id"], "python main.py", timeout=30))

    assert created["backend"] == "vm"
    assert run["ok"] is True
    assert run["backend"] == "vm"
    assert run["fallback_reason"] == ""
    assert run["executed_command"].startswith("metis-vm-jsonl-stdio")
    assert "vm stdout ok" in run["stdout"]
    assert any(str(item["relative_path"]).endswith("vm-result.txt") for item in run["artifacts"])


def test_vm_runtime_strict_fails_when_guest_daemon_missing(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    bundle = tmp_path / "missing-daemon.bundle"
    workspace.mkdir()
    backend_cwd.mkdir()
    bundle.mkdir()
    monkeypatch.chdir(backend_cwd)
    fake_status = {
        "preferred": "vm",
        "vm_pack": {
            "available": True,
            "runnable": True,
            "runner_available": True,
            "reason": "mocked runnable but daemon missing",
            "selected_bundle": {"path": str(bundle), "runner_ready": True},
        },
        "metis_wsl": {"available": False, "reason": "mocked out"},
        "wsl": {"available": False, "reason": "mocked out"},
        "docker": {"available": False, "reason": "mocked out"},
        "local": {"available": True, "kind": "local_copy"},
    }
    monkeypatch.setattr(isolated_runtime, "_detect_sandbox_backends", lambda **_kwargs: fake_status)

    with workspace_root_override(str(workspace)):
        created = _json(metis_runtime_create(task="vm missing daemon", backend="auto", strict_sandbox=True))
        run = _json(metis_runtime_run(created["session_id"], "echo hello", timeout=30))

    assert created["ok"] is True
    assert created["backend"] == "vm"
    assert run["ok"] is False
    assert run["backend"] == "vm"
    assert run["returncode"] == 126
    assert "strict sandbox forbids local fallback" in run["stderr"]
    assert run["fallback_reason"] == "strict sandbox blocked local fallback"


def test_rootfs_source_status_reads_local_manifest(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    downloads = workspace / "downloads"
    workspace.mkdir()
    backend_cwd.mkdir()
    downloads.mkdir()
    monkeypatch.chdir(backend_cwd)
    rootfs = downloads / "metis-rootfs-amd64.tar"
    rootfs.write_bytes(b"fake rootfs payload")
    digest = hashlib.sha256(rootfs.read_bytes()).hexdigest()
    manifest = downloads / "rootfs-source.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "metis.rootfs_source.manifest.v1",
                "version": "0.1.0",
                "assets": [
                    {
                        "name": "metis-rootfs",
                        "url": rootfs.name,
                        "sha256": digest,
                        "size_bytes": rootfs.stat().st_size,
                        "signature_url": "metis-rootfs-amd64.tar.sig",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with workspace_root_override(str(workspace)):
        status = _json(metis_rootfs_source_status(manifest_path=str(manifest)))

    assert status["ok"] is True
    assert status["source"]["kind"] == "manifest_path"
    assert status["manifest"]["asset_count"] == 1
    assert status["selected_asset"]["url"] == str(rootfs.resolve(strict=False))
    assert status["selected_asset"]["sha256"] == digest
    assert status["sha256_available"] is True


def test_rootfs_asset_download_from_file_manifest_registers_asset(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    downloads = workspace / "downloads"
    workspace.mkdir()
    backend_cwd.mkdir()
    downloads.mkdir()
    monkeypatch.chdir(backend_cwd)
    source = downloads / "source-rootfs.tar"
    source.write_bytes(b"downloaded fake rootfs")
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    manifest = downloads / "rootfs-source.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "metis.rootfs_source.manifest.v1",
                "version": "0.1.0",
                "assets": [
                    {
                        "name": "metis-rootfs",
                        "url": source.name,
                        "sha256": digest,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    with workspace_root_override(str(workspace)):
        scaffold = _json(metis_vm_pack_scaffold())
        downloaded = _json(metis_rootfs_asset_download(manifest_path=str(manifest), dry_run=False))
        status = _json(metis_rootfs_asset_status())

    bundle = Path(scaffold["bundle_path"])
    target = bundle / "rootfs.tar"
    manifest_data = json.loads((bundle / "metis-vm-pack.json").read_text(encoding="utf-8"))
    assert downloaded["ok"] is True
    assert downloaded["dry_run"] is False
    assert Path(downloaded["target_path"]) == target
    assert target.read_bytes() == source.read_bytes()
    assert downloaded["verification"]["checksum_verified"] is True
    assert downloaded["registration"]["ok"] is True
    assert manifest_data["assets"]["rootfs"]["path"] == "rootfs.tar"
    assert manifest_data["assets"]["rootfs"]["sha256"] == digest
    assert status["verification"]["verified"] is True


def test_rootfs_asset_download_requires_sha256(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)
    rootfs = workspace / "downloads" / "no-sha-rootfs.tar"
    rootfs.parent.mkdir()
    rootfs.write_bytes(b"missing sha")

    with workspace_root_override(str(workspace)):
        downloaded = _json(metis_rootfs_asset_download(asset_url=str(rootfs), dry_run=True))

    assert downloaded["ok"] is False
    assert downloaded["code"] == "ROOTFS_SOURCE_SHA256_REQUIRED"


def test_rootfs_builder_status_selects_docker_without_writing(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)
    monkeypatch.setattr(
        isolated_runtime,
        "_detect_docker",
        lambda docker_image="": {
            "available": True,
            "executable": "docker",
            "image": docker_image,
            "image_available": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        isolated_runtime,
        "_detect_wsl",
        lambda wsl_distro="": {
            "available": False,
            "reason": "no wsl",
        },
    )

    with workspace_root_override(str(workspace)):
        status = _json(metis_rootfs_builder_status(base_image="ubuntu:22.04"))

    builder_dir = workspace / ".metis" / "runtime-pack" / "metisvm.bundle" / "builder"
    assert status["ok"] is True
    assert status["selected_backend"] == "docker"
    assert status["docker"]["base_image_available"] is True
    assert status["script_status"]["ready"] is False
    assert not builder_dir.exists()


def test_rootfs_build_docker_minimal_exports_and_registers_rootfs(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)
    captured: list[list[str]] = []

    def fake_run(args, **_kwargs):
        arg_list = [str(item) for item in args]
        captured.append(arg_list)
        if arg_list[:2] == ["docker", "version"]:
            return subprocess.CompletedProcess(args, 0, "24.0.0\n", "")
        if arg_list[:3] == ["docker", "image", "inspect"]:
            return subprocess.CompletedProcess(args, 0, "[]\n", "")
        if len(arg_list) > 1 and arg_list[1] == "build":
            return subprocess.CompletedProcess(args, 0, "build ok\n", "")
        if len(arg_list) > 1 and arg_list[1] == "create":
            return subprocess.CompletedProcess(args, 0, "container-id\n", "")
        if len(arg_list) > 1 and arg_list[1] == "export":
            target = Path(arg_list[arg_list.index("-o") + 1])
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(b"metis minimal rootfs tar")
            return subprocess.CompletedProcess(args, 0, "export ok\n", "")
        if len(arg_list) > 1 and arg_list[1] in {"rm", "rmi"}:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(isolated_runtime.subprocess, "run", fake_run)
    monkeypatch.setattr(
        isolated_runtime,
        "_detect_docker",
        lambda docker_image="": {
            "available": True,
            "executable": "docker",
            "image": docker_image,
            "image_available": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(
        isolated_runtime,
        "_detect_wsl",
        lambda wsl_distro="": {
            "available": False,
            "reason": "no wsl",
        },
    )

    with workspace_root_override(str(workspace)):
        built = _json(
            metis_rootfs_build(
                backend="docker",
                base_image="ubuntu:22.04",
                profile="minimal",
                image_tag="metis/rootfs:test",
                dry_run=False,
                allow_network=False,
            )
        )
        status = _json(metis_rootfs_asset_status())

    bundle = workspace / ".metis" / "runtime-pack" / "metisvm.bundle"
    target = bundle / "rootfs.tar"
    manifest = json.loads((bundle / "metis-vm-pack.json").read_text(encoding="utf-8"))
    assert built["ok"] is True
    assert built["dry_run"] is False
    assert target.read_bytes() == b"metis minimal rootfs tar"
    assert (bundle / "builder" / "Dockerfile.rootfs").is_file()
    assert (bundle / "builder" / "build-rootfs-docker.ps1").is_file()
    assert (bundle / "builder" / "build-rootfs-wsl.sh").is_file()
    assert built["verification"]["verified"] is True
    assert built["registration"]["ok"] is True
    assert manifest["assets"]["rootfs"]["path"] == "rootfs.tar"
    assert len(manifest["assets"]["rootfs"]["sha256"]) == 64
    assert status["verification"]["verified"] is True
    assert any(args[:2] == ["docker", "build"] for args in captured)
    assert any(len(args) > 1 and args[1] == "export" for args in captured)
    assert all("pull" not in args for args in captured)


def test_rootfs_image_builder_status_detects_wsl_import_without_writing(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)
    original_which = isolated_runtime.shutil.which
    monkeypatch.setattr(
        isolated_runtime.shutil,
        "which",
        lambda name: "wsl.exe" if name in {"wsl.exe", "wsl"} else original_which(name),
    )
    monkeypatch.setattr(
        isolated_runtime,
        "_detect_wsl_import_features",
        lambda executable="": {
            "import_supported": True,
            "vhd_import_supported": True,
            "import_in_place_supported": True,
            "reason": "",
        },
    )

    with workspace_root_override(str(workspace)):
        status = _json(metis_rootfs_image_builder_status())

    builder_dir = workspace / ".metis" / "runtime-pack" / "metisvm.bundle" / "builder"
    assert status["ok"] is True
    assert status["selected_backend"] == "wsl_import"
    assert status["wsl"]["available"] is True
    assert status["rootfs_tar"]["path"].endswith("rootfs.tar")
    assert status["target_vhdx"]["path"].endswith("rootfs.vhdx")
    assert status["script_status"]["ready"] is False
    assert not builder_dir.exists()


def test_rootfs_image_build_dry_run_does_not_write_bundle(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)
    original_which = isolated_runtime.shutil.which
    monkeypatch.setattr(
        isolated_runtime.shutil,
        "which",
        lambda name: "wsl.exe" if name in {"wsl.exe", "wsl"} else original_which(name),
    )
    monkeypatch.setattr(
        isolated_runtime,
        "_detect_wsl_import_features",
        lambda executable="": {
            "import_supported": True,
            "vhd_import_supported": True,
            "import_in_place_supported": True,
            "reason": "",
        },
    )

    with workspace_root_override(str(workspace)):
        planned = _json(metis_rootfs_image_build(profile="standard", dry_run=True))

    bundle = workspace / ".metis" / "runtime-pack" / "metisvm.bundle"
    assert planned["ok"] is True
    assert planned["dry_run"] is True
    assert planned["builder"]["selected_backend"] == "wsl_import"
    assert planned["plan"]["layout"]["installed_paths"]["metisd"] == "/usr/local/bin/metisd"
    assert any(item["name"] == "python3" for item in planned["plan"]["layout"]["expected_tools"])
    assert not bundle.exists()


def test_rootfs_image_build_wsl_import_copies_vhdx_and_registers(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)
    captured: list[list[str]] = []
    original_which = isolated_runtime.shutil.which

    monkeypatch.setattr(
        isolated_runtime.shutil,
        "which",
        lambda name: "wsl.exe" if name in {"wsl.exe", "wsl"} else original_which(name),
    )
    monkeypatch.setattr(
        isolated_runtime,
        "_detect_wsl_import_features",
        lambda executable="": {
            "import_supported": True,
            "vhd_import_supported": True,
            "import_in_place_supported": True,
            "reason": "",
        },
    )
    monkeypatch.setattr(isolated_runtime, "_wsl_distro_base_path", lambda _distro: Path())

    def fake_run(args, **_kwargs):
        arg_list = [str(item) for item in args]
        captured.append(arg_list)
        if len(arg_list) > 1 and arg_list[1] == "--import":
            install_dir = Path(arg_list[3])
            install_dir.mkdir(parents=True, exist_ok=True)
            (install_dir / "ext4.vhdx").write_bytes(b"metis ext4 vhdx")
            return subprocess.CompletedProcess(args, 0, "import ok\n", "")
        if len(arg_list) > 1 and arg_list[1] in {"--terminate", "--unregister"}:
            return subprocess.CompletedProcess(args, 0, "", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(isolated_runtime.subprocess, "run", fake_run)

    bundle = workspace / ".metis" / "runtime-pack" / "metisvm.bundle"
    rootfs_tar = bundle / "rootfs.tar"
    rootfs_tar.parent.mkdir(parents=True)
    rootfs_tar.write_bytes(b"metis rootfs tar")

    with workspace_root_override(str(workspace)):
        built = _json(
            metis_rootfs_image_build(
                rootfs_tar_path=str(rootfs_tar),
                build_rootfs_tar=False,
                dry_run=False,
                force=True,
            )
        )
        status = _json(metis_rootfs_asset_status())

    target = bundle / "rootfs.vhdx"
    manifest = json.loads((bundle / "metis-vm-pack.json").read_text(encoding="utf-8"))
    image_manifest = json.loads((bundle / "metis-rootfs-image-builder.json").read_text(encoding="utf-8"))
    policy = json.loads((bundle / "builder" / "runtime-policy.json").read_text(encoding="utf-8"))

    assert built["ok"] is True
    assert target.read_bytes() == b"metis ext4 vhdx"
    assert (bundle / "builder" / "build-rootfs-vhdx-wsl.ps1").is_file()
    assert (bundle / "builder" / "rootfs-image-plan.json").is_file()
    assert (bundle / "builder" / "rootfs-image-layout.json").is_file()
    assert built["verification"]["verified"] is True
    assert built["registration"]["ok"] is True
    assert manifest["assets"]["rootfs"]["path"] == "rootfs.vhdx"
    assert manifest["assets"]["rootfs"]["import_mode"] == "vhd"
    assert manifest["rootfs_image_builder"]["built"] is True
    assert image_manifest["rootfs_vhdx_relative"] == "rootfs.vhdx"
    assert policy["permission_request_dir"].endswith(".metis-perm-req")
    assert status["selected_rootfs"]["is_vhd"] is True
    assert any(len(args) > 1 and args[1] == "--import" for args in captured)
    assert any(len(args) > 1 and args[1] == "--unregister" for args in captured)


def test_metis_wsl_runtime_import_dry_run_plans_wsl_import(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    local_app = tmp_path / "localapp"
    workspace.mkdir()
    backend_cwd.mkdir()
    rootfs = workspace / ".metis" / "runtime-pack" / "metisvm.bundle" / "rootfs.tar"
    rootfs.parent.mkdir(parents=True)
    rootfs.write_text("fake rootfs", encoding="utf-8")
    monkeypatch.chdir(backend_cwd)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app))
    monkeypatch.setattr(
        isolated_runtime,
        "_detect_wsl",
        lambda wsl_distro="": {
            "available": False,
            "executable": "wsl.exe",
            "distros": [],
            "selected_distro": "",
            "reason": "no distro found",
        },
    )
    monkeypatch.setattr(
        isolated_runtime,
        "_detect_wsl_import_features",
        lambda _executable="": {
            "import_supported": True,
            "vhd_import_supported": True,
            "import_in_place_supported": True,
            "reason": "",
        },
    )

    with workspace_root_override(str(workspace)):
        registered = _json(metis_rootfs_asset_register(rootfs_path=str(rootfs), copy=False))
        status = _json(metis_wsl_runtime_status())
        planned = _json(metis_wsl_runtime_import(dry_run=True))

    assert registered["ok"] is True
    assert status["ok"] is True
    assert status["ready_to_import"] is True
    assert status["rootfs_verification"]["verified"] is True
    assert status["selected_rootfs"]["path"] == str(rootfs)
    assert planned["ok"] is True
    assert planned["dry_run"] is True
    assert planned["verified"] is True
    assert planned["command"][:2] == ["wsl.exe", "--import"]
    assert "MetisRuntime" in planned["command"]
    assert str(rootfs) in planned["command"]
    assert not (local_app / "Metis" / "runtime" / "wsl" / "MetisRuntime").exists()


def test_metis_wsl_backend_selected_for_vm_and_builds_wsl_command(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)
    (workspace / "main.py").write_text("print('hello')\n", encoding="utf-8")

    fake_status = {
        "preferred": "metis_wsl",
        "vm_pack": {"runnable": False, "reason": "runner planned"},
        "metis_wsl": {
            "available": True,
            "installed": True,
            "distro_name": "MetisRuntime",
            "install_dir": str(tmp_path / "localapp" / "Metis" / "runtime" / "wsl" / "MetisRuntime"),
            "wsl": {"executable": "wsl.exe", "distros": ["MetisRuntime"]},
        },
        "wsl": {"available": False, "reason": "no user distro"},
        "docker": {"available": False, "reason": "daemon unavailable"},
        "local": {"available": True, "kind": "local_copy"},
    }
    captured: list[list[str]] = []

    def fake_run(args, **_kwargs):
        arg_list = [str(item) for item in args]
        captured.append(arg_list)
        if arg_list and arg_list[0] == "wsl.exe":
            return subprocess.CompletedProcess(args, 0, "metis wsl ok\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(isolated_runtime, "_detect_sandbox_backends", lambda **_kwargs: fake_status)
    monkeypatch.setattr(isolated_runtime, "_git_list_files", lambda _root: [])
    monkeypatch.setattr(isolated_runtime, "_initialize_git_baseline", lambda _workspace: {"ok": False, "reason": "mocked"})
    monkeypatch.setattr(isolated_runtime.subprocess, "run", fake_run)

    with workspace_root_override(str(workspace)):
        created = _json(metis_runtime_create(task="metis wsl", backend="vm"))
        run = _json(metis_runtime_run(created["session_id"], "python main.py", timeout=30))

    assert created["backend"] == "metis_wsl"
    assert created["sandbox"]["requested"] == "vm"
    assert "Metis managed WSL runtime" in created["sandbox"]["fallback_reason"]
    assert run["ok"] is True
    assert run["backend"] == "metis_wsl"
    assert "metis wsl ok" in run["stdout"]
    wsl_args = next(args for args in captured if args and args[0] == "wsl.exe")
    assert wsl_args[:4] == ["wsl.exe", "-d", "MetisRuntime", "--"]
    assert wsl_args[4:6] == ["bash", "-lc"]


def test_docker_backend_runs_with_network_disabled_and_artifact_mount(
    tmp_path: Path,
    monkeypatch,
) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)
    (workspace / "main.py").write_text("print('hello')\n", encoding="utf-8")

    fake_status = {
        "preferred": "docker",
        "wsl": {"available": False, "reason": "no distro"},
        "docker": {
            "available": True,
            "executable": "docker",
            "image": "metis/test:latest",
            "image_available": True,
        },
        "local": {"available": True, "kind": "local_copy"},
    }
    captured: list[list[str]] = []

    def fake_run(args, **_kwargs):
        arg_list = [str(item) for item in args]
        captured.append(arg_list)
        if arg_list and arg_list[0] == "docker":
            for index, item in enumerate(arg_list):
                if item == "-v" and index + 1 < len(arg_list) and arg_list[index + 1].endswith(":/artifacts"):
                    host_artifacts = Path(arg_list[index + 1].rsplit(":/artifacts", 1)[0])
                    host_artifacts.mkdir(parents=True, exist_ok=True)
                    (host_artifacts / "docker-result.txt").write_text("ok\n", encoding="utf-8")
            return subprocess.CompletedProcess(args, 0, "docker ok\n", "")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(isolated_runtime, "_detect_sandbox_backends", lambda **_kwargs: fake_status)
    monkeypatch.setattr(isolated_runtime, "_git_list_files", lambda _root: [])
    monkeypatch.setattr(isolated_runtime, "_initialize_git_baseline", lambda _workspace: {"ok": False, "reason": "mocked"})
    monkeypatch.setattr(isolated_runtime.subprocess, "run", fake_run)

    with workspace_root_override(str(workspace)):
        created = _json(metis_runtime_create(task="docker", backend="docker"))
        run = _json(metis_runtime_run(created["session_id"], "python main.py", timeout=30))

    assert created["backend"] == "docker"
    assert run["ok"] is True
    assert run["backend"] == "docker"
    assert "docker ok" in run["stdout"]
    docker_args = next(args for args in captured if args and args[0] == "docker")
    assert "--network" in docker_args
    assert "none" in docker_args
    assert "metis/test:latest" in docker_args
    assert any(str(item["relative_path"]).endswith("docker-result.txt") for item in run["artifacts"])

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import zipfile

from flask import Flask

from backend.runtime import runtime_manager
from backend.web.settings_routes import settings_bp


def _json(data: dict) -> str:
    return json.dumps(data)


def test_runtime_manager_status_aggregates_health(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_manager,
        "metis_sandbox_status",
        lambda **kwargs: _json(
            {
                "ok": True,
                "root": "D:\\project",
                "preferred": "metis_wsl",
                "metis_wsl": {"available": True, "install_dir": "D:\\project\\.metis\\runtime\\wsl\\MetisRuntime"},
                "wsl": {"available": True},
                "docker": {"available": True},
                "vm_pack": {"runnable": False},
            }
        ),
    )
    monkeypatch.setattr(
        runtime_manager,
        "metis_rootfs_asset_status",
        lambda **kwargs: _json(
            {
                "ok": True,
                "bundle_path": "D:\\project\\.metis\\runtime-pack\\metisvm.bundle",
                "selected_rootfs": {"path": "D:\\project\\.metis\\runtime-pack\\metisvm.bundle\\rootfs.tar", "size_bytes": 123},
                "verification": {"verified": True},
            }
        ),
    )
    monkeypatch.setattr(
        runtime_manager,
        "metis_rootfs_builder_status",
        lambda **kwargs: _json({"ok": True, "docker": {"available": True}}),
    )
    monkeypatch.setattr(
        runtime_manager,
        "metis_wsl_runtime_status",
        lambda **kwargs: _json({"ok": True, "available": True, "installed": True}),
    )
    monkeypatch.setattr(
        runtime_manager,
        "metis_vm_bundle_status",
        lambda **kwargs: _json(
            {
                "ok": True,
                "runnable": True,
                "bundle_detected": True,
                "reason": "VM protocol ready",
                "selected_bundle": {
                    "path": "D:\\project\\.metis\\runtime-pack\\metisvm.bundle",
                    "metis_owned": True,
                    "runner_ready": True,
                    "guest_protocol_ready": True,
                    "runner_transport": "jsonl-stdio",
                    "missing_required": [],
                    "total_known_bytes": 456,
                },
                "configured_candidates": [],
            }
        ),
    )
    monkeypatch.setattr(
        runtime_manager,
        "metis_runtime_status",
        lambda **kwargs: _json({"ok": True, "sessions": [{"session_id": "rt_1", "backend": "metis_wsl", "status": "ran"}]}),
    )
    monkeypatch.setattr(
        runtime_manager,
        "metis_runtime_job_status",
        lambda **kwargs: _json({"ok": True, "jobs": [{"job_id": "job_1", "backend": "metis_wsl", "status": "done"}]}),
    )

    status = runtime_manager.runtime_manager_status()

    assert status["ok"] is True
    assert status["health"]["preferred_backend"] == "metis_wsl"
    assert status["health"]["metis_wsl_ready"] is True
    assert status["health"]["rootfs_ready"] is True
    assert status["health"]["runtime_bundle_ready"] is False
    assert status["health"]["vm_guest_protocol_ready"] is True
    assert status["paths"]["rootfs"].endswith("rootfs.tar")
    assert status["paths"]["vm_runtime_bundle"].endswith("metisvm.bundle")
    assert status["paths"]["runtime_bundle_manifest"].endswith("metis-runtime-bundle.json")
    assert "vm_runtime" in status
    assert "release_integration" in status
    assert "runtime_bundle" in status
    assert any(action["id"] == "prepare-bundle" for action in status["actions"])
    assert any(action["id"] == "startup-test" for action in status["actions"])
    assert any(action["id"] == "smoke" for action in status["actions"])
    assert status["jobs"]["jobs"][0]["job_id"] == "job_1"


def test_runtime_manager_smoke_requires_artifact(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_manager,
        "metis_runtime_create",
        lambda **kwargs: _json({"ok": True, "session_id": "rt_smoke", "backend": "metis_wsl"}),
    )
    monkeypatch.setattr(
        runtime_manager,
        "metis_runtime_run",
        lambda *args, **kwargs: _json(
            {
                "ok": True,
                "session_id": "rt_smoke",
                "backend": "metis_wsl",
                "artifacts": [{"relative_path": "runtime-smoke.txt", "path": "D:\\project\\.metis\\artifacts\\runtime-smoke.txt"}],
            }
        ),
    )

    result = runtime_manager.runtime_manager_smoke()

    assert result["ok"] is True
    assert result["created"]["session_id"] == "rt_smoke"


def test_runtime_selftest_debug_classifies_missing_pack() -> None:
    debug = runtime_manager._selftest_debug(
        passed=False,
        used_local=True,
        reason="runtime bundle not found",
        backend="local",
        stdout="",
        stderr="",
    )

    assert debug["debug_category"] == "missing_runtime_pack"
    assert "运行时包" in debug["debug_summary"]


def test_runtime_manager_repair_installs_bundled_pack(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "resources" / "runtime-pack" / "metisvm.bundle"
    source.mkdir(parents=True)
    files = {
        "vmlinuz": b"kernel",
        "initrd": b"initrd",
        "rootfs.vhdx": b"rootfs",
        "metis-bin.vhdx": b"guest-tools",
        "metis-vm-pack.json": json.dumps(
            {"schema": "metis.vm_runtime_pack.manifest.v1", "owner": "metis", "version": "test"},
            sort_keys=True,
        ).encode("utf-8"),
    }
    for name, content in files.items():
        (source / name).write_bytes(content)
    (source / "SHA256SUMS.txt").write_text(
        "".join(f"{hashlib.sha256(content).hexdigest()}  {name}\n" for name, content in files.items()),
        encoding="utf-8",
    )

    monkeypatch.setenv("METIS_BUNDLED_RUNTIME_PACK_DIR", str(tmp_path / "resources" / "runtime-pack"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
    monkeypatch.delenv("METIS_VM_BUNDLE_DIR", raising=False)
    monkeypatch.delenv("METIS_RUNTIME_BUNDLE_DIR", raising=False)

    def fake_vm_bundle_status(**kwargs: object) -> str:
        path = str(kwargs.get("bundle_path") or tmp_path / "LocalAppData" / "Metis" / "vm_bundles" / "metisvm.bundle")
        exists = Path(path).is_dir()
        return _json(
            {
                "ok": True,
                "runnable": exists,
                "bundle_detected": exists,
                "reason": "test",
                "selected_bundle": {
                    "path": path,
                    "metis_owned": exists,
                    "runner_ready": exists,
                    "guest_protocol_ready": exists,
                    "runner_transport": "jsonl-stdio" if exists else "",
                    "missing_required": [],
                }
                if exists
                else {},
                "configured_candidates": [],
            }
        )

    monkeypatch.setattr(runtime_manager, "metis_vm_bundle_status", fake_vm_bundle_status)

    result = runtime_manager.runtime_manager_repair(root=str(tmp_path), source="auto")

    install_dir = tmp_path / "LocalAppData" / "Metis" / "vm_bundles" / "metisvm.bundle"
    assert result["ok"] is True
    assert (install_dir / "rootfs.vhdx").is_file()
    assert result["vm_runtime"]["asset_report"]["required_present"] is True
    assert result["vm_runtime"]["asset_report"]["sha256_verified"] is True


def test_runtime_manager_build_vm_assets_dry_run_reports_real_asset_plan(tmp_path: Path) -> None:
    result = runtime_manager.runtime_manager_build_vm_assets(
        root=str(tmp_path),
        dry_run=True,
        allow_network=False,
        package_bundle=True,
        version="test",
        channel="direct",
    )

    assert result["ok"] is True
    assert result["dry_run"] is True
    assert result["schema"] == "metis.runtime_manager.build_vm_assets.v1"
    assert "rootfs_vhdx" in result["plan"]["requirements"]
    assert "vmlinuz/initrd" in json.dumps(result["plan"])


def test_runtime_manager_validate_release_source_verifies_zip_and_sha(tmp_path: Path) -> None:
    release_dir = tmp_path / "release"
    payload_dir = release_dir / "payload"
    bundle_dir = payload_dir / "metis-runtime-bundle-v2"
    bundle_dir.mkdir(parents=True)
    files = {
        "vmlinuz": b"kernel",
        "initrd": b"initrd",
        "rootfs.vhdx.zst": b"compressed-rootfs",
        "metis-bin.vhdx": b"metis-bin",
        "metis-vm-pack.json": json.dumps(
            {"schema": "metis.vm_runtime_pack.manifest.v1", "owner": "metis", "version": "release-test"},
            sort_keys=True,
        ).encode("utf-8"),
    }
    for name, content in files.items():
        (bundle_dir / name).write_bytes(content)
    (bundle_dir / "SHA256SUMS.txt").write_text(
        "".join(f"{hashlib.sha256(content).hexdigest()}  {name}\n" for name, content in files.items()),
        encoding="utf-8",
    )
    package = release_dir / "metis-runtime-bundle-v2-test-direct.zip"
    with zipfile.ZipFile(package, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for item in bundle_dir.rglob("*"):
            archive.write(item, item.relative_to(payload_dir).as_posix())
    package_sha = hashlib.sha256(package.read_bytes()).hexdigest()
    manifest = release_dir / "metis-runtime-bundle-v2-latest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "metis.runtime_bundle.package.v2",
                "package": {
                    "name": package.name,
                    "url": package.as_uri(),
                    "sha256": package_sha,
                    "size_bytes": package.stat().st_size,
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    result = runtime_manager.runtime_manager_validate_release_source(root=str(tmp_path), url=manifest.as_uri())

    assert result["ok"] is True
    assert result["schema"] == "metis.runtime_manager.validate_release.v1"
    assert result["package_sha256"] == package_sha
    assert result["report"]["required_present"] is True
    assert result["report"]["sha256_verified"] is True


def test_runtime_manager_settings_routes_round_trip(monkeypatch) -> None:
    monkeypatch.setattr(runtime_manager, "runtime_manager_status", lambda **kwargs: {"ok": True, "schema": "status"})
    monkeypatch.setattr(runtime_manager, "runtime_manager_import_plan", lambda **kwargs: {"ok": True, "schema": "import-plan"})
    monkeypatch.setattr(runtime_manager, "runtime_manager_import", lambda **kwargs: {"ok": True, "schema": "import"})
    monkeypatch.setattr(
        runtime_manager,
        "runtime_manager_build_plan",
        lambda **kwargs: {"ok": True, "schema": "build-plan", "profile": kwargs.get("profile")},
    )
    monkeypatch.setattr(
        runtime_manager,
        "runtime_manager_prepare_bundle",
        lambda **kwargs: {"ok": True, "schema": "prepare-bundle", "channel": kwargs.get("channel")},
    )
    monkeypatch.setattr(
        runtime_manager,
        "runtime_manager_package_bundle",
        lambda **kwargs: {"ok": True, "schema": "package-bundle", "channel": kwargs.get("channel")},
    )
    monkeypatch.setattr(
        runtime_manager,
        "runtime_manager_package_vm_bundle",
        lambda **kwargs: {"ok": True, "schema": "package-vm-bundle", "channel": kwargs.get("channel")},
    )
    monkeypatch.setattr(
        runtime_manager,
        "runtime_manager_build_vm_assets",
        lambda **kwargs: {"ok": True, "schema": "build-vm-assets", "dry_run": kwargs.get("dry_run")},
    )
    monkeypatch.setattr(
        runtime_manager,
        "runtime_manager_validate_release_source",
        lambda **kwargs: {"ok": True, "schema": "validate-release", "url": kwargs.get("url")},
    )
    monkeypatch.setattr(runtime_manager, "runtime_manager_startup_test", lambda **kwargs: {"ok": True, "schema": "startup-test"})
    monkeypatch.setattr(
        runtime_manager,
        "runtime_manager_repair",
        lambda **kwargs: {"ok": True, "schema": "repair", "source": kwargs.get("source")},
    )
    monkeypatch.setattr(runtime_manager, "runtime_manager_ensure", lambda **kwargs: {"ok": True, "schema": "ensure"})
    monkeypatch.setattr(runtime_manager, "runtime_manager_smoke", lambda **kwargs: {"ok": True, "schema": "smoke"})
    monkeypatch.setattr(
        runtime_manager,
        "runtime_manager_export_diagnostics",
        lambda **kwargs: {"ok": True, "schema": "diagnostics", "session_id": kwargs.get("session_id")},
    )

    app = Flask(__name__)
    app.register_blueprint(settings_bp)

    with app.test_client() as client:
        assert client.get("/settings/runtime-manager").get_json()["schema"] == "status"
        assert client.post("/settings/runtime-manager/import-plan", json={}).get_json()["schema"] == "import-plan"
        assert client.post("/settings/runtime-manager/import", json={}).get_json()["schema"] == "import"
        assert client.post("/settings/runtime-manager/build-plan", json={"profile": "office"}).get_json()["profile"] == "office"
        assert client.post("/settings/runtime-manager/prepare-bundle", json={"channel": "stable"}).get_json()["channel"] == "stable"
        assert client.post("/settings/runtime-manager/package-bundle", json={"channel": "stable"}).get_json()["schema"] == "package-bundle"
        assert client.post("/settings/runtime-manager/package-vm-bundle", json={"channel": "direct"}).get_json()["schema"] == "package-vm-bundle"
        assert client.post("/settings/runtime-manager/build-vm-assets", json={"dry_run": False}).get_json()["dry_run"] is False
        assert client.post("/settings/runtime-manager/validate-release", json={"url": "file:///runtime.json"}).get_json()["url"] == "file:///runtime.json"
        assert client.post("/settings/runtime-manager/startup-test", json={}).get_json()["schema"] == "startup-test"
        assert client.post("/settings/runtime-manager/repair", json={"source": "bundled"}).get_json()["source"] == "bundled"
        assert client.post("/settings/runtime-manager/ensure", json={}).get_json()["schema"] == "ensure"
        assert client.post("/settings/runtime-manager/smoke", json={}).get_json()["schema"] == "smoke"
        diagnostics = client.post("/settings/runtime-manager/diagnostics", json={"session_id": "rt_1"}).get_json()

    assert diagnostics["schema"] == "diagnostics"
    assert diagnostics["session_id"] == "rt_1"

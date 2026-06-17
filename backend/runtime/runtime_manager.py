from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse
from urllib.request import urlopen
import uuid
import zipfile

from backend.runtime.isolated_runtime import (
    metis_rootfs_asset_status,
    metis_rootfs_build,
    metis_rootfs_builder_status,
    metis_rootfs_image_build,
    metis_runtime_bundle_package,
    metis_runtime_bundle_package_v2,
    metis_runtime_bundle_prepare,
    metis_runtime_create,
    metis_runtime_export_diagnostics,
    metis_runtime_run,
    metis_runtime_status,
    metis_sandbox_status,
    metis_vm_direct_assets_prepare,
    metis_vm_direct_runner_prepare,
    metis_vm_guest_handshake_prepare,
    metis_vm_bundle_status,
    metis_wsl_runtime_import,
    metis_wsl_runtime_status,
)
from backend.runtime.runtime_job import metis_runtime_job_status

RUNTIME_MANAGER_SCHEMA = "metis.runtime_manager.v1"
RUNTIME_MANAGER_REPAIR_SCHEMA = "metis.runtime_manager.repair.v1"
RUNTIME_MANAGER_STARTUP_TEST_SCHEMA = "metis.runtime_manager.startup_test.v1"
RUNTIME_MANAGER_RELEASE_SCHEMA = "metis.runtime_manager.release_integration.v1"
RUNTIME_MANAGER_PACKAGE_VM_SCHEMA = "metis.runtime_manager.package_vm_bundle.v1"
RUNTIME_MANAGER_BUILD_VM_ASSETS_SCHEMA = "metis.runtime_manager.build_vm_assets.v1"
RUNTIME_MANAGER_VALIDATE_RELEASE_SCHEMA = "metis.runtime_manager.validate_release.v1"
VM_REQUIRED_RUNTIME_FILES = ("vmlinuz", "initrd", "metis-vm-pack.json", "metis-bin.vhdx")
VM_ROOTFS_FILE = "rootfs.vhdx"
VM_ROOTFS_ZST_FILE = "rootfs.vhdx.zst"
DEFAULT_RUNTIME_PACK_URL = "https://github.com/linyeping/Metis/releases/latest/download/metis-runtime-bundle-v2-latest.json"


def runtime_manager_status(root: str = ".") -> Dict[str, Any]:
    """Aggregate runtime health for the desktop Runtime Manager panel."""
    sandbox = _loads(metis_sandbox_status(root=root))
    rootfs = _loads(metis_rootfs_asset_status(root=root))
    builder = _loads(metis_rootfs_builder_status(root=root))
    wsl_runtime = _loads(metis_wsl_runtime_status(root=root))
    vm_bundle = _loads(metis_vm_bundle_status(root=root))
    sessions = _loads(metis_runtime_status(root=root))
    jobs = _loads(metis_runtime_job_status(root=root))

    rootfs_asset = _first_dict(rootfs.get("selected_rootfs"), rootfs.get("selectedRootfs"))
    rootfs_verification = _first_dict(rootfs.get("verification"), rootfs.get("rootfs_verification"))
    metis_wsl = _first_dict(sandbox.get("metis_wsl"), wsl_runtime)
    docker = _first_dict(sandbox.get("docker"), builder.get("docker"))
    wsl = _first_dict(sandbox.get("wsl"), metis_wsl.get("wsl"))
    vm_pack = _first_dict(sandbox.get("vm_pack"))
    root_path = str(sandbox.get("root") or rootfs.get("root") or sessions.get("root") or "")
    runtime_bundle = _runtime_bundle_from_path(str(rootfs.get("bundle_path") or builder.get("bundle_path") or ""))
    vm_runtime = _vm_runtime_status(root=root_path or root, vm_bundle=vm_bundle)
    release_integration = _release_integration_status(vm_runtime=vm_runtime)

    health = {
        "preferred_backend": str(sandbox.get("preferred") or "local"),
        "ready": bool(
            vm_runtime.get("runner_ready")
            or metis_wsl.get("available")
            or sandbox.get("preferred") in {"metis_wsl", "wsl", "docker", "local"}
        ),
        "metis_wsl_ready": bool(metis_wsl.get("available")),
        "wsl_available": bool(wsl.get("available")),
        "docker_available": bool(docker.get("available")),
        "rootfs_ready": bool(rootfs.get("ok") and rootfs_verification.get("verified")),
        "vm_pack_ready": bool(vm_pack.get("runnable") or vm_runtime.get("runner_ready")),
        "runtime_bundle_ready": bool(runtime_bundle.get("ready")),
        "vm_runtime_installed": bool(vm_runtime.get("installed")),
        "vm_guest_protocol_ready": bool(vm_runtime.get("guest_protocol_ready")),
        "vm_hcs_direct_ready": bool(vm_runtime.get("hcs_direct_ready")),
        "vm_assets_verified": bool(vm_runtime.get("assets_verified")),
        "vm_asset_bytes": int(vm_runtime.get("asset_bytes") or 0),
        "bundled_runtime_pack_available": bool(release_integration.get("bundled_available")),
        "runtime_download_available": bool(release_integration.get("download_available")),
    }
    paths = {
        "root": root_path,
        "rootfs": str(rootfs_asset.get("path") or ""),
        "wsl_install_dir": str(metis_wsl.get("install_dir") or metis_wsl.get("registered_install_dir") or ""),
        "bundle_path": str(rootfs.get("bundle_path") or builder.get("bundle_path") or ""),
        "vm_runtime_bundle": str(vm_runtime.get("bundle_path") or ""),
        "runtime_pack_install_dir": str(vm_runtime.get("install_dir") or ""),
        "bundled_runtime_pack": str(release_integration.get("bundled_path") or ""),
        "runtime_bundle_manifest": str(runtime_bundle.get("manifest_path") or ""),
        "artifacts_root": str(Path(root_path) / ".metis" / "artifacts") if root_path else "",
        "diagnostics_root": str(Path(root_path) / ".metis" / "diagnostics") if root_path else "",
        "runtime_jobs_root": str(Path(root_path) / ".metis" / "runtime-jobs") if root_path else "",
    }

    return {
        "ok": True,
        "schema": RUNTIME_MANAGER_SCHEMA,
        "generated_at": time.time(),
        "root": root_path,
        "health": health,
        "paths": paths,
        "versions": _runtime_versions_from_sessions(sessions),
        "actions": _recommended_actions(
            health=health,
            rootfs=rootfs,
            builder=builder,
            wsl_runtime=metis_wsl,
            vm_runtime=vm_runtime,
            release_integration=release_integration,
        ),
        "sandbox": sandbox,
        "rootfs": rootfs,
        "builder": builder,
        "vm_bundle": vm_bundle,
        "vm_runtime": vm_runtime,
        "release_integration": release_integration,
        "runtime_bundle": runtime_bundle,
        "wsl_runtime": wsl_runtime,
        "sessions": sessions,
        "jobs": jobs,
        "notes": [
            "Metis Runtime Manager is read-mostly by default.",
            "WSL import is only offered when MetisRuntime is not already installed.",
            "VM runtime install can use a bundled pack resource or a configured download URL.",
            "Long rootfs builds should run as an explicit background task in a later product pass.",
        ],
    }


def runtime_manager_import_plan(root: str = ".") -> Dict[str, Any]:
    return _loads(metis_wsl_runtime_import(root=root, dry_run=True))


def runtime_manager_import(root: str = ".") -> Dict[str, Any]:
    status = _loads(metis_wsl_runtime_status(root=root))
    if status.get("installed"):
        return {
            "ok": True,
            "already_installed": True,
            "message": "MetisRuntime is already installed.",
            "status": status,
        }
    return _loads(metis_wsl_runtime_import(root=root, dry_run=False))


def runtime_manager_build_plan(root: str = ".", profile: str = "standard") -> Dict[str, Any]:
    normalized = str(profile or "standard").strip().lower()
    allow_network = normalized not in {"minimal", "smoke"}
    return _loads(
        metis_rootfs_build(
            root=root,
            backend="auto",
            profile=normalized,
            dry_run=True,
            allow_network=allow_network,
            register=True,
            force=True,
        )
    )


def runtime_manager_prepare_bundle(root: str = ".", version: str = "", channel: str = "local") -> Dict[str, Any]:
    return _loads(
        metis_runtime_bundle_prepare(
            root=root,
            version=version,
            channel=channel,
            dry_run=False,
        )
    )


def runtime_manager_package_bundle(root: str = ".", version: str = "", channel: str = "local") -> Dict[str, Any]:
    return _loads(
        metis_runtime_bundle_package(
            root=root,
            version=version,
            channel=channel,
            include_rootfs=True,
            dry_run=False,
        )
    )


def runtime_manager_package_vm_bundle(root: str = ".", version: str = "", channel: str = "direct") -> Dict[str, Any]:
    result = _loads(
        metis_runtime_bundle_package_v2(
            root=root,
            version=version,
            channel=channel,
            include_sessiondata=False,
            dry_run=False,
        )
    )
    result.setdefault("schema", RUNTIME_MANAGER_PACKAGE_VM_SCHEMA)
    result.setdefault(
        "message",
        "VM runtime release bundle packaged." if result.get("ok") else "VM runtime release bundle packaging failed.",
    )
    return result


def runtime_manager_build_vm_assets(
    root: str = ".",
    version: str = "",
    channel: str = "direct",
    profile: str = "standard",
    allow_network: bool = False,
    dry_run: bool = True,
    force: bool = False,
    package_bundle: bool = False,
    rootfs_vhdx_path: str = "",
    kernel_path: str = "",
    initrd_path: str = "",
    metis_bin_path: str = "",
) -> Dict[str, Any]:
    """Build or plan the real VM runtime asset set used by release bundles."""
    source_root = Path(str(root or ".")).resolve(strict=False)
    bundle = source_root / ".metis" / "runtime-pack" / "metisvm.bundle"
    selected_version = str(version or "").strip()
    selected_channel = str(channel or "direct").strip() or "direct"
    selected_profile = str(profile or "standard").strip() or "standard"
    kernel = _resolve_first_existing(kernel_path, "METIS_RUNTIME_KERNEL_PATH", ["vmlinuz", "vmlinuz-linux", "kernel"], bundle)
    initrd = _resolve_first_existing(initrd_path, "METIS_RUNTIME_INITRD_PATH", ["initrd", "initrd.img", "initramfs-linux.img"], bundle)
    rootfs = _resolve_first_existing(rootfs_vhdx_path, "METIS_RUNTIME_ROOTFS_VHDX_PATH", ["rootfs.vhdx"], bundle)
    metis_bin = _resolve_first_existing(metis_bin_path, "METIS_RUNTIME_METIS_BIN_VHDX_PATH", ["metis-bin.vhdx"], bundle)
    plan = {
        "schema": RUNTIME_MANAGER_BUILD_VM_ASSETS_SCHEMA,
        "root": str(source_root),
        "bundle_path": str(bundle),
        "version": selected_version,
        "channel": selected_channel,
        "profile": selected_profile,
        "allow_network": bool(allow_network),
        "force": bool(force),
        "package_bundle": bool(package_bundle),
        "inputs": {
            "rootfs_vhdx_path": str(rootfs),
            "kernel_path": str(kernel),
            "initrd_path": str(initrd),
            "metis_bin_path": str(metis_bin),
        },
        "steps": [
            "prepare guest protocol files",
            "build rootfs.vhdx through WSL import when missing",
            "extract or copy vmlinuz/initrd",
            "build metis-bin.vhdx as a Metis-owned guest-tools image when missing",
            "prepare direct VM asset manifest",
            "prepare guest handshake verifier",
            "optionally package runtime bundle v2",
        ],
        "requirements": {
            "rootfs_vhdx": "WSL2 import plus Docker/rootfs.tar when missing",
            "vmlinuz_initrd": "provided paths or Docker kernel extraction with allow_network=true",
            "metis_bin_vhdx": "WSL2 import from a generated Metis guest-tools tar when missing",
            "package": "python zstandard or zstd.exe unless rootfs.vhdx.zst already exists",
        },
    }
    if dry_run:
        return {
            "ok": True,
            "schema": RUNTIME_MANAGER_BUILD_VM_ASSETS_SCHEMA,
            "dry_run": True,
            "message": "VM runtime asset build plan generated.",
            "plan": plan,
            "status": _vm_asset_build_status(bundle),
        }

    bundle.mkdir(parents=True, exist_ok=True)
    steps: List[Dict[str, Any]] = []

    runner = _loads(metis_vm_direct_runner_prepare(root=str(source_root), bundle_path=str(bundle), version=selected_version, dry_run=False))
    steps.append({"step": "prepare_guest_protocol", "ok": bool(runner.get("ok")), "result": runner})
    if not runner.get("ok"):
        return _build_vm_assets_result(False, bundle, plan, steps, "Guest protocol preparation failed.")

    if not rootfs.is_file() or force:
        rootfs_build = _loads(
            metis_rootfs_image_build(
                root=str(source_root),
                bundle_path=str(bundle),
                profile=selected_profile,
                dry_run=False,
                allow_network=bool(allow_network),
                force=bool(force),
                register=True,
                build_rootfs_tar=True,
            )
        )
        steps.append({"step": "build_rootfs_vhdx", "ok": bool(rootfs_build.get("ok")), "result": rootfs_build})
        if not rootfs_build.get("ok"):
            return _build_vm_assets_result(False, bundle, plan, steps, "rootfs.vhdx build failed.")
        rootfs = Path(str(rootfs_build.get("target_path") or bundle / "rootfs.vhdx"))
    else:
        steps.append({"step": "build_rootfs_vhdx", "ok": True, "skipped": True, "path": str(rootfs)})

    if (not kernel.is_file() or not initrd.is_file()) or force:
        kernel_result = _build_kernel_initrd_assets(bundle, allow_network=bool(allow_network), force=bool(force), timeout=1800)
        steps.append({"step": "build_kernel_initrd", "ok": bool(kernel_result.get("ok")), "result": kernel_result})
        if not kernel_result.get("ok"):
            return _build_vm_assets_result(False, bundle, plan, steps, str(kernel_result.get("error") or "kernel/initrd build failed."))
        kernel = Path(str(kernel_result.get("kernel_path") or bundle / "vmlinuz"))
        initrd = Path(str(kernel_result.get("initrd_path") or bundle / "initrd"))
    else:
        steps.append({"step": "build_kernel_initrd", "ok": True, "skipped": True, "kernel_path": str(kernel), "initrd_path": str(initrd)})

    if not metis_bin.is_file() or force:
        metis_bin_result = _build_metis_bin_vhdx(source_root=source_root, bundle=bundle, force=bool(force), timeout=900)
        steps.append({"step": "build_metis_bin_vhdx", "ok": bool(metis_bin_result.get("ok")), "result": metis_bin_result})
        if not metis_bin_result.get("ok"):
            return _build_vm_assets_result(False, bundle, plan, steps, str(metis_bin_result.get("error") or "metis-bin.vhdx build failed."))
        metis_bin = Path(str(metis_bin_result.get("metis_bin_path") or bundle / "metis-bin.vhdx"))
    else:
        steps.append({"step": "build_metis_bin_vhdx", "ok": True, "skipped": True, "path": str(metis_bin)})

    assets = _loads(
        metis_vm_direct_assets_prepare(
            root=str(source_root),
            bundle_path=str(bundle),
            rootfs_vhdx_path=str(rootfs),
            kernel_path=str(kernel),
            initrd_path=str(initrd),
            metis_bin_path=str(metis_bin),
            version=selected_version,
            copy_assets=True,
            create_vhdx_scripts=True,
            create_vhdx=False,
            force=bool(force),
            dry_run=False,
        )
    )
    steps.append({"step": "prepare_direct_vm_assets", "ok": bool(assets.get("ok")), "result": assets})
    if not assets.get("ok"):
        return _build_vm_assets_result(False, bundle, plan, steps, "Direct VM asset preparation failed.")

    handshake = _loads(
        metis_vm_guest_handshake_prepare(
            root=str(source_root),
            bundle_path=str(bundle),
            version=selected_version,
            transport="jsonl-stdio",
            force=bool(force),
            dry_run=False,
        )
    )
    steps.append({"step": "prepare_guest_handshake", "ok": bool(handshake.get("ok")), "result": handshake})
    if not handshake.get("ok"):
        return _build_vm_assets_result(False, bundle, plan, steps, "Guest handshake preparation failed.")

    package = {}
    if package_bundle:
        package = _loads(
            metis_runtime_bundle_package_v2(
                root=str(source_root),
                bundle_path=str(bundle),
                version=selected_version,
                channel=selected_channel,
                dry_run=False,
                force=bool(force),
            )
        )
        steps.append({"step": "package_runtime_bundle_v2", "ok": bool(package.get("ok")), "result": package})
        if not package.get("ok"):
            return _build_vm_assets_result(False, bundle, plan, steps, "Runtime bundle v2 packaging failed.", package=package)

    return _build_vm_assets_result(True, bundle, plan, steps, "VM runtime assets are ready.", package=package)


def runtime_manager_validate_release_source(root: str = ".", url: str = "") -> Dict[str, Any]:
    selected_url = str(url or _runtime_pack_download_url() or "").strip()
    if not selected_url:
        return {
            "ok": False,
            "schema": RUNTIME_MANAGER_VALIDATE_RELEASE_SCHEMA,
            "error": "No runtime release URL is configured.",
            "message": "No runtime release URL is configured.",
        }
    downloaded = _download_runtime_pack(selected_url, root=root, validate_only=True)
    return {
        **downloaded,
        "schema": RUNTIME_MANAGER_VALIDATE_RELEASE_SCHEMA,
        "message": "Runtime release source validated." if downloaded.get("ok") else downloaded.get("error") or "Runtime release source validation failed.",
    }


def runtime_manager_startup_test(root: str = ".") -> Dict[str, Any]:
    result = runtime_manager_smoke(root=root)
    return {
        **result,
        "schema": RUNTIME_MANAGER_STARTUP_TEST_SCHEMA,
        "startup_test": True,
        "message": "Runtime startup test completed." if result.get("ok") else result.get("message") or "Runtime startup test failed.",
    }


def runtime_manager_repair(
    root: str = ".",
    source: str = "auto",
    allow_download: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    """Install or repair the local VM runtime pack from a bundled or downloaded source."""
    normalized_source = str(source or "auto").strip().lower() or "auto"
    try:
        vm_bundle = _loads(metis_vm_bundle_status(root=root))
        vm_runtime = _vm_runtime_status(root=root, vm_bundle=vm_bundle)
        release = _release_integration_status(vm_runtime=vm_runtime)
        if vm_runtime.get("installed") and vm_runtime.get("assets_verified") and not force:
            return {
                "ok": True,
                "schema": RUNTIME_MANAGER_REPAIR_SCHEMA,
                "already_installed": True,
                "message": "Metis VM runtime is already installed and verified.",
                "vm_runtime": vm_runtime,
                "release_integration": release,
            }

        install_dir = _runtime_pack_install_dir()
        selected_source = _select_repair_source(release=release, source=normalized_source, allow_download=allow_download)
        if not selected_source.get("ok"):
            return {
                "ok": False,
                "schema": RUNTIME_MANAGER_REPAIR_SCHEMA,
                "code": selected_source.get("code") or "METIS_RUNTIME_PACK_SOURCE_MISSING",
                "error": selected_source.get("error") or "No runtime pack source is available.",
                "message": selected_source.get("error") or "No runtime pack source is available.",
                "vm_runtime": vm_runtime,
                "release_integration": release,
            }

        source_dir = Path(str(selected_source.get("path") or ""))
        if selected_source.get("kind") == "download":
            downloaded = _download_runtime_pack(str(selected_source.get("url") or ""), root=root)
            if not downloaded.get("ok"):
                return {**downloaded, "schema": RUNTIME_MANAGER_REPAIR_SCHEMA}
            source_dir = Path(str(downloaded.get("bundle_path") or downloaded.get("extract_dir") or ""))

        installed = _install_runtime_pack_from_dir(source_dir, install_dir, force=force)
        refreshed = _loads(metis_vm_bundle_status(root=root, bundle_path=str(install_dir)))
        refreshed_runtime = _vm_runtime_status(root=root, vm_bundle=refreshed)
        ok = bool(installed.get("ok")) and bool(refreshed_runtime.get("installed"))
        return {
            "ok": ok,
            "schema": RUNTIME_MANAGER_REPAIR_SCHEMA,
            "message": "Metis VM runtime repaired." if ok else installed.get("error") or "Metis VM runtime repair did not complete.",
            "source": selected_source,
            "install_dir": str(install_dir),
            "installed": installed,
            "vm_runtime": refreshed_runtime,
            "release_integration": _release_integration_status(vm_runtime=refreshed_runtime),
        }
    except Exception as exc:
        return {
            "ok": False,
            "schema": RUNTIME_MANAGER_REPAIR_SCHEMA,
            "error": f"{type(exc).__name__}: {exc}",
            "message": "Metis VM runtime repair failed.",
        }


def runtime_manager_ensure(root: str = ".") -> Dict[str, Any]:
    allow_download = _env_flag("METIS_RUNTIME_AUTO_DOWNLOAD") or _env_flag("METIS_RUNTIME_AUTO_ENSURE")
    return runtime_manager_repair(root=root, source="auto", allow_download=allow_download, force=False)


def runtime_manager_smoke(root: str = ".") -> Dict[str, Any]:
    created = _loads(metis_runtime_create(task="Runtime Manager smoke", root=root, backend="auto", max_files=1200))
    if not created.get("ok"):
        return {
            "ok": False,
            "schema": RUNTIME_MANAGER_SCHEMA,
            "created": created,
            "run": {},
            "message": "Runtime session creation failed.",
        }
    backend = str(created.get("backend") or "local")
    command = _smoke_command(backend)
    run = _loads(metis_runtime_run(str(created.get("session_id") or ""), command, timeout=90))
    artifacts = run.get("artifacts") if isinstance(run.get("artifacts"), list) else []
    return {
        "ok": bool(run.get("ok")) and any(str(item.get("relative_path") or "") == "runtime-smoke.txt" for item in artifacts if isinstance(item, dict)),
        "schema": RUNTIME_MANAGER_SCHEMA,
        "created": created,
        "run": run,
        "message": "Runtime smoke completed." if run.get("ok") else "Runtime smoke failed.",
    }


def runtime_manager_export_diagnostics(session_id: str = "", root: str = ".") -> Dict[str, Any]:
    target_session = str(session_id or "").strip()
    if not target_session:
        sessions = _loads(metis_runtime_status(root=root))
        rows = sessions.get("sessions") if isinstance(sessions.get("sessions"), list) else []
        if rows and isinstance(rows[0], dict):
            target_session = str(rows[0].get("session_id") or "")
    if not target_session:
        return {"ok": False, "error": "No runtime session is available for diagnostics.", "schema": RUNTIME_MANAGER_SCHEMA}
    return _loads(metis_runtime_export_diagnostics(target_session))


def _build_vm_assets_result(
    ok: bool,
    bundle: Path,
    plan: Dict[str, Any],
    steps: List[Dict[str, Any]],
    message: str,
    *,
    package: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    vm_bundle = _loads(metis_vm_bundle_status(root=str(plan.get("root") or "."), bundle_path=str(bundle)))
    vm_runtime = _vm_runtime_status(root=str(plan.get("root") or "."), vm_bundle=vm_bundle)
    return {
        "ok": bool(ok),
        "schema": RUNTIME_MANAGER_BUILD_VM_ASSETS_SCHEMA,
        "dry_run": False,
        "message": message,
        "bundle_path": str(bundle),
        "plan": plan,
        "steps": steps,
        "status": _vm_asset_build_status(bundle),
        "vm_bundle": vm_bundle,
        "vm_runtime": vm_runtime,
        "package": package or {},
    }


def _vm_asset_build_status(bundle: Path) -> Dict[str, Any]:
    report = _bundle_report(bundle)
    files = report.get("files") if isinstance(report.get("files"), list) else []
    by_name = {Path(str(item.get("relative_path") or "")).name: item for item in files if isinstance(item, dict)}
    return {
        "bundle_path": str(bundle),
        "required_present": bool(report.get("required_present")),
        "sha256_verified": bool(report.get("sha256_verified")),
        "missing_required": list(report.get("missing_required") or []),
        "asset_bytes": int(report.get("total_bytes") or 0),
        "assets": {
            "rootfs.vhdx": by_name.get("rootfs.vhdx", {}),
            "vmlinuz": by_name.get("vmlinuz", {}),
            "initrd": by_name.get("initrd", {}),
            "metis-bin.vhdx": by_name.get("metis-bin.vhdx", {}),
            "metis-vm-pack.json": by_name.get("metis-vm-pack.json", {}),
        },
    }


def _resolve_first_existing(explicit: str, env_name: str, names: List[str], bundle: Path) -> Path:
    raw = str(explicit or "").strip() or str(os.environ.get(env_name) or "").strip()
    if raw:
        return Path(raw).expanduser().resolve(strict=False)
    for name in names:
        candidate = bundle / name
        if candidate.is_file():
            return candidate.resolve(strict=False)
    return (bundle / names[0]).resolve(strict=False)


def _build_kernel_initrd_assets(bundle: Path, *, allow_network: bool, force: bool, timeout: int) -> Dict[str, Any]:
    kernel = bundle / "vmlinuz"
    initrd = bundle / "initrd"
    if kernel.is_file() and initrd.is_file() and not force:
        return {"ok": True, "skipped": True, "kernel_path": str(kernel), "initrd_path": str(initrd)}
    if not allow_network:
        return {
            "ok": False,
            "code": "METIS_KERNEL_INITRD_NETWORK_REQUIRED",
            "error": "vmlinuz/initrd are missing. Set allow_network=true so Docker can install linux-image-virtual and extract real boot assets, or provide METIS_RUNTIME_KERNEL_PATH and METIS_RUNTIME_INITRD_PATH.",
        }
    docker = shutil.which("docker.exe") or shutil.which("docker")
    if not docker:
        return {"ok": False, "code": "METIS_KERNEL_INITRD_DOCKER_MISSING", "error": "Docker is required to build vmlinuz/initrd automatically."}
    bundle.mkdir(parents=True, exist_ok=True)
    script = r"""
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y --no-install-recommends ca-certificates linux-image-virtual
kernel="$(find /boot -maxdepth 1 -type f -name 'vmlinuz-*' | sort -V | tail -n 1)"
initrd="$(find /boot -maxdepth 1 -type f -name 'initrd.img-*' | sort -V | tail -n 1)"
test -n "$kernel"
test -n "$initrd"
cp "$kernel" /out/vmlinuz
cp "$initrd" /out/initrd
chmod 0644 /out/vmlinuz /out/initrd
sha256sum /out/vmlinuz /out/initrd > /out/kernel-initrd.SHA256SUMS.txt
"""
    result = _run_command(
        [
            docker,
            "run",
            "--rm",
            "-v",
            f"{str(bundle)}:/out",
            "ubuntu:22.04",
            "bash",
            "-lc",
            script,
        ],
        cwd=bundle,
        timeout=max(120, int(timeout or 1800)),
    )
    ok = result.get("returncode") == 0 and kernel.is_file() and initrd.is_file()
    if ok:
        for asset in (kernel, initrd):
            (bundle / f".{asset.name}.origin").write_text(_sha256_file(asset) + "\n", encoding="utf-8", newline="\n")
    return {
        "ok": ok,
        "code": "" if ok else "METIS_KERNEL_INITRD_BUILD_FAILED",
        "error": "" if ok else result.get("stderr") or result.get("stdout") or "kernel/initrd Docker build failed",
        "kernel_path": str(kernel),
        "initrd_path": str(initrd),
        "command": result,
    }


def _build_metis_bin_vhdx(*, source_root: Path, bundle: Path, force: bool, timeout: int) -> Dict[str, Any]:
    target = bundle / "metis-bin.vhdx"
    if target.is_file() and not force:
        return {"ok": True, "skipped": True, "metis_bin_path": str(target)}
    wsl = shutil.which("wsl.exe") or shutil.which("wsl")
    if not wsl:
        return {"ok": False, "code": "METIS_BIN_WSL_MISSING", "error": "WSL is required to build metis-bin.vhdx from the generated guest-tools tar."}
    builder_dir = bundle / "builder"
    builder_dir.mkdir(parents=True, exist_ok=True)
    tar_path = builder_dir / "metis-bin-rootfs.tar"
    _write_metis_bin_rootfs_tar(bundle, tar_path)
    distro = f"MetisBinBuilder-{uuid.uuid4().hex[:8]}"
    install_dir = builder_dir / distro
    if install_dir.exists() and force:
        shutil.rmtree(install_dir)
    steps: List[Dict[str, Any]] = []
    try:
        import_step = _run_command([wsl, "--import", distro, str(install_dir), str(tar_path), "--version", "2"], cwd=bundle, timeout=max(60, timeout))
        steps.append({"step": "wsl_import_metis_bin", **import_step})
        if import_step.get("returncode") != 0:
            return {"ok": False, "code": "METIS_BIN_WSL_IMPORT_FAILED", "error": import_step.get("stderr") or import_step.get("stdout"), "steps": steps}
        ext4 = install_dir / "ext4.vhdx"
        if not ext4.is_file():
            return {"ok": False, "code": "METIS_BIN_EXT4_MISSING", "error": f"WSL import did not produce ext4.vhdx: {ext4}", "steps": steps}
        if target.exists() and force:
            target.unlink()
        shutil.copy2(ext4, target)
        (bundle / ".metis-bin.vhdx.origin").write_text(_sha256_file(target) + "\n", encoding="utf-8", newline="\n")
        return {
            "ok": True,
            "metis_bin_path": str(target),
            "tar_path": str(tar_path),
            "install_dir": str(install_dir),
            "size_bytes": target.stat().st_size,
            "sha256": _sha256_file(target),
            "steps": steps,
        }
    finally:
        unregister = _run_command([wsl, "--unregister", distro], cwd=bundle, timeout=60)
        steps.append({"step": "wsl_unregister_metis_bin", **unregister})
        try:
            shutil.rmtree(install_dir, ignore_errors=True)
        except Exception:
            pass


def _write_metis_bin_rootfs_tar(bundle: Path, tar_path: Path) -> None:
    daemon = bundle / "guest" / "metisd.py"
    daemon_text = daemon.read_text(encoding="utf-8", errors="replace") if daemon.is_file() else "#!/usr/bin/env python3\nprint('metisd placeholder')\n"
    policy = {
        "schema": "metis.runtime_policy.v1",
        "workspace": "/workspace",
        "artifacts": "/artifacts",
        "diagnostics": "/diagnostics",
        "network_default": "deny",
        "deny_read": ["/root/.ssh", "/root/.gnupg", ".env", ".env.*"],
        "deny_write": [".git", ".env", ".env.*"],
    }
    tar_path.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(tar_path, "w") as archive:
        for directory in (
            "usr",
            "usr/local",
            "usr/local/bin",
            "etc",
            "etc/metis",
            "workspace",
            "artifacts",
            "diagnostics",
            "uploads",
            "outputs",
        ):
            info = tarfile.TarInfo(directory)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            archive.addfile(info)
        _tar_add_bytes(archive, "usr/local/bin/metisd", daemon_text.encode("utf-8"), mode=0o755)
        _tar_add_bytes(
            archive,
            "usr/local/bin/metis-sandbox-helper",
            b"#!/bin/sh\nexec \"$@\"\n",
            mode=0o755,
        )
        _tar_add_bytes(archive, "etc/metis/runtime-policy.json", json.dumps(policy, indent=2, sort_keys=True).encode("utf-8") + b"\n")


def _tar_add_bytes(archive: tarfile.TarFile, name: str, data: bytes, *, mode: int = 0o644) -> None:
    info = tarfile.TarInfo(name)
    info.size = len(data)
    info.mode = mode
    info.mtime = int(time.time())
    import io

    archive.addfile(info, io.BytesIO(data))


def _run_command(args: List[str], *, cwd: Path, timeout: int) -> Dict[str, Any]:
    started = time.time()
    try:
        proc = subprocess.run(
            args,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return {
            "ok": proc.returncode == 0,
            "args": args,
            "returncode": proc.returncode,
            "stdout": (proc.stdout or "")[-8000:],
            "stderr": (proc.stderr or "")[-8000:],
            "duration_ms": int((time.time() - started) * 1000),
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "args": args,
            "returncode": None,
            "stdout": (exc.stdout or "")[-8000:] if isinstance(exc.stdout, str) else "",
            "stderr": (exc.stderr or "")[-8000:] if isinstance(exc.stderr, str) else "",
            "timed_out": True,
            "duration_ms": int((time.time() - started) * 1000),
        }


def _vm_runtime_status(root: str, vm_bundle: Dict[str, Any]) -> Dict[str, Any]:
    selected = _first_dict(vm_bundle.get("selected_bundle"))
    selected_path = str(selected.get("path") or "")
    install_dir = _runtime_pack_install_dir()
    installed_bundle = _bundle_report(install_dir)
    selected_report = _bundle_report(Path(selected_path)) if selected_path else {}
    report = selected_report if selected_report.get("exists") else installed_bundle
    asset_bytes = int(report.get("total_bytes") or selected.get("total_known_bytes") or 0)
    assets_verified = bool(report.get("sha256_verified"))
    bundle_path = str(report.get("path") or selected_path or install_dir)
    installed = bool(Path(bundle_path).is_dir() and (selected.get("metis_owned") or report.get("metis_owned") or report.get("required_present")))
    runner_ready = bool(selected.get("runner_ready") or vm_bundle.get("runnable"))
    return {
        "schema": "metis.runtime_manager.vm_runtime.v1",
        "installed": installed,
        "install_dir": str(install_dir),
        "bundle_path": bundle_path,
        "selected_bundle": selected,
        "bundle_detected": bool(vm_bundle.get("bundle_detected") or report.get("exists")),
        "metis_owned": bool(selected.get("metis_owned") or report.get("metis_owned")),
        "runner_ready": runner_ready,
        "guest_protocol_ready": bool(selected.get("guest_protocol_ready")),
        "hcs_direct_ready": bool(selected.get("hcs_direct_ready")),
        "runner_transport": str(selected.get("runner_transport") or ""),
        "assets_verified": assets_verified,
        "asset_bytes": asset_bytes,
        "missing_required": list(report.get("missing_required") or selected.get("missing_required") or []),
        "asset_report": report,
        "candidate_count": len(vm_bundle.get("configured_candidates") or []),
        "reason": str(vm_bundle.get("reason") or ""),
        "host": _first_dict(vm_bundle.get("host")),
    }


def _release_integration_status(*, vm_runtime: Dict[str, Any]) -> Dict[str, Any]:
    bundled = _bundled_runtime_pack_dir()
    bundled_report = _bundle_report(bundled) if bundled else {}
    bundled_ready = bool(bundled_report.get("required_present"))
    download_url = _runtime_pack_download_url()
    installed_report = _bundle_report(_runtime_pack_install_dir())
    strategies = [
        {
            "id": "bundled",
            "label": "Installer bundled runtime pack",
            "available": bundled_ready,
            "description": "Put a prepared metisvm.bundle or runtime-bundle-v2 payload under desktop/resources/runtime-pack before building the installer.",
        },
        {
            "id": "download",
            "label": "First-start runtime pack download",
            "available": bool(download_url),
            "description": "Set METIS_RUNTIME_PACK_URL to a zip or release manifest URL; repair can download and install it on first run.",
        },
    ]
    if bundled_ready and download_url:
        selected_strategy = "bundled-first-download-fallback"
    elif bundled_ready:
        selected_strategy = "bundled"
    elif download_url:
        selected_strategy = "download"
    else:
        selected_strategy = "manual"
    return {
        "ok": True,
        "schema": RUNTIME_MANAGER_RELEASE_SCHEMA,
        "install_strategy": selected_strategy,
        "installed_path": str(vm_runtime.get("bundle_path") or _runtime_pack_install_dir()),
        "installed_report": installed_report,
        "bundled_available": bundled_ready,
        "bundled_path": str(bundled or ""),
        "bundled_report": bundled_report,
        "download_available": bool(download_url),
        "download_url": download_url,
        "auto_prepare_enabled": _env_flag("METIS_RUNTIME_AUTO_ENSURE"),
        "strategies": strategies,
        "notes": [
            "Bundled mode keeps first launch offline-capable but can add 1GB+ to the installer.",
            "Download mode keeps the installer small and installs the runtime pack after launch.",
            "Both modes install into the Metis-owned vm_bundles/metisvm.bundle directory used by backend=auto.",
        ],
    }


def _runtime_pack_install_dir() -> Path:
    for key in ("METIS_VM_BUNDLE_DIR", "METIS_RUNTIME_BUNDLE_DIR"):
        raw = str(os.environ.get(key) or "").strip()
        if raw:
            return Path(raw).expanduser().resolve(strict=False)
    local_app = str(os.environ.get("LOCALAPPDATA") or "").strip()
    if local_app:
        return (Path(local_app) / "Metis" / "vm_bundles" / "metisvm.bundle").resolve(strict=False)
    app_data = str(os.environ.get("APPDATA") or "").strip()
    if app_data:
        return (Path(app_data) / "Metis" / "vm_bundles" / "metisvm.bundle").resolve(strict=False)
    metis_home = str(os.environ.get("METIS_HOME") or "").strip()
    if metis_home:
        return (Path(metis_home) / "vm_bundles" / "metisvm.bundle").resolve(strict=False)
    return (Path.home() / ".metis" / "vm_bundles" / "metisvm.bundle").resolve(strict=False)


def _bundled_runtime_pack_dir() -> Path | None:
    for key in ("METIS_BUNDLED_RUNTIME_PACK_DIR", "METIS_RUNTIME_PACK_BUNDLED_DIR"):
        raw = str(os.environ.get(key) or "").strip()
        if not raw:
            continue
        candidate = _normalize_runtime_pack_source(Path(raw).expanduser().resolve(strict=False))
        if candidate.exists():
            return candidate
    return None


def _runtime_pack_download_url() -> str:
    for key in ("METIS_RUNTIME_PACK_URL", "METIS_RUNTIME_BUNDLE_URL"):
        raw = str(os.environ.get(key) or "").strip()
        if raw:
            return raw
    if str(os.environ.get("METIS_RUNTIME_PACK_DISABLE_DEFAULT") or "").strip() == "1":
        return ""
    return DEFAULT_RUNTIME_PACK_URL


def _select_repair_source(*, release: Dict[str, Any], source: str, allow_download: bool) -> Dict[str, Any]:
    if source not in {"auto", "bundled", "download", "manual"}:
        return {"ok": False, "code": "METIS_RUNTIME_PACK_SOURCE_INVALID", "error": f"Unsupported runtime pack source: {source}"}
    bundled_path = str(release.get("bundled_path") or "")
    if source in {"auto", "bundled"} and release.get("bundled_available") and bundled_path and Path(bundled_path).exists():
        return {"ok": True, "kind": "bundled", "path": bundled_path}
    if source == "bundled":
        return {"ok": False, "code": "METIS_BUNDLED_RUNTIME_PACK_MISSING", "error": "Bundled runtime pack is not present in this installer."}
    download_url = str(release.get("download_url") or "")
    if source == "download" or (source == "auto" and allow_download and download_url):
        if not download_url:
            return {"ok": False, "code": "METIS_RUNTIME_PACK_URL_MISSING", "error": "METIS_RUNTIME_PACK_URL is not configured."}
        return {"ok": True, "kind": "download", "url": download_url, "path": ""}
    return {
        "ok": False,
        "code": "METIS_RUNTIME_PACK_SOURCE_MISSING",
        "error": "No bundled runtime pack is available. Configure METIS_RUNTIME_PACK_URL or place a pack under desktop/resources/runtime-pack.",
    }


def _normalize_runtime_pack_source(path: Path) -> Path:
    if (path / "metisvm.bundle").is_dir():
        return path / "metisvm.bundle"
    if (path / "metis-runtime-bundle-v2").is_dir():
        return path / "metis-runtime-bundle-v2"
    return path


def _install_runtime_pack_from_dir(source_dir: Path, install_dir: Path, *, force: bool) -> Dict[str, Any]:
    source = _normalize_runtime_pack_source(source_dir)
    if not source.is_dir():
        return {"ok": False, "code": "METIS_RUNTIME_PACK_SOURCE_NOT_DIR", "error": f"runtime pack source is not a directory: {source}"}
    if install_dir.exists() and force:
        shutil.rmtree(install_dir)
    install_dir.mkdir(parents=True, exist_ok=True)
    copied: List[Dict[str, Any]] = []
    for item in source.rglob("*"):
        if not item.is_file():
            continue
        rel = item.relative_to(source)
        if _runtime_pack_copy_excluded(rel):
            continue
        dest = install_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and not force and _sha256_file(dest) == _sha256_file(item):
            continue
        shutil.copy2(item, dest)
        copied.append({"relative_path": rel.as_posix(), "path": str(dest), "size_bytes": dest.stat().st_size})
    extraction = _extract_rootfs_zst_if_needed(install_dir, force=force)
    report = _bundle_report(install_dir)
    ok = bool(report.get("required_present")) and bool(extraction.get("ok", True))
    return {
        "ok": ok,
        "source_dir": str(source),
        "install_dir": str(install_dir),
        "copied_count": len(copied),
        "copied": copied[:80],
        "extraction": extraction,
        "report": report,
        "error": "" if ok else extraction.get("error") or f"required assets are missing: {report.get('missing_required')}",
    }


def _runtime_pack_copy_excluded(rel: Path) -> bool:
    parts = {part.lower() for part in rel.parts}
    name = rel.name.lower()
    if name in {".env", "id_rsa", "id_dsa"}:
        return True
    if name.endswith((".key", ".pfx", ".pem")):
        return True
    return "__pycache__" in parts


def _download_runtime_pack(url: str, *, root: str, validate_only: bool = False) -> Dict[str, Any]:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https", "file"}:
        return {"ok": False, "code": "METIS_RUNTIME_PACK_URL_INVALID", "error": "runtime pack URL must be http, https, or file."}
    downloads = Path(str(root or ".")).resolve(strict=False) / ".metis" / "runtime-pack" / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = _runtime_pack_release_descriptor(url)
        effective_url = str(descriptor.get("download_url") or url)
        expected_package_sha = str(descriptor.get("package_sha256") or "").strip().lower()
        filename = Path(urlparse(effective_url).path).name or "metis-runtime-pack.zip"
        target = downloads / filename
        with urlopen(effective_url, timeout=90) as response, target.open("wb") as handle:  # noqa: S310 - user-configured release URL.
            shutil.copyfileobj(response, handle)
        package_sha = _sha256_file(target)
        if expected_package_sha and package_sha.lower() != expected_package_sha:
            return {
                "ok": False,
                "code": "METIS_RUNTIME_PACK_PACKAGE_SHA_MISMATCH",
                "error": "Downloaded runtime pack zip SHA256 does not match release manifest.",
                "expected_sha256": expected_package_sha,
                "actual_sha256": package_sha,
                "download_path": str(target),
                "release": descriptor,
            }
        if target.suffix.lower() != ".zip":
            return {"ok": False, "code": "METIS_RUNTIME_PACK_DOWNLOAD_NOT_ZIP", "error": f"Downloaded runtime pack is not a zip: {target}"}
        extract_dir = downloads / target.stem
        if extract_dir.exists():
            shutil.rmtree(extract_dir)
        _safe_extract_zip(target, extract_dir)
        bundle = _normalize_runtime_pack_source(extract_dir)
        report = _bundle_report(bundle)
        if not report.get("required_present"):
            return {
                "ok": False,
                "code": "METIS_RUNTIME_PACK_REQUIRED_ASSETS_MISSING",
                "error": f"runtime pack is missing required assets: {report.get('missing_required')}",
                "url": effective_url,
                "download_path": str(target),
                "extract_dir": str(extract_dir),
                "bundle_path": str(bundle),
                "package_sha256": package_sha,
                "release": descriptor,
                "report": report,
            }
        if report.get("has_sha256s") and not report.get("sha256_verified"):
            return {
                "ok": False,
                "code": "METIS_RUNTIME_PACK_FILE_SHA_MISMATCH",
                "error": "runtime pack internal SHA256SUMS.txt verification failed.",
                "url": effective_url,
                "download_path": str(target),
                "extract_dir": str(extract_dir),
                "bundle_path": str(bundle),
                "package_sha256": package_sha,
                "release": descriptor,
                "report": report,
            }
        return {
            "ok": True,
            "url": effective_url,
            "download_path": str(target),
            "extract_dir": str(extract_dir),
            "bundle_path": str(bundle),
            "package_sha256": package_sha,
            "expected_package_sha256": expected_package_sha,
            "validate_only": bool(validate_only),
            "release": descriptor,
            "report": report,
        }
    except Exception as exc:
        return {"ok": False, "code": "METIS_RUNTIME_PACK_DOWNLOAD_FAILED", "error": f"{type(exc).__name__}: {exc}"}


def _runtime_pack_release_descriptor(url: str) -> Dict[str, Any]:
    if not url.lower().endswith(".json"):
        return {"kind": "zip", "source_url": url, "download_url": url, "package_sha256": ""}
    with urlopen(url, timeout=30) as response:  # noqa: S310 - user-configured release manifest URL.
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        return {"kind": "manifest", "source_url": url, "download_url": url, "package_sha256": "", "manifest": {}}
    package = _first_dict(payload.get("package"))
    download_url = ""
    for key in ("url", "download_url", "browser_download_url"):
        value = str(package.get(key) or payload.get(key) or "").strip()
        if value:
            download_url = value
            break
    name = str(package.get("name") or "").strip()
    if not download_url and name:
        download_url = f"{url.rsplit('/', 1)[0]}/{name}"
    return {
        "kind": "manifest",
        "source_url": url,
        "download_url": download_url or url,
        "package_name": name,
        "package_sha256": str(package.get("sha256") or payload.get("package_sha256") or payload.get("sha256") or "").strip().lower(),
        "package_size_bytes": int(package.get("size_bytes") or payload.get("package_size_bytes") or 0),
        "manifest_schema": str(payload.get("schema") or ""),
        "manifest": payload,
    }


def _resolve_runtime_pack_download_url(url: str) -> str:
    if not url.lower().endswith(".json"):
        return url
    with urlopen(url, timeout=30) as response:  # noqa: S310 - user-configured release manifest URL.
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        return url
    package = _first_dict(payload.get("package"))
    for key in ("url", "download_url", "browser_download_url"):
        value = str(package.get(key) or payload.get(key) or "").strip()
        if value:
            return value
    name = str(package.get("name") or "").strip()
    base = url.rsplit("/", 1)[0]
    return f"{base}/{name}" if name else url


def _safe_extract_zip(zip_path: Path, target_dir: Path) -> None:
    target_dir.mkdir(parents=True, exist_ok=True)
    target_root = target_dir.resolve(strict=False)
    with zipfile.ZipFile(zip_path) as archive:
        for info in archive.infolist():
            dest = (target_root / info.filename).resolve(strict=False)
            if os.path.commonpath([str(target_root), str(dest)]) != str(target_root):
                raise ValueError(f"unsafe zip entry: {info.filename}")
        archive.extractall(target_root)


def _extract_rootfs_zst_if_needed(bundle_dir: Path, *, force: bool) -> Dict[str, Any]:
    rootfs = bundle_dir / VM_ROOTFS_FILE
    compressed = bundle_dir / VM_ROOTFS_ZST_FILE
    if rootfs.is_file() and not force:
        return {"ok": True, "needed": False, "path": str(rootfs)}
    if not compressed.is_file():
        return {"ok": True, "needed": False, "path": str(rootfs), "reason": "rootfs.vhdx.zst not present"}
    if rootfs.exists() and force:
        rootfs.unlink()
    try:
        import zstandard  # type: ignore

        dctx = zstandard.ZstdDecompressor()
        with compressed.open("rb") as src, rootfs.open("wb") as dst:
            dctx.copy_stream(src, dst)
        return {"ok": True, "needed": True, "kind": "python-zstandard", "path": str(rootfs), "source": str(compressed)}
    except Exception as py_exc:
        zstd = shutil.which("zstd.exe") or shutil.which("zstd")
        if not zstd:
            return {
                "ok": False,
                "needed": True,
                "code": "METIS_RUNTIME_PACK_ZSTD_MISSING",
                "error": f"rootfs.vhdx.zst requires python zstandard or zstd.exe to extract ({type(py_exc).__name__}: {py_exc})",
            }
        proc = subprocess.run([zstd, "-d", "-f", "-o", str(rootfs), str(compressed)], capture_output=True, text=True, timeout=3600, check=False)
        return {
            "ok": proc.returncode == 0,
            "needed": True,
            "kind": "zstd-cli",
            "path": str(rootfs),
            "source": str(compressed),
            "returncode": proc.returncode,
            "stdout": proc.stdout[-4000:],
            "stderr": proc.stderr[-4000:],
            "error": "" if proc.returncode == 0 else proc.stderr[-4000:] or proc.stdout[-4000:],
        }


def _bundle_report(path: Path) -> Dict[str, Any]:
    if not path:
        return {}
    exists = path.exists()
    is_dir = exists and path.is_dir()
    files: List[Dict[str, Any]] = []
    total_bytes = 0
    if is_dir:
        for item in path.rglob("*"):
            if item.is_file():
                size = item.stat().st_size
                total_bytes += size
                files.append({"relative_path": item.relative_to(path).as_posix(), "size_bytes": size})
    required = list(VM_REQUIRED_RUNTIME_FILES)
    missing = [name for name in required if not (path / name).is_file()]
    rootfs_present = (path / VM_ROOTFS_FILE).is_file() or (path / VM_ROOTFS_ZST_FILE).is_file()
    if not rootfs_present:
        missing.append(VM_ROOTFS_FILE)
    sha = _verify_sha256s(path) if is_dir else {"has_sha256s": False, "verified": False, "checked": [], "errors": []}
    manifest = _read_json_file(path / "metis-vm-pack.json") if is_dir else {}
    owner = str(manifest.get("owner") or "").lower()
    return {
        "path": str(path),
        "exists": exists,
        "is_dir": is_dir,
        "total_bytes": total_bytes,
        "file_count": len(files),
        "required_files": [*required, VM_ROOTFS_FILE],
        "missing_required": missing,
        "required_present": is_dir and not missing,
        "rootfs_present": rootfs_present,
        "rootfs_compressed": (path / VM_ROOTFS_ZST_FILE).is_file(),
        "metis_owned": owner == "metis",
        "manifest_schema": str(manifest.get("schema") or ""),
        "version": str(manifest.get("version") or ""),
        "has_sha256s": bool(sha.get("has_sha256s")),
        "sha256_verified": bool(sha.get("verified")),
        "sha256": sha,
        "files": files[:160],
    }


def _verify_sha256s(bundle_dir: Path) -> Dict[str, Any]:
    sha_path = bundle_dir / "SHA256SUMS.txt"
    if not sha_path.is_file():
        return {"has_sha256s": False, "verified": False, "checked": [], "errors": []}
    checked: List[Dict[str, Any]] = []
    errors: List[str] = []
    for raw in sha_path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        if len(parts) < 2:
            continue
        expected, rel = parts[0].lower(), parts[-1].lstrip("*")
        candidates = [bundle_dir / rel, bundle_dir / rel.replace("metis-runtime-bundle-v2/", "", 1)]
        file_path = next((candidate for candidate in candidates if candidate.is_file()), candidates[0])
        if not file_path.is_file():
            errors.append(f"missing {rel}")
            checked.append({"relative_path": rel, "ok": False, "reason": "missing"})
            continue
        actual = _sha256_file(file_path)
        ok = actual.lower() == expected
        if not ok:
            errors.append(f"sha256 mismatch {rel}")
        checked.append({"relative_path": rel, "ok": ok, "expected": expected, "actual": actual})
    return {"has_sha256s": True, "verified": bool(checked) and not errors, "checked": checked, "errors": errors}


def _read_json_file(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _env_flag(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _loads(text: str) -> Dict[str, Any]:
    try:
        data = json.loads(text or "{}")
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "raw": str(text or "")}
    return data if isinstance(data, dict) else {"ok": False, "error": "Expected JSON object", "raw": data}


def _first_dict(*values: Any) -> Dict[str, Any]:
    for value in values:
        if isinstance(value, dict):
            return value
    return {}


def _recommended_actions(
    *,
    health: Dict[str, Any],
    rootfs: Dict[str, Any],
    builder: Dict[str, Any],
    wsl_runtime: Dict[str, Any],
    vm_runtime: Dict[str, Any],
    release_integration: Dict[str, Any],
) -> List[Dict[str, Any]]:
    actions: List[Dict[str, Any]] = []
    if not vm_runtime.get("installed") or not vm_runtime.get("assets_verified"):
        actions.append(
            {
                "id": "repair-runtime",
                "label": "Repair or install VM runtime",
                "status": "available"
                if release_integration.get("bundled_available") or release_integration.get("download_available")
                else "blocked",
                "description": "Install the Metis VM runtime pack from bundled release assets or a configured runtime download URL.",
            }
        )
    if vm_runtime.get("missing_required"):
        actions.append(
            {
                "id": "build-vm-assets",
                "label": "Build real VM runtime assets",
                "status": "available" if health.get("docker_available") or health.get("wsl_available") else "blocked",
                "description": "Build or assemble rootfs.vhdx, vmlinuz, initrd, metis-bin.vhdx, guest protocol files, and optional release package.",
            }
        )
    if release_integration.get("download_available"):
        actions.append(
            {
                "id": "validate-release",
                "label": "Validate runtime release source",
                "status": "available",
                "description": "Download the configured runtime release source and verify package SHA plus internal SHA256SUMS before install.",
            }
        )
    if vm_runtime.get("runner_ready"):
        actions.append(
            {
                "id": "startup-test",
                "label": "Run VM/runtime startup test",
                "status": "ready",
                "description": "Create an auto runtime session, run a smoke command, and verify artifact collection.",
            }
        )
    if vm_runtime.get("asset_report", {}).get("required_present"):
        actions.append(
            {
                "id": "package-vm-bundle",
                "label": "Package VM runtime release bundle",
                "status": "available",
                "description": "Create the v2 release zip, manifest, SHA256SUMS, install script, and latest metadata.",
            }
        )
    if health.get("rootfs_ready") and not health.get("runtime_bundle_ready"):
        actions.append(
            {
                "id": "prepare-bundle",
                "label": "Prepare Metis runtime bundle",
                "status": "available",
                "description": "Write Metis-owned runtime manifest, origin markers, install scripts, and latest metadata.",
            }
        )
    if health.get("runtime_bundle_ready"):
        actions.append(
            {
                "id": "package-bundle",
                "label": "Package runtime bundle",
                "status": "available",
                "description": "Create a release zip, SHA256 file, and runtime release manifest.",
            }
        )
    if health.get("metis_wsl_ready"):
        actions.append(
            {
                "id": "smoke",
                "label": "Run runtime smoke",
                "status": "ready",
                "description": "Verify Python, Node, Git, rg, and artifact collection inside the selected backend.",
            }
        )
    elif health.get("rootfs_ready") and wsl_runtime.get("ready_to_import"):
        actions.append(
            {
                "id": "import",
                "label": "Import MetisRuntime",
                "status": "available",
                "description": "Register the verified rootfs as the managed MetisRuntime WSL distro.",
            }
        )
    elif not rootfs.get("ok") or not health.get("rootfs_ready"):
        actions.append(
            {
                "id": "build-plan",
                "label": "Prepare rootfs build plan",
                "status": "available" if _first_dict(builder.get("docker")).get("available") else "blocked",
                "description": "Create a rootfs build plan. Actual builds should run as an explicit long task.",
            }
        )
    if not health.get("docker_available") and not health.get("wsl_available"):
        actions.append(
            {
                "id": "fallback",
                "label": "Use local-copy fallback",
                "status": "ready",
                "description": "Metis can still run isolated copy-mode tasks without Docker or WSL.",
            }
        )
    actions.append(
        {
            "id": "diagnostics",
            "label": "Export runtime diagnostics",
            "status": "ready",
            "description": "Collect recent runtime manifests, command logs, artifacts, and patch summaries.",
        }
    )
    return actions


def _runtime_bundle_from_path(bundle_path: str) -> Dict[str, Any]:
    path_text = str(bundle_path or "").strip()
    if not path_text:
        return {}
    manifest = Path(path_text) / "metis-runtime-bundle.json"
    if not manifest.is_file():
        return {
            "ready": False,
            "manifest_path": str(manifest),
            "reason": "metis-runtime-bundle.json not found",
        }
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "ready": False,
            "manifest_path": str(manifest),
            "reason": f"{type(exc).__name__}: {exc}",
        }
    if not isinstance(data, dict):
        return {
            "ready": False,
            "manifest_path": str(manifest),
            "reason": "runtime bundle manifest must be an object",
        }
    return {
        **data,
        "manifest_path": str(manifest),
    }


def _runtime_versions_from_sessions(sessions: Dict[str, Any]) -> Dict[str, str]:
    rows = sessions.get("sessions") if isinstance(sessions.get("sessions"), list) else []
    for row in rows:
        if isinstance(row, dict) and row.get("backend") == "metis_wsl":
            return {"last_backend": "metis_wsl", "last_session": str(row.get("session_id") or "")}
    return {"last_backend": "", "last_session": ""}


def _smoke_command(backend: str) -> str:
    code = r"""
import os
import pathlib
import shutil
import subprocess
import sys

print("python=" + sys.version.split()[0])
artifacts = pathlib.Path(os.environ.get("METIS_RUNTIME_ARTIFACTS_DIR", "."))
artifacts.mkdir(parents=True, exist_ok=True)
(artifacts / "runtime-smoke.txt").write_text("ok from Metis runtime manager\n", encoding="utf-8")

for name in ("node", "npm", "git", "rg", "pdfinfo"):
    path = shutil.which(name)
    if not path:
        print(f"{name}=missing")
        continue
    try:
        proc = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=12)
        line = (proc.stdout or proc.stderr or "").splitlines()[0] if (proc.stdout or proc.stderr) else "ok"
        print(f"{name}={line}")
    except Exception as exc:
        print(f"{name}=error:{type(exc).__name__}")

try:
    import docx, openpyxl, pdfplumber, pypdf, reportlab  # noqa: F401
    print("python_doc_libs=ok")
except Exception as exc:
    print(f"python_doc_libs=error:{type(exc).__name__}:{exc}")
"""
    encoded = base64.b64encode(code.encode("utf-8")).decode("ascii")
    exe = "python3" if backend in {"metis_wsl", "wsl", "docker"} else "python"
    return f'{exe} -c "exec(__import__(\'base64\').b64decode(\'{encoded}\').decode(\'utf-8\'))"'

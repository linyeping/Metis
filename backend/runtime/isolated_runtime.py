from __future__ import annotations

import difflib
import fnmatch
import hashlib
import json
import os
import re
import shlex
import shutil
import socket
import subprocess
import time
import urllib.parse
import urllib.request
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from backend.runtime.python_env import (
    configured_python_executable,
    shell_command_with_configured_python,
    subprocess_env_with_configured_python,
)
from backend.runtime.sandbox_boundary import runtime_manifest_boundary
from backend.tools.coding.foundation.core_mechanisms.execution_boundary_context import (
    get_effective_sub_allow,
)
from backend.tools.coding.foundation.core_mechanisms.path_security import (
    PathSecurityError,
    get_workspace_root,
    safe_path_for_read,
    safe_path_for_write,
)


RUNTIME_SCHEMA = "metis.isolated_runtime.v1"
SESSION_SCHEMA = "metis.isolated_runtime.session.v1"
SANDBOX_STATUS_SCHEMA = "metis.sandbox_runtime.status.v1"
VM_BUNDLE_STATUS_SCHEMA = "metis.vm_runtime_pack.status.v1"
VM_PACK_SCAFFOLD_SCHEMA = "metis.vm_runtime_pack.scaffold.v1"
VM_PACK_REFERENCE_ADOPT_SCHEMA = "metis.vm_runtime_pack.reference_adopt.v1"
VM_PACK_MANIFEST_SCHEMA = "metis.vm_runtime_pack.manifest.v1"
METIS_RUNTIME_BUNDLE_SCHEMA = "metis.runtime_bundle.prepare.v1"
METIS_RUNTIME_BUNDLE_MANIFEST_SCHEMA = "metis.runtime_bundle.manifest.v1"
METIS_RUNTIME_BUNDLE_PACKAGE_SCHEMA = "metis.runtime_bundle.package.v1"
METIS_RUNTIME_BUNDLE_PACKAGE_V2_SCHEMA = "metis.runtime_bundle.package.v2"
METIS_VM_DIRECT_ASSETS_SCHEMA = "metis.vm_direct.assets.v1"
METIS_VM_DIRECT_RUNNER_SCHEMA = "metis.vm_direct.runner.v1"
METIS_VM_HCS_STARTER_SCHEMA = "metis.vm_direct.hcs_starter.v1"
METIS_VM_ROOTFS_BOOT_VERIFIER_SCHEMA = "metis.vm_direct.rootfs_boot_verifier.v1"
METIS_VM_GUEST_HANDSHAKE_SCHEMA = "metis.vm_direct.guest_handshake.v1"
METIS_WSL_STATUS_SCHEMA = "metis.wsl_runtime.status.v1"
METIS_WSL_IMPORT_SCHEMA = "metis.wsl_runtime.import.v1"
ROOTFS_ASSET_STATUS_SCHEMA = "metis.rootfs_asset.status.v1"
ROOTFS_ASSET_REGISTER_SCHEMA = "metis.rootfs_asset.register.v1"
ROOTFS_SOURCE_STATUS_SCHEMA = "metis.rootfs_source.status.v1"
ROOTFS_DOWNLOAD_SCHEMA = "metis.rootfs_asset.download.v1"
ROOTFS_BUILDER_STATUS_SCHEMA = "metis.rootfs_builder.status.v1"
ROOTFS_BUILD_SCHEMA = "metis.rootfs_builder.build.v1"
ROOTFS_IMAGE_BUILDER_STATUS_SCHEMA = "metis.rootfs_image_builder.status.v1"
ROOTFS_IMAGE_BUILD_SCHEMA = "metis.rootfs_image_builder.build.v1"

DEFAULT_MAX_FILES = 2000
DEFAULT_MAX_BYTES = 80 * 1024 * 1024
MAX_TEXT_DIFF_BYTES = 512 * 1024
MAX_RETURN_CHARS = 6000

EXCLUDED_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".metis",
    ".miro",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    "dist",
    "build",
    "out",
    "release",
    "venv",
    ".venv",
    "env",
    ".idea",
    ".vscode",
}

EXCLUDED_FILE_PATTERNS = {
    ".env",
    ".env.*",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "id_rsa",
    "id_ed25519",
    "*.key",
    "*.pem",
    "*.pfx",
    "*.p12",
    "*.ppk",
    "*.pyc",
    "*.pyo",
    "*.log",
    "*.exe",
    "*.msi",
    "*.dll",
    "*.so",
    "*.dylib",
    "*.zip",
    "*.7z",
    "*.rar",
}

ARTIFACT_PATTERNS = {
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.webp",
    "*.bmp",
    "*.svg",
    "*.pdf",
    "*.docx",
    "*.xlsx",
    "*.pptx",
    "*.csv",
    "*.tsv",
    "*.json",
    "*.md",
    "*.txt",
    "*.log",
}

VM_REQUIRED_FILES = ("rootfs.vhdx", "vmlinuz", "initrd")
VM_OPTIONAL_FILES = (
    "sessiondata.vhdx",
    "smol-bin.vhdx",
    "metis-bin.vhdx",
    "rootfs.vhdx.zst",
    "vmlinuz.zst",
    "initrd.zst",
)
VM_MANIFEST_NAME = "metis-vm-pack.json"
RUNTIME_BUNDLE_MANIFEST_NAME = "metis-runtime-bundle.json"
RUNTIME_BUNDLE_LATEST_NAME = "metis-runtime-latest.json"
DIRECT_VM_ASSETS_MANIFEST_NAME = "metis-direct-vm-assets.json"
DIRECT_VM_RUNNER_MANIFEST_NAME = "metis-direct-runner.json"
HCS_STARTER_MANIFEST_NAME = "metis-hcs-starter.json"
ROOTFS_BOOT_VERIFIER_MANIFEST_NAME = "metis-rootfs-boot-verifier.json"
GUEST_HANDSHAKE_MANIFEST_NAME = "metis-guest-handshake.json"
ROOTFS_IMAGE_BUILDER_MANIFEST_NAME = "metis-rootfs-image-builder.json"
DEFAULT_METIS_WSL_DISTRO = "MetisRuntime"
ROOTFS_IMPORT_ASSET_NAMES = (
    "rootfs.tar",
    "rootfs.tar.gz",
    "rootfs.tgz",
    "rootfs.tar.zst",
    "rootfs.vhdx",
)

NETWORK_COMMAND_RE = re.compile(
    r"(?i)(^|[;&|()\s])("
    r"curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod|"
    r"pip\s+install|python\s+-m\s+pip\s+install|py\s+-m\s+pip\s+install|"
    r"npm\s+(install|i|add)|pnpm\s+(install|add)|yarn\s+(install|add)|"
    r"git\s+(clone|pull|push|fetch)|gh\s+release|ssh|scp|rsync"
    r")(\s|$)",
)


def metis_vm_bundle_status(root: str = ".", bundle_path: str = "") -> str:
    """Detect Claude-style / Metis VM Runtime Pack assets without starting a VM."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
    except Exception:
        source_root = Path(str(root or ".")).expanduser().resolve(strict=False)
    status = _detect_vm_runtime_pack(source_root=source_root, bundle_path=bundle_path)
    return _json(
        {
            "ok": True,
            "schema": VM_BUNDLE_STATUS_SCHEMA,
            "root": str(source_root),
            **status,
        }
    )


def metis_vm_pack_scaffold(root: str = ".", output_path: str = "", force: bool = False) -> str:
    """Create a clean-room Metis VM Runtime Pack scaffold without VM boot assets."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        target = _resolve_vm_scaffold_output(source_root, output_path)
        if target.exists() and not target.is_dir():
            return _json_error(
                f"VM pack scaffold target is a file, expected directory: {target}",
                code="VM_PACK_SCAFFOLD_TARGET_FILE",
            )
        if target.exists() and any(target.iterdir()) and not force:
            return _json_error(
                f"VM pack scaffold target is not empty: {target}",
                code="VM_PACK_SCAFFOLD_EXISTS",
            )
        target.mkdir(parents=True, exist_ok=True)
        written = _write_vm_pack_scaffold_files(target)
        status = _detect_vm_runtime_pack(source_root=source_root, bundle_path=str(target))
        return _json(
            {
                "ok": True,
                "schema": VM_PACK_SCAFFOLD_SCHEMA,
                "root": str(source_root),
                "bundle_path": str(target),
                "written": written,
                "status": status,
                "next_steps": [
                    "Build or download Metis-owned rootfs.vhdx, vmlinuz, and initrd into this bundle.",
                    "Build metisd and sandbox-helper into metis-bin.vhdx or another guest tools image.",
                    "Implement the host VM runner against this manifest and guest protocol.",
                    "Keep Claude VM assets as reference-only; do not copy them into this bundle.",
                ],
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="VM_PACK_SCAFFOLD_FAILED")


def metis_vm_pack_adopt_reference(
    reference_bundle_path: str,
    root: str = ".",
    output_path: str = "",
    copy_assets: bool = False,
    hash_assets: bool = False,
    force: bool = False,
) -> str:
    """Create a Metis-owned VM pack plan from a Claude-style reference bundle.

    By default this is manifest-only: it records file shape, origin markers, and
    next steps without copying third-party assets.
    """
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        reference = safe_path_for_read(
            str(reference_bundle_path or ""),
            allow_paths_outside_workspace=True,
        )
        if not reference.is_dir():
            return _json_error(
                f"reference bundle is not a directory: {reference}",
                code="REFERENCE_BUNDLE_NOT_FOUND",
            )
        target = _resolve_vm_scaffold_output(source_root, output_path)
        target.mkdir(parents=True, exist_ok=True)
        written = _write_vm_pack_scaffold_files(target) if not (target / VM_MANIFEST_NAME).is_file() or force else []
        reference_status = _inspect_vm_bundle_path(reference, usage="reference_only")
        adoption = _build_reference_adoption_plan(
            reference=reference,
            target=target,
            reference_status=reference_status,
            hash_assets=bool(hash_assets),
        )
        copied: List[Dict[str, Any]] = []
        if copy_assets:
            copied = _copy_reference_bundle_assets(reference, target, force=bool(force))
            adoption["copied_assets"] = copied
        _write_reference_adoption_manifest(target, adoption, copied_assets=copied)
        plan_path = target / "reference-adoption-plan.json"
        plan_path.write_text(json.dumps(adoption, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        status = _detect_vm_runtime_pack(source_root=source_root, bundle_path=str(target))
        return _json(
            {
                "ok": True,
                "schema": VM_PACK_REFERENCE_ADOPT_SCHEMA,
                "root": str(source_root),
                "reference_bundle_path": str(reference),
                "bundle_path": str(target),
                "copy_assets": bool(copy_assets),
                "hash_assets": bool(hash_assets),
                "written": written,
                "copied_assets": copied,
                "plan_path": str(plan_path),
                "adoption": adoption,
                "status": status,
                "next_steps": [
                    "Review reference-adoption-plan.json.",
                    "Build or register Metis-owned rootfs assets before enabling strict runtime.",
                    "Use copy_assets=true only when you intentionally want to copy the referenced VM assets.",
                ],
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="VM_PACK_REFERENCE_ADOPT_FAILED")


def metis_runtime_bundle_prepare(
    root: str = ".",
    bundle_path: str = "",
    rootfs_path: str = "",
    version: str = "",
    channel: str = "local",
    expected_sha256: str = "",
    source_url: str = "",
    signature_path: str = "",
    public_key_path: str = "",
    copy_rootfs: bool = True,
    force: bool = False,
    dry_run: bool = False,
) -> str:
    """Prepare a Metis-owned runtime bundle around a verified rootfs asset."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        bundle = _resolve_vm_bundle_dir(source_root, bundle_path)
        normalized_version = _normalize_runtime_bundle_version(version)
        normalized_channel = _normalize_runtime_bundle_channel(channel)
        resolved_rootfs = ""
        registration: Dict[str, Any] = {}
        would_write = _runtime_bundle_would_write(bundle)

        if rootfs_path:
            rootfs_source = safe_path_for_read(
                str(rootfs_path or ""),
                allow_paths_outside_workspace=get_effective_sub_allow("allow_paths_outside_workspace"),
            )
            resolved_rootfs = str(rootfs_source)
            if not dry_run:
                registration = json.loads(
                    metis_rootfs_asset_register(
                        rootfs_path=str(rootfs_source),
                        root=str(source_root),
                        bundle_path=str(bundle),
                        expected_sha256=expected_sha256,
                        signature_path=signature_path,
                        public_key_path=public_key_path,
                        source_url=source_url or f"metis-owned:{rootfs_source.name}",
                        copy=bool(copy_rootfs),
                        force=bool(force),
                    )
                )
                if not registration.get("ok"):
                    return _json(registration)
                resolved_rootfs = str(registration.get("rootfs_path") or resolved_rootfs)

        if dry_run:
            rootfs_status = _detect_runtime_bundle_rootfs(source_root, bundle, resolved_rootfs)
            plan = _build_runtime_bundle_manifest(
                source_root=source_root,
                bundle_dir=bundle,
                version=normalized_version,
                channel=normalized_channel,
                rootfs_status=rootfs_status,
                registration=registration,
            )
            return _json(
                {
                    "ok": True,
                    "schema": METIS_RUNTIME_BUNDLE_SCHEMA,
                    "dry_run": True,
                    "root": str(source_root),
                    "bundle_path": str(bundle),
                    "version": normalized_version,
                    "channel": normalized_channel,
                    "rootfs_path": resolved_rootfs,
                    "would_write": would_write,
                    "plan": plan,
                    "next_steps": [
                        "Set dry_run=false to write the Metis runtime bundle manifest and install scripts.",
                        "Provide rootfs_path when preparing a fresh bundle from a rootfs asset.",
                    ],
                }
            )

        bundle.mkdir(parents=True, exist_ok=True)
        scaffold_written = _ensure_runtime_bundle_scaffold(bundle, force=bool(force))
        rootfs_status = _detect_runtime_bundle_rootfs(source_root, bundle, resolved_rootfs)
        manifest = _build_runtime_bundle_manifest(
            source_root=source_root,
            bundle_dir=bundle,
            version=normalized_version,
            channel=normalized_channel,
            rootfs_status=rootfs_status,
            registration=registration,
        )
        manifest_path = bundle / RUNTIME_BUNDLE_MANIFEST_NAME
        latest_path = bundle / RUNTIME_BUNDLE_LATEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        latest = _runtime_bundle_latest_manifest(manifest)
        latest_path.write_text(
            json.dumps(latest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        origins = _write_runtime_bundle_origin_markers(bundle, manifest)
        scripts = _write_runtime_bundle_scripts(bundle, manifest)
        vm_manifest = _upsert_runtime_bundle_vm_manifest(bundle, manifest)
        runtime_rootfs = _runtime_bundle_rootfs_abs_path(bundle, manifest)
        wsl_status = _detect_metis_wsl_runtime(
            source_root=source_root,
            rootfs_path=str(runtime_rootfs) if runtime_rootfs else "",
        )
        return _json(
            {
                "ok": True,
                "schema": METIS_RUNTIME_BUNDLE_SCHEMA,
                "dry_run": False,
                "root": str(source_root),
                "bundle_path": str(bundle),
                "version": normalized_version,
                "channel": normalized_channel,
                "rootfs_path": str(runtime_rootfs) if runtime_rootfs else "",
                "ready": bool(manifest.get("ready")),
                "registration": registration,
                "written": [
                    *scaffold_written,
                    {"relative_path": RUNTIME_BUNDLE_MANIFEST_NAME, "path": str(manifest_path), "size_bytes": manifest_path.stat().st_size},
                    {"relative_path": RUNTIME_BUNDLE_LATEST_NAME, "path": str(latest_path), "size_bytes": latest_path.stat().st_size},
                    *origins,
                    *scripts,
                ],
                "manifest": manifest,
                "vm_manifest": vm_manifest,
                "wsl_status": wsl_status,
                "next_steps": [
                    "Use install-metis-runtime.ps1 or metis_wsl_runtime_import to import the bundle as MetisRuntime.",
                    "After import, backend=auto can select metis_wsl for background tasks.",
                    "The bundle is Metis-owned only when rootfs verification is true and reference_adoption is absent.",
                ],
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="METIS_RUNTIME_BUNDLE_PREPARE_FAILED")


def metis_runtime_bundle_package(
    root: str = ".",
    bundle_path: str = "",
    output_dir: str = "",
    version: str = "",
    channel: str = "",
    include_rootfs: bool = True,
    force: bool = False,
    dry_run: bool = False,
) -> str:
    """Package a prepared Metis runtime bundle for release distribution."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        bundle = _resolve_vm_bundle_dir(source_root, bundle_path)
        manifest_path = bundle / RUNTIME_BUNDLE_MANIFEST_NAME
        if not manifest_path.is_file():
            return _json_error(
                f"runtime bundle manifest not found: {manifest_path}",
                code="METIS_RUNTIME_BUNDLE_NOT_PREPARED",
            )
        manifest = _read_json_object(manifest_path)
        if str(manifest.get("schema") or "") != METIS_RUNTIME_BUNDLE_MANIFEST_SCHEMA:
            return _json_error(
                f"unexpected runtime bundle manifest schema: {manifest.get('schema')}",
                code="METIS_RUNTIME_BUNDLE_SCHEMA_INVALID",
            )
        package_version = _normalize_runtime_bundle_version(version or str(manifest.get("version") or ""))
        package_channel = _normalize_runtime_bundle_channel(channel or str(manifest.get("channel") or "local"))
        release_dir = _resolve_runtime_bundle_release_dir(source_root, output_dir)
        package_name = _runtime_bundle_package_name(package_version, package_channel, include_rootfs=bool(include_rootfs))
        package_path = release_dir / package_name
        files = _runtime_bundle_package_files(bundle, manifest, include_rootfs=bool(include_rootfs))
        total_bytes = sum(int(item.get("size_bytes") or 0) for item in files)
        if dry_run:
            return _json(
                {
                    "ok": True,
                    "schema": METIS_RUNTIME_BUNDLE_PACKAGE_SCHEMA,
                    "dry_run": True,
                    "root": str(source_root),
                    "bundle_path": str(bundle),
                    "output_dir": str(release_dir),
                    "package_path": str(package_path),
                    "version": package_version,
                    "channel": package_channel,
                    "include_rootfs": bool(include_rootfs),
                    "file_count": len(files),
                    "total_bytes": total_bytes,
                    "files": files,
                    "next_steps": [
                        "Set dry_run=false to create the release zip, sha256 file, and release manifest.",
                        "Use include_rootfs=false only for metadata-only test packages.",
                    ],
                }
            )
        if package_path.exists() and not force:
            return _json_error(
                f"runtime bundle package already exists: {package_path}",
                code="METIS_RUNTIME_BUNDLE_PACKAGE_EXISTS",
            )
        release_dir.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in files:
                path = Path(str(item.get("path") or ""))
                arcname = str(item.get("archive_path") or "")
                _zip_add_if_exists(archive, path, arcname)
        package_sha = _sha256_file(package_path)
        sha_path = package_path.with_suffix(package_path.suffix + ".sha256")
        sha_path.write_text(f"{package_sha}  {package_path.name}\n", encoding="utf-8", newline="\n")
        release_manifest = _runtime_bundle_release_manifest(
            bundle_manifest=manifest,
            package_path=package_path,
            package_sha256=package_sha,
            files=files,
            include_rootfs=bool(include_rootfs),
            version=package_version,
            channel=package_channel,
        )
        release_manifest_path = release_dir / f"metis-runtime-release-{package_version}-{package_channel}.json"
        release_manifest_path.write_text(
            json.dumps(release_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        latest_path = release_dir / "metis-runtime-release-latest.json"
        latest_path.write_text(
            json.dumps(release_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return _json(
            {
                "ok": True,
                "schema": METIS_RUNTIME_BUNDLE_PACKAGE_SCHEMA,
                "dry_run": False,
                "root": str(source_root),
                "bundle_path": str(bundle),
                "output_dir": str(release_dir),
                "package_path": str(package_path),
                "sha256_path": str(sha_path),
                "release_manifest_path": str(release_manifest_path),
                "latest_manifest_path": str(latest_path),
                "version": package_version,
                "channel": package_channel,
                "include_rootfs": bool(include_rootfs),
                "file_count": len(files),
                "total_bytes": total_bytes,
                "package_size_bytes": package_path.stat().st_size,
                "package_sha256": package_sha,
                "release_manifest": release_manifest,
                "next_steps": [
                    "Upload the zip, .sha256, and release manifest as release assets.",
                    "Point METIS_UPDATE_URL or the runtime update channel at metis-runtime-release-latest.json when runtime auto-update is implemented.",
                ],
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="METIS_RUNTIME_BUNDLE_PACKAGE_FAILED")


def metis_runtime_bundle_package_v2(
    root: str = ".",
    bundle_path: str = "",
    output_dir: str = "",
    version: str = "",
    channel: str = "",
    package_name: str = "",
    include_sessiondata: bool = False,
    force: bool = False,
    dry_run: bool = False,
) -> str:
    """Package Metis direct-VM runtime assets for release distribution."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        bundle = _resolve_vm_bundle_dir(source_root, bundle_path)
        vm_manifest = _load_vm_manifest_data(bundle)
        if not vm_manifest:
            return _json_error(
                f"VM pack manifest not found: {bundle / VM_MANIFEST_NAME}",
                code="METIS_RUNTIME_BUNDLE_V2_NOT_PREPARED",
            )
        package_version = _normalize_runtime_bundle_version(version or str(vm_manifest.get("version") or ""))
        package_channel = _normalize_runtime_bundle_channel(channel or "direct")
        release_dir = _resolve_runtime_bundle_release_dir(source_root, output_dir)
        release_name = _runtime_bundle_package_v2_name(
            package_version,
            package_channel,
            package_name=package_name,
        )
        package_path = release_dir / f"{release_name}.zip"
        asset_status = _runtime_bundle_v2_asset_status(bundle, include_sessiondata=bool(include_sessiondata))
        compression = _detect_zstd_compressor()
        plan = _runtime_bundle_package_v2_plan(
            bundle=bundle,
            release_dir=release_dir,
            package_path=package_path,
            version=package_version,
            channel=package_channel,
            asset_status=asset_status,
            compression=compression,
            include_sessiondata=bool(include_sessiondata),
        )
        if dry_run:
            return _json(
                {
                    "ok": True,
                    "schema": METIS_RUNTIME_BUNDLE_PACKAGE_V2_SCHEMA,
                    "dry_run": True,
                    "root": str(source_root),
                    "bundle_path": str(bundle),
                    "output_dir": str(release_dir),
                    "package_path": str(package_path),
                    "version": package_version,
                    "channel": package_channel,
                    "asset_status": asset_status,
                    "compression": compression,
                    "plan": plan,
                    "next_steps": [
                        "Set dry_run=false to create rootfs.vhdx.zst, manifest, sha256 files, install scripts, and release zip.",
                        "Provide vmlinuz, initrd, rootfs.vhdx, and metis-bin.vhdx before packaging.",
                        "Install python zstandard or zstd.exe on the packaging machine if rootfs.vhdx.zst is missing.",
                    ],
                }
            )
        if package_path.exists() and not force:
            return _json_error(
                f"runtime bundle v2 package already exists: {package_path}",
                code="METIS_RUNTIME_BUNDLE_V2_PACKAGE_EXISTS",
            )
        missing_required = [item["name"] for item in asset_status.get("assets", []) if item.get("required") and not item.get("exists")]
        if missing_required:
            return _json_error(
                f"required runtime bundle v2 assets are missing: {', '.join(missing_required)}",
                code="METIS_RUNTIME_BUNDLE_V2_ASSETS_MISSING",
            )
        release_dir.mkdir(parents=True, exist_ok=True)
        prepared = _prepare_runtime_bundle_v2_release_files(
            bundle=bundle,
            release_dir=release_dir,
            version=package_version,
            channel=package_channel,
            compression=compression,
            include_sessiondata=bool(include_sessiondata),
            force=bool(force),
        )
        if not prepared.get("ok"):
            return _json(prepared)
        files = prepared.get("files") if isinstance(prepared.get("files"), list) else []
        with zipfile.ZipFile(package_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for item in files:
                path = Path(str(item.get("path") or ""))
                arcname = str(item.get("archive_path") or item.get("relative_path") or path.name)
                _zip_add_if_exists(archive, path, arcname)
        package_sha = _sha256_file(package_path)
        package_sha_path = package_path.with_suffix(package_path.suffix + ".sha256")
        package_sha_path.write_text(f"{package_sha}  {package_path.name}\n", encoding="utf-8", newline="\n")
        release_manifest = dict(prepared.get("release_manifest") or {})
        release_manifest["package"] = {
            "name": package_path.name,
            "path": str(package_path),
            "sha256": package_sha,
            "size_bytes": package_path.stat().st_size,
        }
        release_manifest_path = release_dir / f"metis-runtime-bundle-v2-{package_version}-{package_channel}.json"
        release_manifest_path.write_text(
            json.dumps(release_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        latest_path = release_dir / "metis-runtime-bundle-v2-latest.json"
        latest_path.write_text(
            json.dumps(release_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        return _json(
            {
                "ok": True,
                "schema": METIS_RUNTIME_BUNDLE_PACKAGE_V2_SCHEMA,
                "dry_run": False,
                "root": str(source_root),
                "bundle_path": str(bundle),
                "output_dir": str(release_dir),
                "package_path": str(package_path),
                "sha256_path": str(package_sha_path),
                "release_manifest_path": str(release_manifest_path),
                "latest_manifest_path": str(latest_path),
                "version": package_version,
                "channel": package_channel,
                "package_sha256": package_sha,
                "package_size_bytes": package_path.stat().st_size,
                "file_count": len(files),
                "total_bytes": sum(int(item.get("size_bytes") or 0) for item in files),
                "files": files,
                "compression": compression,
                "release_manifest": release_manifest,
                "next_steps": [
                    "Upload the zip, .sha256, manifest, and latest manifest as release assets.",
                    "End users can download the zip, run verify-metis-runtime-bundle-v2.ps1, then install-metis-runtime-bundle-v2.ps1.",
                    "This package removes the need for end users to install Docker or WSL just to build the runtime assets.",
                ],
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="METIS_RUNTIME_BUNDLE_PACKAGE_V2_FAILED")


def metis_vm_direct_assets_prepare(
    root: str = ".",
    bundle_path: str = "",
    rootfs_vhdx_path: str = "",
    kernel_path: str = "",
    initrd_path: str = "",
    metis_bin_path: str = "",
    sessiondata_path: str = "",
    version: str = "",
    copy_assets: bool = True,
    create_vhdx_scripts: bool = True,
    create_vhdx: bool = False,
    sessiondata_size_gb: int = 8,
    metis_bin_size_mb: int = 256,
    force: bool = False,
    dry_run: bool = False,
) -> str:
    """Prepare Metis direct-VM assets and HCS/Hyper-V runner scaffolding."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        bundle = _resolve_vm_bundle_dir(source_root, bundle_path)
        normalized_version = _normalize_runtime_bundle_version(version)
        inputs = {
            "rootfs.vhdx": rootfs_vhdx_path,
            "vmlinuz": kernel_path,
            "initrd": initrd_path,
            "metis-bin.vhdx": metis_bin_path,
            "sessiondata.vhdx": sessiondata_path,
        }
        plan = _build_direct_vm_assets_plan(
            source_root=source_root,
            bundle=bundle,
            version=normalized_version,
            inputs=inputs,
            copy_assets=bool(copy_assets),
            create_vhdx_scripts=bool(create_vhdx_scripts),
            create_vhdx=bool(create_vhdx),
            sessiondata_size_gb=max(1, int(sessiondata_size_gb or 8)),
            metis_bin_size_mb=max(64, int(metis_bin_size_mb or 256)),
        )
        if dry_run:
            return _json(
                {
                    "ok": True,
                    "schema": METIS_VM_DIRECT_ASSETS_SCHEMA,
                    "dry_run": True,
                    "root": str(source_root),
                    "bundle_path": str(bundle),
                    "version": normalized_version,
                    "plan": plan,
                    "next_steps": [
                        "Set dry_run=false to write direct VM asset scripts and manifest.",
                        "Provide rootfs_vhdx_path, kernel_path, and initrd_path when Metis-owned boot assets are available.",
                    ],
                }
            )

        bundle.mkdir(parents=True, exist_ok=True)
        scaffold_written = _ensure_runtime_bundle_scaffold(bundle, force=bool(force))
        copied = _copy_direct_vm_input_assets(bundle, inputs, copy_assets=bool(copy_assets), force=bool(force))
        scripts = _write_direct_vm_asset_scripts(
            bundle,
            sessiondata_size_gb=max(1, int(sessiondata_size_gb or 8)),
            metis_bin_size_mb=max(64, int(metis_bin_size_mb or 256)),
        )
        vhdx_results: List[Dict[str, Any]] = []
        if create_vhdx:
            vhdx_results = _run_direct_vm_vhdx_creation(bundle, timeout=900)
        status = _inspect_direct_vm_assets(bundle)
        manifest = _build_direct_vm_assets_manifest(
            source_root=source_root,
            bundle=bundle,
            version=normalized_version,
            status=status,
            copied=copied,
            vhdx_results=vhdx_results,
        )
        manifest_path = bundle / DIRECT_VM_ASSETS_MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        vm_manifest = _upsert_direct_vm_pack_manifest(bundle, manifest)
        return _json(
            {
                "ok": True,
                "schema": METIS_VM_DIRECT_ASSETS_SCHEMA,
                "dry_run": False,
                "root": str(source_root),
                "bundle_path": str(bundle),
                "version": normalized_version,
                "assets_ready": bool(manifest.get("assets_ready")),
                "runner_ready": bool(manifest.get("runner_ready")),
                "copied": copied,
                "vhdx_results": vhdx_results,
                "written": [
                    *scaffold_written,
                    {"relative_path": DIRECT_VM_ASSETS_MANIFEST_NAME, "path": str(manifest_path), "size_bytes": manifest_path.stat().st_size},
                    *scripts,
                ],
                "manifest": manifest,
                "vm_manifest": vm_manifest,
                "next_steps": [
                    "Supply Metis-owned rootfs.vhdx, vmlinuz, and initrd before HCS direct runner can start.",
                    "Run create-direct-vm-assets.ps1 on a Hyper-V capable Windows host to create sessiondata.vhdx and metis-bin.vhdx.",
                    "Use hcs-runner-plan.json and host/hcs-runner.ps1 as the implementation contract for the later direct runner.",
                ],
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="METIS_VM_DIRECT_ASSETS_PREPARE_FAILED")


def metis_vm_direct_runner_prepare(
    root: str = ".",
    bundle_path: str = "",
    version: str = "",
    transport: str = "jsonl-stdio",
    force: bool = False,
    dry_run: bool = False,
) -> str:
    """Prepare the direct VM host runner, guest daemon, artifact sync, and lifecycle contract."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        bundle = _resolve_vm_bundle_dir(source_root, bundle_path)
        normalized_version = _normalize_runtime_bundle_version(version)
        selected_transport = _normalize_direct_runner_transport(transport)
        plan = _build_direct_vm_runner_prepare_plan(
            source_root=source_root,
            bundle=bundle,
            version=normalized_version,
            transport=selected_transport,
        )
        if dry_run:
            return _json(
                {
                    "ok": True,
                    "schema": METIS_VM_DIRECT_RUNNER_SCHEMA,
                    "dry_run": True,
                    "root": str(source_root),
                    "bundle_path": str(bundle),
                    "version": normalized_version,
                    "transport": selected_transport,
                    "plan": plan,
                    "next_steps": [
                        "Set dry_run=false to write host runner, guest daemon, lifecycle, and artifact sync files.",
                        "Use metis_vm_direct_runner_smoke after prepare to validate JSONL host/guest protocol locally.",
                    ],
                }
            )
        bundle.mkdir(parents=True, exist_ok=True)
        scaffold_written = _ensure_runtime_bundle_scaffold(bundle, force=bool(force))
        written = _write_direct_vm_runner_files(
            bundle,
            version=normalized_version,
            transport=selected_transport,
        )
        status = _inspect_direct_vm_runner(bundle)
        manifest = _build_direct_vm_runner_manifest(
            source_root=source_root,
            bundle=bundle,
            version=normalized_version,
            transport=selected_transport,
            status=status,
        )
        manifest_path = bundle / DIRECT_VM_RUNNER_MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        vm_manifest = _upsert_direct_vm_runner_pack_manifest(bundle, manifest)
        return _json(
            {
                "ok": True,
                "schema": METIS_VM_DIRECT_RUNNER_SCHEMA,
                "dry_run": False,
                "root": str(source_root),
                "bundle_path": str(bundle),
                "version": normalized_version,
                "transport": selected_transport,
                "runner_ready": bool(manifest.get("runner_ready")),
                "hcs_ready": bool((manifest.get("hcs") or {}).get("ready")) if isinstance(manifest.get("hcs"), dict) else False,
                "stdio_smoke_ready": bool((manifest.get("transport") or {}).get("stdio_smoke_ready")) if isinstance(manifest.get("transport"), dict) else False,
                "written": [
                    *scaffold_written,
                    *written,
                    {
                        "relative_path": DIRECT_VM_RUNNER_MANIFEST_NAME,
                        "path": str(manifest_path),
                        "size_bytes": manifest_path.stat().st_size,
                    },
                ],
                "manifest": manifest,
                "vm_manifest": vm_manifest,
                "next_steps": [
                    "Run metis_vm_direct_runner_smoke to verify the JSONL guest protocol and artifact sync on the host.",
                    "HCS direct boot remains disabled until ComputeSystem start and host/guest transport are implemented.",
                    "WSL import remains the first production runnable backend.",
                ],
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="METIS_VM_DIRECT_RUNNER_PREPARE_FAILED")


def metis_vm_direct_runner_smoke(
    root: str = ".",
    bundle_path: str = "",
    command: str = "",
    timeout: int = 30,
    prepare_if_missing: bool = True,
) -> str:
    """Smoke-test the direct VM JSONL guest protocol through stdio without starting HCS."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        bundle = _resolve_vm_bundle_dir(source_root, bundle_path)
        daemon = bundle / "guest" / "metisd.py"
        if (prepare_if_missing and not daemon.is_file()) or not (bundle / DIRECT_VM_RUNNER_MANIFEST_NAME).is_file():
            prepared = json.loads(
                metis_vm_direct_runner_prepare(
                    root=str(source_root),
                    bundle_path=str(bundle),
                    dry_run=False,
                )
            )
            if not prepared.get("ok"):
                return _json(prepared)
        if not daemon.is_file():
            return _json_error(
                f"guest daemon not found: {daemon}",
                code="METIS_VM_GUEST_DAEMON_MISSING",
            )
        smoke_id = f"vm_smoke_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        smoke_root = source_root / ".metis" / "direct-vm-smoke" / smoke_id
        workspace = smoke_root / "workspace"
        artifacts = smoke_root / "artifacts"
        diagnostics = smoke_root / "diagnostics"
        workspace.mkdir(parents=True, exist_ok=True)
        artifacts.mkdir(parents=True, exist_ok=True)
        diagnostics.mkdir(parents=True, exist_ok=True)
        python_exe = configured_python_executable() or shutil.which("python") or shutil.which("py") or "python"
        command_text = str(command or "").strip()
        if not command_text:
            task = workspace / "smoke_task.py"
            task.write_text(
                "import os\n"
                "from pathlib import Path\n"
                "out = Path(os.environ['METIS_RUNTIME_ARTIFACTS_DIR'])\n"
                "out.mkdir(parents=True, exist_ok=True)\n"
                "(out / 'vm-smoke.txt').write_text('metis direct vm smoke ok\\n', encoding='utf-8')\n"
                "print('metis-vm-smoke-ok')\n",
                encoding="utf-8",
                newline="\n",
            )
            command_text = f'"{python_exe}" "{task}"'
        messages = [
            {
                "id": "mount",
                "method": "session.mount",
                "params": {
                    "workspace": str(workspace),
                    "artifacts": str(artifacts),
                    "diagnostics": str(diagnostics),
                },
            },
            {"id": "hello", "method": "runtime.hello", "params": {"protocol": "metis.vm.guest.v1"}},
            {
                "id": "run",
                "method": "process.run",
                "params": {
                    "command": command_text,
                    "cwd": str(workspace),
                    "timeout_ms": max(1, int(timeout or 30)) * 1000,
                },
            },
            {"id": "list", "method": "artifact.list", "params": {}},
            {"id": "collect", "method": "artifact.collect", "params": {"patterns": ["*.txt", "*.json", "*.log", "*.md"]}},
            {"id": "diagnostics", "method": "diagnostics.export", "params": {}},
            {"id": "shutdown", "method": "runtime.shutdown", "params": {}},
        ]
        proc = subprocess.run(
            [python_exe, str(daemon)],
            input="\n".join(json.dumps(item, ensure_ascii=False) for item in messages) + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(5, int(timeout or 30) + 10),
            check=False,
        )
        responses = _parse_jsonl_responses(proc.stdout)
        by_id = {str(item.get("id") or ""): item for item in responses}
        run_result = by_id.get("run", {}).get("result") if isinstance(by_id.get("run"), dict) else {}
        list_result = by_id.get("list", {}).get("result") if isinstance(by_id.get("list"), dict) else {}
        diagnostics_result = by_id.get("diagnostics", {}).get("result") if isinstance(by_id.get("diagnostics"), dict) else {}
        artifacts_list = list_result.get("artifacts") if isinstance(list_result, dict) else []
        diagnostics_zip = str(diagnostics_result.get("diagnostics_zip") or "") if isinstance(diagnostics_result, dict) else ""
        ok = (
            proc.returncode == 0
            and bool((by_id.get("hello", {}).get("result") if isinstance(by_id.get("hello"), dict) else {}).get("ok"))
            and bool(run_result.get("ok") if isinstance(run_result, dict) else False)
            and isinstance(artifacts_list, list)
            and len(artifacts_list) > 0
            and bool(diagnostics_zip)
            and Path(diagnostics_zip).is_file()
        )
        return _json(
            {
                "ok": ok,
                "schema": METIS_VM_DIRECT_RUNNER_SCHEMA,
                "root": str(source_root),
                "bundle_path": str(bundle),
                "smoke_id": smoke_id,
                "smoke_root": str(smoke_root),
                "workspace": str(workspace),
                "artifacts_dir": str(artifacts),
                "diagnostics_dir": str(diagnostics),
                "returncode": proc.returncode,
                "stdout": _truncate(proc.stdout),
                "stderr": _truncate(proc.stderr),
                "responses": responses,
                "run": run_result if isinstance(run_result, dict) else {},
                "artifacts": artifacts_list if isinstance(artifacts_list, list) else [],
                "diagnostics_zip": diagnostics_zip,
                "lifecycle_log": str(diagnostics / "lifecycle.jsonl"),
                "notes": [
                    "This smoke validates the guest JSONL protocol through stdio on the host.",
                    "It does not start an HCS/Hyper-V VM yet.",
                    "The same guest daemon/protocol files are intended to be embedded into the future Metis VM image.",
                ],
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="METIS_VM_DIRECT_RUNNER_SMOKE_FAILED")


def metis_vm_hcs_starter_prepare(
    root: str = ".",
    bundle_path: str = "",
    version: str = "",
    memory_mb: int = 2048,
    processor_count: int = 2,
    kernel_cmdline: str = "",
    force: bool = False,
    dry_run: bool = False,
) -> str:
    """Prepare an experimental HCS ComputeSystem starter for the Metis direct VM pack."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        bundle = _resolve_vm_bundle_dir(source_root, bundle_path)
        normalized_version = _normalize_runtime_bundle_version(version)
        memory = max(512, int(memory_mb or 2048))
        processors = max(1, int(processor_count or 2))
        cmdline = _normalize_hcs_kernel_cmdline(kernel_cmdline)
        plan = _build_hcs_starter_plan(
            source_root=source_root,
            bundle=bundle,
            version=normalized_version,
            memory_mb=memory,
            processor_count=processors,
            kernel_cmdline=cmdline,
        )
        if dry_run:
            return _json(
                {
                    "ok": True,
                    "schema": METIS_VM_HCS_STARTER_SCHEMA,
                    "dry_run": True,
                    "root": str(source_root),
                    "bundle_path": str(bundle),
                    "version": normalized_version,
                    "plan": plan,
                    "next_steps": [
                        "Set dry_run=false to write HCS ComputeSystem document, C# API bridge, and PowerShell starter.",
                        "Use metis_vm_hcs_starter_start(dry_run=true) to inspect the exact start command.",
                    ],
                }
            )
        bundle.mkdir(parents=True, exist_ok=True)
        scaffold_written = _ensure_runtime_bundle_scaffold(bundle, force=bool(force))
        runner_prepare = json.loads(
            metis_vm_direct_runner_prepare(
                root=str(source_root),
                bundle_path=str(bundle),
                version=normalized_version,
                force=bool(force),
                dry_run=False,
            )
        )
        if not runner_prepare.get("ok"):
            return _json(runner_prepare)
        written = _write_hcs_starter_files(
            bundle,
            version=normalized_version,
            memory_mb=memory,
            processor_count=processors,
            kernel_cmdline=cmdline,
        )
        status = _inspect_hcs_starter(bundle)
        manifest = _build_hcs_starter_manifest(
            source_root=source_root,
            bundle=bundle,
            version=normalized_version,
            memory_mb=memory,
            processor_count=processors,
            kernel_cmdline=cmdline,
            status=status,
        )
        manifest_path = bundle / HCS_STARTER_MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        vm_manifest = _upsert_hcs_starter_pack_manifest(bundle, manifest)
        return _json(
            {
                "ok": True,
                "schema": METIS_VM_HCS_STARTER_SCHEMA,
                "dry_run": False,
                "root": str(source_root),
                "bundle_path": str(bundle),
                "version": normalized_version,
                "hcs_ready": bool(manifest.get("hcs_ready")),
                "assets_ready": bool(manifest.get("assets_ready")),
                "written": [
                    *scaffold_written,
                    *list(runner_prepare.get("written") or []),
                    *written,
                    {
                        "relative_path": HCS_STARTER_MANIFEST_NAME,
                        "path": str(manifest_path),
                        "size_bytes": manifest_path.stat().st_size,
                    },
                ],
                "manifest": manifest,
                "vm_manifest": vm_manifest,
                "next_steps": [
                    "Run metis_vm_hcs_starter_start(dry_run=true) to inspect the HCS start attempt.",
                    "Run with enable_experimental_hcs=true and dry_run=false only on a Hyper-V capable host with Metis-owned assets.",
                    "If HCS rejects the generated document, inspect diagnostics/hcs-starter-lifecycle.jsonl and the returned HCS result document.",
                ],
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="METIS_VM_HCS_STARTER_PREPARE_FAILED")


def metis_vm_hcs_starter_start(
    root: str = ".",
    bundle_path: str = "",
    compute_system_id: str = "",
    compute_document_path: str = "",
    timeout: int = 120,
    hold_seconds: int = 3,
    keep_running: bool = False,
    enable_experimental_hcs: bool = False,
    prepare_if_missing: bool = True,
    dry_run: bool = True,
) -> str:
    """Plan or execute an experimental HCS ComputeSystem start attempt."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        bundle = _resolve_vm_bundle_dir(source_root, bundle_path)
        if prepare_if_missing and not (bundle / HCS_STARTER_MANIFEST_NAME).is_file():
            prepared = json.loads(
                metis_vm_hcs_starter_prepare(
                    root=str(source_root),
                    bundle_path=str(bundle),
                    dry_run=False,
                )
            )
            if not prepared.get("ok"):
                return _json(prepared)
        status = _inspect_hcs_starter(bundle)
        system_id = _safe_compute_system_id(compute_system_id)
        document = _resolve_hcs_compute_document(bundle, compute_document_path)
        diagnostics = source_root / ".metis" / "diagnostics" / system_id
        lifecycle_log = diagnostics / "hcs-starter-lifecycle.jsonl"
        starter = bundle / "host" / "hcs-starter.ps1"
        powershell = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell"
        command = [
            powershell,
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(starter),
            "-Bundle",
            str(bundle),
            "-ComputeSystemId",
            system_id,
            "-ComputeDocument",
            str(document),
            "-LifecycleLog",
            str(lifecycle_log),
            "-TimeoutSeconds",
            str(max(1, int(timeout or 120))),
            "-HoldSeconds",
            str(max(0, int(hold_seconds or 0))),
        ]
        if keep_running:
            command.append("-KeepRunning")
        if enable_experimental_hcs:
            command.append("-EnableExperimentalHcsStart")
        plan = {
            "schema": METIS_VM_HCS_STARTER_SCHEMA,
            "root": str(source_root),
            "bundle_path": str(bundle),
            "compute_system_id": system_id,
            "compute_document": str(document),
            "diagnostics_dir": str(diagnostics),
            "lifecycle_log": str(lifecycle_log),
            "command": command,
            "status": status,
            "dry_run": bool(dry_run),
            "enable_experimental_hcs": bool(enable_experimental_hcs),
        }
        if dry_run:
            return _json(
                {
                    "ok": True,
                    "schema": METIS_VM_HCS_STARTER_SCHEMA,
                    "dry_run": True,
                    "plan": plan,
                    "warning": "This is a real HCS start path. Set dry_run=false and enable_experimental_hcs=true only after reviewing the generated ComputeSystem document.",
                }
            )
        if not enable_experimental_hcs:
            return _json_error(
                "HCS start is experimental and requires enable_experimental_hcs=true",
                code="METIS_HCS_EXPERIMENTAL_FLAG_REQUIRED",
            )
        if not status.get("starter_ready"):
            return _json_error(
                f"HCS starter files are missing: {status.get('missing_files')}",
                code="METIS_HCS_STARTER_NOT_READY",
            )
        if not status.get("assets_ready"):
            return _json_error(
                f"direct VM assets are missing: {status.get('missing_assets')}",
                code="METIS_HCS_ASSETS_NOT_READY",
            )
        diagnostics.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            command,
            cwd=str(bundle),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(5, int(timeout or 120) + max(0, int(hold_seconds or 0)) + 30),
            check=False,
        )
        lifecycle_text = lifecycle_log.read_text(encoding="utf-8", errors="replace") if lifecycle_log.is_file() else ""
        return _json(
            {
                "ok": proc.returncode == 0,
                "schema": METIS_VM_HCS_STARTER_SCHEMA,
                "dry_run": False,
                "compute_system_id": system_id,
                "bundle_path": str(bundle),
                "compute_document": str(document),
                "diagnostics_dir": str(diagnostics),
                "lifecycle_log": str(lifecycle_log),
                "returncode": proc.returncode,
                "stdout": _truncate(proc.stdout or ""),
                "stderr": _truncate(proc.stderr or ""),
                "lifecycle": _truncate(lifecycle_text),
                "status": _inspect_hcs_starter(bundle),
                "notes": [
                    "This path calls the Windows HCS API through the generated C# bridge.",
                    "Success means HCS accepted create/start and, unless keep_running=true, terminate.",
                    "Failure usually means the ComputeSystem document or kernel/rootfs command line needs host-specific adjustment.",
                ],
            }
        )
    except subprocess.TimeoutExpired as exc:
        return _json_error(
            f"HCS starter timed out: {exc}",
            code="METIS_HCS_STARTER_TIMEOUT",
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="METIS_VM_HCS_STARTER_START_FAILED")


def metis_vm_rootfs_boot_verifier_prepare(
    root: str = ".",
    bundle_path: str = "",
    version: str = "",
    root_device_candidates: Optional[List[str]] = None,
    init_candidates: Optional[List[str]] = None,
    force: bool = False,
    dry_run: bool = False,
) -> str:
    """Prepare rootfs boot verification plans for Metis direct VM."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        bundle = _resolve_vm_bundle_dir(source_root, bundle_path)
        normalized_version = _normalize_runtime_bundle_version(version)
        matrix = _build_rootfs_boot_cmdline_matrix(
            root_device_candidates=root_device_candidates or [],
            init_candidates=init_candidates or [],
        )
        plan = _build_rootfs_boot_verifier_plan(
            source_root=source_root,
            bundle=bundle,
            version=normalized_version,
            matrix=matrix,
        )
        if dry_run:
            return _json(
                {
                    "ok": True,
                    "schema": METIS_VM_ROOTFS_BOOT_VERIFIER_SCHEMA,
                    "dry_run": True,
                    "root": str(source_root),
                    "bundle_path": str(bundle),
                    "version": normalized_version,
                    "plan": plan,
                    "next_steps": [
                        "Set dry_run=false to write rootfs boot verifier scripts and manifest.",
                        "Run metis_vm_rootfs_boot_verify(dry_run=true) to inspect every HCS boot candidate.",
                    ],
                }
            )
        bundle.mkdir(parents=True, exist_ok=True)
        starter_prepare = json.loads(
            metis_vm_hcs_starter_prepare(
                root=str(source_root),
                bundle_path=str(bundle),
                version=normalized_version,
                force=bool(force),
                dry_run=False,
            )
        )
        if not starter_prepare.get("ok"):
            return _json(starter_prepare)
        written = _write_rootfs_boot_verifier_files(bundle, version=normalized_version, matrix=matrix)
        status = _inspect_rootfs_boot_verifier(bundle)
        manifest = _build_rootfs_boot_verifier_manifest(
            source_root=source_root,
            bundle=bundle,
            version=normalized_version,
            matrix=matrix,
            status=status,
        )
        manifest_path = bundle / ROOTFS_BOOT_VERIFIER_MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        vm_manifest = _upsert_rootfs_boot_verifier_pack_manifest(bundle, manifest)
        return _json(
            {
                "ok": True,
                "schema": METIS_VM_ROOTFS_BOOT_VERIFIER_SCHEMA,
                "dry_run": False,
                "root": str(source_root),
                "bundle_path": str(bundle),
                "version": normalized_version,
                "verifier_ready": bool(manifest.get("verifier_ready")),
                "assets_ready": bool(manifest.get("assets_ready")),
                "candidate_count": len(matrix),
                "written": [
                    *list(starter_prepare.get("written") or []),
                    *written,
                    {
                        "relative_path": ROOTFS_BOOT_VERIFIER_MANIFEST_NAME,
                        "path": str(manifest_path),
                        "size_bytes": manifest_path.stat().st_size,
                    },
                ],
                "manifest": manifest,
                "vm_manifest": vm_manifest,
                "next_steps": [
                    "Run host/rootfs-inspect.ps1 to inspect VHDX mountability and guest daemon hints when permitted.",
                    "Run metis_vm_rootfs_boot_verify(dry_run=true) to inspect HCS boot attempts.",
                    "Only use dry_run=false with enable_experimental_hcs=true after reviewing generated compute documents.",
                ],
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="METIS_VM_ROOTFS_BOOT_VERIFIER_PREPARE_FAILED")


def metis_vm_rootfs_boot_verify(
    root: str = ".",
    bundle_path: str = "",
    candidate_ids: Optional[List[str]] = None,
    timeout: int = 120,
    hold_seconds: int = 3,
    stop_after_first_success: bool = True,
    enable_experimental_hcs: bool = False,
    prepare_if_missing: bool = True,
    dry_run: bool = True,
) -> str:
    """Verify or plan rootfs boot attempts across kernel command-line candidates."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        bundle = _resolve_vm_bundle_dir(source_root, bundle_path)
        if prepare_if_missing and not (bundle / ROOTFS_BOOT_VERIFIER_MANIFEST_NAME).is_file():
            prepared = json.loads(
                metis_vm_rootfs_boot_verifier_prepare(
                    root=str(source_root),
                    bundle_path=str(bundle),
                    dry_run=False,
                )
            )
            if not prepared.get("ok"):
                return _json(prepared)
        manifest_path = bundle / ROOTFS_BOOT_VERIFIER_MANIFEST_NAME
        verifier_manifest = _read_json_object(manifest_path) if manifest_path.is_file() else {}
        raw_matrix = verifier_manifest.get("cmdline_matrix") if isinstance(verifier_manifest.get("cmdline_matrix"), list) else []
        matrix = [item for item in raw_matrix if isinstance(item, dict)]
        selected_ids = {str(item).strip() for item in (candidate_ids or []) if str(item).strip()}
        if selected_ids:
            matrix = [item for item in matrix if str(item.get("id") or "") in selected_ids]
        if not matrix:
            return _json_error(
                "No rootfs boot candidates found. Run metis_vm_rootfs_boot_verifier_prepare first.",
                code="METIS_ROOTFS_BOOT_CANDIDATES_MISSING",
            )
        status = _inspect_rootfs_boot_verifier(bundle)
        attempts_plan = _rootfs_boot_attempt_plan(
            source_root=source_root,
            bundle=bundle,
            matrix=matrix,
            timeout=max(1, int(timeout or 120)),
            hold_seconds=max(0, int(hold_seconds or 0)),
            enable_experimental_hcs=bool(enable_experimental_hcs),
        )
        if dry_run:
            return _json(
                {
                    "ok": True,
                    "schema": METIS_VM_ROOTFS_BOOT_VERIFIER_SCHEMA,
                    "dry_run": True,
                    "root": str(source_root),
                    "bundle_path": str(bundle),
                    "status": status,
                    "attempts": attempts_plan,
                    "warning": "dry_run=true only builds per-candidate HCS start plans; it does not start a VM.",
                }
            )
        if not enable_experimental_hcs:
            return _json_error(
                "Rootfs boot verification requires enable_experimental_hcs=true when dry_run=false.",
                code="METIS_ROOTFS_BOOT_EXPERIMENTAL_FLAG_REQUIRED",
            )
        if not status.get("assets_ready"):
            return _json_error(
                f"direct VM assets are missing: {status.get('missing_assets')}",
                code="METIS_ROOTFS_BOOT_ASSETS_NOT_READY",
            )
        results: List[Dict[str, Any]] = []
        for attempt in attempts_plan:
            compute_document = _write_rootfs_boot_candidate_compute_document(
                bundle,
                candidate_id=str(attempt.get("candidate_id") or ""),
                kernel_cmdline=str(attempt.get("kernel_cmdline") or ""),
            )
            result = json.loads(
                metis_vm_hcs_starter_start(
                    root=str(source_root),
                    bundle_path=str(bundle),
                    compute_system_id=str(attempt.get("compute_system_id") or ""),
                    compute_document_path=str(compute_document),
                    timeout=max(1, int(timeout or 120)),
                    hold_seconds=max(0, int(hold_seconds or 0)),
                    keep_running=False,
                    enable_experimental_hcs=True,
                    prepare_if_missing=True,
                    dry_run=False,
                )
            )
            result["candidate_id"] = attempt.get("candidate_id")
            result["kernel_cmdline"] = attempt.get("kernel_cmdline")
            result["handshake_verified"] = False
            result["handshake_reason"] = "Run metis_vm_guest_handshake_verify after a kept-running HCS guest exists; this pass only verifies create/start/terminate evidence."
            results.append(result)
            if bool(result.get("ok")) and stop_after_first_success:
                break
        summary = _summarize_rootfs_boot_results(results)
        _write_rootfs_boot_results(bundle, results=results, summary=summary)
        return _json(
            {
                "ok": bool(summary.get("hcs_start_succeeded")),
                "schema": METIS_VM_ROOTFS_BOOT_VERIFIER_SCHEMA,
                "dry_run": False,
                "root": str(source_root),
                "bundle_path": str(bundle),
                "summary": summary,
                "results": results,
                "runner_ready": False,
                "runner_ready_reason": "runner_ready remains false until guest handshake to metisd is verified.",
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="METIS_VM_ROOTFS_BOOT_VERIFY_FAILED")


def metis_vm_guest_handshake_prepare(
    root: str = ".",
    bundle_path: str = "",
    version: str = "",
    transport: str = "hcs-vsock-jsonl",
    timeout: int = 30,
    force: bool = False,
    dry_run: bool = False,
) -> str:
    """Prepare the guest handshake verifier that gates direct-VM runner readiness."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        bundle = _resolve_vm_bundle_dir(source_root, bundle_path)
        normalized_version = _normalize_runtime_bundle_version(version)
        selected_transport = _normalize_guest_handshake_transport(transport)
        timeout_seconds = max(1, int(timeout or 30))
        plan = _build_guest_handshake_plan(
            source_root=source_root,
            bundle=bundle,
            version=normalized_version,
            transport=selected_transport,
            timeout=timeout_seconds,
        )
        if dry_run:
            return _json(
                {
                    "ok": True,
                    "schema": METIS_VM_GUEST_HANDSHAKE_SCHEMA,
                    "dry_run": True,
                    "root": str(source_root),
                    "bundle_path": str(bundle),
                    "version": normalized_version,
                    "transport": selected_transport,
                    "plan": plan,
                    "next_steps": [
                        "Set dry_run=false to write the guest handshake verifier plan and manifest.",
                        "Use transport=jsonl-stdio for host-only protocol smoke; it cannot promote VM runner_ready.",
                        "Use transport=hcs-vsock-jsonl only after the HCS host/guest transport bridge exists.",
                    ],
                }
            )
        bundle.mkdir(parents=True, exist_ok=True)
        scaffold_written = _ensure_runtime_bundle_scaffold(bundle, force=bool(force))
        hcs_prepare = json.loads(
            metis_vm_hcs_starter_prepare(
                root=str(source_root),
                bundle_path=str(bundle),
                version=normalized_version,
                force=bool(force),
                dry_run=False,
            )
        )
        if not hcs_prepare.get("ok"):
            return _json(hcs_prepare)
        written = _write_guest_handshake_files(
            bundle,
            version=normalized_version,
            transport=selected_transport,
            timeout=timeout_seconds,
        )
        status = _inspect_guest_handshake(bundle)
        manifest = _build_guest_handshake_manifest(
            source_root=source_root,
            bundle=bundle,
            version=normalized_version,
            transport=selected_transport,
            timeout=timeout_seconds,
            status=status,
        )
        manifest_path = bundle / GUEST_HANDSHAKE_MANIFEST_NAME
        manifest_path.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
            newline="\n",
        )
        vm_manifest = _upsert_guest_handshake_pack_manifest(bundle, manifest)
        return _json(
            {
                "ok": True,
                "schema": METIS_VM_GUEST_HANDSHAKE_SCHEMA,
                "dry_run": False,
                "root": str(source_root),
                "bundle_path": str(bundle),
                "version": normalized_version,
                "transport": selected_transport,
                "verifier_ready": bool(manifest.get("verifier_ready")),
                "runner_ready": bool(manifest.get("runner_ready")),
                "written": [
                    *scaffold_written,
                    *list(hcs_prepare.get("written") or []),
                    *written,
                    {
                        "relative_path": GUEST_HANDSHAKE_MANIFEST_NAME,
                        "path": str(manifest_path),
                        "size_bytes": manifest_path.stat().st_size,
                    },
                ],
                "manifest": manifest,
                "vm_manifest": vm_manifest,
                "next_steps": [
                    "Run metis_vm_guest_handshake_verify(dry_run=true) to inspect the handshake attempt plan.",
                    "Run transport=jsonl-stdio, dry_run=false to validate the guest daemon protocol locally.",
                    "Do not treat the VM backend as runnable until transport=hcs-vsock-jsonl returns runtime.hello from a booted guest.",
                ],
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="METIS_VM_GUEST_HANDSHAKE_PREPARE_FAILED")


def metis_vm_guest_handshake_verify(
    root: str = ".",
    bundle_path: str = "",
    transport: str = "hcs-vsock-jsonl",
    compute_system_id: str = "",
    timeout: int = 30,
    enable_experimental_hcs: bool = False,
    prepare_if_missing: bool = True,
    dry_run: bool = True,
) -> str:
    """Verify that a booted guest metisd answers runtime.hello before runner_ready is promoted."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        bundle = _resolve_vm_bundle_dir(source_root, bundle_path)
        selected_transport = _normalize_guest_handshake_transport(transport)
        timeout_seconds = max(1, int(timeout or 30))
        if prepare_if_missing and not (bundle / GUEST_HANDSHAKE_MANIFEST_NAME).is_file():
            prepared = json.loads(
                metis_vm_guest_handshake_prepare(
                    root=str(source_root),
                    bundle_path=str(bundle),
                    transport=selected_transport,
                    timeout=timeout_seconds,
                    dry_run=False,
                )
            )
            if not prepared.get("ok"):
                return _json(prepared)
        status = _inspect_guest_handshake(bundle)
        plan = _guest_handshake_attempt_plan(
            source_root=source_root,
            bundle=bundle,
            transport=selected_transport,
            compute_system_id=compute_system_id,
            timeout=timeout_seconds,
            enable_experimental_hcs=bool(enable_experimental_hcs),
        )
        if dry_run:
            return _json(
                {
                    "ok": True,
                    "schema": METIS_VM_GUEST_HANDSHAKE_SCHEMA,
                    "dry_run": True,
                    "root": str(source_root),
                    "bundle_path": str(bundle),
                    "transport": selected_transport,
                    "status": status,
                    "plan": plan,
                    "runner_ready": bool(status.get("runner_ready")),
                    "warning": "dry_run=true only plans the runtime.hello verifier; it does not start or connect to a VM.",
                }
            )
        if selected_transport == "jsonl-stdio":
            receipt = _verify_guest_handshake_stdio(
                source_root=source_root,
                bundle=bundle,
                timeout=timeout_seconds,
            )
            manifest = _record_guest_handshake_receipt(
                bundle,
                receipt=receipt,
                runner_ready=False,
                runner_ready_reason="jsonl-stdio verified the guest protocol only; HCS/vsock runtime.hello is still required for VM backend readiness.",
            )
            return _json(
                {
                    "ok": bool(receipt.get("handshake_verified")),
                    "schema": METIS_VM_GUEST_HANDSHAKE_SCHEMA,
                    "dry_run": False,
                    "root": str(source_root),
                    "bundle_path": str(bundle),
                    "transport": selected_transport,
                    "handshake_verified": bool(receipt.get("handshake_verified")),
                    "stdio_handshake_verified": bool(receipt.get("handshake_verified")),
                    "hcs_handshake_verified": False,
                    "runner_ready": False,
                    "runner_ready_reason": "stdio smoke does not prove a booted HCS guest; runner_ready remains false.",
                    "receipt": receipt,
                    "manifest": manifest,
                }
            )
        if not enable_experimental_hcs:
            return _json_error(
                "HCS guest handshake requires enable_experimental_hcs=true when dry_run=false.",
                code="METIS_GUEST_HANDSHAKE_EXPERIMENTAL_FLAG_REQUIRED",
            )
        return _json_error(
            "HCS/vsock JSONL transport is not implemented yet; cannot receive runtime.hello from a booted guest.",
            code="METIS_GUEST_HANDSHAKE_TRANSPORT_UNAVAILABLE",
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="METIS_VM_GUEST_HANDSHAKE_VERIFY_FAILED")


def metis_wsl_runtime_status(
    root: str = ".",
    distro_name: str = "",
    install_dir: str = "",
    rootfs_path: str = "",
) -> str:
    """Inspect the Metis-managed WSL import runtime without mutating the system."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
    except Exception:
        source_root = Path(str(root or ".")).expanduser().resolve(strict=False)
    status = _detect_metis_wsl_runtime(
        source_root=source_root,
        distro_name=distro_name,
        install_dir=install_dir,
        rootfs_path=rootfs_path,
    )
    return _json(
        {
            "ok": True,
            "schema": METIS_WSL_STATUS_SCHEMA,
            "root": str(source_root),
            **status,
        }
    )


def metis_wsl_runtime_import(
    root: str = ".",
    rootfs_path: str = "",
    distro_name: str = "",
    install_dir: str = "",
    version: int = 2,
    dry_run: bool = True,
    allow_existing: bool = False,
) -> str:
    """Import a Metis-owned rootfs tar/VHDX into a managed WSL distro."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        status = _detect_metis_wsl_runtime(
            source_root=source_root,
            distro_name=distro_name,
            install_dir=install_dir,
            rootfs_path=rootfs_path,
        )
        wsl = status.get("wsl") if isinstance(status.get("wsl"), dict) else {}
        executable = str(wsl.get("executable") or "")
        if not executable:
            return _json_error(
                "wsl.exe is required for Metis WSL runtime import",
                code="METIS_WSL_NOT_FOUND",
            )
        if status.get("installed") and not allow_existing:
            return _json(
                {
                    "ok": True,
                    "schema": METIS_WSL_IMPORT_SCHEMA,
                    "already_installed": True,
                    "distro_name": status.get("distro_name"),
                    "install_dir": status.get("install_dir"),
                    "status": status,
                    "next_steps": [
                        "Use metis_runtime_create with backend=vm or backend=metis_wsl.",
                        "Set allow_existing=true only when you intentionally want import planning to continue.",
                    ],
                }
            )
        rootfs = status.get("selected_rootfs") if isinstance(status.get("selected_rootfs"), dict) else {}
        rootfs_asset = str(rootfs.get("path") or "")
        if not rootfs_asset or not Path(rootfs_asset).is_file():
            return _json_error(
                "No Metis rootfs import asset found. Expected rootfs.tar, rootfs.tar.gz, rootfs.tar.zst, or rootfs.vhdx in the Metis VM pack.",
                code="METIS_ROOTFS_ASSET_MISSING",
            )
        verification = status.get("rootfs_verification") if isinstance(status.get("rootfs_verification"), dict) else {}
        import_mode = str(rootfs.get("import_mode") or "tar")
        distro = str(status.get("distro_name") or DEFAULT_METIS_WSL_DISTRO)
        target = Path(str(status.get("install_dir") or "")).resolve(strict=False)
        args = _build_wsl_import_args(
            executable=executable,
            distro=distro,
            install_dir=target,
            rootfs_asset=Path(rootfs_asset),
            version=max(1, int(version or 2)),
            import_mode=import_mode,
        )
        if dry_run:
            return _json(
                {
                    "ok": True,
                    "schema": METIS_WSL_IMPORT_SCHEMA,
                    "dry_run": True,
                    "distro_name": distro,
                    "install_dir": str(target),
                    "rootfs_asset": rootfs_asset,
                    "import_mode": import_mode,
                    "command": args,
                    "verification": verification,
                    "verified": bool(verification.get("verified")),
                    "warning": ""
                    if verification.get("verified")
                    else "rootfs asset is not verified; dry_run=false will be blocked until it is registered with SHA256",
                    "status": status,
                    "next_steps": [
                        "Register the rootfs with metis_rootfs_asset_register if verification is false.",
                        "Set dry_run=false to execute the WSL import after confirming the rootfs asset is Metis-owned and verified.",
                        "After import, backend=auto can select metis_wsl when the distro is installed.",
                    ],
                }
            )
        if not verification.get("verified"):
            return _json_error(
                "Metis rootfs import asset is not verified. Register it with metis_rootfs_asset_register before dry_run=false.",
                code="METIS_ROOTFS_ASSET_UNVERIFIED",
            )
        target.mkdir(parents=True, exist_ok=True)
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=900,
            check=False,
        )
        imported_status = _detect_metis_wsl_runtime(
            source_root=source_root,
            distro_name=distro,
            install_dir=str(target),
            rootfs_path=rootfs_asset,
        )
        smoke = _quick_command([executable, "-d", distro, "--", "sh", "-lc", "printf metis-wsl-ok"], timeout=30)
        return _json(
            {
                "ok": proc.returncode == 0 and imported_status.get("installed"),
                "schema": METIS_WSL_IMPORT_SCHEMA,
                "dry_run": False,
                "distro_name": distro,
                "install_dir": str(target),
                "rootfs_asset": rootfs_asset,
                "import_mode": import_mode,
                "command": args,
                "returncode": proc.returncode,
                "stdout": _truncate(proc.stdout or ""),
                "stderr": _truncate(proc.stderr or ""),
                "smoke": smoke,
                "status": imported_status,
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="METIS_WSL_IMPORT_FAILED")


def metis_rootfs_asset_status(
    root: str = ".",
    bundle_path: str = "",
    rootfs_path: str = "",
    expected_sha256: str = "",
    signature_path: str = "",
    public_key_path: str = "",
) -> str:
    """Inspect and optionally verify a Metis rootfs import asset."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        bundle = _resolve_vm_bundle_dir(source_root, bundle_path)
        candidates = _metis_rootfs_asset_candidates(source_root, rootfs_path=rootfs_path)
        selected = _select_rootfs_asset_candidate(candidates, explicit_path=rootfs_path)
        verification = {}
        if selected.get("exists"):
            verification = _verify_rootfs_asset(
                Path(str(selected.get("path"))),
                bundle_dir=bundle,
                expected_sha256=expected_sha256,
                signature_path=signature_path,
                public_key_path=public_key_path,
            )
        return _json(
            {
                "ok": True,
                "schema": ROOTFS_ASSET_STATUS_SCHEMA,
                "root": str(source_root),
                "bundle_path": str(bundle),
                "selected_rootfs": selected,
                "verification": verification,
                "candidates": candidates,
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="ROOTFS_ASSET_STATUS_FAILED")


def metis_rootfs_asset_register(
    rootfs_path: str,
    root: str = ".",
    bundle_path: str = "",
    expected_sha256: str = "",
    signature_path: str = "",
    public_key_path: str = "",
    source_url: str = "",
    copy: bool = True,
    force: bool = False,
) -> str:
    """Register a Metis-owned rootfs asset in the VM pack manifest."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        source = safe_path_for_read(
            str(rootfs_path or ""),
            allow_paths_outside_workspace=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        bundle = _resolve_vm_bundle_dir(source_root, bundle_path)
        bundle.mkdir(parents=True, exist_ok=True)
        target = source
        copied = False
        if copy:
            target = bundle / _canonical_rootfs_asset_name(source)
            if target.exists() and target.resolve(strict=False) != source.resolve(strict=False) and not force:
                return _json_error(
                    f"rootfs asset already exists in bundle: {target}",
                    code="ROOTFS_ASSET_EXISTS",
                )
            if target.resolve(strict=False) != source.resolve(strict=False):
                shutil.copy2(source, target)
                copied = True
        verification = _verify_rootfs_asset(
            target,
            bundle_dir=bundle,
            expected_sha256=expected_sha256,
            signature_path=signature_path,
            public_key_path=public_key_path,
            require_expected=bool(expected_sha256),
        )
        if expected_sha256 and not verification.get("checksum_verified"):
            return _json_error(
                "rootfs SHA256 does not match expected_sha256",
                code="ROOTFS_SHA256_MISMATCH",
            )
        if signature_path or public_key_path:
            signature = verification.get("signature") if isinstance(verification.get("signature"), dict) else {}
            if not signature.get("verified"):
                return _json_error(
                    f"rootfs signature verification failed: {signature.get('reason') or 'unknown error'}",
                    code="ROOTFS_SIGNATURE_INVALID",
                )
        manifest = _upsert_rootfs_asset_manifest(
            bundle,
            target,
            verification=verification,
            source_url=source_url,
            signature_path=signature_path,
            public_key_path=public_key_path,
        )
        status = _detect_metis_wsl_runtime(
            source_root=source_root,
            rootfs_path=str(target),
        )
        return _json(
            {
                "ok": True,
                "schema": ROOTFS_ASSET_REGISTER_SCHEMA,
                "root": str(source_root),
                "bundle_path": str(bundle),
                "source_path": str(source),
                "rootfs_path": str(target),
                "copied": copied,
                "verification": verification,
                "manifest": manifest,
                "status": status,
                "next_steps": [
                    "Run metis_wsl_runtime_import(dry_run=true) to inspect the import command.",
                    "Run metis_wsl_runtime_import(dry_run=false) only after confirming the asset is trusted.",
                ],
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="ROOTFS_ASSET_REGISTER_FAILED")


def metis_rootfs_source_status(
    root: str = ".",
    manifest_url: str = "",
    manifest_path: str = "",
    asset_url: str = "",
    expected_sha256: str = "",
) -> str:
    """Resolve a configured Metis rootfs source without downloading the asset."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        source = _resolve_rootfs_source(
            source_root=source_root,
            manifest_url=manifest_url,
            manifest_path=manifest_path,
            asset_url=asset_url,
            expected_sha256=expected_sha256,
        )
        return _json(
            {
                "ok": True,
                "schema": ROOTFS_SOURCE_STATUS_SCHEMA,
                "root": str(source_root),
                **source,
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="ROOTFS_SOURCE_STATUS_FAILED")


def metis_rootfs_asset_download(
    root: str = ".",
    bundle_path: str = "",
    manifest_url: str = "",
    manifest_path: str = "",
    asset_url: str = "",
    expected_sha256: str = "",
    signature_url: str = "",
    public_key_path: str = "",
    output_path: str = "",
    dry_run: bool = True,
    force: bool = False,
    register: bool = True,
) -> str:
    """Download or copy a configured Metis rootfs asset, then optionally register it."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        bundle = _resolve_vm_bundle_dir(source_root, bundle_path)
        source = _resolve_rootfs_source(
            source_root=source_root,
            manifest_url=manifest_url,
            manifest_path=manifest_path,
            asset_url=asset_url,
            expected_sha256=expected_sha256,
            signature_url=signature_url,
        )
        selected = source.get("selected_asset") if isinstance(source.get("selected_asset"), dict) else {}
        url = str(selected.get("url") or "")
        if not url:
            return _json_error(
                "No rootfs asset source configured. Provide manifest_path, manifest_url, or asset_url.",
                code="ROOTFS_SOURCE_MISSING",
            )
        expected = _normalize_sha256(str(selected.get("sha256") or expected_sha256 or ""))
        if not expected:
            return _json_error(
                "Rootfs download requires expected_sha256 from manifest or explicit argument.",
                code="ROOTFS_SOURCE_SHA256_REQUIRED",
            )
        target = _resolve_rootfs_download_target(
            source_root=source_root,
            bundle_dir=bundle,
            output_path=output_path,
            asset=selected,
        )
        plan = {
            "url": url,
            "target_path": str(target),
            "expected_sha256": expected,
            "signature_url": str(selected.get("signature_url") or ""),
            "register": bool(register),
        }
        if dry_run:
            return _json(
                {
                    "ok": True,
                    "schema": ROOTFS_DOWNLOAD_SCHEMA,
                    "dry_run": True,
                    "root": str(source_root),
                    "bundle_path": str(bundle),
                    "source": source,
                    "plan": plan,
                    "next_steps": [
                        "Set dry_run=false to download/copy the rootfs asset.",
                        "The download will be blocked unless SHA256 is available.",
                        "After download, register=true will write the asset into metis-vm-pack.json.",
                    ],
                }
            )
        if target.exists() and not force:
            existing = _verify_rootfs_asset(target, bundle_dir=bundle, expected_sha256=expected)
            if existing.get("checksum_verified"):
                registration = {}
                if register:
                    registration = json.loads(
                        metis_rootfs_asset_register(
                            rootfs_path=str(target),
                            root=str(source_root),
                            bundle_path=str(bundle),
                            expected_sha256=expected,
                            signature_path="",
                            public_key_path=public_key_path,
                            source_url=url,
                            copy=False,
                            force=True,
                        )
                    )
                return _json(
                    {
                        "ok": True,
                        "schema": ROOTFS_DOWNLOAD_SCHEMA,
                        "dry_run": False,
                        "already_exists": True,
                        "root": str(source_root),
                        "bundle_path": str(bundle),
                        "target_path": str(target),
                        "verification": existing,
                        "registration": registration,
                    }
                )
            return _json_error(
                f"target already exists and checksum does not match expected SHA256: {target}",
                code="ROOTFS_DOWNLOAD_TARGET_EXISTS",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        _download_or_copy_rootfs(url, target)
        verification = _verify_rootfs_asset(target, bundle_dir=bundle, expected_sha256=expected)
        if not verification.get("checksum_verified"):
            return _json_error(
                "downloaded rootfs SHA256 does not match expected SHA256",
                code="ROOTFS_DOWNLOAD_SHA256_MISMATCH",
            )
        signature_path = ""
        sig_url = str(selected.get("signature_url") or "")
        if sig_url:
            sig_target = target.with_suffix(target.suffix + ".sig")
            _download_or_copy_rootfs(sig_url, sig_target)
            signature_path = str(sig_target)
        registration = {}
        if register:
            registration = json.loads(
                metis_rootfs_asset_register(
                    rootfs_path=str(target),
                    root=str(source_root),
                    bundle_path=str(bundle),
                    expected_sha256=expected,
                    signature_path=signature_path,
                    public_key_path=public_key_path,
                    source_url=url,
                    copy=False,
                    force=True,
                )
            )
            if not registration.get("ok"):
                return _json_error(
                    str(registration.get("error") or "rootfs registration failed"),
                    code=str(registration.get("code") or "ROOTFS_REGISTER_FAILED"),
                )
        return _json(
            {
                "ok": True,
                "schema": ROOTFS_DOWNLOAD_SCHEMA,
                "dry_run": False,
                "root": str(source_root),
                "bundle_path": str(bundle),
                "source": source,
                "target_path": str(target),
                "verification": verification,
                "signature_path": signature_path,
                "registration": registration,
                "next_steps": [
                    "Run metis_wsl_runtime_import(dry_run=true) to inspect the import plan.",
                    "Run metis_wsl_runtime_import(dry_run=false) only after approval.",
                ],
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="ROOTFS_DOWNLOAD_FAILED")


def metis_rootfs_builder_status(
    root: str = ".",
    bundle_path: str = "",
    backend: str = "auto",
    base_image: str = "ubuntu:22.04",
    wsl_distro: str = "",
) -> str:
    """Inspect the Metis rootfs builder without writing files."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        bundle = _resolve_vm_bundle_dir(source_root, bundle_path)
        status = _detect_rootfs_builder(
            source_root=source_root,
            bundle_dir=bundle,
            backend=backend,
            base_image=base_image,
            wsl_distro=wsl_distro,
        )
        return _json(
            {
                "ok": True,
                "schema": ROOTFS_BUILDER_STATUS_SCHEMA,
                "root": str(source_root),
                "bundle_path": str(bundle),
                **status,
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="ROOTFS_BUILDER_STATUS_FAILED")


def metis_rootfs_build(
    root: str = ".",
    bundle_path: str = "",
    backend: str = "auto",
    base_image: str = "ubuntu:22.04",
    profile: str = "standard",
    output_path: str = "",
    image_tag: str = "",
    wsl_distro: str = "",
    dry_run: bool = True,
    allow_network: bool = False,
    register: bool = True,
    force: bool = False,
    keep_image: bool = True,
    timeout: int = 1800,
) -> str:
    """Build a Metis-owned rootfs.tar through the supported builder backend."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        bundle = _resolve_vm_bundle_dir(source_root, bundle_path)
        normalized_profile = _normalize_rootfs_build_profile(profile)
        selected_image = _normalize_docker_reference(base_image, default="ubuntu:22.04")
        selected_tag = _normalize_docker_reference(image_tag, default=_default_rootfs_image_tag())
        target = _resolve_rootfs_build_output(bundle_dir=bundle, output_path=output_path)
        status = _detect_rootfs_builder(
            source_root=source_root,
            bundle_dir=bundle,
            backend=backend,
            base_image=selected_image,
            wsl_distro=wsl_distro,
        )
        plan = _rootfs_build_plan(
            bundle_dir=bundle,
            target=target,
            backend=str(status.get("selected_backend") or "script_only"),
            base_image=selected_image,
            image_tag=selected_tag,
            profile=normalized_profile,
            allow_network=allow_network,
            register=register,
            keep_image=keep_image,
        )
        if dry_run:
            return _json(
                {
                    "ok": True,
                    "schema": ROOTFS_BUILD_SCHEMA,
                    "dry_run": True,
                    "root": str(source_root),
                    "bundle_path": str(bundle),
                    "target_path": str(target),
                    "builder": status,
                    "plan": plan,
                    "next_steps": [
                        "Set dry_run=false to write builder scripts and run the selected builder.",
                        "Use profile=minimal for an offline smoke rootfs when the base image already exists.",
                        "Use profile=standard with allow_network=true for developer runtime tools.",
                        "Use profile=office with allow_network=true when LibreOffice/ImageMagick should be bundled too.",
                    ],
                }
            )
        written = _write_rootfs_builder_files(
            bundle_dir=bundle,
            base_image=selected_image,
            image_tag=selected_tag,
            profile=normalized_profile,
            target=target,
        )
        selected_backend = str(status.get("selected_backend") or "script_only")
        if selected_backend != "docker":
            return _json_error(
                "Rootfs build execution currently requires Docker. Builder scripts were written for manual/WSL follow-up.",
                code="ROOTFS_BUILDER_BACKEND_UNAVAILABLE",
            )
        docker = status.get("docker") if isinstance(status.get("docker"), dict) else {}
        docker_exe = str(docker.get("executable") or shutil.which("docker") or "docker")
        base_available = bool(docker.get("base_image_available"))
        network_required = normalized_profile in {"standard", "office"}
        if network_required and not allow_network:
            return _json_error(
                f"profile={normalized_profile} installs packages with apt/pip/npm and requires allow_network=true. Use profile=minimal for an offline smoke rootfs.",
                code="ROOTFS_BUILD_NETWORK_REQUIRED",
            )
        if not base_available and not allow_network:
            return _json_error(
                f"Docker base image is not available locally and network is disabled: {selected_image}",
                code="ROOTFS_BUILD_BASE_IMAGE_MISSING",
            )
        if target.exists() and not force:
            return _json_error(
                f"rootfs build target already exists: {target}",
                code="ROOTFS_BUILD_TARGET_EXISTS",
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        steps, cleanup_steps = _run_docker_rootfs_build(
            docker_exe=docker_exe,
            bundle_dir=bundle,
            target=target,
            base_image=selected_image,
            image_tag=selected_tag,
            profile=normalized_profile,
            allow_network=allow_network,
            keep_image=keep_image,
            timeout=max(60, int(timeout or 1800)),
        )
        verification = _verify_rootfs_asset(target, bundle_dir=bundle, expected_sha256="")
        built_sha = str(verification.get("sha256") or "")
        registration = {}
        if register:
            registration = json.loads(
                metis_rootfs_asset_register(
                    rootfs_path=str(target),
                    root=str(source_root),
                    bundle_path=str(bundle),
                    expected_sha256=built_sha,
                    source_url=f"docker://{selected_image}",
                    copy=False,
                    force=True,
                )
            )
            if not registration.get("ok"):
                return _json_error(
                    str(registration.get("error") or "rootfs registration failed"),
                    code=str(registration.get("code") or "ROOTFS_REGISTER_FAILED"),
                )
            verification = _verify_rootfs_asset(target, bundle_dir=bundle, expected_sha256=built_sha)
        return _json(
            {
                "ok": True,
                "schema": ROOTFS_BUILD_SCHEMA,
                "dry_run": False,
                "root": str(source_root),
                "bundle_path": str(bundle),
                "target_path": str(target),
                "builder": status,
                "plan": plan,
                "written": written,
                "steps": steps,
                "cleanup_steps": cleanup_steps,
                "verification": verification,
                "registration": registration,
                "next_steps": [
                    "Run metis_wsl_runtime_import(dry_run=true) to inspect the import command.",
                    "Run metis_wsl_runtime_import(dry_run=false) only after approving the WSL import.",
                ],
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="ROOTFS_BUILD_FAILED")


def metis_rootfs_image_builder_status(
    root: str = ".",
    bundle_path: str = "",
    backend: str = "auto",
    rootfs_tar_path: str = "",
    output_path: str = "",
    temp_distro_name: str = "",
    install_dir: str = "",
) -> str:
    """Inspect the rootfs.vhdx image builder without writing files."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        bundle = _resolve_vm_bundle_dir(source_root, bundle_path)
        status = _detect_rootfs_image_builder(
            source_root=source_root,
            bundle_dir=bundle,
            backend=backend,
            rootfs_tar_path=rootfs_tar_path,
            output_path=output_path,
            temp_distro_name=temp_distro_name,
            install_dir=install_dir,
        )
        return _json(
            {
                "ok": True,
                "schema": ROOTFS_IMAGE_BUILDER_STATUS_SCHEMA,
                "root": str(source_root),
                "bundle_path": str(bundle),
                **status,
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="ROOTFS_IMAGE_BUILDER_STATUS_FAILED")


def metis_rootfs_image_build(
    root: str = ".",
    bundle_path: str = "",
    backend: str = "auto",
    rootfs_tar_path: str = "",
    output_path: str = "",
    temp_distro_name: str = "",
    install_dir: str = "",
    build_rootfs_tar: bool = True,
    rootfs_backend: str = "auto",
    base_image: str = "ubuntu:22.04",
    profile: str = "standard",
    image_tag: str = "",
    dry_run: bool = True,
    allow_network: bool = False,
    register: bool = True,
    force: bool = False,
    cleanup: bool = True,
    timeout: int = 1800,
) -> str:
    """Build a Metis-owned rootfs.vhdx image through the supported image backend."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        bundle = _resolve_vm_bundle_dir(source_root, bundle_path)
        normalized_profile = _normalize_rootfs_build_profile(profile)
        selected_backend = _normalize_rootfs_image_backend(backend)
        target = _resolve_rootfs_image_output(bundle_dir=bundle, output_path=output_path)
        tar_source = _resolve_rootfs_image_tar(bundle_dir=bundle, rootfs_tar_path=rootfs_tar_path)
        distro = _normalize_rootfs_image_distro_name(temp_distro_name)
        install_path = _resolve_rootfs_image_install_dir(source_root, install_dir, distro)
        status = _detect_rootfs_image_builder(
            source_root=source_root,
            bundle_dir=bundle,
            backend=selected_backend,
            rootfs_tar_path=str(tar_source),
            output_path=str(target),
            temp_distro_name=distro,
            install_dir=str(install_path),
        )
        plan = _rootfs_image_build_plan(
            source_root=source_root,
            bundle_dir=bundle,
            backend=str(status.get("selected_backend") or "script_only"),
            rootfs_tar=tar_source,
            target=target,
            distro=distro,
            install_dir=install_path,
            profile=normalized_profile,
            build_rootfs_tar=bool(build_rootfs_tar),
            rootfs_backend=rootfs_backend,
            base_image=base_image,
            image_tag=image_tag,
            allow_network=allow_network,
            register=register,
            cleanup=cleanup,
        )
        if dry_run:
            return _json(
                {
                    "ok": True,
                    "schema": ROOTFS_IMAGE_BUILD_SCHEMA,
                    "dry_run": True,
                    "root": str(source_root),
                    "bundle_path": str(bundle),
                    "target_path": str(target),
                    "rootfs_tar_path": str(tar_source),
                    "builder": status,
                    "plan": plan,
                    "next_steps": [
                        "Set dry_run=false to write image-builder scripts and execute the selected backend.",
                        "The executable backend is WSL2 import: rootfs.tar -> temporary distro ext4.vhdx -> rootfs.vhdx.",
                        "If rootfs.tar is missing, keep build_rootfs_tar=true so metis_rootfs_build creates it first.",
                    ],
                }
            )
        written = _write_rootfs_image_builder_files(
            bundle_dir=bundle,
            plan=plan,
            profile=normalized_profile,
        )
        selected = str(status.get("selected_backend") or "script_only")
        if selected != "wsl_import":
            return _json_error(
                "Rootfs image build execution currently requires WSL2 import support. Builder scripts were written for manual follow-up.",
                code="ROOTFS_IMAGE_BUILDER_BACKEND_UNAVAILABLE",
            )
        if target.exists() and not force:
            return _json_error(
                f"rootfs.vhdx target already exists: {target}",
                code="ROOTFS_IMAGE_TARGET_EXISTS",
            )
        steps: List[Dict[str, Any]] = []
        tar_build: Dict[str, Any] = {}
        if not tar_source.is_file():
            if not build_rootfs_tar:
                return _json_error(
                    f"rootfs.tar is missing and build_rootfs_tar=false: {tar_source}",
                    code="ROOTFS_IMAGE_SOURCE_TAR_MISSING",
                )
            tar_build = json.loads(
                metis_rootfs_build(
                    root=str(source_root),
                    bundle_path=str(bundle),
                    backend=rootfs_backend,
                    base_image=base_image,
                    profile=normalized_profile,
                    output_path=str(tar_source),
                    image_tag=image_tag,
                    dry_run=False,
                    allow_network=allow_network,
                    register=False,
                    force=force,
                    keep_image=True,
                    timeout=max(60, int(timeout or 1800)),
                )
            )
            if not tar_build.get("ok"):
                return _json(tar_build)
        result = _run_wsl_rootfs_image_build(
            wsl_exe=str((status.get("wsl") or {}).get("executable") or shutil.which("wsl.exe") or shutil.which("wsl") or "wsl"),
            rootfs_tar=tar_source,
            target=target,
            distro=distro,
            install_dir=install_path,
            cleanup=bool(cleanup),
            force=bool(force),
            timeout=max(60, int(timeout or 1800)),
        )
        steps.extend(result.get("steps") if isinstance(result.get("steps"), list) else [])
        if not result.get("ok"):
            return _json_error(
                str(result.get("error") or "rootfs image build failed"),
                code=str(result.get("code") or "ROOTFS_IMAGE_BUILD_EXECUTION_FAILED"),
            )
        verification = _verify_rootfs_asset(target, bundle_dir=bundle, expected_sha256="")
        registration = {}
        if register:
            registration = json.loads(
                metis_rootfs_asset_register(
                    rootfs_path=str(target),
                    root=str(source_root),
                    bundle_path=str(bundle),
                    expected_sha256=str(verification.get("sha256") or ""),
                    source_url=f"wsl-import://{distro}",
                    copy=False,
                    force=True,
                )
            )
            if not registration.get("ok"):
                return _json_error(
                    str(registration.get("error") or "rootfs.vhdx registration failed"),
                    code=str(registration.get("code") or "ROOTFS_IMAGE_REGISTER_FAILED"),
                )
            verification = _verify_rootfs_asset(target, bundle_dir=bundle, expected_sha256=str(verification.get("sha256") or ""))
        manifest = _write_rootfs_image_builder_manifest(
            bundle,
            plan=plan,
            target=target,
            rootfs_tar=tar_source,
            verification=verification,
            registration=registration,
            result=result,
            profile=normalized_profile,
        )
        return _json(
            {
                "ok": True,
                "schema": ROOTFS_IMAGE_BUILD_SCHEMA,
                "dry_run": False,
                "root": str(source_root),
                "bundle_path": str(bundle),
                "target_path": str(target),
                "rootfs_tar_path": str(tar_source),
                "builder": status,
                "plan": plan,
                "written": written,
                "tar_build": tar_build,
                "steps": steps,
                "verification": verification,
                "registration": registration,
                "manifest": manifest,
                "next_steps": [
                    "Run metis_vm_direct_assets_prepare with rootfs_vhdx_path set to this rootfs.vhdx plus kernel/initrd assets.",
                    "Run metis_vm_rootfs_boot_verifier_prepare after vmlinuz/initrd/sessiondata/metis-bin assets are present.",
                    "Run metis_vm_guest_handshake_verify only after HCS transport exists.",
                ],
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="ROOTFS_IMAGE_BUILD_FAILED")


def metis_sandbox_status(
    root: str = ".",
    docker_image: str = "",
    wsl_distro: str = "",
    vm_bundle_path: str = "",
    metis_wsl_distro: str = "",
) -> str:
    """Detect optional sandbox backends for Metis Runtime."""
    try:
        source_root = _resolve_runtime_root(
            root,
            allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
    except Exception:
        source_root = Path(str(root or ".")).expanduser().resolve(strict=False)
    status = _detect_sandbox_backends(
        docker_image=docker_image,
        wsl_distro=wsl_distro,
        vm_bundle_path=vm_bundle_path,
        metis_wsl_distro=metis_wsl_distro,
        source_root=source_root,
    )
    return _json(
        {
            "ok": True,
            "schema": SANDBOX_STATUS_SCHEMA,
            "root": str(source_root),
            **status,
        }
    )


@dataclass(frozen=True)
class RuntimePaths:
    source_root: Path
    session_root: Path
    workspace_dir: Path
    artifacts_dir: Path
    diagnostics_dir: Path
    manifest_path: Path
    runs_path: Path


@dataclass
class RuntimePolicy:
    allow_network: bool = False
    allow_cross_drive: bool = False
    allow_project_write: bool = False
    allow_desktop_write: bool = False
    strict_sandbox: bool = False

    def to_dict(self) -> Dict[str, bool]:
        return {
            "allow_network": self.allow_network,
            "allow_cross_drive": self.allow_cross_drive,
            "allow_project_write": self.allow_project_write,
            "allow_desktop_write": self.allow_desktop_write,
            "strict_sandbox": self.strict_sandbox,
        }


@dataclass
class RuntimeManifest:
    session_id: str
    task: str
    mode: str
    backend: str
    paths: RuntimePaths
    policy: RuntimePolicy
    sandbox: Dict[str, Any] = field(default_factory=dict)
    status: str = "created"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    baseline_files: List[Dict[str, Any]] = field(default_factory=list)
    runtimes: Dict[str, Dict[str, str]] = field(default_factory=dict)
    copy_stats: Dict[str, Any] = field(default_factory=dict)
    git_baseline: Dict[str, Any] = field(default_factory=dict)
    last_diagnostics_zip: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": SESSION_SCHEMA,
            "session_id": self.session_id,
            "task": self.task,
            "mode": self.mode,
            "backend": self.backend,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "source_root": str(self.paths.source_root),
            "session_root": str(self.paths.session_root),
            "workspace_dir": str(self.paths.workspace_dir),
            "artifacts_dir": str(self.paths.artifacts_dir),
            "diagnostics_dir": str(self.paths.diagnostics_dir),
            "policy": self.policy.to_dict(),
            "boundary": runtime_manifest_boundary(
                workspace_dir=self.paths.workspace_dir,
                artifacts_dir=self.paths.artifacts_dir,
                diagnostics_dir=self.paths.diagnostics_dir,
                source_root=self.paths.source_root,
                backend=self.backend,
                mode=self.mode,
                allow_network=self.policy.allow_network,
                strict_sandbox=self.policy.strict_sandbox,
            ),
            "sandbox": self.sandbox,
            "baseline_file_count": len(self.baseline_files),
            "baseline_files": self.baseline_files,
            "runtimes": self.runtimes,
            "copy_stats": self.copy_stats,
            "git_baseline": self.git_baseline,
            "last_diagnostics_zip": self.last_diagnostics_zip,
        }


@dataclass
class BackendRunResult:
    returncode: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool
    executed_command: str
    backend: str
    fallback_reason: str = ""


def metis_runtime_create(
    task: str = "",
    root: str = ".",
    mode: str = "copy",
    backend: str = "local",
    docker_image: str = "",
    wsl_distro: str = "",
    vm_bundle_path: str = "",
    metis_wsl_distro: str = "",
    metis_wsl_install_dir: str = "",
    include_patterns: Optional[List[str]] = None,
    exclude_patterns: Optional[List[str]] = None,
    max_files: int = DEFAULT_MAX_FILES,
    max_bytes: int = DEFAULT_MAX_BYTES,
    allow_network: bool = False,
    allow_cross_drive: bool = False,
    allow_project_write: bool = False,
    allow_desktop_write: bool = False,
    strict_sandbox: bool = False,
) -> str:
    """Create an isolated runtime session for background code/artifact work."""
    try:
        source_root = _resolve_runtime_root(root, allow_cross_drive=allow_cross_drive)
        mode_value = _normalize_mode(mode)
        backend_selection = _select_runtime_backend(
            backend,
            docker_image=docker_image,
            wsl_distro=wsl_distro,
            vm_bundle_path=vm_bundle_path,
            metis_wsl_distro=metis_wsl_distro,
            metis_wsl_install_dir=metis_wsl_install_dir,
            source_root=source_root,
        )
        if bool(strict_sandbox) and str(backend_selection.get("selected") or "").lower() == "local":
            return _json(
                {
                    "ok": False,
                    "schema": SESSION_SCHEMA,
                    "code": "STRICT_SANDBOX_UNAVAILABLE",
                    "error": "strict sandbox requires MetisRuntime, WSL, Docker, or a runnable VM Pack; local-copy fallback is disabled",
                    "backend": "local",
                    "sandbox": backend_selection,
                    "next_steps": [
                        "Install or import MetisRuntime WSL, enable an existing WSL distro, or start Docker.",
                        "Use strict_sandbox=false when local-copy compatibility fallback is acceptable.",
                    ],
                }
            )
        policy = RuntimePolicy(
            allow_network=bool(allow_network),
            allow_cross_drive=bool(allow_cross_drive),
            allow_project_write=bool(allow_project_write),
            allow_desktop_write=bool(allow_desktop_write),
            strict_sandbox=bool(strict_sandbox),
        )
        if mode_value == "mount" and not policy.allow_project_write:
            return _json_error(
                "mount mode requires allow_project_write=true because commands run in the source project",
                code="PROJECT_WRITE_NOT_AUTHORIZED",
            )
        paths = _new_runtime_paths(source_root)
        paths.session_root.mkdir(parents=True, exist_ok=False)
        paths.artifacts_dir.mkdir(parents=True, exist_ok=True)
        paths.diagnostics_dir.mkdir(parents=True, exist_ok=True)
        # HCS is itself the isolation boundary: the VM gets workspace files
        # pushed in over vsock per run and writes nothing back to the source,
        # so we skip the host-side snapshot entirely (no disk bloat).
        hcs_direct = mode_value == "copy" and backend_selection["selected"] == "hcs"
        if hcs_direct:
            copy_stats = {
                "mode": "hcs-direct",
                "copied_files": 0,
                "copied_bytes": 0,
                "skipped": [],
                "note": "HCS VM is the isolation boundary; workspace is pushed into the VM per run, no host snapshot",
            }
            paths = RuntimePaths(
                source_root=paths.source_root,
                session_root=paths.session_root,
                workspace_dir=source_root,  # read-only push source; VM writes go to artifacts
                artifacts_dir=paths.artifacts_dir,
                diagnostics_dir=paths.diagnostics_dir,
                manifest_path=paths.manifest_path,
                runs_path=paths.runs_path,
            )
            baseline = _scan_baseline(source_root, max_files=max(1, int(max_files or DEFAULT_MAX_FILES)))
        elif mode_value == "copy":
            paths.workspace_dir.mkdir(parents=True, exist_ok=True)
            copy_stats, baseline = _copy_workspace_snapshot(
                source_root,
                paths.workspace_dir,
                include_patterns=include_patterns or [],
                exclude_patterns=exclude_patterns or [],
                max_files=max(1, int(max_files or DEFAULT_MAX_FILES)),
                max_bytes=max(1024, int(max_bytes or DEFAULT_MAX_BYTES)),
            )
        else:
            paths.workspace_dir.mkdir(parents=True, exist_ok=True)
            copy_stats = {
                "mode": "mount",
                "copied_files": 0,
                "copied_bytes": 0,
                "skipped": [],
                "note": "workspace_dir points at the source project through mount mode",
            }
            paths = RuntimePaths(
                source_root=paths.source_root,
                session_root=paths.session_root,
                workspace_dir=source_root,
                artifacts_dir=paths.artifacts_dir,
                diagnostics_dir=paths.diagnostics_dir,
                manifest_path=paths.manifest_path,
                runs_path=paths.runs_path,
            )
            baseline = _scan_baseline(source_root, max_files=max(1, int(max_files or DEFAULT_MAX_FILES)))

        manifest = RuntimeManifest(
            session_id=paths.session_root.name,
            task=str(task or ""),
            mode=mode_value,
            backend=backend_selection["selected"],
            paths=paths,
            policy=policy,
            sandbox=backend_selection,
            baseline_files=baseline,
            copy_stats=copy_stats,
            runtimes=_detect_runtime_commands(),
        )
        if mode_value == "copy" and not hcs_direct:
            manifest.git_baseline = _initialize_git_baseline(paths.workspace_dir)
        _write_manifest(manifest)
        return _json(
            {
                "ok": True,
                "schema": SESSION_SCHEMA,
                "session_id": manifest.session_id,
                "status": manifest.status,
                "mode": manifest.mode,
                "backend": manifest.backend,
                "source_root": str(paths.source_root),
                "workspace_dir": str(paths.workspace_dir),
                "artifacts_dir": str(paths.artifacts_dir),
                "diagnostics_dir": str(paths.diagnostics_dir),
                "policy": manifest.policy.to_dict(),
                "boundary": runtime_manifest_boundary(
                    workspace_dir=paths.workspace_dir,
                    artifacts_dir=paths.artifacts_dir,
                    diagnostics_dir=paths.diagnostics_dir,
                    source_root=paths.source_root,
                    backend=manifest.backend,
                    mode=manifest.mode,
                    allow_network=manifest.policy.allow_network,
                    strict_sandbox=manifest.policy.strict_sandbox,
                ),
                "sandbox": manifest.sandbox,
                "baseline_file_count": len(manifest.baseline_files),
                "copy_stats": manifest.copy_stats,
                "runtimes": manifest.runtimes,
                "git_baseline": manifest.git_baseline,
                "next_steps": [
                    "Run scripts with metis_runtime_run.",
                    "Write generated files to METIS_RUNTIME_ARTIFACTS_DIR.",
                    "Export changes with metis_runtime_export_patch before touching the source project.",
                ],
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="RUNTIME_CREATE_FAILED")


def metis_runtime_run(
    session_id: str,
    command: str,
    cwd: str = "",
    timeout: int = 120,
    allow_network: bool = False,
    env: Optional[Dict[str, str]] = None,
) -> str:
    """Run a shell command inside an isolated runtime session."""
    started = time.time()
    try:
        manifest = _load_manifest(session_id)
        policy = manifest.policy
        network_allowed = bool(allow_network) or policy.allow_network
        command_text = str(command or "").strip()
        if not command_text:
            return _json_error("command is required", code="COMMAND_REQUIRED")
        if not network_allowed and _looks_like_network_command(command_text):
            return _json_error(
                "network-like command blocked by runtime policy; set allow_network only after explicit user authorization",
                code="NETWORK_BLOCKED",
                session_id=manifest.session_id,
            )
        work_dir = _resolve_session_cwd(manifest.paths.workspace_dir, cwd)
        run_id = f"run_{int(started * 1000)}_{uuid.uuid4().hex[:8]}"
        run_dir = manifest.paths.diagnostics_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=True)
        env_map = subprocess_env_with_configured_python()
        env_map.update(
            {
                "METIS_RUNTIME_SESSION_ID": manifest.session_id,
                "METIS_RUNTIME_WORKSPACE": str(manifest.paths.workspace_dir),
                "METIS_RUNTIME_ARTIFACTS_DIR": str(manifest.paths.artifacts_dir),
                "METIS_RUNTIME_DIAGNOSTICS_DIR": str(manifest.paths.diagnostics_dir),
                "METIS_RUNTIME_SOURCE_ROOT": str(manifest.paths.source_root),
            }
        )
        if env:
            env_map.update({str(key): str(value) for key, value in dict(env).items()})
        backend_result = _run_runtime_command(
            manifest,
            command_text,
            work_dir=work_dir,
            timeout=max(1, int(timeout or 120)),
            env_map=env_map,
            network_allowed=network_allowed,
        )
        returncode = backend_result.returncode
        stdout = backend_result.stdout
        stderr = backend_result.stderr
        timed_out = backend_result.timed_out
        duration_ms = int((time.time() - started) * 1000)
        stdout_path = run_dir / "stdout.txt"
        stderr_path = run_dir / "stderr.txt"
        stdout_path.write_text(stdout, encoding="utf-8", errors="replace")
        stderr_path.write_text(stderr, encoding="utf-8", errors="replace")
        row = {
            "run_id": run_id,
            "at": time.time(),
            "command": command_text,
            "executed_command": backend_result.executed_command,
            "backend": backend_result.backend,
            "fallback_reason": backend_result.fallback_reason,
            "cwd": str(work_dir),
            "returncode": returncode,
            "timed_out": timed_out,
            "duration_ms": duration_ms,
            "stdout_path": str(stdout_path),
            "stderr_path": str(stderr_path),
            "network_allowed": network_allowed,
        }
        _append_run(manifest.paths.runs_path, row)
        manifest.status = "failed" if timed_out or returncode not in (0, None) else "ran"
        if returncode == 0:
            manifest.status = "ran"
        manifest.updated_at = time.time()
        diagnostics_zip = ""
        if timed_out or returncode not in (0, None):
            diagnostics = _export_diagnostics(manifest)
            diagnostics_zip = str(diagnostics.get("diagnostics_zip") or "")
            manifest.last_diagnostics_zip = diagnostics_zip
        _write_manifest(manifest)
        artifacts = _list_artifacts(manifest.paths.artifacts_dir)
        return _json(
            {
                "ok": returncode == 0 and not timed_out,
                "schema": RUNTIME_SCHEMA,
                "session_id": manifest.session_id,
                "run_id": run_id,
                "command": command_text,
                "executed_command": backend_result.executed_command,
                "backend": backend_result.backend,
                "fallback_reason": backend_result.fallback_reason,
                "cwd": str(work_dir),
                "returncode": returncode,
                "timed_out": timed_out,
                "duration_ms": duration_ms,
                "stdout": _truncate(stdout),
                "stderr": _truncate(stderr),
                "stdout_path": str(stdout_path),
                "stderr_path": str(stderr_path),
                "artifacts_dir": str(manifest.paths.artifacts_dir),
                "artifacts": artifacts,
                "diagnostics_zip": diagnostics_zip,
                "boundary": runtime_manifest_boundary(
                    workspace_dir=manifest.paths.workspace_dir,
                    artifacts_dir=manifest.paths.artifacts_dir,
                    diagnostics_dir=manifest.paths.diagnostics_dir,
                    source_root=manifest.paths.source_root,
                    backend=backend_result.backend,
                    mode=manifest.mode,
                    allow_network=network_allowed,
                    strict_sandbox=manifest.policy.strict_sandbox,
                ),
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="RUNTIME_RUN_FAILED", session_id=session_id)


def metis_runtime_collect_artifacts(
    session_id: str,
    patterns: Optional[List[str]] = None,
    max_files: int = 200,
    max_bytes_per_file: int = 20 * 1024 * 1024,
) -> str:
    """Copy generated deliverables from the runtime workspace into .metis/artifacts."""
    try:
        manifest = _load_manifest(session_id)
        copied: List[Dict[str, Any]] = []
        selected_patterns = [str(item) for item in (patterns or []) if str(item).strip()] or sorted(ARTIFACT_PATTERNS)
        collected_dir = manifest.paths.artifacts_dir / "collected"
        collected_dir.mkdir(parents=True, exist_ok=True)
        for path in _iter_files(manifest.paths.workspace_dir):
            if len(copied) >= max(1, int(max_files or 200)):
                break
            if _is_within(path, manifest.paths.artifacts_dir):
                continue
            rel = _relative_to(path, manifest.paths.workspace_dir)
            if not _matches_any(rel, selected_patterns):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            if size > max(1, int(max_bytes_per_file or 1)):
                continue
            target = collected_dir / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            copied.append({"path": str(target), "source": str(path), "relative_path": rel, "size": size})
        manifest.updated_at = time.time()
        _write_manifest(manifest)
        return _json(
            {
                "ok": True,
                "schema": RUNTIME_SCHEMA,
                "session_id": manifest.session_id,
                "artifacts_dir": str(manifest.paths.artifacts_dir),
                "copied": copied,
                "artifacts": _list_artifacts(manifest.paths.artifacts_dir),
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="ARTIFACT_COLLECTION_FAILED", session_id=session_id)


def metis_runtime_export_patch(session_id: str, output_path: str = "") -> str:
    """Export source changes made inside the isolated runtime as a patch artifact."""
    try:
        manifest = _load_manifest(session_id)
        if manifest.mode == "mount":
            return _json_error(
                "patch export is not available for mount mode because commands ran directly in the source project",
                code="MOUNT_MODE_NO_PATCH",
                session_id=manifest.session_id,
            )
        patch_text, changed_files = _build_patch(manifest)
        target = _patch_output_path(manifest, output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(patch_text, encoding="utf-8", errors="replace")
        manifest.updated_at = time.time()
        _write_manifest(manifest)
        return _json(
            {
                "ok": True,
                "schema": RUNTIME_SCHEMA,
                "session_id": manifest.session_id,
                "patch_path": str(target),
                "changed_files": changed_files,
                "changed_file_count": len(changed_files),
                "empty": not bool(patch_text.strip()),
                "summary": _patch_summary(changed_files),
            }
        )
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="PATCH_EXPORT_FAILED", session_id=session_id)


def metis_runtime_export_diagnostics(session_id: str) -> str:
    """Export manifest, command logs, artifact listing, and patch summary as a zip."""
    try:
        manifest = _load_manifest(session_id)
        payload = _export_diagnostics(manifest)
        manifest.last_diagnostics_zip = str(payload.get("diagnostics_zip") or "")
        manifest.updated_at = time.time()
        _write_manifest(manifest)
        return _json(payload)
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="DIAGNOSTICS_EXPORT_FAILED", session_id=session_id)


def metis_runtime_status(session_id: str = "", root: str = ".") -> str:
    """Return one runtime session or list recent runtime sessions for a workspace."""
    try:
        if session_id:
            manifest = _load_manifest(session_id)
            data = manifest.to_dict()
            data["ok"] = True
            data["runs"] = _read_runs(manifest.paths.runs_path)
            data["artifacts"] = _list_artifacts(manifest.paths.artifacts_dir)
            return _json(data)
        source_root = _resolve_runtime_root(root, allow_cross_drive=get_effective_sub_allow("allow_paths_outside_workspace"))
        sessions_root = source_root / ".metis" / "runtime"
        sessions = []
        if sessions_root.is_dir():
            for manifest_path in sorted(sessions_root.glob("*/manifest.json"), key=lambda item: item.stat().st_mtime, reverse=True):
                try:
                    data = json.loads(manifest_path.read_text(encoding="utf-8"))
                except Exception:
                    continue
                sessions.append(
                    {
                        "session_id": data.get("session_id", manifest_path.parent.name),
                        "task": data.get("task", ""),
                        "status": data.get("status", ""),
                        "mode": data.get("mode", ""),
                        "backend": data.get("backend", "local"),
                        "updated_at": data.get("updated_at", 0),
                        "workspace_dir": data.get("workspace_dir", ""),
                        "artifacts_dir": data.get("artifacts_dir", ""),
                    }
                )
        return _json({"ok": True, "schema": RUNTIME_SCHEMA, "root": str(source_root), "sessions": sessions[:20]})
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="RUNTIME_STATUS_FAILED", session_id=session_id)


def _resolve_runtime_root(root: str, *, allow_cross_drive: bool = False) -> Path:
    raw = str(root or ".").strip() or "."
    context_root = get_workspace_root()
    try:
        source = safe_path_for_read(raw, allow_paths_outside_workspace=allow_cross_drive or None)
    except PathSecurityError:
        source = safe_path_for_read(raw)
    if not source.is_dir():
        raise PathSecurityError(f"runtime root is not a directory: {source}")
    if not allow_cross_drive and source.drive and context_root.drive and source.drive.lower() != context_root.drive.lower():
        raise PathSecurityError(
            f"cross-drive runtime root requires allow_cross_drive=true: {source} (workspace drive {context_root.drive})"
        )
    return source


def _normalize_mode(mode: str) -> str:
    value = str(mode or "copy").strip().lower().replace("-", "_")
    if value in {"copy", "snapshot"}:
        return "copy"
    if value in {"mount", "source"}:
        return "mount"
    return "copy"


def _new_runtime_paths(source_root: Path) -> RuntimePaths:
    session_id = f"rt_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    session_root = source_root / ".metis" / "runtime" / session_id
    artifacts_dir = source_root / ".metis" / "artifacts" / session_id
    diagnostics_dir = source_root / ".metis" / "diagnostics" / session_id
    return RuntimePaths(
        source_root=source_root,
        session_root=session_root,
        workspace_dir=session_root / "workspace",
        artifacts_dir=artifacts_dir,
        diagnostics_dir=diagnostics_dir,
        manifest_path=session_root / "manifest.json",
        runs_path=session_root / "runs.jsonl",
    )


def _paths_from_manifest(data: Dict[str, Any]) -> RuntimePaths:
    source_root = Path(str(data.get("source_root") or ".")).resolve(strict=False)
    session_root = Path(str(data.get("session_root") or source_root / ".metis" / "runtime" / str(data.get("session_id") or ""))).resolve(strict=False)
    return RuntimePaths(
        source_root=source_root,
        session_root=session_root,
        workspace_dir=Path(str(data.get("workspace_dir") or session_root / "workspace")).resolve(strict=False),
        artifacts_dir=Path(str(data.get("artifacts_dir") or source_root / ".metis" / "artifacts" / session_root.name)).resolve(strict=False),
        diagnostics_dir=Path(str(data.get("diagnostics_dir") or source_root / ".metis" / "diagnostics" / session_root.name)).resolve(strict=False),
        manifest_path=session_root / "manifest.json",
        runs_path=session_root / "runs.jsonl",
    )


def _load_manifest(session_id: str) -> RuntimeManifest:
    manifest_path = _find_manifest_path(session_id)
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    paths = _paths_from_manifest(data)
    policy_data = data.get("policy") if isinstance(data.get("policy"), dict) else {}
    manifest = RuntimeManifest(
        session_id=str(data.get("session_id") or manifest_path.parent.name),
        task=str(data.get("task") or ""),
        mode=str(data.get("mode") or "copy"),
        backend=str(data.get("backend") or "local"),
        paths=paths,
        policy=RuntimePolicy(
            allow_network=bool(policy_data.get("allow_network")),
            allow_cross_drive=bool(policy_data.get("allow_cross_drive")),
            allow_project_write=bool(policy_data.get("allow_project_write")),
            allow_desktop_write=bool(policy_data.get("allow_desktop_write")),
            strict_sandbox=bool(policy_data.get("strict_sandbox")),
        ),
        sandbox=dict(data.get("sandbox") or {"selected": str(data.get("backend") or "local")}),
        status=str(data.get("status") or "created"),
        created_at=float(data.get("created_at") or time.time()),
        updated_at=float(data.get("updated_at") or time.time()),
        baseline_files=list(data.get("baseline_files") or []),
        runtimes=dict(data.get("runtimes") or {}),
        copy_stats=dict(data.get("copy_stats") or {}),
        git_baseline=dict(data.get("git_baseline") or {}),
        last_diagnostics_zip=str(data.get("last_diagnostics_zip") or ""),
    )
    return manifest


def _find_manifest_path(session_id: str) -> Path:
    safe = _safe_session_id(session_id)
    if not safe:
        raise ValueError("session_id is required")
    root = get_workspace_root()
    candidate = root / ".metis" / "runtime" / safe / "manifest.json"
    if candidate.is_file():
        return candidate
    for path in root.glob(f".metis/runtime/{safe}/manifest.json"):
        if path.is_file():
            return path
    raise FileNotFoundError(f"runtime session not found: {safe}")


def _write_manifest(manifest: RuntimeManifest) -> None:
    manifest.paths.manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest.paths.manifest_path.write_text(
        json.dumps(manifest.to_dict(), ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# Runtime storage hygiene (size reporting + retention GC)
# ---------------------------------------------------------------------------

RUNTIME_STORAGE_SCHEMA = "metis.runtime_storage.usage.v1"
RUNTIME_GC_SCHEMA = "metis.runtime_storage.gc.v1"
_RUNTIME_KINDS = ("runtime", "artifacts", "diagnostics")


def _is_runtime_session_dir(name: str) -> bool:
    """True only for runtime session dirs (rt_*). Protects siblings like
    .metis/runtime/wsl (managed WSL distro) from being GC'd."""
    return name.startswith("rt_")


def _dir_size(path: Path) -> int:
    total = 0
    if not path.is_dir():
        return 0
    for item in path.rglob("*"):
        try:
            if item.is_file():
                total += item.stat().st_size
        except OSError:
            continue
    return total


def _force_rmtree(path: Path) -> bool:
    """Remove a tree, clearing the read-only bit on Windows (e.g. .git objects)
    that would otherwise make shutil.rmtree silently fail. Returns True if gone."""
    def _onerror(func, p, _exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except Exception:
            pass
    try:
        shutil.rmtree(path, onerror=_onerror)
    except Exception:
        pass
    return not path.exists()


def _metis_root(root: str) -> Path:
    raw = str(root or ".").strip() or "."
    if raw == ".":
        base = get_workspace_root()
    else:
        try:
            base = safe_path_for_read(raw, allow_paths_outside_workspace=get_effective_sub_allow("allow_paths_outside_workspace"))
        except Exception:
            base = Path(raw).expanduser().resolve(strict=False)
    if base.is_file():
        base = base.parent
    return base / ".metis"


def metis_runtime_storage_usage(root: str = ".") -> str:
    """Report disk used by runtime sessions/artifacts/diagnostics/jobs."""
    try:
        metis = _metis_root(root)
        by_kind: Dict[str, int] = {}
        for kind in _RUNTIME_KINDS:
            by_kind[kind] = _dir_size(metis / kind)
        jobs_dir = metis / "runtime-jobs"
        by_kind["jobs"] = _dir_size(jobs_dir)
        session_ids = set()
        runtime_dir = metis / "runtime"
        if runtime_dir.is_dir():
            session_ids = {p.name for p in runtime_dir.iterdir() if p.is_dir() and _is_runtime_session_dir(p.name)}
        total = sum(by_kind.values())
        return _json({
            "ok": True,
            "schema": RUNTIME_STORAGE_SCHEMA,
            "root": str(metis.parent),
            "metis_dir": str(metis),
            "total_bytes": total,
            "by_kind": by_kind,
            "session_count": len(session_ids),
            "job_count": len(list(jobs_dir.glob("job_*.json"))) if jobs_dir.is_dir() else 0,
        })
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="RUNTIME_STORAGE_FAILED")


def metis_runtime_gc(
    root: str = ".",
    keep_recent: int = 20,
    max_age_days: float = 7.0,
    aggressive: bool = False,
) -> str:
    """Prune old runtime sessions and job manifests.

    Keeps the `keep_recent` newest sessions and anything newer than
    `max_age_days`; deletes the rest (runtime + artifacts + diagnostics).
    `aggressive=True` ignores keep_recent/age and removes everything.
    """
    try:
        metis = _metis_root(root)
        runtime_dir = metis / "runtime"
        sessions = []
        if runtime_dir.is_dir():
            for p in runtime_dir.iterdir():
                # ONLY ever touch runtime session dirs (rt_*). Never delete other
                # things stored under .metis/runtime (e.g. the managed WSL distro
                # at .metis/runtime/wsl/) — that would destroy user data.
                if p.is_dir() and _is_runtime_session_dir(p.name):
                    try:
                        sessions.append((p.name, p.stat().st_mtime))
                    except OSError:
                        sessions.append((p.name, 0.0))
        sessions.sort(key=lambda x: x[1], reverse=True)  # newest first

        now = time.time()
        cutoff = now - max_age_days * 86400
        keep: set = set()
        if not aggressive:
            # Keep only the N newest sessions that are also within max_age
            # (keep_recent is a hard cap; age prunes within the cap).
            for idx, (sid, mtime) in enumerate(sessions):
                if idx < max(0, int(keep_recent)) and mtime >= cutoff:
                    keep.add(sid)

        removed = []
        freed = 0
        for sid, _mtime in sessions:
            if sid in keep:
                continue
            any_removed = False
            for kind in _RUNTIME_KINDS:
                d = metis / kind / sid
                if d.is_dir():
                    sz = _dir_size(d)
                    if _force_rmtree(d):
                        freed += sz
                        any_removed = True
            if any_removed:
                removed.append(sid)

        # Prune orphan job manifests for removed sessions / beyond keep_recent.
        jobs_dir = metis / "runtime-jobs"
        jobs_removed = 0
        if jobs_dir.is_dir():
            job_files = sorted(jobs_dir.glob("job_*.json"), key=lambda f: f.stat().st_mtime, reverse=True)
            for idx, jf in enumerate(job_files):
                drop = aggressive or (idx >= max(0, int(keep_recent)))
                if not drop:
                    try:
                        if jf.stat().st_mtime < cutoff:
                            drop = True
                    except OSError:
                        pass
                if drop:
                    try:
                        freed += jf.stat().st_size
                        jf.unlink()
                        jobs_removed += 1
                    except OSError:
                        continue

        return _json({
            "ok": True,
            "schema": RUNTIME_GC_SCHEMA,
            "root": str(metis.parent),
            "removed_sessions": removed,
            "removed_session_count": len(removed),
            "removed_job_count": jobs_removed,
            "kept_session_count": len(keep),
            "freed_bytes": freed,
            "policy": {"keep_recent": keep_recent, "max_age_days": max_age_days, "aggressive": aggressive},
        })
    except Exception as exc:
        return _json_error(f"{type(exc).__name__}: {exc}", code="RUNTIME_GC_FAILED")


def _copy_workspace_snapshot(
    source_root: Path,
    workspace_dir: Path,
    *,
    include_patterns: List[str],
    exclude_patterns: List[str],
    max_files: int,
    max_bytes: int,
) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    files = _candidate_source_files(source_root, include_patterns=include_patterns, exclude_patterns=exclude_patterns)
    copied = 0
    copied_bytes = 0
    skipped: List[Dict[str, str]] = []
    baseline: List[Dict[str, Any]] = []
    for source in files:
        if copied >= max_files:
            skipped.append({"path": str(source), "reason": "max_files reached"})
            break
        rel = _relative_to(source, source_root)
        try:
            size = source.stat().st_size
        except OSError as exc:
            skipped.append({"path": rel, "reason": str(exc)})
            continue
        if copied_bytes + size > max_bytes:
            skipped.append({"path": rel, "reason": "max_bytes reached"})
            continue
        target = workspace_dir / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied += 1
        copied_bytes += size
        baseline.append({"relative_path": rel, "size": size, "sha256": _sha256_file(source)})
    return (
        {
            "mode": "copy",
            "copied_files": copied,
            "copied_bytes": copied_bytes,
            "skipped": skipped[:80],
            "truncated_skipped_count": max(0, len(skipped) - 80),
        },
        baseline,
    )


def _candidate_source_files(source_root: Path, *, include_patterns: List[str], exclude_patterns: List[str]) -> List[Path]:
    git_files = _git_list_files(source_root)
    if git_files:
        candidates = [source_root / rel for rel in git_files]
    else:
        candidates = list(_iter_files(source_root))
    out: List[Path] = []
    for path in candidates:
        if not path.is_file():
            continue
        rel = _relative_to(path, source_root)
        if _is_excluded(rel, include_patterns=include_patterns, exclude_patterns=exclude_patterns):
            continue
        out.append(path)
    return sorted(out, key=lambda item: _relative_to(item, source_root).lower())


def _git_list_files(source_root: Path) -> List[str]:
    if not shutil.which("git"):
        return []
    try:
        proc = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=str(source_root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
            check=False,
        )
    except Exception:
        return []
    if proc.returncode != 0:
        return []
    return [line.strip().replace("/", os.sep) for line in (proc.stdout or "").splitlines() if line.strip()]


def _scan_baseline(root: Path, *, max_files: int) -> List[Dict[str, Any]]:
    baseline: List[Dict[str, Any]] = []
    for path in _iter_files(root):
        if len(baseline) >= max_files:
            break
        rel = _relative_to(path, root)
        if _is_excluded(rel, include_patterns=[], exclude_patterns=[]):
            continue
        try:
            baseline.append({"relative_path": rel, "size": path.stat().st_size, "sha256": _sha256_file(path)})
        except OSError:
            continue
    return baseline


def _initialize_git_baseline(workspace_dir: Path) -> Dict[str, Any]:
    if not shutil.which("git"):
        return {"ok": False, "reason": "git not found; patch export will use file comparison fallback"}
    commands = [
        ["git", "init"],
        ["git", "add", "-A"],
        ["git", "-c", "user.email=metis-runtime@local", "-c", "user.name=Metis Runtime", "commit", "-m", "metis runtime baseline"],
    ]
    last_stdout = ""
    last_stderr = ""
    for command in commands:
        try:
            proc = subprocess.run(
                command,
                cwd=str(workspace_dir),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=60,
                check=False,
            )
        except Exception as exc:
            return {"ok": False, "reason": f"{type(exc).__name__}: {exc}"}
        last_stdout = proc.stdout or ""
        last_stderr = proc.stderr or ""
        if proc.returncode != 0:
            if command[:2] == ["git", "-c"] and "nothing to commit" in (last_stdout + last_stderr).lower():
                continue
            return {"ok": False, "command": " ".join(command), "stdout": _truncate(last_stdout), "stderr": _truncate(last_stderr)}
    return {"ok": True, "repo": str(workspace_dir / ".git")}


def _detect_runtime_commands() -> Dict[str, Dict[str, str]]:
    commands = {
        "python": configured_python_executable() or shutil.which("python") or shutil.which("py") or "",
        "node": shutil.which("node") or "",
        "git": shutil.which("git") or "",
        "rg": shutil.which("rg") or "",
        "pytest": shutil.which("pytest") or "",
        "npm": shutil.which("npm") or "",
    }
    return {
        name: {
            "available": "true" if path else "false",
            "path": str(path or ""),
        }
        for name, path in commands.items()
    }


def _hcs_backend_available() -> bool:
    """Check if HCS VM sandbox is usable.

    Either the privileged service is running (non-elevated path, no UAC) or
    HCS is directly reachable from this process (elevated) with a VM bundle.
    """
    try:
        from backend.runtime import svc_client
        if svc_client.service_available():
            return True
    except Exception:
        pass
    try:
        from backend.runtime.hcs_runtime import hcs_runtime_available
        ok, _ = hcs_runtime_available()
        return ok
    except Exception:
        return False


def _detect_sandbox_backends(
    docker_image: str = "",
    wsl_distro: str = "",
    vm_bundle_path: str = "",
    metis_wsl_distro: str = "",
    metis_wsl_install_dir: str = "",
    source_root: Optional[Path] = None,
) -> Dict[str, Any]:
    root = source_root or Path.cwd()
    vm_pack = _detect_vm_runtime_pack(source_root=root, bundle_path=vm_bundle_path)
    metis_wsl = _detect_metis_wsl_runtime(
        source_root=root,
        distro_name=metis_wsl_distro,
        install_dir=metis_wsl_install_dir,
    )
    wsl = _detect_wsl(wsl_distro=wsl_distro)
    docker = _detect_docker(docker_image=docker_image)
    preferred = (
        "metis_wsl"
        if metis_wsl.get("available")
        else
        "vm"
        if vm_pack.get("runnable")
        else "wsl"
        if wsl.get("available")
        else "docker"
        if docker.get("available")
        else "local"
    )
    return {
        "preferred": preferred,
        "vm_pack": vm_pack,
        "metis_wsl": metis_wsl,
        "wsl": wsl,
        "docker": docker,
        "local": {
            "available": True,
            "kind": "local_copy",
            "description": "Always available fallback: copy-mode runtime workspace on the host.",
        },
    }


def _detect_vm_runtime_pack(source_root: Path, bundle_path: str = "") -> Dict[str, Any]:
    host = _detect_vm_host_capabilities()
    configured_candidates = _vm_bundle_candidates(source_root=source_root, bundle_path=bundle_path)
    configured = [_inspect_vm_bundle_path(path, usage="metis_runtime_pack") for path in configured_candidates]
    asset_ready = next((item for item in configured if item.get("ready")), None)
    metis_ready = next((item for item in configured if item.get("ready") and item.get("metis_owned")), None)
    blueprint = next((item for item in configured if item.get("metis_owned")), None)
    runner_prepared = next((item for item in configured if item.get("direct_runner", {}).get("prepared")), None)
    handshake_prepared = next((item for item in configured if item.get("guest_handshake", {}).get("verifier_ready")), None)
    runner_runnable = next(
        (item for item in configured if item.get("metis_owned") and item.get("runner_ready")),
        None,
    )
    selected = runner_runnable or metis_ready or asset_ready or blueprint
    reference = []
    if _should_include_reference_vm_bundles(bundle_path=bundle_path):
        reference = [
            _inspect_vm_bundle_path(path, usage="reference_only")
            for path in _reference_vm_bundle_candidates()
            if path not in configured_candidates and path.exists()
        ]
    runner_available = bool(runner_runnable)
    reason = "no Metis VM bundle configured or installed"
    if runner_runnable and runner_runnable.get("hcs_direct_ready"):
        reason = "Metis direct VM runner is available after guest runtime.hello handshake"
    elif runner_runnable and runner_runnable.get("guest_protocol_ready"):
        reason = "Metis VM guest protocol runner is available through the JSONL stdio bridge; HCS direct boot remains gated"
    elif metis_ready and handshake_prepared:
        reason = "Metis direct VM HCS/rootfs layers are prepared, but booted guest runtime.hello is not verified"
    elif metis_ready and runner_prepared:
        reason = "Metis direct VM protocol/artifact/lifecycle runner is prepared, but guest handshake verifier is not complete"
    elif metis_ready:
        reason = "Metis VM bundle detected, but HCS direct runner is not implemented"
    elif asset_ready:
        reason = "VM boot assets detected, but this is not a Metis-owned runnable bundle"
    elif blueprint:
        reason = "Metis VM pack scaffold detected; rootfs.vhdx, vmlinuz, and initrd are still missing"
    elif any(item.get("exists") for item in configured):
        reason = "Metis VM bundle candidate found but required files are missing"
    return {
        "available": runner_available,
        "runnable": runner_available,
        "bundle_detected": bool(asset_ready or blueprint),
        "metis_owned_bundle_detected": bool(metis_ready),
        "blueprint_detected": bool(blueprint),
        "runner_prepared": bool(runner_prepared),
        "runner_available": runner_available,
        "reason": reason,
        "host": host,
        "selected_bundle": selected or {},
        "configured_candidates": configured,
        "reference_bundles": reference,
        "notes": [
            "Metis VM Pack is a clean-room bundle format inspired by the observed VM-pack architecture.",
            "A runnable pack must be Metis-owned and include rootfs.vhdx, vmlinuz, initrd, and a guest tools image.",
            "Direct runner v1 can prepare protocol/lifecycle/artifact files and execute through the guest JSONL protocol bridge.",
            "Full HCS direct readiness is promoted only after the booted guest metisd responds to runtime.hello.",
            "backend=auto can choose VM only for a Metis-owned bundle with a runnable guest protocol or verified HCS guest handshake.",
        ],
    }


def _detect_vm_host_capabilities() -> Dict[str, Any]:
    if os.name != "nt":
        return {
            "platform": os.name,
            "windows": False,
            "hcsdiag": "",
            "vmcompute_state": "",
            "reason": "Metis VM Pack v1 discovery is Windows-focused",
        }
    hcsdiag = shutil.which("hcsdiag.exe") or ""
    wsl = shutil.which("wsl.exe") or shutil.which("wsl") or ""
    vmcompute = _query_windows_service_state("vmcompute")
    return {
        "platform": "windows",
        "windows": True,
        "hcsdiag": hcsdiag,
        "wsl": wsl,
        "vmcompute_state": vmcompute.get("state", ""),
        "vmcompute_available": vmcompute.get("available", False),
        "vmcompute_raw": vmcompute.get("raw", ""),
        "reason": "" if hcsdiag or vmcompute.get("available") else "Windows virtualization host tools not detected",
    }


def _query_windows_service_state(name: str) -> Dict[str, Any]:
    sc = shutil.which("sc.exe") or "sc.exe"
    result = _quick_command([sc, "query", name], timeout=5)
    text = f"{result.get('stdout') or ''}\n{result.get('stderr') or ''}".strip()
    state = ""
    for line in text.splitlines():
        if "STATE" in line.upper():
            state = line.strip()
            break
    return {
        "available": result.get("returncode") == 0,
        "state": state,
        "raw": _truncate(text, 800),
    }


def _vm_bundle_candidates(source_root: Path, bundle_path: str = "") -> List[Path]:
    candidates: List[Path] = []
    for raw in (
        bundle_path,
        os.environ.get("METIS_VM_BUNDLE_DIR", ""),
        os.environ.get("METIS_RUNTIME_BUNDLE_DIR", ""),
    ):
        value = str(raw or "").strip()
        if value:
            candidates.append(Path(value).expanduser().resolve(strict=False))
    candidates.append((source_root / ".metis" / "runtime-pack" / "metisvm.bundle").resolve(strict=False))
    local_app = os.environ.get("LOCALAPPDATA", "")
    app_data = os.environ.get("APPDATA", "")
    if local_app:
        candidates.append(Path(local_app) / "Metis" / "vm_bundles" / "metisvm.bundle")
    if app_data:
        candidates.append(Path(app_data) / "Metis" / "vm_bundles" / "metisvm.bundle")
    return _dedupe_paths(candidates)


def _resolve_vm_scaffold_output(source_root: Path, output_path: str = "") -> Path:
    raw = str(output_path or "").strip()
    if raw:
        return safe_path_for_write(raw).resolve(strict=False)
    return (source_root / ".metis" / "runtime-pack" / "metisvm.bundle").resolve(strict=False)


def _resolve_vm_bundle_dir(source_root: Path, bundle_path: str = "") -> Path:
    raw = str(bundle_path or "").strip()
    if raw:
        return safe_path_for_write(raw).resolve(strict=False)
    return (source_root / ".metis" / "runtime-pack" / "metisvm.bundle").resolve(strict=False)


def _write_vm_pack_scaffold_files(target: Path) -> List[Dict[str, Any]]:
    files = {
        VM_MANIFEST_NAME: _vm_pack_manifest_template(),
        "guest/PROTOCOL.md": _vm_guest_protocol_template(),
        "guest/metisd.py": _vm_guest_metisd_stub(),
        "guest/sandbox-helper.md": _vm_sandbox_helper_template(),
        "host/README.md": _vm_host_runner_template(),
        "channels/README.md": _vm_channels_template(),
        "builder/README.md": _vm_rootfs_builder_readme(),
        "builder/install-runtime-tools.sh": _vm_rootfs_install_tools_script(),
    }
    written: List[Dict[str, Any]] = []
    for rel, content in files.items():
        path = target / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append({"relative_path": rel, "path": str(path), "size_bytes": len(content.encode("utf-8"))})
    return written


def _vm_pack_manifest_template() -> str:
    payload = {
        "schema": VM_PACK_MANIFEST_SCHEMA,
        "name": "metisvm",
        "owner": "metis",
        "version": "0.1.0-blueprint",
        "base_os": {
            "family": "ubuntu",
            "version": "22.04",
            "arch": "amd64",
        },
        "assets": {
            "rootfs": {},
        },
        "integrity": {
            "sha256_required_for_import": True,
            "signature": {
                "supported": True,
                "mode": "openssl-dgst-sha256-verify",
                "required_for_import": False,
            },
        },
        "required_boot_assets": list(VM_REQUIRED_FILES),
        "optional_assets": list(VM_OPTIONAL_FILES),
        "guest_tools": {
            "daemon": "/usr/local/bin/metisd",
            "sandbox_helper": "/usr/local/bin/metis-sandbox-helper",
            "sdk_daemon": "/usr/local/bin/metis-sdk-daemon",
            "tools_image": "metis-bin.vhdx",
        },
        "channels": {
            "transport": ["vsock", "stdio-fallback"],
            "shared_root": "/mnt/.metisfs-root/shared",
            "permission_request_dir": ".metis-perm-req",
            "permission_response_dir": ".metis-perm-resp",
            "uploads_dir": "uploads",
            "outputs_dir": "outputs",
        },
        "security": {
            "filesystem": {
                "default_write_scope": "workspace-copy",
                "deny_read": ["~/.ssh", "~/.gnupg", ".env", ".env.*"],
                "deny_write": [".git", ".env", ".env.*"],
                "delete_denied_by_default": True,
                "rename_denied_by_default": False,
            },
            "network": {
                "default": "deny",
                "allowed_domains": [],
                "host_proxy_required": True,
            },
            "process": {
                "cgroup_required": True,
                "seccomp_required": True,
                "timeout_required": True,
            },
        },
        "runtime_tools": {
            "python": "required",
            "node": "required",
            "git": "required",
            "rg": "required",
            "poppler": "recommended",
            "libreoffice": "recommended",
            "imagemagick": "recommended",
        },
        "runner": {
            "status": "planned",
            "windows_backend_candidates": ["HCS/Hyper-V", "WSL import fallback"],
            "docker_backend": "fallback-only",
        },
    }
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _vm_guest_protocol_template() -> str:
    return """# Metis VM Guest Protocol v1

This is the clean-room guest protocol for the Metis VM Runtime Pack.

## Roles

- `metisd`: long-running guest daemon.
- `metis-sandbox-helper`: per-command jail helper for cgroup, seccomp, mounts, filesystem policy, and network proxy rules.
- `metis-sdk-daemon`: optional artifact and document helper process.
- Host runner: starts the VM, mounts shared folders, sends JSON-RPC commands, and collects evidence.

## Transport

Preferred transport is host/guest vsock. A stdio fallback is allowed for WSL or local test mode.

Every frame is UTF-8 JSON Lines. The same frame contract works over stdio
for local smoke tests and over a future HCS/vsock/named-pipe transport.

```json
{"id":"1","method":"runtime.hello","params":{"protocol":"metis.vm.guest.v1"}}
{"id":"2","method":"session.mount","params":{"workspace":"/workspace","artifacts":"/artifacts"}}
{"id":"3","method":"process.run","params":{"command":"pytest -q","cwd":"/workspace","timeout_ms":120000}}
```

## Required Methods

- `runtime.hello`
- `runtime.status`
- `session.mount`
- `permission.request`
- `permission.response`
- `process.run`
- `process.cancel`
- `artifact.list`
- `artifact.collect`
- `diagnostics.export`
- `runtime.shutdown`

## Lifecycle

The guest writes lifecycle events as JSON Lines:

```json
{"schema":"metis.vm_direct.lifecycle.event.v1","state":"running","message":"starting process","data":{"run_id":"..."}}
```

Terminal states are `completed`, `failed`, `cancelled`, `timed_out`, and
`blocked`.

## Evidence Chain

Each `process.run` response must include:

- command
- cwd
- exit code
- duration
- stdout/stderr paths
- artifact paths
- policy applied
- verifier summary
"""


def _vm_guest_metisd_stub() -> str:
    return '''#!/usr/bin/env python3
"""Metis direct VM guest daemon.

This dependency-free daemon speaks UTF-8 JSON Lines over stdin/stdout. The
same protocol can be carried by stdio for local smoke tests, or by a future
HCS/vsock/named-pipe transport inside a real Metis VM.
"""
from __future__ import annotations

import fnmatch
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
import zipfile
from pathlib import Path


PROTOCOL = "metis.vm.guest.v1"
STATE = {
    "workspace": Path(os.environ.get("METIS_RUNTIME_WORKSPACE") or "/workspace"),
    "artifacts": Path(os.environ.get("METIS_RUNTIME_ARTIFACTS_DIR") or "/artifacts"),
    "diagnostics": Path(os.environ.get("METIS_RUNTIME_DIAGNOSTICS_DIR") or "/diagnostics"),
    "started_at": time.time(),
    "last_run": {},
    "cancel_requested": False,
}


def respond(message_id, result=None, error=None):
    payload = {"id": message_id, "result": result or {}, "error": error}
    sys.stdout.write(json.dumps(payload, ensure_ascii=False) + "\\n")
    sys.stdout.flush()


def lifecycle(state, message="", data=None):
    diagnostics = Path(STATE["diagnostics"])
    diagnostics.mkdir(parents=True, exist_ok=True)
    row = {
        "schema": "metis.vm_direct.lifecycle.event.v1",
        "at": int(time.time() * 1000),
        "state": state,
        "message": message,
        "data": data or {},
    }
    with (diagnostics / "lifecycle.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\\n")
    return row


def safe_path(raw, root):
    value = Path(str(raw or root)).expanduser()
    if not value.is_absolute():
        value = Path(root) / value
    resolved = value.resolve()
    root_resolved = Path(root).resolve()
    try:
        resolved.relative_to(root_resolved)
    except ValueError as exc:
        raise ValueError(f"path escapes allowed root: {resolved}") from exc
    return resolved


def rel(path, root):
    try:
        return str(Path(path).resolve().relative_to(Path(root).resolve()))
    except ValueError:
        return Path(path).name


def iter_files(root):
    root = Path(root)
    if not root.is_dir():
        return
    for current, dirs, files in os.walk(root):
        dirs[:] = [item for item in dirs if item not in {".git", ".metis", "node_modules", "__pycache__"}]
        for name in files:
            yield Path(current) / name


def list_artifacts(params=None):
    params = params or {}
    roots = [Path(STATE["artifacts"])]
    include_workspace = bool(params.get("include_workspace"))
    if include_workspace:
        roots.append(Path(STATE["workspace"]))
    patterns = params.get("patterns") if isinstance(params.get("patterns"), list) else []
    patterns = [str(item) for item in patterns if str(item).strip()] or [
        "*.png", "*.jpg", "*.jpeg", "*.webp", "*.pdf", "*.docx", "*.xlsx", "*.pptx",
        "*.csv", "*.tsv", "*.json", "*.md", "*.txt", "*.log",
    ]
    limit = max(1, int(params.get("limit") or 200))
    rows = []
    for root in roots:
        for path in iter_files(root):
            if len(rows) >= limit:
                return rows
            relative = rel(path, root)
            if not any(fnmatch.fnmatch(relative.replace("\\\\", "/"), pattern.replace("\\\\", "/")) or fnmatch.fnmatch(path.name, pattern) for pattern in patterns):
                continue
            try:
                size = path.stat().st_size
            except OSError:
                continue
            rows.append({
                "path": str(path),
                "relative_path": relative,
                "root": str(root),
                "size": size,
            })
    return rows


def collect_artifacts(params):
    workspace = Path(STATE["workspace"])
    artifacts = Path(STATE["artifacts"])
    collected = artifacts / "collected"
    collected.mkdir(parents=True, exist_ok=True)
    patterns = params.get("patterns") if isinstance(params.get("patterns"), list) else []
    patterns = [str(item) for item in patterns if str(item).strip()] or ["*.png", "*.pdf", "*.docx", "*.json", "*.md", "*.txt", "*.log"]
    max_files = max(1, int(params.get("max_files") or 200))
    max_bytes = max(1, int(params.get("max_bytes_per_file") or 20 * 1024 * 1024))
    copied = []
    for path in iter_files(workspace):
        if len(copied) >= max_files:
            break
        relative = rel(path, workspace)
        if not any(fnmatch.fnmatch(relative.replace("\\\\", "/"), pattern.replace("\\\\", "/")) or fnmatch.fnmatch(path.name, pattern) for pattern in patterns):
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > max_bytes:
            continue
        target = collected / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, target)
        copied.append({"source": str(path), "path": str(target), "relative_path": relative, "size": size})
    lifecycle("collecting_artifacts", "collected workspace artifacts", {"count": len(copied)})
    return {"ok": True, "copied": copied, "artifacts": list_artifacts({})}


def run_process(params):
    command = str(params.get("command") or "").strip()
    workspace = Path(STATE["workspace"])
    cwd = safe_path(params.get("cwd") or workspace, workspace)
    timeout_ms = int(params.get("timeout_ms") or 120000)
    started = time.time()
    if not command:
        return {"ok": False, "error": "command is required"}
    diagnostics = Path(STATE["diagnostics"])
    diagnostics.mkdir(parents=True, exist_ok=True)
    run_id = f"guest_run_{int(started * 1000)}_{uuid.uuid4().hex[:8]}"
    run_dir = diagnostics / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.update({
        "METIS_RUNTIME_WORKSPACE": str(workspace),
        "METIS_RUNTIME_ARTIFACTS_DIR": str(STATE["artifacts"]),
        "METIS_RUNTIME_DIAGNOSTICS_DIR": str(diagnostics),
        "METIS_RUNTIME_BACKEND": "metis_direct_vm",
    })
    lifecycle("running", "starting process", {"run_id": run_id, "command": command, "cwd": str(cwd)})
    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            env=env,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(1, timeout_ms // 1000),
            check=False,
        )
        timed_out = False
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        returncode = proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = ((exc.stderr if isinstance(exc.stderr, str) else "") + f"\\nTimeout after {max(1, timeout_ms // 1000)}s").strip()
        returncode = None
    (run_dir / "stdout.txt").write_text(stdout, encoding="utf-8", errors="replace")
    (run_dir / "stderr.txt").write_text(stderr, encoding="utf-8", errors="replace")
    duration = int((time.time() - started) * 1000)
    result = {
        "ok": returncode == 0 and not timed_out,
        "run_id": run_id,
        "command": command,
        "cwd": str(cwd),
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_ms": duration,
        "stdout": stdout,
        "stderr": stderr,
        "stdout_path": str(run_dir / "stdout.txt"),
        "stderr_path": str(run_dir / "stderr.txt"),
        "artifacts": list_artifacts({}),
        "backend": "metis-direct-vm-jsonl",
    }
    STATE["last_run"] = result
    lifecycle("completed" if result["ok"] else "failed", "process finished", {"run_id": run_id, "returncode": returncode, "timed_out": timed_out})
    return result


def mount_session(params):
    workspace = Path(str(params.get("workspace") or STATE["workspace"])).resolve()
    artifacts = Path(str(params.get("artifacts") or STATE["artifacts"])).resolve()
    diagnostics = Path(str(params.get("diagnostics") or STATE["diagnostics"])).resolve()
    workspace.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)
    diagnostics.mkdir(parents=True, exist_ok=True)
    STATE["workspace"] = workspace
    STATE["artifacts"] = artifacts
    STATE["diagnostics"] = diagnostics
    lifecycle("mounting_session", "session paths mounted", {"workspace": str(workspace), "artifacts": str(artifacts), "diagnostics": str(diagnostics)})
    return {
        "ok": True,
        "workspace": str(workspace),
        "artifacts": str(artifacts),
        "diagnostics": str(diagnostics),
    }


def export_diagnostics(params=None):
    diagnostics = Path(STATE["diagnostics"])
    artifacts = Path(STATE["artifacts"])
    diagnostics.mkdir(parents=True, exist_ok=True)
    lifecycle("exporting_diagnostics", "exporting diagnostics", {})
    summary = {
        "schema": "metis.vm_direct.diagnostics.v1",
        "protocol": PROTOCOL,
        "started_at": STATE["started_at"],
        "exported_at": time.time(),
        "workspace": str(STATE["workspace"]),
        "artifacts": str(artifacts),
        "diagnostics": str(diagnostics),
        "last_run": STATE.get("last_run") or {},
        "artifacts_list": list_artifacts({}),
    }
    summary_path = diagnostics / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    zip_path = diagnostics / "metis-direct-vm-diagnostics.zip"
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in iter_files(diagnostics):
            if path == zip_path:
                continue
            archive.write(path, str(path.relative_to(diagnostics)))
        for item in list_artifacts({}):
            path = Path(str(item.get("path") or ""))
            if path.is_file():
                archive.write(path, f"artifacts/{item.get('relative_path') or path.name}")
    return {"ok": True, "summary_path": str(summary_path), "diagnostics_zip": str(zip_path)}


def handle(message):
    method = str(message.get("method") or "")
    params = message.get("params") if isinstance(message.get("params"), dict) else {}
    if method == "runtime.hello":
        lifecycle("handshake", "runtime hello", {"pid": os.getpid()})
        return {"ok": True, "protocol": PROTOCOL, "pid": os.getpid(), "methods": [
            "runtime.hello", "runtime.status", "session.mount", "process.run",
            "process.cancel", "artifact.list", "artifact.collect",
            "diagnostics.export", "runtime.shutdown",
        ]}
    if method == "runtime.status":
        return {
            "ok": True,
            "protocol": PROTOCOL,
            "cwd": os.getcwd(),
            "workspace": str(STATE["workspace"]),
            "artifacts": str(STATE["artifacts"]),
            "diagnostics": str(STATE["diagnostics"]),
            "last_run": STATE.get("last_run") or {},
        }
    if method == "session.mount":
        return mount_session(params)
    if method == "process.run":
        return run_process(params)
    if method == "process.cancel":
        STATE["cancel_requested"] = True
        lifecycle("cancelled", "cancel requested", {})
        return {"ok": True, "cancel_requested": True}
    if method == "artifact.list":
        return {"ok": True, "artifacts": list_artifacts(params)}
    if method == "artifact.collect":
        return collect_artifacts(params)
    if method == "diagnostics.export":
        return export_diagnostics(params)
    if method == "runtime.shutdown":
        lifecycle("shutting_down", "shutdown requested", {})
        return {"ok": True, "shutdown": True}
    return {"ok": False, "error": f"unknown method: {method}"}


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            result = handle(message)
            respond(message.get("id"), result=result)
            if message.get("method") == "runtime.shutdown":
                return 0
        except Exception as exc:  # pragma: no cover - guest safety net
            respond(None, error={"type": type(exc).__name__, "message": str(exc)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
'''


def _vm_sandbox_helper_template() -> str:
    return """# Metis Sandbox Helper Blueprint

The real helper should be a small native or Go/Rust binary inside the guest.

Expected responsibilities:

- create a per-command cgroup
- apply CPU, memory, pid, and timeout limits
- apply filesystem write policy
- deny delete/rename when requested by host policy
- route network through host-approved proxy only
- emit a policy receipt into the evidence chain

The current scaffold only records the contract. It does not provide isolation.
"""


def _vm_host_runner_template() -> str:
    return """# Metis VM Host Runner Blueprint

The host runner will eventually:

1. verify `metis-vm-pack.json`
2. verify checksums/signatures for boot assets
3. start the VM through a Windows virtualization backend
4. mount workspace, artifacts, uploads, and permission channels
5. connect to `metisd`
6. execute `process.run`
7. collect artifacts, logs, and diagnostics
8. shut down or recycle the VM

The first implementation may use a WSL-import fallback. The product goal is a
Claude-style VM pack with Metis-owned assets and stable host/guest protocol.
"""


def _vm_channels_template() -> str:
    return """# Metis VM Shared Channels

Required shared channel layout:

```text
shared/
  .metis-perm-req/
  .metis-perm-resp/
  uploads/
  outputs/
  diagnostics/
  workspace/
```

The guest must never read host paths directly. The host runner decides what is
mounted into `shared/workspace` and what gets copied back to the project.
"""


def _vm_rootfs_builder_readme() -> str:
    return """# Metis Rootfs Builder Blueprint

This directory defines the first practical VM-pack build path:

1. Create a Metis-owned Ubuntu/Debian root filesystem.
2. Install the runtime tools listed in `install-runtime-tools.sh`.
3. Add `guest/metisd.py` as `/usr/local/bin/metisd`.
4. Add future native `metis-sandbox-helper`.
5. Export the filesystem as one of:
   - `rootfs.tar`
   - `rootfs.tar.gz`
   - `rootfs.tar.zst`
   - `rootfs.vhdx`
6. Register it with `metis_rootfs_asset_register` so SHA256 is written to
   `metis-vm-pack.json`.
7. Import it with `metis_wsl_runtime_import`.

The first executable runner is WSL import because it is available on many
Windows machines and does not require Docker. The later product runner can use
HCS/Hyper-V directly while keeping the same guest protocol.
"""


def _vm_rootfs_install_tools_script() -> str:
    return """#!/usr/bin/env bash
set -euo pipefail

PROFILE="${METIS_ROOTFS_PROFILE:-standard}"
export DEBIAN_FRONTEND=noninteractive

APT_PACKAGES=(
  ca-certificates \\
  curl \\
  git \\
  jq \\
  python3 \\
  python3-pip \\
  ripgrep \\
  poppler-utils
)

if [ "$PROFILE" = "office" ]; then
  APT_PACKAGES+=(
    libreoffice \\
    imagemagick
  )
fi

apt-get -o Acquire::Retries=5 update
apt-get -o Acquire::Retries=5 install -y --no-install-recommends "${APT_PACKAGES[@]}"
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get -o Acquire::Retries=5 install -y --no-install-recommends nodejs

python3 -m pip install --no-cache-dir --retries 5 --timeout 60 \\
  magika \\
  markitdown \\
  pdfplumber \\
  pypdf \\
  reportlab \\
  python-docx \\
  openpyxl

npm config set fetch-retries 5
npm config set fetch-retry-mintimeout 20000
npm config set fetch-retry-maxtimeout 120000
npm install -g \\
  typescript \\
  ts-node \\
  tsx \\
  pdf-lib \\
  pdfjs-dist \\
  pptxgenjs

install -d /usr/local/bin
install -d /etc/metis
install -d /workspace /artifacts /diagnostics /uploads /outputs
install -d /mnt/.metisfs-root/shared/workspace
install -d /mnt/.metisfs-root/shared/artifacts
install -d /mnt/.metisfs-root/shared/diagnostics
install -d /mnt/.metisfs-root/shared/uploads
install -d /mnt/.metisfs-root/shared/outputs
install -d /mnt/.metisfs-root/shared/.metis-perm-req
install -d /mnt/.metisfs-root/shared/.metis-perm-resp
"""


def _reference_vm_bundle_candidates() -> List[Path]:
    candidates: List[Path] = []
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        candidates.append(
            Path(local_app)
            / "Packages"
            / "Claude_pzs8sxrjxfjjc"
            / "LocalCache"
            / "Roaming"
            / "Claude"
            / "vm_bundles"
            / "claudevm.bundle"
        )
    candidates.append(Path("E:/ClaudeCode/cache/vm_bundles/claudevm.bundle"))
    return _dedupe_paths(candidates)


def _should_include_reference_vm_bundles(bundle_path: str = "") -> bool:
    if os.environ.get("METIS_VM_DISCOVER_REFERENCE_BUNDLES", "").strip().lower() in {"1", "true", "yes"}:
        return True
    return "claude" in str(bundle_path or "").lower()


def _dedupe_paths(paths: Iterable[Path]) -> List[Path]:
    out: List[Path] = []
    seen: set[str] = set()
    for path in paths:
        key = str(path).lower()
        if key and key not in seen:
            seen.add(key)
            out.append(path)
    return out


def _inspect_vm_bundle_path(path: Path, *, usage: str) -> Dict[str, Any]:
    exists = path.exists()
    files = []
    total_bytes = 0
    manifest = _read_vm_pack_manifest(path / VM_MANIFEST_NAME) if exists and path.is_dir() else {}
    manifest_data = manifest.get("data") if isinstance(manifest.get("data"), dict) else {}
    manifest_is_metis = (
        manifest.get("ok") is True
        and manifest_data.get("schema") == VM_PACK_MANIFEST_SCHEMA
        and str(manifest_data.get("owner") or "").lower() == "metis"
    )
    reference_adoption = manifest_data.get("reference_adoption")
    reference_only = bool(reference_adoption.get("reference_only")) if isinstance(reference_adoption, dict) else False
    metis_owned = manifest_is_metis and not reference_only
    if exists and path.is_dir():
        for name in (*VM_REQUIRED_FILES, *VM_OPTIONAL_FILES):
            item = path / name
            size = item.stat().st_size if item.exists() and item.is_file() else 0
            total_bytes += size
            origin_marker = path / f".{name}.origin"
            files.append(
                {
                    "name": name,
                    "exists": item.exists(),
                    "size_bytes": size,
                    "origin": origin_marker.read_text(encoding="utf-8", errors="replace").strip()
                    if origin_marker.exists()
                    else "",
                }
            )
    missing = [name for name in VM_REQUIRED_FILES if not (path / name).is_file()]
    ready = exists and path.is_dir() and not missing
    direct_runner = _inspect_direct_vm_runner(path) if exists and path.is_dir() else {}
    hcs_starter = _inspect_hcs_starter(path) if exists and path.is_dir() else {}
    rootfs_boot_verifier = _inspect_rootfs_boot_verifier(path) if exists and path.is_dir() else {}
    guest_handshake = _inspect_guest_handshake(path) if exists and path.is_dir() else {}
    hcs_direct_ready = bool(guest_handshake.get("runner_ready"))
    guest_protocol_ready = bool(
        ready
        and metis_owned
        and direct_runner.get("guest_protocol_ready")
        and (path / "guest" / "metisd.py").is_file()
    )
    runner_ready = bool(hcs_direct_ready or guest_protocol_ready)
    if hcs_direct_ready:
        runner_transport = "hcs-vsock-jsonl"
    elif guest_protocol_ready:
        runner_transport = "jsonl-stdio"
    else:
        runner_transport = ""
    return {
        "path": str(path),
        "exists": exists,
        "is_dir": path.is_dir() if exists else False,
        "usage": usage,
        "ready": ready,
        "runner_ready": runner_ready,
        "hcs_direct_ready": hcs_direct_ready,
        "guest_protocol_ready": guest_protocol_ready,
        "runner_transport": runner_transport,
        "direct_runner": direct_runner,
        "hcs_starter": hcs_starter,
        "rootfs_boot_verifier": rootfs_boot_verifier,
        "guest_handshake": guest_handshake,
        "metis_owned": metis_owned,
        "manifest": manifest,
        "required_files": list(VM_REQUIRED_FILES),
        "missing_required": missing,
        "total_known_bytes": total_bytes,
        "files": files,
    }


def _build_reference_adoption_plan(
    *,
    reference: Path,
    target: Path,
    reference_status: Dict[str, Any],
    hash_assets: bool,
) -> Dict[str, Any]:
    files: List[Dict[str, Any]] = []
    for raw in reference_status.get("files") or []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "")
        src = reference / name
        sha = _sha256_file(src) if hash_assets and src.is_file() else ""
        compressed = src.with_name(f"{src.name}.zst")
        files.append(
            {
                "name": name,
                "required": name in VM_REQUIRED_FILES,
                "exists": bool(raw.get("exists")),
                "size_bytes": int(raw.get("size_bytes") or 0),
                "origin": str(raw.get("origin") or ""),
                "sha256": sha,
                "compressed_counterpart": compressed.name if compressed.is_file() else "",
                "target_path": str(target / name),
                "copy_planned": bool(raw.get("exists")),
            }
        )
    missing_required = [item["name"] for item in files if item["required"] and not item["exists"]]
    total_bytes = sum(int(item.get("size_bytes") or 0) for item in files)
    return {
        "schema": VM_PACK_REFERENCE_ADOPT_SCHEMA,
        "reference_bundle_path": str(reference),
        "target_bundle_path": str(target),
        "reference_only": True,
        "ready_shape": not missing_required,
        "missing_required": missing_required,
        "required_files": list(VM_REQUIRED_FILES),
        "optional_files": list(VM_OPTIONAL_FILES),
        "total_known_bytes": total_bytes,
        "hash_assets": bool(hash_assets),
        "files": files,
        "notes": [
            "This plan records a Claude-style VM bundle shape for Metis adaptation.",
            "Manifest-only adoption does not copy rootfs/kernel/initrd assets.",
            "Reference assets are not treated as clean-room Metis-owned assets.",
            "Metis strict runtime still requires a verified Metis-owned rootfs or an explicitly imported runtime.",
        ],
    }


def _copy_reference_bundle_assets(reference: Path, target: Path, *, force: bool) -> List[Dict[str, Any]]:
    copied: List[Dict[str, Any]] = []
    for name in (*VM_REQUIRED_FILES, *VM_OPTIONAL_FILES):
        src = reference / name
        if not src.is_file():
            continue
        dest = target / name
        if dest.exists() and not force:
            copied.append(
                {
                    "name": name,
                    "source": str(src),
                    "target": str(dest),
                    "copied": False,
                    "reason": "target exists; pass force=true to overwrite",
                }
            )
            continue
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dest)
        origin_src = reference / f".{name}.origin"
        if origin_src.is_file():
            shutil.copy2(origin_src, target / origin_src.name)
        copied.append(
            {
                "name": name,
                "source": str(src),
                "target": str(dest),
                "copied": True,
                "size_bytes": dest.stat().st_size,
            }
        )
    return copied


def _write_reference_adoption_manifest(
    target: Path,
    adoption: Dict[str, Any],
    *,
    copied_assets: List[Dict[str, Any]],
) -> Dict[str, Any]:
    data = _load_vm_manifest_data(target)
    if not data:
        data = json.loads(_vm_pack_manifest_template())
    data["reference_adoption"] = {
        "schema": VM_PACK_REFERENCE_ADOPT_SCHEMA,
        "reference_only": True,
        "reference_bundle_path": adoption.get("reference_bundle_path"),
        "plan_path": "reference-adoption-plan.json",
        "copied_asset_count": len([item for item in copied_assets if item.get("copied")]),
        "total_known_bytes": adoption.get("total_known_bytes"),
        "ready_shape": adoption.get("ready_shape"),
    }
    assets = data.setdefault("assets", {})
    if not isinstance(assets, dict):
        assets = {}
        data["assets"] = assets
    rootfs_entry = next(
        (
            item
            for item in adoption.get("files", [])
            if isinstance(item, dict) and item.get("name") == "rootfs.vhdx" and item.get("exists")
        ),
        {},
    )
    if rootfs_entry:
        assets["rootfs"] = {
            "path": "rootfs.vhdx",
            "sha256": str(rootfs_entry.get("sha256") or ""),
            "size_bytes": int(rootfs_entry.get("size_bytes") or 0),
            "import_mode": "vhd",
            "source_url": str(rootfs_entry.get("origin") or ""),
            "reference_only": True,
            "registered_at": time.time(),
        }
    _write_vm_manifest_data(target, data)
    return data


def _normalize_runtime_bundle_version(value: str) -> str:
    text = str(value or "").strip()
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.+-]{0,63}", text):
        return text
    return "0.1.0-local"


def _normalize_runtime_bundle_channel(value: str) -> str:
    text = str(value or "local").strip().lower()
    if re.fullmatch(r"[a-z0-9][a-z0-9_.-]{0,31}", text):
        return text
    return "local"


def _runtime_bundle_would_write(bundle: Path) -> List[Dict[str, Any]]:
    names = [
        VM_MANIFEST_NAME,
        RUNTIME_BUNDLE_MANIFEST_NAME,
        RUNTIME_BUNDLE_LATEST_NAME,
        "install-metis-runtime.ps1",
        "relocate-metis-runtime-pack.ps1",
        "smoke-metis-runtime.ps1",
        "README.md",
        "origins/README.md",
    ]
    return [
        {
            "relative_path": name,
            "path": str(bundle / name),
            "exists": (bundle / name).exists(),
        }
        for name in names
    ]


def _ensure_runtime_bundle_scaffold(bundle: Path, *, force: bool) -> List[Dict[str, Any]]:
    written: List[Dict[str, Any]] = []
    if force or not (bundle / VM_MANIFEST_NAME).is_file():
        written.extend(_write_vm_pack_scaffold_files(bundle))
    else:
        for rel, content in {
            "guest/PROTOCOL.md": _vm_guest_protocol_template(),
            "guest/metisd.py": _vm_guest_metisd_stub(),
            "guest/sandbox-helper.md": _vm_sandbox_helper_template(),
            "host/README.md": _vm_host_runner_template(),
            "channels/README.md": _vm_channels_template(),
            "builder/README.md": _vm_rootfs_builder_readme(),
            "builder/install-runtime-tools.sh": _vm_rootfs_install_tools_script(),
        }.items():
            path = bundle / rel
            if path.is_file() and not force:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
            written.append({"relative_path": rel, "path": str(path), "size_bytes": path.stat().st_size})
    return written


def _detect_runtime_bundle_rootfs(source_root: Path, bundle: Path, rootfs_path: str = "") -> Dict[str, Any]:
    explicit = str(rootfs_path or "").strip()
    candidates = _metis_rootfs_asset_candidates(source_root, rootfs_path=explicit)
    selected = _select_rootfs_asset_candidate(candidates, explicit_path=explicit)
    verification: Dict[str, Any] = {}
    if selected.get("exists"):
        selected_path = Path(str(selected.get("path") or ""))
        verification = _verify_rootfs_asset(
            selected_path,
            bundle_dir=bundle,
            require_expected=True,
        )
    return {
        "selected_rootfs": selected,
        "verification": verification,
        "candidates": candidates,
    }


def _build_runtime_bundle_manifest(
    *,
    source_root: Path,
    bundle_dir: Path,
    version: str,
    channel: str,
    rootfs_status: Dict[str, Any],
    registration: Dict[str, Any],
) -> Dict[str, Any]:
    selected = rootfs_status.get("selected_rootfs") if isinstance(rootfs_status.get("selected_rootfs"), dict) else {}
    verification = rootfs_status.get("verification") if isinstance(rootfs_status.get("verification"), dict) else {}
    rootfs_text = str(selected.get("path") or registration.get("rootfs_path") or "").strip()
    rootfs_path = Path(rootfs_text).resolve(strict=False) if rootfs_text else None
    rootfs_rel = (
        _relative_to(rootfs_path, bundle_dir)
        if rootfs_path is not None and _is_relative_to(rootfs_path, bundle_dir)
        else str(rootfs_path)
        if rootfs_path is not None
        else ""
    )
    import_mode = str(
        selected.get("import_mode")
        or (_inspect_rootfs_import_asset(rootfs_path).get("import_mode") if rootfs_path is not None else "tar")
    )
    rootfs_asset = {
        "path": rootfs_rel,
        "absolute_path": str(rootfs_path) if rootfs_path is not None else "",
        "exists": bool(rootfs_path is not None and rootfs_path.is_file()),
        "import_mode": import_mode,
        "sha256": str(verification.get("sha256") or ""),
        "expected_sha256": str(verification.get("expected_sha256") or ""),
        "size_bytes": int(verification.get("size_bytes") or selected.get("size_bytes") or 0),
        "verified": bool(verification.get("verified")),
        "checksum_verified": bool(verification.get("checksum_verified")),
        "source_url": str(((verification.get("manifest_entry") or {}) if isinstance(verification.get("manifest_entry"), dict) else {}).get("source_url") or registration.get("source_url") or ""),
    }
    ready = bool(rootfs_asset["exists"] and rootfs_asset["verified"])
    bundle_id_seed = "|".join(
        [
            METIS_RUNTIME_BUNDLE_MANIFEST_SCHEMA,
            version,
            channel,
            rootfs_asset["sha256"],
            rootfs_asset["path"],
        ]
    )
    bundle_id = hashlib.sha256(bundle_id_seed.encode("utf-8")).hexdigest()[:32]
    return {
        "schema": METIS_RUNTIME_BUNDLE_MANIFEST_SCHEMA,
        "bundle_id": bundle_id,
        "owner": "metis",
        "name": "metis-runtime",
        "version": version,
        "channel": channel,
        "created_at": time.time(),
        "bundle_path": str(bundle_dir),
        "source_root": str(source_root),
        "kind": "metis-owned-wsl-import-v1",
        "ready": ready,
        "assets": {
            "rootfs": rootfs_asset,
        },
        "capabilities": {
            "python": True,
            "node": True,
            "git": True,
            "ripgrep": True,
            "pdf": True,
            "docx": True,
            "artifacts": True,
            "wsl_import": True,
            "hcs_vm_runner": False,
        },
        "security": {
            "filesystem_default": "workspace-copy",
            "network_default": "deny-unless-task-allows",
            "third_party_reference_assets": False,
            "requires_checksum": True,
        },
        "install": {
            "script": "install-metis-runtime.ps1",
            "distro_name": DEFAULT_METIS_WSL_DISTRO,
            "default_install_dir": "%LOCALAPPDATA%/Metis/runtime/wsl/MetisRuntime",
            "import_mode": import_mode,
        },
        "verification": {
            "rootfs": verification,
        },
        "notes": [
            "This is a Metis-owned runtime bundle manifest.",
            "The first runnable backend is WSL import; HCS/Hyper-V direct runner remains a later layer.",
            "Do not mix reference-only Claude assets into this bundle unless explicitly marked reference_only.",
        ],
    }


def _runtime_bundle_latest_manifest(manifest: Dict[str, Any]) -> Dict[str, Any]:
    rootfs = (manifest.get("assets") or {}).get("rootfs", {}) if isinstance(manifest.get("assets"), dict) else {}
    return {
        "schema": "metis.runtime_bundle.latest.v1",
        "version": manifest.get("version"),
        "channel": manifest.get("channel"),
        "bundle_id": manifest.get("bundle_id"),
        "ready": bool(manifest.get("ready")),
        "rootfs": {
            "path": rootfs.get("path") if isinstance(rootfs, dict) else "",
            "sha256": rootfs.get("sha256") if isinstance(rootfs, dict) else "",
            "size_bytes": rootfs.get("size_bytes") if isinstance(rootfs, dict) else 0,
            "import_mode": rootfs.get("import_mode") if isinstance(rootfs, dict) else "",
        },
        "manifest": RUNTIME_BUNDLE_MANIFEST_NAME,
        "generated_at": time.time(),
    }


def _runtime_bundle_rootfs_abs_path(bundle: Path, manifest: Dict[str, Any]) -> Optional[Path]:
    assets = manifest.get("assets") if isinstance(manifest.get("assets"), dict) else {}
    rootfs = assets.get("rootfs") if isinstance(assets.get("rootfs"), dict) else {}
    raw = str(rootfs.get("absolute_path") or rootfs.get("path") or "").strip()
    if not raw:
        return None
    path = Path(raw)
    if not path.is_absolute():
        path = bundle / path
    return path.resolve(strict=False)


def _write_runtime_bundle_origin_markers(bundle: Path, manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    written: List[Dict[str, Any]] = []
    origins_dir = bundle / "origins"
    origins_dir.mkdir(parents=True, exist_ok=True)
    readme = origins_dir / "README.md"
    readme.write_text(
        "Metis runtime origin metadata.\n\n"
        "Dot-origin files next to assets contain the registered checksum or bundle origin id.\n"
        "JSON files in this directory contain the detailed Metis-owned asset provenance.\n",
        encoding="utf-8",
        newline="\n",
    )
    written.append({"relative_path": "origins/README.md", "path": str(readme), "size_bytes": readme.stat().st_size})
    assets = manifest.get("assets") if isinstance(manifest.get("assets"), dict) else {}
    for asset_name, asset in assets.items():
        if not isinstance(asset, dict):
            continue
        path_text = str(asset.get("absolute_path") or asset.get("path") or "")
        if not path_text:
            continue
        path = Path(path_text)
        if not path.is_absolute():
            path = bundle / path
        if not path.name:
            continue
        checksum = str(asset.get("sha256") or manifest.get("bundle_id") or "")
        dot_origin = bundle / f".{path.name}.origin"
        dot_origin.write_text(checksum + "\n", encoding="utf-8", newline="\n")
        written.append({"relative_path": dot_origin.name, "path": str(dot_origin), "size_bytes": dot_origin.stat().st_size})
        detail = {
            "schema": "metis.runtime_bundle.origin.v1",
            "asset": asset_name,
            "path": _relative_to(path, bundle) if _is_relative_to(path, bundle) else str(path),
            "sha256": checksum,
            "bundle_id": manifest.get("bundle_id"),
            "version": manifest.get("version"),
            "channel": manifest.get("channel"),
            "owner": "metis",
            "reference_only": False,
            "created_at": time.time(),
        }
        detail_path = origins_dir / f"{path.name}.origin.json"
        detail_path.write_text(json.dumps(detail, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        written.append({"relative_path": _relative_to(detail_path, bundle), "path": str(detail_path), "size_bytes": detail_path.stat().st_size})
    return written


def _write_runtime_bundle_scripts(bundle: Path, manifest: Dict[str, Any]) -> List[Dict[str, Any]]:
    files = {
        "install-metis-runtime.ps1": _runtime_bundle_install_script(manifest),
        "relocate-metis-runtime-pack.ps1": _runtime_bundle_relocate_script(),
        "smoke-metis-runtime.ps1": _runtime_bundle_smoke_script(),
        "README.md": _runtime_bundle_readme(manifest),
    }
    written: List[Dict[str, Any]] = []
    for rel, content in files.items():
        path = bundle / rel
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append({"relative_path": rel, "path": str(path), "size_bytes": path.stat().st_size})
    return written


def _upsert_runtime_bundle_vm_manifest(bundle: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    data = _load_vm_manifest_data(bundle)
    if not data:
        data = json.loads(_vm_pack_manifest_template())
    runtime = {
        "schema": METIS_RUNTIME_BUNDLE_MANIFEST_SCHEMA,
        "bundle_id": manifest.get("bundle_id"),
        "version": manifest.get("version"),
        "channel": manifest.get("channel"),
        "manifest": RUNTIME_BUNDLE_MANIFEST_NAME,
        "latest": RUNTIME_BUNDLE_LATEST_NAME,
        "ready": bool(manifest.get("ready")),
        "owner": "metis",
        "kind": manifest.get("kind"),
    }
    data["runtime_bundle"] = runtime
    reference_adoption = data.get("reference_adoption")
    if isinstance(reference_adoption, dict) and reference_adoption.get("reference_only"):
        data["runtime_bundle"]["blocked_by_reference_adoption"] = True
    _write_vm_manifest_data(bundle, data)
    return data


def _runtime_bundle_install_script(manifest: Dict[str, Any]) -> str:
    rootfs = (manifest.get("assets") or {}).get("rootfs", {}) if isinstance(manifest.get("assets"), dict) else {}
    rootfs_path = str(rootfs.get("path") or "rootfs.tar") if isinstance(rootfs, dict) else "rootfs.tar"
    import_mode = str(rootfs.get("import_mode") or "tar") if isinstance(rootfs, dict) else "tar"
    vhd_switch = " @('--vhd')" if import_mode == "vhd" else " @()"
    rootfs_literal = rootfs_path.replace("'", "''")
    return f"""param(
  [string]$DistroName = '{DEFAULT_METIS_WSL_DISTRO}',
  [string]$InstallDir = "$env:LOCALAPPDATA\\Metis\\runtime\\wsl\\{DEFAULT_METIS_WSL_DISTRO}",
  [int]$Version = 2,
  [switch]$AllowExisting
)

$ErrorActionPreference = 'Stop'
$Bundle = Split-Path -Parent $MyInvocation.MyCommand.Path
$RootfsValue = '{rootfs_literal}'
if ([System.IO.Path]::IsPathRooted($RootfsValue)) {{
  $Rootfs = $RootfsValue
}} else {{
  $Rootfs = Join-Path $Bundle $RootfsValue
}}
if (-not (Test-Path $Rootfs)) {{
  throw "Metis runtime rootfs asset not found: $Rootfs"
}}
$Wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if (-not $Wsl) {{
  throw "wsl.exe is required to install the Metis runtime bundle."
}}
$Existing = & $Wsl.Source --list --quiet 2>$null | Where-Object {{ $_ -eq $DistroName }}
if ($Existing -and -not $AllowExisting) {{
  Write-Host "Metis runtime distro already exists: $DistroName"
  exit 0
}}
New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
$VhdSwitch ={vhd_switch}
& $Wsl.Source --import $DistroName $InstallDir $Rootfs --version $Version @VhdSwitch
& $Wsl.Source -d $DistroName -- sh -lc "printf metis-runtime-ok"
"""


def _runtime_bundle_relocate_script() -> str:
    return """param(
  [string]$Destination
)

$ErrorActionPreference = 'Stop'
$Source = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not $Destination) {
  throw "Destination is required. Example: .\\relocate-metis-runtime-pack.ps1 -Destination E:\\MetisRuntime\\metisvm.bundle"
}
if ((Get-Process -Name 'Metis*' -ErrorAction SilentlyContinue)) {
  Write-Host "Close Metis before relocating the runtime bundle." -ForegroundColor Yellow
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Destination) | Out-Null
Copy-Item -Path $Source -Destination $Destination -Recurse -Force
Write-Host "Copied Metis runtime bundle to $Destination"
Write-Host "Update METIS_VM_BUNDLE_PATH or the Runtime settings page to point at the new bundle."
"""


def _runtime_bundle_smoke_script() -> str:
    return f"""param(
  [string]$DistroName = '{DEFAULT_METIS_WSL_DISTRO}'
)

$ErrorActionPreference = 'Stop'
$Wsl = Get-Command wsl.exe -ErrorAction SilentlyContinue
if (-not $Wsl) {{
  throw "wsl.exe is required for Metis runtime smoke."
}}
& $Wsl.Source -d $DistroName -- sh -lc "python3 --version && node --version 2>/dev/null || true && git --version && rg --version | head -n 1 && printf metis-runtime-smoke-ok"
"""


def _runtime_bundle_readme(manifest: Dict[str, Any]) -> str:
    return f"""# Metis Runtime Bundle

This directory is a Metis-owned runtime bundle.

- Schema: `{METIS_RUNTIME_BUNDLE_MANIFEST_SCHEMA}`
- Version: `{manifest.get("version")}`
- Channel: `{manifest.get("channel")}`
- Ready: `{bool(manifest.get("ready"))}`

## Files

- `{RUNTIME_BUNDLE_MANIFEST_NAME}`: full bundle manifest.
- `{RUNTIME_BUNDLE_LATEST_NAME}`: compact update/status manifest.
- `install-metis-runtime.ps1`: imports the verified rootfs as `{DEFAULT_METIS_WSL_DISTRO}`.
- `relocate-metis-runtime-pack.ps1`: copies this bundle to another disk.
- `smoke-metis-runtime.ps1`: verifies the imported runtime.
- `.rootfs.*.origin` and `origins/*.json`: Metis-owned asset provenance.

## Runtime Boundary

The first runnable backend is WSL import. The HCS/Hyper-V direct runner is a later layer that can reuse this manifest and rootfs provenance.
"""


def _read_json_object(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"failed to read JSON object from {path}: {type(exc).__name__}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return data


def _resolve_runtime_bundle_release_dir(source_root: Path, output_dir: str = "") -> Path:
    raw = str(output_dir or "").strip()
    if raw:
        return safe_path_for_write(raw).resolve(strict=False)
    return (source_root / ".metis" / "runtime-pack" / "releases").resolve(strict=False)


def _runtime_bundle_package_name(version: str, channel: str, *, include_rootfs: bool) -> str:
    suffix = "full" if include_rootfs else "metadata"
    return f"metis-runtime-{_safe_filename_part(version)}-{_safe_filename_part(channel)}-{suffix}.zip"


def _safe_filename_part(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    return text or "local"


def _runtime_bundle_package_files(
    bundle: Path,
    manifest: Dict[str, Any],
    *,
    include_rootfs: bool,
) -> List[Dict[str, Any]]:
    files: List[Dict[str, Any]] = []
    rootfs_paths = _runtime_bundle_rootfs_paths(bundle, manifest)
    releases_dir = (bundle / "releases").resolve(strict=False)
    for path in _iter_files(bundle):
        resolved = path.resolve(strict=False)
        if _is_relative_to(resolved, releases_dir):
            continue
        if not include_rootfs and any(resolved == item for item in rootfs_paths):
            continue
        rel = _relative_to(path, bundle)
        files.append(
            {
                "path": str(path),
                "relative_path": rel,
                "archive_path": f"{bundle.name}/{rel.replace(os.sep, '/')}",
                "size_bytes": path.stat().st_size,
                "sha256": _sha256_file(path),
            }
        )
    if include_rootfs:
        for path in rootfs_paths:
            if not path.is_file() or _is_relative_to(path, bundle):
                continue
            arcname = f"{bundle.name}/{path.name}"
            if any(item.get("archive_path") == arcname for item in files):
                continue
            files.append(
                {
                    "path": str(path),
                    "relative_path": path.name,
                    "archive_path": arcname,
                    "size_bytes": path.stat().st_size,
                    "sha256": _sha256_file(path),
                    "external_asset": True,
                }
            )
    files.sort(key=lambda item: str(item.get("archive_path") or ""))
    return files


def _runtime_bundle_rootfs_paths(bundle: Path, manifest: Dict[str, Any]) -> List[Path]:
    paths: List[Path] = []
    rootfs_abs = _runtime_bundle_rootfs_abs_path(bundle, manifest)
    if rootfs_abs is not None:
        paths.append(rootfs_abs.resolve(strict=False))
    for name in ROOTFS_IMPORT_ASSET_NAMES:
        candidate = (bundle / name).resolve(strict=False)
        if candidate.is_file():
            paths.append(candidate)
    return _dedupe_paths(paths)


def _runtime_bundle_release_manifest(
    *,
    bundle_manifest: Dict[str, Any],
    package_path: Path,
    package_sha256: str,
    files: List[Dict[str, Any]],
    include_rootfs: bool,
    version: str,
    channel: str,
) -> Dict[str, Any]:
    rootfs = (bundle_manifest.get("assets") or {}).get("rootfs", {}) if isinstance(bundle_manifest.get("assets"), dict) else {}
    return {
        "schema": METIS_RUNTIME_BUNDLE_PACKAGE_SCHEMA,
        "bundle_schema": bundle_manifest.get("schema"),
        "bundle_id": bundle_manifest.get("bundle_id"),
        "version": version,
        "channel": channel,
        "owner": "metis",
        "package": {
            "name": package_path.name,
            "path": str(package_path),
            "sha256": package_sha256,
            "size_bytes": package_path.stat().st_size,
            "include_rootfs": bool(include_rootfs),
        },
        "rootfs": {
            "path": rootfs.get("path") if isinstance(rootfs, dict) else "",
            "sha256": rootfs.get("sha256") if isinstance(rootfs, dict) else "",
            "size_bytes": rootfs.get("size_bytes") if isinstance(rootfs, dict) else 0,
            "import_mode": rootfs.get("import_mode") if isinstance(rootfs, dict) else "",
            "verified": bool(rootfs.get("verified")) if isinstance(rootfs, dict) else False,
        },
        "files": files,
        "file_count": len(files),
        "created_at": time.time(),
        "install": bundle_manifest.get("install") if isinstance(bundle_manifest.get("install"), dict) else {},
        "notes": [
            "This release manifest describes a Metis-owned runtime bundle package.",
            "The first runnable install path is WSL import via install-metis-runtime.ps1.",
            "HCS/Hyper-V direct VM runner assets are not required for this v1 package.",
        ],
    }


def _runtime_bundle_package_v2_name(version: str, channel: str, *, package_name: str = "") -> str:
    raw = str(package_name or "").strip()
    if raw:
        cleaned = _safe_filename_part(raw)
        return cleaned[:-4] if cleaned.lower().endswith(".zip") else cleaned
    return f"metis-runtime-bundle-v2-{_safe_filename_part(version)}-{_safe_filename_part(channel)}"


def _runtime_bundle_v2_asset_status(bundle: Path, *, include_sessiondata: bool) -> Dict[str, Any]:
    assets = _runtime_bundle_v2_asset_specs(bundle, include_sessiondata=include_sessiondata)
    rows: List[Dict[str, Any]] = []
    for spec in assets:
        path = Path(str(spec.get("path") or ""))
        row = dict(spec)
        row.update(
            {
                "exists": path.is_file(),
                "size_bytes": path.stat().st_size if path.is_file() else 0,
                "sha256": _sha256_file(path) if path.is_file() else "",
                "relative_path": _relative_to(path, bundle) if path.is_file() and _is_relative_to(path, bundle) else str(spec.get("relative_path") or path.name),
            }
        )
        rows.append(row)
    missing_required = [item["name"] for item in rows if item.get("required") and not item.get("exists")]
    compressed = bundle / "rootfs.vhdx.zst"
    return {
        "bundle_path": str(bundle),
        "ready": not missing_required,
        "missing_required": missing_required,
        "rootfs_compressed_exists": compressed.is_file(),
        "assets": rows,
    }


def _runtime_bundle_v2_asset_specs(bundle: Path, *, include_sessiondata: bool) -> List[Dict[str, Any]]:
    specs = [
        {"name": "vmlinuz", "path": str(bundle / "vmlinuz"), "required": True, "role": "kernel"},
        {"name": "initrd", "path": str(bundle / "initrd"), "required": True, "role": "initrd"},
        {"name": "rootfs.vhdx", "path": str(bundle / "rootfs.vhdx"), "required": True, "role": "rootfs-source"},
        {"name": "metis-bin.vhdx", "path": str(bundle / "metis-bin.vhdx"), "required": True, "role": "guest-tools"},
        {"name": VM_MANIFEST_NAME, "path": str(bundle / VM_MANIFEST_NAME), "required": True, "role": "manifest"},
    ]
    if include_sessiondata:
        specs.append({"name": "sessiondata.vhdx", "path": str(bundle / "sessiondata.vhdx"), "required": False, "role": "session-disk"})
    for name in (
        DIRECT_VM_ASSETS_MANIFEST_NAME,
        DIRECT_VM_RUNNER_MANIFEST_NAME,
        HCS_STARTER_MANIFEST_NAME,
        ROOTFS_BOOT_VERIFIER_MANIFEST_NAME,
        GUEST_HANDSHAKE_MANIFEST_NAME,
        ROOTFS_IMAGE_BUILDER_MANIFEST_NAME,
    ):
        specs.append({"name": name, "path": str(bundle / name), "required": False, "role": "metis-manifest"})
    return specs


def _detect_zstd_compressor() -> Dict[str, Any]:
    try:
        import zstandard  # type: ignore  # noqa: F401

        return {
            "available": True,
            "kind": "python-zstandard",
            "executable": "",
            "reason": "",
        }
    except Exception as exc:
        python_reason = f"{type(exc).__name__}: {exc}"
    zstd = shutil.which("zstd.exe") or shutil.which("zstd")
    if zstd:
        version = _quick_command([zstd, "--version"], timeout=8)
        return {
            "available": True,
            "kind": "zstd-cli",
            "executable": zstd,
            "version": _truncate(str(version.get("stdout") or version.get("stderr") or ""), 300),
            "reason": "",
        }
    return {
        "available": False,
        "kind": "",
        "executable": "",
        "reason": f"Neither python zstandard nor zstd.exe is available ({python_reason})",
    }


def _runtime_bundle_package_v2_plan(
    *,
    bundle: Path,
    release_dir: Path,
    package_path: Path,
    version: str,
    channel: str,
    asset_status: Dict[str, Any],
    compression: Dict[str, Any],
    include_sessiondata: bool,
) -> Dict[str, Any]:
    return {
        "schema": METIS_RUNTIME_BUNDLE_PACKAGE_V2_SCHEMA,
        "bundle_path": str(bundle),
        "release_dir": str(release_dir),
        "package_path": str(package_path),
        "version": version,
        "channel": channel,
        "include_sessiondata": bool(include_sessiondata),
        "asset_status": asset_status,
        "compression": compression,
        "would_write": [
            str(release_dir / "rootfs.vhdx.zst"),
            str(release_dir / "runtime-bundle-v2-manifest.json"),
            str(release_dir / "SHA256SUMS.txt"),
            str(release_dir / "install-metis-runtime-bundle-v2.ps1"),
            str(release_dir / "verify-metis-runtime-bundle-v2.ps1"),
            str(release_dir / "README.md"),
            str(package_path),
            str(package_path.with_suffix(package_path.suffix + ".sha256")),
        ],
        "install_requires": {
            "docker": False,
            "wsl": False,
            "zstd_for_extract": True,
            "note": "End users need an extractor for rootfs.vhdx.zst, not Docker/WSL build tooling.",
        },
    }


def _prepare_runtime_bundle_v2_release_files(
    *,
    bundle: Path,
    release_dir: Path,
    version: str,
    channel: str,
    compression: Dict[str, Any],
    include_sessiondata: bool,
    force: bool,
) -> Dict[str, Any]:
    rootfs = bundle / "rootfs.vhdx"
    compressed_rootfs = release_dir / "rootfs.vhdx.zst"
    bundled_compressed_rootfs = bundle / "rootfs.vhdx.zst"
    if compressed_rootfs.exists() and not force:
        return json.loads(
            _json_error(
                f"compressed rootfs already exists: {compressed_rootfs}",
                code="METIS_RUNTIME_BUNDLE_V2_ROOTFS_ZST_EXISTS",
            )
        )
    if bundled_compressed_rootfs.is_file() and not force:
        release_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy2(bundled_compressed_rootfs, compressed_rootfs)
        compression_result = {
            "ok": True,
            "kind": "precompressed",
            "source": str(bundled_compressed_rootfs),
            "target": str(compressed_rootfs),
            "source_size_bytes": rootfs.stat().st_size if rootfs.is_file() else 0,
            "target_size_bytes": compressed_rootfs.stat().st_size,
            "duration_ms": 0,
        }
    elif not compression.get("available"):
        return json.loads(
            _json_error(
                str(compression.get("reason") or "zstd compressor unavailable"),
                code="METIS_RUNTIME_BUNDLE_V2_ZSTD_UNAVAILABLE",
            )
        )
    else:
        compression_result = _compress_file_zst(rootfs, compressed_rootfs, compression=compression, force=force)
        if not compression_result.get("ok"):
            return json.loads(
                _json_error(
                    str(compression_result.get("error") or "rootfs compression failed"),
                    code=str(compression_result.get("code") or "METIS_RUNTIME_BUNDLE_V2_COMPRESS_FAILED"),
                )
            )
    asset_files = _runtime_bundle_v2_release_asset_files(
        bundle=bundle,
        release_dir=release_dir,
        compressed_rootfs=compressed_rootfs,
        include_sessiondata=include_sessiondata,
    )
    release_manifest = _runtime_bundle_v2_release_manifest(
        bundle=bundle,
        version=version,
        channel=channel,
        files=asset_files,
        compression=compression_result,
    )
    manifest_path = release_dir / "runtime-bundle-v2-manifest.json"
    manifest_path.write_text(
        json.dumps(release_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    sha_path = release_dir / "SHA256SUMS.txt"
    sha_path.write_text(_runtime_bundle_v2_sha256s(asset_files, manifest_path), encoding="utf-8", newline="\n")
    scripts = {
        "install-metis-runtime-bundle-v2.ps1": _runtime_bundle_v2_install_script(),
        "verify-metis-runtime-bundle-v2.ps1": _runtime_bundle_v2_verify_script(),
        "README.md": _runtime_bundle_v2_readme(version=version, channel=channel),
    }
    script_files: List[Dict[str, Any]] = []
    for name, content in scripts.items():
        path = release_dir / name
        path.write_text(content, encoding="utf-8", newline="\n")
        script_files.append(_runtime_bundle_v2_file_row(path, release_dir=release_dir, role="script"))
    manifest_row = _runtime_bundle_v2_file_row(manifest_path, release_dir=release_dir, role="release-manifest")
    sha_row = _runtime_bundle_v2_file_row(sha_path, release_dir=release_dir, role="sha256s")
    files = [*asset_files, manifest_row, sha_row, *script_files]
    for item in files:
        item["archive_path"] = f"metis-runtime-bundle-v2/{str(item.get('relative_path') or Path(str(item.get('path'))).name).replace(os.sep, '/')}"
    return {
        "ok": True,
        "files": files,
        "release_manifest": release_manifest,
        "compression_result": compression_result,
    }


def _compress_file_zst(source: Path, target: Path, *, compression: Dict[str, Any], force: bool) -> Dict[str, Any]:
    if not source.is_file():
        return {"ok": False, "code": "METIS_RUNTIME_BUNDLE_V2_ROOTFS_MISSING", "error": f"rootfs.vhdx not found: {source}"}
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and not force:
        return {"ok": False, "code": "METIS_RUNTIME_BUNDLE_V2_ROOTFS_ZST_EXISTS", "error": f"target exists: {target}"}
    started = time.time()
    kind = str(compression.get("kind") or "")
    if kind == "python-zstandard":
        try:
            import zstandard  # type: ignore

            cctx = zstandard.ZstdCompressor(level=10, threads=-1)
            tmp = target.with_name(f".{target.name}.part-{uuid.uuid4().hex[:8]}")
            with source.open("rb") as src, tmp.open("wb") as dst:
                cctx.copy_stream(src, dst)
            os.replace(tmp, target)
            return {
                "ok": True,
                "kind": kind,
                "source": str(source),
                "target": str(target),
                "source_size_bytes": source.stat().st_size,
                "target_size_bytes": target.stat().st_size,
                "duration_ms": int((time.time() - started) * 1000),
            }
        except Exception as exc:
            return {"ok": False, "code": "METIS_RUNTIME_BUNDLE_V2_PY_ZSTD_FAILED", "error": f"{type(exc).__name__}: {exc}"}
    if kind == "zstd-cli":
        zstd = str(compression.get("executable") or shutil.which("zstd.exe") or shutil.which("zstd") or "zstd")
        args = [zstd, "-T0", "-10", "-f", "-o", str(target), str(source)]
        step = _run_rootfs_builder_step(args, cwd=target.parent, timeout=3600, step="compress_rootfs_vhdx_zst")
        if int(step.get("returncode") or 0) != 0:
            return {"ok": False, "code": "METIS_RUNTIME_BUNDLE_V2_ZSTD_CLI_FAILED", "error": step.get("stderr") or step.get("stdout"), "step": step}
        return {
            "ok": True,
            "kind": kind,
            "source": str(source),
            "target": str(target),
            "source_size_bytes": source.stat().st_size,
            "target_size_bytes": target.stat().st_size,
            "duration_ms": int((time.time() - started) * 1000),
            "step": step,
        }
    return {"ok": False, "code": "METIS_RUNTIME_BUNDLE_V2_ZSTD_UNAVAILABLE", "error": str(compression.get("reason") or "zstd compressor unavailable")}


def _runtime_bundle_v2_release_asset_files(
    *,
    bundle: Path,
    release_dir: Path,
    compressed_rootfs: Path,
    include_sessiondata: bool,
) -> List[Dict[str, Any]]:
    names = ["vmlinuz", "initrd", "metis-bin.vhdx", VM_MANIFEST_NAME]
    if include_sessiondata and (bundle / "sessiondata.vhdx").is_file():
        names.append("sessiondata.vhdx")
    for optional in (
        DIRECT_VM_ASSETS_MANIFEST_NAME,
        DIRECT_VM_RUNNER_MANIFEST_NAME,
        HCS_STARTER_MANIFEST_NAME,
        ROOTFS_BOOT_VERIFIER_MANIFEST_NAME,
        GUEST_HANDSHAKE_MANIFEST_NAME,
        ROOTFS_IMAGE_BUILDER_MANIFEST_NAME,
    ):
        if (bundle / optional).is_file():
            names.append(optional)
    rows = [_runtime_bundle_v2_file_row(compressed_rootfs, release_dir=release_dir, role="compressed-rootfs")]
    for name in names:
        path = bundle / name
        if not path.is_file():
            continue
        rows.append(_runtime_bundle_v2_file_row(path, release_dir=release_dir, role=_runtime_bundle_v2_role_for_name(name)))
    return rows


def _runtime_bundle_v2_file_row(path: Path, *, release_dir: Path, role: str) -> Dict[str, Any]:
    if _is_relative_to(path, release_dir):
        rel = _relative_to(path, release_dir)
    else:
        rel = path.name
    return {
        "path": str(path),
        "relative_path": rel,
        "name": path.name,
        "role": role,
        "size_bytes": path.stat().st_size if path.is_file() else 0,
        "sha256": _sha256_file(path) if path.is_file() else "",
    }


def _runtime_bundle_v2_role_for_name(name: str) -> str:
    if name == "vmlinuz":
        return "kernel"
    if name == "initrd":
        return "initrd"
    if name == "metis-bin.vhdx":
        return "guest-tools"
    if name == "sessiondata.vhdx":
        return "session-disk"
    if name == VM_MANIFEST_NAME:
        return "vm-pack-manifest"
    if name.endswith(".json"):
        return "metis-manifest"
    return "asset"


def _runtime_bundle_v2_release_manifest(
    *,
    bundle: Path,
    version: str,
    channel: str,
    files: List[Dict[str, Any]],
    compression: Dict[str, Any],
) -> Dict[str, Any]:
    vm_manifest = _load_vm_manifest_data(bundle)
    assets = {str(item.get("name") or ""): item for item in files}
    return {
        "schema": METIS_RUNTIME_BUNDLE_PACKAGE_V2_SCHEMA,
        "owner": "metis",
        "version": version,
        "channel": channel,
        "created_at": time.time(),
        "bundle_source": str(bundle),
        "bundle_manifest_schema": vm_manifest.get("schema"),
        "assets": assets,
        "file_count": len(files),
        "total_bytes": sum(int(item.get("size_bytes") or 0) for item in files),
        "compression": compression,
        "install": {
            "verify_script": "verify-metis-runtime-bundle-v2.ps1",
            "install_script": "install-metis-runtime-bundle-v2.ps1",
            "default_destination": "%LOCALAPPDATA%/Metis/vm_bundles/metisvm.bundle",
            "requires_docker": False,
            "requires_wsl_build": False,
            "requires_zstd_extract": True,
        },
        "notes": [
            "This v2 package is for distributing prebuilt Metis direct-VM assets.",
            "End users download, verify, extract rootfs.vhdx.zst, and point Metis at the bundle.",
            "The package does not make runner_ready true; HCS boot and guest handshake still gate execution.",
        ],
    }


def _runtime_bundle_v2_sha256s(files: List[Dict[str, Any]], manifest_path: Path) -> str:
    rows: List[str] = []
    for item in sorted(files, key=lambda row: str(row.get("relative_path") or "")):
        sha = str(item.get("sha256") or "")
        rel = str(item.get("relative_path") or item.get("name") or "")
        if sha and rel:
            rows.append(f"{sha}  {rel}")
    rows.append(f"{_sha256_file(manifest_path)}  {manifest_path.name}")
    return "\n".join(rows) + "\n"


def _runtime_bundle_v2_verify_script() -> str:
    return """param(
  [string]$Bundle = "",
  [string]$Manifest = "runtime-bundle-v2-manifest.json"
)

$ErrorActionPreference = 'Stop'
if (-not $Bundle) { $Bundle = Split-Path -Parent $MyInvocation.MyCommand.Path }
$ManifestPath = Join-Path $Bundle $Manifest
$ShaPath = Join-Path $Bundle 'SHA256SUMS.txt'
if (-not (Test-Path $ManifestPath)) { throw "manifest not found: $ManifestPath" }
if (-not (Test-Path $ShaPath)) { throw "SHA256SUMS.txt not found: $ShaPath" }
$Failures = @()
Get-Content -LiteralPath $ShaPath -Encoding UTF8 | ForEach-Object {
  $Line = $_.Trim()
  if (-not $Line) { return }
  $Parts = $Line -split '\\s+', 2
  if ($Parts.Count -lt 2) { return }
  $Expected = $Parts[0].Trim().ToLowerInvariant()
  $Rel = $Parts[1].Trim()
  $Path = Join-Path $Bundle $Rel
  if (-not (Test-Path $Path)) {
    $Failures += "missing: $Rel"
    return
  }
  $Actual = (Get-FileHash -Algorithm SHA256 -LiteralPath $Path).Hash.ToLowerInvariant()
  if ($Actual -ne $Expected) {
    $Failures += "sha256 mismatch: $Rel"
  }
}
if ($Failures.Count -gt 0) {
  throw ($Failures -join '; ')
}
Write-Host "Metis runtime bundle v2 verification passed."
"""


def _runtime_bundle_v2_install_script() -> str:
    return """param(
  [string]$Bundle = "",
  [string]$Destination = "$env:LOCALAPPDATA\\Metis\\vm_bundles\\metisvm.bundle",
  [switch]$SkipVerify,
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
if (-not $Bundle) { $Bundle = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $SkipVerify) {
  & (Join-Path $Bundle 'verify-metis-runtime-bundle-v2.ps1') -Bundle $Bundle
}
$RootfsZst = Join-Path $Bundle 'rootfs.vhdx.zst'
if (-not (Test-Path $RootfsZst)) { throw "rootfs.vhdx.zst not found: $RootfsZst" }
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$Zstd = Get-Command zstd.exe -ErrorAction SilentlyContinue
if (-not $Zstd) { $Zstd = Get-Command zstd -ErrorAction SilentlyContinue }
if (-not $Zstd) {
  throw "zstd is required to extract rootfs.vhdx.zst. Install zstd or extract rootfs.vhdx manually before running install."
}
Get-ChildItem -LiteralPath $Bundle -File | Where-Object {
  $_.Name -ne 'rootfs.vhdx.zst' -and $_.Name -ne 'SHA256SUMS.txt' -and $_.Name -notlike '*.sha256'
} | ForEach-Object {
  Copy-Item -LiteralPath $_.FullName -Destination (Join-Path $Destination $_.Name) -Force:$Force
}
& $Zstd.Source -d -f -o (Join-Path $Destination 'rootfs.vhdx') $RootfsZst
Write-Host "Installed Metis runtime bundle v2 to $Destination"
Write-Host "Set METIS_VM_BUNDLE_DIR or configure Metis Runtime settings to this path."
"""


def _runtime_bundle_v2_readme(*, version: str, channel: str) -> str:
    return f"""# Metis Runtime Bundle v2

Version: `{version}`
Channel: `{channel}`

This release contains prebuilt Metis direct-VM runtime assets:

- `vmlinuz`
- `initrd`
- `rootfs.vhdx.zst`
- `metis-bin.vhdx`
- Metis manifests
- `SHA256SUMS.txt`
- install and verify scripts

End users do not need Docker or WSL to build these assets. They only need to
download, verify, and extract/install the package.

## Verify

```powershell
.\\verify-metis-runtime-bundle-v2.ps1
```

## Install

```powershell
.\\install-metis-runtime-bundle-v2.ps1
```

`rootfs.vhdx.zst` extraction requires `zstd.exe` or `zstd` on PATH.
"""


def _build_direct_vm_assets_plan(
    *,
    source_root: Path,
    bundle: Path,
    version: str,
    inputs: Dict[str, str],
    copy_assets: bool,
    create_vhdx_scripts: bool,
    create_vhdx: bool,
    sessiondata_size_gb: int,
    metis_bin_size_mb: int,
) -> Dict[str, Any]:
    host = _detect_vm_host_capabilities()
    status = _inspect_direct_vm_assets(bundle)
    resolved_inputs = {
        name: str(Path(value).expanduser().resolve(strict=False)) if str(value or "").strip() else ""
        for name, value in inputs.items()
    }
    commands = {
        "create_vhdx": [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(bundle / "create-direct-vm-assets.ps1"),
        ],
        "runner_plan": [
            "powershell",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(bundle / "host" / "hcs-runner.ps1"),
            "-PlanOnly",
        ],
    }
    return {
        "schema": METIS_VM_DIRECT_ASSETS_SCHEMA,
        "source_root": str(source_root),
        "bundle_path": str(bundle),
        "version": version,
        "copy_assets": bool(copy_assets),
        "create_vhdx_scripts": bool(create_vhdx_scripts),
        "create_vhdx": bool(create_vhdx),
        "sessiondata_size_gb": sessiondata_size_gb,
        "metis_bin_size_mb": metis_bin_size_mb,
        "inputs": resolved_inputs,
        "host": host,
        "status": status,
        "commands": commands,
        "notes": [
            "Direct VM mode requires Metis-owned rootfs.vhdx, vmlinuz, initrd, metis-bin.vhdx, and sessiondata.vhdx.",
            "The v1 scripts create/check VHDX files and produce an HCS runner contract; they do not fake a running VM.",
            "WSL import remains the runnable fallback until HCS/Hyper-V runner is implemented end to end.",
        ],
    }


def _copy_direct_vm_input_assets(
    bundle: Path,
    inputs: Dict[str, str],
    *,
    copy_assets: bool,
    force: bool,
) -> List[Dict[str, Any]]:
    copied: List[Dict[str, Any]] = []
    for target_name, raw in inputs.items():
        source_text = str(raw or "").strip()
        if not source_text:
            continue
        source = safe_path_for_read(
            source_text,
            allow_paths_outside_workspace=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        if not source.is_file():
            copied.append(
                {
                    "name": target_name,
                    "source": str(source),
                    "copied": False,
                    "reason": "source asset is not a file",
                }
            )
            continue
        target = bundle / target_name
        if not copy_assets:
            copied.append(
                {
                    "name": target_name,
                    "source": str(source),
                    "target": str(target),
                    "copied": False,
                    "reason": "copy_assets=false",
                }
            )
            continue
        if target.exists() and target.resolve(strict=False) != source.resolve(strict=False) and not force:
            copied.append(
                {
                    "name": target_name,
                    "source": str(source),
                    "target": str(target),
                    "copied": False,
                    "reason": "target exists; pass force=true to overwrite",
                }
            )
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.resolve(strict=False) != source.resolve(strict=False):
            shutil.copy2(source, target)
            did_copy = True
        else:
            did_copy = False
        checksum = _sha256_file(target)
        origin = bundle / f".{target.name}.origin"
        origin.write_text(checksum + "\n", encoding="utf-8", newline="\n")
        copied.append(
            {
                "name": target_name,
                "source": str(source),
                "target": str(target),
                "copied": did_copy,
                "size_bytes": target.stat().st_size,
                "sha256": checksum,
                "origin_path": str(origin),
            }
        )
    return copied


def _write_direct_vm_asset_scripts(
    bundle: Path,
    *,
    sessiondata_size_gb: int,
    metis_bin_size_mb: int,
) -> List[Dict[str, Any]]:
    files = {
        "create-direct-vm-assets.ps1": _direct_vm_create_assets_script(
            sessiondata_size_gb=sessiondata_size_gb,
            metis_bin_size_mb=metis_bin_size_mb,
        ),
        "host/hcs-runner.ps1": _direct_vm_hcs_runner_script(),
        "host/hcs-runner-plan.json": json.dumps(
            _direct_vm_hcs_runner_plan(bundle),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    }
    written: List[Dict[str, Any]] = []
    for rel, content in files.items():
        path = bundle / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append({"relative_path": rel, "path": str(path), "size_bytes": path.stat().st_size})
    return written


def _direct_vm_create_assets_script(*, sessiondata_size_gb: int, metis_bin_size_mb: int) -> str:
    return f"""param(
  [int]$SessionDataSizeGB = {sessiondata_size_gb},
  [int]$MetisBinSizeMB = {metis_bin_size_mb},
  [switch]$Force
)

$ErrorActionPreference = 'Stop'
$Bundle = Split-Path -Parent $MyInvocation.MyCommand.Path
$NewVhd = Get-Command New-VHD -ErrorAction SilentlyContinue
if (-not $NewVhd) {{
  throw "New-VHD is required. Enable Hyper-V PowerShell module or install Hyper-V management tools."
}}

function New-MetisVhdx {{
  param([string]$Name, [UInt64]$SizeBytes)
  $Path = Join-Path $Bundle $Name
  if ((Test-Path $Path) -and -not $Force) {{
    Write-Host "$Name already exists: $Path"
    return
  }}
  if (Test-Path $Path) {{
    Remove-Item -LiteralPath $Path -Force
  }}
  New-VHD -Path $Path -SizeBytes $SizeBytes -Dynamic | Out-Null
  Write-Host "Created $Name at $Path"
}}

New-MetisVhdx -Name 'sessiondata.vhdx' -SizeBytes ([UInt64]$SessionDataSizeGB * 1GB)
New-MetisVhdx -Name 'metis-bin.vhdx' -SizeBytes ([UInt64]$MetisBinSizeMB * 1MB)
"""


def _direct_vm_hcs_runner_script() -> str:
    required = ", ".join(f"'{name}'" for name in ("rootfs.vhdx", "vmlinuz", "initrd", "metis-bin.vhdx", "sessiondata.vhdx"))
    return f"""param(
  [switch]$PlanOnly,
  [string]$Command = "",
  [string]$Workspace = "",
  [string]$Artifacts = "",
  [string]$Diagnostics = "",
  [int]$TimeoutSeconds = 120,
  [ValidateSet('stdio','hcs')]
  [string]$Transport = "stdio",
  [switch]$EnableExperimentalHcsStart
)

$ErrorActionPreference = 'Stop'
$Bundle = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Required = @({required})
$Missing = @()
foreach ($Name in $Required) {{
  $Path = Join-Path $Bundle $Name
  if (-not (Test-Path $Path)) {{ $Missing += $Name }}
}}
$HostCaps = @{{
  HcsDiag = [bool](Get-Command hcsdiag.exe -ErrorAction SilentlyContinue)
  VmCompute = $false
  HyperVPowerShell = [bool](Get-Command New-VHD -ErrorAction SilentlyContinue)
}}
try {{
  $svc = Get-Service vmcompute -ErrorAction Stop
  $HostCaps.VmCompute = ($svc.Status -eq 'Running')
}} catch {{}}
$RunnerManifest = Join-Path $Bundle '{DIRECT_VM_RUNNER_MANIFEST_NAME}'
$GuestDaemon = Join-Path $Bundle 'guest\\metisd.py'
$PlanPath = Join-Path $Bundle 'host\\hcs-runner-plan.json'
$DiagnosticsRoot = if ($Diagnostics) {{ $Diagnostics }} else {{ Join-Path $Bundle 'diagnostics' }}
New-Item -ItemType Directory -Force -Path $DiagnosticsRoot | Out-Null
$LifecycleLog = Join-Path $DiagnosticsRoot 'hcs-runner-lifecycle.jsonl'
function Write-Lifecycle {{
  param([string]$State, [string]$Message = "", [hashtable]$Data = @{{}})
  $row = @{{
    schema = 'metis.vm_direct.lifecycle.event.v1'
    at = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    state = $State
    message = $Message
    data = $Data
  }}
  ($row | ConvertTo-Json -Depth 8 -Compress) | Add-Content -LiteralPath $LifecycleLog -Encoding UTF8
}}
$Plan = @{{
  Schema = 'metis.vm_direct.hcs_runner_plan.v1'
  Bundle = $Bundle
  Required = $Required
  Missing = $Missing
  Host = $HostCaps
  RunnerManifest = $RunnerManifest
  GuestDaemon = $GuestDaemon
  LifecycleLog = $LifecycleLog
  Transport = $Transport
  StdioSmokeReady = (Test-Path $GuestDaemon)
  HcsReady = $false
  RunnerStatus = if ($Missing.Count -eq 0 -and (Test-Path $RunnerManifest) -and (Test-Path $GuestDaemon)) {{ 'protocol-ready' }} else {{ 'blocked' }}
  Message = 'Metis direct runner v1 has lifecycle/protocol/artifact contracts. HCS ComputeSystem start remains gated behind the experimental flag and is not production-ready.'
}}
$Plan | ConvertTo-Json -Depth 6
if ($PlanOnly) {{ exit 0 }}
Write-Lifecycle -State 'validating_assets' -Message 'validated direct VM runner inputs' -Data @{{ missing = $Missing; transport = $Transport }}
if ($Missing.Count -gt 0) {{
  Write-Lifecycle -State 'failed' -Message 'required assets are missing' -Data @{{ missing = $Missing }}
  throw "Metis direct VM required assets are missing: $($Missing -join ', ')"
}}
if ($Transport -eq 'stdio') {{
  if (-not (Test-Path $GuestDaemon)) {{
    Write-Lifecycle -State 'failed' -Message 'guest daemon missing' -Data @{{ guestDaemon = $GuestDaemon }}
    throw "guest daemon missing: $GuestDaemon"
  }}
  if (-not $Workspace) {{ $Workspace = Join-Path $Bundle 'workspace-smoke' }}
  if (-not $Artifacts) {{ $Artifacts = Join-Path $DiagnosticsRoot 'artifacts' }}
  New-Item -ItemType Directory -Force -Path $Workspace, $Artifacts | Out-Null
  $Python = Get-Command python -ErrorAction SilentlyContinue
  if (-not $Python) {{
    Write-Lifecycle -State 'failed' -Message 'python is required for stdio smoke'
    throw "python is required for stdio transport smoke"
  }}
  $Frames = @(
    @{{ id='hello'; method='runtime.hello'; params=@{{ protocol='metis.vm.guest.v1' }} }},
    @{{ id='mount'; method='session.mount'; params=@{{ workspace=$Workspace; artifacts=$Artifacts; diagnostics=$DiagnosticsRoot }} }},
    @{{ id='run'; method='process.run'; params=@{{ command=$Command; cwd=$Workspace; timeout_ms=($TimeoutSeconds * 1000) }} }},
    @{{ id='list'; method='artifact.list'; params=@{{}} }},
    @{{ id='diagnostics'; method='diagnostics.export'; params=@{{}} }},
    @{{ id='shutdown'; method='runtime.shutdown'; params=@{{}} }}
  )
  $InputText = ($Frames | ForEach-Object {{ $_ | ConvertTo-Json -Depth 8 -Compress }}) -join "`n"
  Write-Lifecycle -State 'running' -Message 'running guest daemon over stdio'
  $InputText | & $Python.Source $GuestDaemon
  Write-Lifecycle -State 'completed' -Message 'stdio guest daemon run finished'
  exit $LASTEXITCODE
}}
if (-not $EnableExperimentalHcsStart) {{
  Write-Lifecycle -State 'blocked' -Message 'HCS start requires EnableExperimentalHcsStart'
  throw "HCS direct start is still gated. Re-run with -EnableExperimentalHcsStart only after implementing ComputeSystem/vsock lifecycle."
}}
$Starter = Join-Path $Bundle 'host\\hcs-starter.ps1'
if (-not (Test-Path $Starter)) {{
  Write-Lifecycle -State 'blocked' -Message 'HCS starter script missing' -Data @{{ starter = $Starter }}
  throw "HCS starter script not found: $Starter. Run metis_vm_hcs_starter_prepare first."
}}
$ComputeDocument = Join-Path $Bundle 'host\\hcs-compute-system.json'
Write-Lifecycle -State 'starting_hcs_compute_system' -Message 'delegating to HCS starter' -Data @{{ starter = $Starter; computeDocument = $ComputeDocument }}
& $Starter -Bundle $Bundle -ComputeSystemId "metis-hcs-$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())" -ComputeDocument $ComputeDocument -LifecycleLog $LifecycleLog -TimeoutSeconds $TimeoutSeconds -HoldSeconds 3 -EnableExperimentalHcsStart
exit $LASTEXITCODE
"""


def _direct_vm_hcs_runner_plan(bundle: Path) -> Dict[str, Any]:
    return {
        "schema": "metis.vm_direct.hcs_runner_plan.v1",
        "bundle_path": str(bundle),
        "required_assets": ["rootfs.vhdx", "vmlinuz", "initrd", "metis-bin.vhdx", "sessiondata.vhdx"],
        "host_requirements": ["Windows", "Hyper-V", "vmcompute service", "hcsdiag.exe"],
        "transport": {
            "preferred": "hcs-vsock-jsonl",
            "fallback": "jsonl-stdio-smoke",
        },
        "phases": [
            "validate-assets",
            "create-session-disk",
            "start-hcs-compute-system",
            "connect-guest-daemon",
            "mount-workspace-copy",
            "run-command",
            "collect-artifacts",
            "export-diagnostics",
            "shutdown",
        ],
        "lifecycle_states": _direct_vm_lifecycle_states(),
        "guest_methods": _direct_vm_guest_methods(),
        "status": "protocol-ready-hcs-gated",
    }


def _run_direct_vm_vhdx_creation(bundle: Path, *, timeout: int) -> List[Dict[str, Any]]:
    script = bundle / "create-direct-vm-assets.ps1"
    powershell = shutil.which("powershell.exe") or shutil.which("powershell")
    if not powershell:
        return [{"ok": False, "step": "create_vhdx", "reason": "powershell not found"}]
    result = _run_rootfs_builder_step(
        [powershell, "-ExecutionPolicy", "Bypass", "-File", str(script)],
        cwd=bundle,
        timeout=timeout,
        step="create_direct_vm_vhdx",
    )
    return [result]


def _inspect_direct_vm_assets(bundle: Path) -> Dict[str, Any]:
    required = ["rootfs.vhdx", "vmlinuz", "initrd", "metis-bin.vhdx", "sessiondata.vhdx"]
    optional = ["rootfs.vhdx.zst", "vmlinuz.zst", "initrd.zst", "smol-bin.vhdx"]
    rows: List[Dict[str, Any]] = []
    for name in [*required, *optional]:
        path = bundle / name
        origin = bundle / f".{name}.origin"
        row = {
            "name": name,
            "path": str(path),
            "required": name in required,
            "exists": path.is_file(),
            "size_bytes": path.stat().st_size if path.is_file() else 0,
            "origin": origin.read_text(encoding="utf-8", errors="replace").strip() if origin.is_file() else "",
        }
        if path.is_file() and path.stat().st_size <= 128 * 1024 * 1024:
            row["sha256"] = _sha256_file(path)
        rows.append(row)
    missing = [row["name"] for row in rows if row["required"] and not row["exists"]]
    host = _detect_vm_host_capabilities()
    host_ready = bool(host.get("vmcompute_available"))
    return {
        "bundle_path": str(bundle),
        "assets_ready": not missing,
        "runner_ready": False,
        "missing_required": missing,
        "required_assets": required,
        "optional_assets": optional,
        "assets": rows,
        "host": host,
        "runner": {
            "status": "contract-only" if not missing else "missing-assets",
            "implemented": False,
            "host_ready": host_ready,
            "reason": "HCS direct VM start is contract-only in this pass" if not missing else "required assets are missing",
        },
    }


def _build_direct_vm_assets_manifest(
    *,
    source_root: Path,
    bundle: Path,
    version: str,
    status: Dict[str, Any],
    copied: List[Dict[str, Any]],
    vhdx_results: List[Dict[str, Any]],
) -> Dict[str, Any]:
    return {
        "schema": METIS_VM_DIRECT_ASSETS_SCHEMA,
        "owner": "metis",
        "version": version,
        "created_at": time.time(),
        "source_root": str(source_root),
        "bundle_path": str(bundle),
        "assets_ready": bool(status.get("assets_ready")),
        "runner_ready": bool(status.get("runner_ready")),
        "required_assets": status.get("required_assets") or [],
        "optional_assets": status.get("optional_assets") or [],
        "missing_required": status.get("missing_required") or [],
        "assets": status.get("assets") or [],
        "host": status.get("host") if isinstance(status.get("host"), dict) else {},
        "runner": {
            "backend": "hcs-hyperv",
            "implemented": False,
            "script": "host/hcs-runner.ps1",
            "plan": "host/hcs-runner-plan.json",
            "status": (status.get("runner") or {}).get("status") if isinstance(status.get("runner"), dict) else "unknown",
        },
        "copied": copied,
        "vhdx_results": vhdx_results,
        "notes": [
            "This manifest records Metis-owned direct VM boot/runtime assets.",
            "The pass prepares assets and the HCS runner contract; it does not claim full direct-VM execution.",
            "WSL-import runtime remains the runnable fallback until the HCS runner is completed.",
        ],
    }


def _upsert_direct_vm_pack_manifest(bundle: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    data = _load_vm_manifest_data(bundle)
    if not data:
        data = json.loads(_vm_pack_manifest_template())
    data["direct_vm"] = {
        "schema": METIS_VM_DIRECT_ASSETS_SCHEMA,
        "manifest": DIRECT_VM_ASSETS_MANIFEST_NAME,
        "assets_ready": bool(manifest.get("assets_ready")),
        "runner_ready": bool(manifest.get("runner_ready")),
        "required_assets": manifest.get("required_assets") or [],
        "missing_required": manifest.get("missing_required") or [],
        "runner": manifest.get("runner") if isinstance(manifest.get("runner"), dict) else {},
        "owner": "metis",
        "version": manifest.get("version"),
    }
    data["required_boot_assets"] = list(VM_REQUIRED_FILES)
    optional = set(str(item) for item in data.get("optional_assets") or [])
    optional.update(VM_OPTIONAL_FILES)
    data["optional_assets"] = sorted(optional)
    _write_vm_manifest_data(bundle, data)
    return data


def _normalize_direct_runner_transport(value: str) -> str:
    text = str(value or "jsonl-stdio").strip().lower().replace("_", "-")
    if text in {"stdio", "jsonl", "jsonl-stdio", "stdio-jsonl"}:
        return "jsonl-stdio"
    if text in {"hcs", "vsock", "hcs-vsock", "hcs-jsonl", "hcs-vsock-jsonl"}:
        return "hcs-vsock-jsonl"
    return "jsonl-stdio"


def _direct_vm_lifecycle_states() -> List[str]:
    return [
        "preparing",
        "validating_assets",
        "starting_hcs_compute_system",
        "waiting_guest",
        "handshake",
        "mounting_session",
        "running",
        "collecting_artifacts",
        "exporting_diagnostics",
        "shutting_down",
        "completed",
        "failed",
        "cancelled",
        "timed_out",
        "blocked",
    ]


def _direct_vm_guest_methods() -> List[str]:
    return [
        "runtime.hello",
        "runtime.status",
        "session.mount",
        "process.run",
        "process.cancel",
        "artifact.list",
        "artifact.collect",
        "diagnostics.export",
        "runtime.shutdown",
    ]


def _build_direct_vm_runner_prepare_plan(
    *,
    source_root: Path,
    bundle: Path,
    version: str,
    transport: str,
) -> Dict[str, Any]:
    assets = _inspect_direct_vm_assets(bundle)
    runner = _inspect_direct_vm_runner(bundle)
    would_write = [
        "guest/PROTOCOL.md",
        "guest/metisd.py",
        "host/hcs-runner.ps1",
        "host/hcs-runner-plan.json",
        "host/artifact-sync.ps1",
        "host/lifecycle-schema.json",
        "host/README.md",
        "channels/README.md",
        DIRECT_VM_RUNNER_MANIFEST_NAME,
    ]
    return {
        "schema": METIS_VM_DIRECT_RUNNER_SCHEMA,
        "source_root": str(source_root),
        "bundle_path": str(bundle),
        "version": version,
        "transport": transport,
        "assets": assets,
        "runner": runner,
        "would_write": [
            {
                "relative_path": rel,
                "path": str(bundle / rel),
                "exists": (bundle / rel).is_file(),
            }
            for rel in would_write
        ],
        "lifecycle_states": _direct_vm_lifecycle_states(),
        "guest_methods": _direct_vm_guest_methods(),
        "notes": [
            "This prepares the direct runner contract, guest daemon, JSONL protocol, artifact sync, and lifecycle files.",
            "The stdio transport can be smoke-tested on the host.",
            "HCS direct boot remains gated until ComputeSystem start and host/guest transport are implemented.",
        ],
    }


def _write_direct_vm_runner_files(bundle: Path, *, version: str, transport: str) -> List[Dict[str, Any]]:
    files = {
        "guest/PROTOCOL.md": _vm_guest_protocol_template(),
        "guest/metisd.py": _vm_guest_metisd_stub(),
        "host/hcs-runner.ps1": _direct_vm_hcs_runner_script(),
        "host/hcs-runner-plan.json": json.dumps(
            _direct_vm_hcs_runner_plan(bundle),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "host/artifact-sync.ps1": _direct_vm_artifact_sync_script(),
        "host/lifecycle-schema.json": json.dumps(
            _direct_vm_lifecycle_schema(version=version, transport=transport),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "host/README.md": _vm_host_runner_template(),
        "channels/README.md": _vm_channels_template(),
    }
    written: List[Dict[str, Any]] = []
    for rel, content in files.items():
        path = bundle / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append({"relative_path": rel, "path": str(path), "size_bytes": path.stat().st_size})
    return written


def _inspect_direct_vm_runner(bundle: Path) -> Dict[str, Any]:
    manifest_path = bundle / DIRECT_VM_RUNNER_MANIFEST_NAME
    manifest: Dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest = _read_json_object(manifest_path)
        except Exception as exc:
            manifest = {"schema_error": f"{type(exc).__name__}: {exc}"}
    assets = _inspect_direct_vm_assets(bundle)
    files = {
        "manifest": manifest_path,
        "guest_daemon": bundle / "guest" / "metisd.py",
        "guest_protocol": bundle / "guest" / "PROTOCOL.md",
        "host_runner": bundle / "host" / "hcs-runner.ps1",
        "runner_plan": bundle / "host" / "hcs-runner-plan.json",
        "artifact_sync": bundle / "host" / "artifact-sync.ps1",
        "lifecycle_schema": bundle / "host" / "lifecycle-schema.json",
    }
    file_rows = [
        {
            "name": name,
            "relative_path": _relative_to(path, bundle),
            "path": str(path),
            "exists": path.is_file(),
            "size_bytes": path.stat().st_size if path.is_file() else 0,
        }
        for name, path in files.items()
    ]
    missing = [row["relative_path"] for row in file_rows if not row["exists"]]
    runner = manifest.get("runner") if isinstance(manifest.get("runner"), dict) else {}
    hcs = manifest.get("hcs") if isinstance(manifest.get("hcs"), dict) else {}
    implemented = bool(runner.get("implemented"))
    hcs_ready = bool(implemented and assets.get("assets_ready") and assets.get("host", {}).get("vmcompute_available"))
    stdio_smoke_ready = (bundle / "guest" / "metisd.py").is_file()
    guest_protocol_ready = bool(manifest_path.is_file() and not missing and stdio_smoke_ready)
    return {
        "schema": METIS_VM_DIRECT_RUNNER_SCHEMA,
        "bundle_path": str(bundle),
        "manifest_path": str(manifest_path),
        "prepared": manifest_path.is_file() and not missing,
        "runner_ready": False,
        "hcs_ready": hcs_ready,
        "implemented": implemented,
        "stdio_smoke_ready": stdio_smoke_ready,
        "guest_protocol_ready": guest_protocol_ready,
        "guest_protocol_transport": "jsonl-stdio" if guest_protocol_ready else "",
        "missing_files": missing,
        "files": file_rows,
        "assets_ready": bool(assets.get("assets_ready")),
        "assets": assets,
        "manifest": manifest,
        "hcs": hcs,
        "reason": "HCS direct start is gated; JSONL guest protocol bridge is available" if guest_protocol_ready and not implemented else "",
    }


def _build_direct_vm_runner_manifest(
    *,
    source_root: Path,
    bundle: Path,
    version: str,
    transport: str,
    status: Dict[str, Any],
) -> Dict[str, Any]:
    host = _detect_vm_host_capabilities()
    assets = status.get("assets") if isinstance(status.get("assets"), dict) else _inspect_direct_vm_assets(bundle)
    return {
        "schema": METIS_VM_DIRECT_RUNNER_SCHEMA,
        "owner": "metis",
        "version": version,
        "created_at": time.time(),
        "source_root": str(source_root),
        "bundle_path": str(bundle),
        "runner_ready": False,
        "prepared": True,
        "runner": {
            "backend": "hcs-hyperv",
            "implemented": False,
            "script": "host/hcs-runner.ps1",
            "plan": "host/hcs-runner-plan.json",
            "status": "protocol-ready-hcs-gated",
        },
        "hcs": {
            "ready": False,
            "host": host,
            "requirements": ["Windows", "vmcompute", "hcsdiag.exe", "Metis-owned direct VM boot assets"],
            "blocked_by": [
                "ComputeSystem start is not implemented",
                "vsock or named-pipe transport is not implemented",
                "guest daemon has not been embedded into a booted rootfs image",
            ],
        },
        "transport": {
            "selected": transport,
            "preferred": "hcs-vsock-jsonl",
            "smoke": "jsonl-stdio",
            "stdio_smoke_ready": bool(status.get("stdio_smoke_ready")),
            "frame": "utf8-json-lines",
            "methods": _direct_vm_guest_methods(),
        },
        "lifecycle": {
            "states": _direct_vm_lifecycle_states(),
            "event_schema": "metis.vm_direct.lifecycle.event.v1",
            "log": "diagnostics/hcs-runner-lifecycle.jsonl",
            "terminal_states": ["completed", "failed", "cancelled", "timed_out", "blocked"],
        },
        "artifact_sync": {
            "script": "host/artifact-sync.ps1",
            "guest_methods": ["artifact.list", "artifact.collect", "diagnostics.export"],
            "host_artifacts_root": ".metis/artifacts/<session>",
            "diagnostics_root": ".metis/diagnostics/<session>",
        },
        "guest": {
            "daemon": "guest/metisd.py",
            "protocol": "guest/PROTOCOL.md",
            "install_path": "/usr/local/bin/metisd",
            "protocol_version": "metis.vm.guest.v1",
        },
        "assets_ready": bool(assets.get("assets_ready")),
        "missing_assets": assets.get("missing_required") or [],
        "notes": [
            "This manifest upgrades the direct VM pack from asset-only to protocol/lifecycle/artifact contracts.",
            "metis_vm_direct_runner_smoke can validate the guest daemon over stdio without HCS.",
            "runner_ready remains false until HCS ComputeSystem start and host/guest transport are implemented.",
        ],
    }


def _upsert_direct_vm_runner_pack_manifest(bundle: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    data = _load_vm_manifest_data(bundle)
    if not data:
        data = json.loads(_vm_pack_manifest_template())
    data["direct_runner"] = {
        "schema": METIS_VM_DIRECT_RUNNER_SCHEMA,
        "manifest": DIRECT_VM_RUNNER_MANIFEST_NAME,
        "prepared": bool(manifest.get("prepared")),
        "runner_ready": bool(manifest.get("runner_ready")),
        "guest_protocol_ready": bool((manifest.get("transport") or {}).get("stdio_smoke_ready")) if isinstance(manifest.get("transport"), dict) else False,
        "guest_protocol_transport": "jsonl-stdio",
        "runner": manifest.get("runner") if isinstance(manifest.get("runner"), dict) else {},
        "transport": manifest.get("transport") if isinstance(manifest.get("transport"), dict) else {},
        "lifecycle": manifest.get("lifecycle") if isinstance(manifest.get("lifecycle"), dict) else {},
        "artifact_sync": manifest.get("artifact_sync") if isinstance(manifest.get("artifact_sync"), dict) else {},
        "owner": "metis",
        "version": manifest.get("version"),
    }
    runner = data.setdefault("runner", {})
    if not isinstance(runner, dict):
        runner = {}
        data["runner"] = runner
    runner["status"] = "protocol-ready-hcs-gated"
    runner["hcs_direct_ready"] = False
    runner["stdio_smoke_ready"] = bool((manifest.get("transport") or {}).get("stdio_smoke_ready")) if isinstance(manifest.get("transport"), dict) else False
    runner["guest_protocol_ready"] = bool(data["direct_runner"]["guest_protocol_ready"])
    runner["guest_protocol_transport"] = "jsonl-stdio" if runner["guest_protocol_ready"] else ""
    _write_vm_manifest_data(bundle, data)
    return data


def _direct_vm_artifact_sync_script() -> str:
    return """param(
  [string]$Source,
  [string]$Destination,
  [string[]]$Patterns = @('*.png','*.jpg','*.jpeg','*.webp','*.pdf','*.docx','*.xlsx','*.pptx','*.csv','*.tsv','*.json','*.md','*.txt','*.log'),
  [int]$MaxFiles = 200,
  [int64]$MaxBytesPerFile = 20971520
)

$ErrorActionPreference = 'Stop'
if (-not $Source) { throw "Source is required" }
if (-not $Destination) { throw "Destination is required" }
if (-not (Test-Path $Source)) { throw "Artifact source does not exist: $Source" }
New-Item -ItemType Directory -Force -Path $Destination | Out-Null
$Copied = @()
Get-ChildItem -LiteralPath $Source -Recurse -File | ForEach-Object {
  if ($Copied.Count -ge $MaxFiles) { return }
  $File = $_
  $Match = $false
  foreach ($Pattern in $Patterns) {
    if ($File.Name -like $Pattern) { $Match = $true; break }
  }
  if (-not $Match) { return }
  if ($File.Length -gt $MaxBytesPerFile) { return }
  $Rel = [System.IO.Path]::GetRelativePath((Resolve-Path $Source), $File.FullName)
  $Target = Join-Path $Destination $Rel
  New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Target) | Out-Null
  Copy-Item -LiteralPath $File.FullName -Destination $Target -Force
  $Copied += @{ source = $File.FullName; target = $Target; relative_path = $Rel; size = $File.Length }
}
@{
  schema = 'metis.vm_direct.artifact_sync.v1'
  source = $Source
  destination = $Destination
  copied = $Copied
  count = $Copied.Count
} | ConvertTo-Json -Depth 8
"""


def _direct_vm_lifecycle_schema(*, version: str, transport: str) -> Dict[str, Any]:
    return {
        "schema": "metis.vm_direct.lifecycle.schema.v1",
        "version": version,
        "transport": transport,
        "event_schema": "metis.vm_direct.lifecycle.event.v1",
        "states": _direct_vm_lifecycle_states(),
        "terminal_states": ["completed", "failed", "cancelled", "timed_out", "blocked"],
        "required_event_fields": ["schema", "at", "state", "message", "data"],
        "evidence_chain": [
            "asset validation receipt",
            "host capability receipt",
            "guest handshake response",
            "session mount receipt",
            "process run receipt",
            "artifact list receipt",
            "diagnostics export receipt",
            "shutdown receipt",
        ],
    }


def _parse_jsonl_responses(text: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in str(text or "").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            rows.append({"parse_error": line[:500]})
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def _normalize_hcs_kernel_cmdline(value: str) -> str:
    text = str(value or "").strip()
    if text:
        return text
    return (
        "console=ttyS0 panic=-1 root=/dev/sda rw "
        "init=/usr/local/bin/metisd METIS_DIRECT_VM=1"
    )


def _safe_compute_system_id(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip()).strip(".-")
    if text:
        return text[:64]
    return f"metis-hcs-{int(time.time() * 1000)}-{uuid.uuid4().hex[:8]}"


def _resolve_hcs_compute_document(bundle: Path, compute_document_path: str = "") -> Path:
    raw = str(compute_document_path or "").strip()
    if raw:
        return safe_path_for_read(
            raw,
            allow_paths_outside_workspace=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
    return (bundle / "host" / "hcs-compute-system.json").resolve(strict=False)


def _build_hcs_starter_plan(
    *,
    source_root: Path,
    bundle: Path,
    version: str,
    memory_mb: int,
    processor_count: int,
    kernel_cmdline: str,
) -> Dict[str, Any]:
    assets = _inspect_direct_vm_assets(bundle)
    starter = _inspect_hcs_starter(bundle)
    return {
        "schema": METIS_VM_HCS_STARTER_SCHEMA,
        "source_root": str(source_root),
        "bundle_path": str(bundle),
        "version": version,
        "memory_mb": memory_mb,
        "processor_count": processor_count,
        "kernel_cmdline": kernel_cmdline,
        "assets": assets,
        "starter": starter,
        "would_write": [
            {
                "relative_path": rel,
                "path": str(bundle / rel),
                "exists": (bundle / rel).is_file(),
            }
            for rel in [
                "host/hcs-compute-system.json",
                "host/hcs-starter.ps1",
                "host/HcsApiBridge.cs",
                "host/hcs-start-plan.json",
                HCS_STARTER_MANIFEST_NAME,
            ]
        ],
        "notes": [
            "The generated ComputeSystem document follows hcsshim schema2 fields: ComputeSystem.VirtualMachine, Chipset.LinuxKernelDirect, Scsi.Attachments, and GuestConnection.UseVsock.",
            "The starter uses a generated C# P/Invoke bridge for HcsCreateComputeSystem, HcsStartComputeSystem, and HcsTerminateComputeSystem.",
            "dry_run=true never calls HCS. dry_run=false requires enable_experimental_hcs=true.",
        ],
    }


def _write_hcs_starter_files(
    bundle: Path,
    *,
    version: str,
    memory_mb: int,
    processor_count: int,
    kernel_cmdline: str,
) -> List[Dict[str, Any]]:
    compute_doc = _hcs_compute_system_document(
        bundle,
        memory_mb=memory_mb,
        processor_count=processor_count,
        kernel_cmdline=kernel_cmdline,
    )
    files = {
        "host/hcs-compute-system.json": json.dumps(compute_doc, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        "host/HcsApiBridge.cs": _hcs_api_bridge_cs(),
        "host/hcs-starter.ps1": _hcs_starter_script(),
        "host/hcs-start-plan.json": json.dumps(
            _hcs_start_plan(bundle, version=version, memory_mb=memory_mb, processor_count=processor_count),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    }
    written: List[Dict[str, Any]] = []
    for rel, content in files.items():
        path = bundle / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append({"relative_path": rel, "path": str(path), "size_bytes": path.stat().st_size})
    return written


def _hcs_compute_system_document(
    bundle: Path,
    *,
    memory_mb: int,
    processor_count: int,
    kernel_cmdline: str,
) -> Dict[str, Any]:
    return {
        "Owner": "Metis",
        "SchemaVersion": {"Major": 2, "Minor": 2},
        "ShouldTerminateOnLastHandleClosed": True,
        "VirtualMachine": {
            "StopOnReset": True,
            "Chipset": {
                "UseUtc": True,
                "LinuxKernelDirect": {
                    "KernelFilePath": str((bundle / "vmlinuz").resolve(strict=False)),
                    "InitRdPath": str((bundle / "initrd").resolve(strict=False)),
                    "KernelCmdLine": kernel_cmdline,
                },
            },
            "ComputeTopology": {
                "Memory": {
                    "SizeInMB": max(512, int(memory_mb or 2048)),
                    "AllowOvercommit": True,
                },
                "Processor": {
                    "Count": max(1, int(processor_count or 2)),
                },
            },
            "Devices": {
                "Scsi": {
                    "0": {
                        "Attachments": {
                            "0": {
                                "Type": "VirtualDisk",
                                "Path": str((bundle / "rootfs.vhdx").resolve(strict=False)),
                                "ReadOnly": False,
                                "CachingMode": "Cached",
                            },
                            "1": {
                                "Type": "VirtualDisk",
                                "Path": str((bundle / "sessiondata.vhdx").resolve(strict=False)),
                                "ReadOnly": False,
                                "CachingMode": "Cached",
                            },
                            "2": {
                                "Type": "VirtualDisk",
                                "Path": str((bundle / "metis-bin.vhdx").resolve(strict=False)),
                                "ReadOnly": True,
                                "CachingMode": "Cached",
                            },
                        }
                    }
                },
                "HvSocket": {},
            },
            "GuestConnection": {
                "UseVsock": True,
            },
        },
    }


def _hcs_start_plan(bundle: Path, *, version: str, memory_mb: int, processor_count: int) -> Dict[str, Any]:
    return {
        "schema": "metis.vm_direct.hcs_start_plan.v1",
        "bundle_path": str(bundle),
        "version": version,
        "compute_document": "host/hcs-compute-system.json",
        "starter": "host/hcs-starter.ps1",
        "api_bridge": "host/HcsApiBridge.cs",
        "required_assets": ["rootfs.vhdx", "vmlinuz", "initrd", "sessiondata.vhdx", "metis-bin.vhdx"],
        "memory_mb": memory_mb,
        "processor_count": processor_count,
        "phases": [
            "validate-assets",
            "load-hcs-api-bridge",
            "hcs-create-compute-system",
            "hcs-start-compute-system",
            "hold-or-connect-transport",
            "hcs-terminate-compute-system-unless-keep-running",
            "write-lifecycle",
        ],
        "status": "experimental-start-ready",
    }


def _inspect_hcs_starter(bundle: Path) -> Dict[str, Any]:
    assets = _inspect_direct_vm_assets(bundle)
    files = {
        "manifest": bundle / HCS_STARTER_MANIFEST_NAME,
        "compute_document": bundle / "host" / "hcs-compute-system.json",
        "starter_script": bundle / "host" / "hcs-starter.ps1",
        "api_bridge": bundle / "host" / "HcsApiBridge.cs",
        "start_plan": bundle / "host" / "hcs-start-plan.json",
    }
    rows = [
        {
            "name": name,
            "relative_path": _relative_to(path, bundle),
            "path": str(path),
            "exists": path.is_file(),
            "size_bytes": path.stat().st_size if path.is_file() else 0,
        }
        for name, path in files.items()
    ]
    missing = [row["relative_path"] for row in rows if not row["exists"] and row["name"] != "manifest"]
    host = _detect_vm_host_capabilities()
    return {
        "schema": METIS_VM_HCS_STARTER_SCHEMA,
        "bundle_path": str(bundle),
        "starter_ready": not missing,
        "assets_ready": bool(assets.get("assets_ready")),
        "missing_assets": assets.get("missing_required") or [],
        "missing_files": missing,
        "files": rows,
        "host": host,
        "hcs_host_ready": bool(host.get("vmcompute_available")),
        "hcs_ready": bool(not missing and assets.get("assets_ready") and host.get("vmcompute_available")),
        "assets": assets,
    }


def _build_hcs_starter_manifest(
    *,
    source_root: Path,
    bundle: Path,
    version: str,
    memory_mb: int,
    processor_count: int,
    kernel_cmdline: str,
    status: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "schema": METIS_VM_HCS_STARTER_SCHEMA,
        "owner": "metis",
        "version": version,
        "created_at": time.time(),
        "source_root": str(source_root),
        "bundle_path": str(bundle),
        "starter_ready": bool(status.get("starter_ready")),
        "assets_ready": bool(status.get("assets_ready")),
        "hcs_ready": bool(status.get("hcs_ready")),
        "hcs_host_ready": bool(status.get("hcs_host_ready")),
        "missing_assets": status.get("missing_assets") or [],
        "missing_files": status.get("missing_files") or [],
        "compute_document": "host/hcs-compute-system.json",
        "starter_script": "host/hcs-starter.ps1",
        "api_bridge": "host/HcsApiBridge.cs",
        "start_plan": "host/hcs-start-plan.json",
        "memory_mb": memory_mb,
        "processor_count": processor_count,
        "kernel_cmdline": kernel_cmdline,
        "hcs_api": {
            "dll": "computecore.dll",
            "functions": [
                "HcsCreateComputeSystem",
                "HcsStartComputeSystem",
                "HcsTerminateComputeSystem",
                "HcsWaitForOperationResult",
            ],
        },
        "safety": {
            "dry_run_default": True,
            "requires_enable_experimental_hcs": True,
            "keep_running_default": False,
            "terminates_after_start_by_default": True,
        },
        "notes": [
            "This is the first real HCS API starter path for Metis direct VM.",
            "It can call HcsCreateComputeSystem/HcsStartComputeSystem through a generated C# bridge.",
            "Actual boot success depends on host HCS support, exact rootfs layout, kernel cmdline, and guest metisd being installed in the image.",
        ],
    }


def _upsert_hcs_starter_pack_manifest(bundle: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    data = _load_vm_manifest_data(bundle)
    if not data:
        data = json.loads(_vm_pack_manifest_template())
    data["hcs_starter"] = {
        "schema": METIS_VM_HCS_STARTER_SCHEMA,
        "manifest": HCS_STARTER_MANIFEST_NAME,
        "starter_ready": bool(manifest.get("starter_ready")),
        "assets_ready": bool(manifest.get("assets_ready")),
        "hcs_ready": bool(manifest.get("hcs_ready")),
        "compute_document": manifest.get("compute_document"),
        "starter_script": manifest.get("starter_script"),
        "api_bridge": manifest.get("api_bridge"),
        "owner": "metis",
        "version": manifest.get("version"),
    }
    runner = data.setdefault("runner", {})
    if not isinstance(runner, dict):
        runner = {}
        data["runner"] = runner
    runner["status"] = "hcs-starter-prepared" if manifest.get("starter_ready") else "hcs-starter-missing-files"
    runner["hcs_direct_ready"] = bool(manifest.get("hcs_ready"))
    _write_vm_manifest_data(bundle, data)
    return data


def _hcs_starter_script() -> str:
    return """param(
  [string]$Bundle = "",
  [string]$ComputeSystemId = "",
  [string]$ComputeDocument = "",
  [string]$LifecycleLog = "",
  [int]$TimeoutSeconds = 120,
  [int]$HoldSeconds = 3,
  [switch]$KeepRunning,
  [switch]$EnableExperimentalHcsStart
)

$ErrorActionPreference = 'Stop'
if (-not $Bundle) {
  $Bundle = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}
if (-not $ComputeSystemId) {
  $ComputeSystemId = "metis-hcs-$([DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds())"
}
if (-not $ComputeDocument) {
  $ComputeDocument = Join-Path $Bundle 'host\\hcs-compute-system.json'
}
if (-not $LifecycleLog) {
  $LifecycleLog = Join-Path $Bundle 'diagnostics\\hcs-starter-lifecycle.jsonl'
}
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $LifecycleLog) | Out-Null

function Write-MetisLifecycle {
  param([string]$State, [string]$Message = "", [hashtable]$Data = @{})
  $row = @{
    schema = 'metis.vm_direct.lifecycle.event.v1'
    at = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    state = $State
    message = $Message
    data = $Data
  }
  ($row | ConvertTo-Json -Depth 10 -Compress) | Add-Content -LiteralPath $LifecycleLog -Encoding UTF8
}

Write-MetisLifecycle -State 'validating_assets' -Message 'validating HCS starter inputs' -Data @{
  bundle = $Bundle
  computeDocument = $ComputeDocument
  computeSystemId = $ComputeSystemId
}

$Required = @('rootfs.vhdx','vmlinuz','initrd','sessiondata.vhdx','metis-bin.vhdx')
$Missing = @()
foreach ($Name in $Required) {
  if (-not (Test-Path (Join-Path $Bundle $Name))) { $Missing += $Name }
}
if ($Missing.Count -gt 0) {
  Write-MetisLifecycle -State 'blocked' -Message 'required direct VM assets are missing' -Data @{ missing = $Missing }
  throw "Required direct VM assets are missing: $($Missing -join ', ')"
}
if (-not (Test-Path $ComputeDocument)) {
  Write-MetisLifecycle -State 'blocked' -Message 'compute document missing' -Data @{ computeDocument = $ComputeDocument }
  throw "Compute document not found: $ComputeDocument"
}
if (-not $EnableExperimentalHcsStart) {
  Write-MetisLifecycle -State 'blocked' -Message 'experimental HCS flag missing'
  throw "HCS start requires -EnableExperimentalHcsStart"
}

$Bridge = Join-Path $Bundle 'host\\HcsApiBridge.cs'
if (-not (Test-Path $Bridge)) {
  Write-MetisLifecycle -State 'blocked' -Message 'HCS API bridge missing' -Data @{ bridge = $Bridge }
  throw "HCS API bridge not found: $Bridge"
}

Write-MetisLifecycle -State 'starting_hcs_compute_system' -Message 'compiling HCS API bridge'
Add-Type -Path $Bridge
$ConfigJson = Get-Content -LiteralPath $ComputeDocument -Raw -Encoding UTF8

Write-MetisLifecycle -State 'starting_hcs_compute_system' -Message 'calling HCS create/start'
$Result = [Metis.HcsApiBridge]::CreateStartHoldTerminate(
  $ComputeSystemId,
  $ConfigJson,
  [uint32]([Math]::Max(1, $TimeoutSeconds) * 1000),
  [uint32]([Math]::Max(0, $HoldSeconds) * 1000),
  [bool]$KeepRunning
)
$ResultJson = $Result | ConvertTo-Json -Depth 12
$ResultPath = Join-Path (Split-Path -Parent $LifecycleLog) 'hcs-result.json'
$ResultJson | Set-Content -LiteralPath $ResultPath -Encoding UTF8
Write-MetisLifecycle -State $(if ($Result.Ok) { 'completed' } else { 'failed' }) -Message 'HCS starter finished' -Data @{
  resultPath = $ResultPath
  stage = $Result.Stage
  hresult = $Result.HResult
}
$ResultJson
if (-not $Result.Ok) {
  throw "HCS starter failed at $($Result.Stage), HRESULT=$($Result.HResult). See $ResultPath"
}
"""


def _hcs_api_bridge_cs() -> str:
    return r'''using System;
using System.Runtime.InteropServices;

namespace Metis {
  public sealed class HcsOperationResult {
    public bool Ok { get; set; }
    public string Stage { get; set; }
    public int HResult { get; set; }
    public int WaitHResult { get; set; }
    public string ResultDocument { get; set; }
    public string Message { get; set; }
  }

  public static class HcsApiBridge {
    [DllImport("computecore.dll", ExactSpelling=true)]
    private static extern IntPtr HcsCreateOperation(IntPtr callback, IntPtr context);

    [DllImport("computecore.dll", ExactSpelling=true)]
    private static extern void HcsCloseOperation(IntPtr operation);

    [DllImport("computecore.dll", ExactSpelling=true)]
    private static extern void HcsCloseComputeSystem(IntPtr computeSystem);

    [DllImport("computecore.dll", ExactSpelling=true)]
    private static extern void HcsFreeMemory(IntPtr resultDocument);

    [DllImport("computecore.dll", CharSet=CharSet.Unicode, ExactSpelling=true)]
    private static extern int HcsWaitForOperationResult(IntPtr operation, uint timeoutMs, out IntPtr resultDocument);

    [DllImport("computecore.dll", CharSet=CharSet.Unicode, ExactSpelling=true)]
    private static extern int HcsCreateComputeSystem(string id, string configuration, IntPtr operation, IntPtr securityDescriptor, out IntPtr computeSystem);

    [DllImport("computecore.dll", CharSet=CharSet.Unicode, ExactSpelling=true)]
    private static extern int HcsStartComputeSystem(IntPtr computeSystem, IntPtr operation, string options);

    [DllImport("computecore.dll", CharSet=CharSet.Unicode, ExactSpelling=true)]
    private static extern int HcsTerminateComputeSystem(IntPtr computeSystem, IntPtr operation, string options);

    private static string ReadAndFree(IntPtr ptr) {
      if (ptr == IntPtr.Zero) return "";
      try {
        return Marshal.PtrToStringUni(ptr) ?? "";
      } finally {
        HcsFreeMemory(ptr);
      }
    }

    private static HcsOperationResult WaitOperation(string stage, IntPtr operation, uint timeoutMs, int callHr) {
      IntPtr resultPtr;
      int waitHr = HcsWaitForOperationResult(operation, timeoutMs, out resultPtr);
      string doc = ReadAndFree(resultPtr);
      return new HcsOperationResult {
        Ok = callHr == 0 && waitHr == 0,
        Stage = stage,
        HResult = callHr,
        WaitHResult = waitHr,
        ResultDocument = doc,
        Message = (callHr == 0 && waitHr == 0) ? "ok" : "HCS operation failed"
      };
    }

    public static HcsOperationResult CreateStartHoldTerminate(string id, string configuration, uint timeoutMs, uint holdMs, bool keepRunning) {
      IntPtr system = IntPtr.Zero;
      IntPtr op = IntPtr.Zero;
      try {
        op = HcsCreateOperation(IntPtr.Zero, IntPtr.Zero);
        int createHr = HcsCreateComputeSystem(id, configuration, op, IntPtr.Zero, out system);
        HcsOperationResult createResult = WaitOperation("create", op, timeoutMs, createHr);
        HcsCloseOperation(op);
        op = IntPtr.Zero;
        if (!createResult.Ok) return createResult;

        op = HcsCreateOperation(IntPtr.Zero, IntPtr.Zero);
        int startHr = HcsStartComputeSystem(system, op, "{}");
        HcsOperationResult startResult = WaitOperation("start", op, timeoutMs, startHr);
        HcsCloseOperation(op);
        op = IntPtr.Zero;
        if (!startResult.Ok) return startResult;

        if (holdMs > 0) {
          System.Threading.Thread.Sleep((int)Math.Min(holdMs, int.MaxValue));
        }
        if (keepRunning) {
          startResult.Stage = "start-keep-running";
          startResult.Message = "HCS compute system started and left running";
          return startResult;
        }

        op = HcsCreateOperation(IntPtr.Zero, IntPtr.Zero);
        int termHr = HcsTerminateComputeSystem(system, op, "{}");
        HcsOperationResult termResult = WaitOperation("terminate", op, timeoutMs, termHr);
        HcsCloseOperation(op);
        op = IntPtr.Zero;
        if (!termResult.Ok) return termResult;
        termResult.Stage = "create-start-terminate";
        termResult.Message = "HCS compute system created, started, and terminated";
        return termResult;
      } catch (Exception ex) {
        return new HcsOperationResult {
          Ok = false,
          Stage = "exception",
          HResult = unchecked((int)0x80004005),
          WaitHResult = 0,
          ResultDocument = "",
          Message = ex.GetType().Name + ": " + ex.Message
        };
      } finally {
        if (op != IntPtr.Zero) HcsCloseOperation(op);
        if (system != IntPtr.Zero && !keepRunning) HcsCloseComputeSystem(system);
      }
    }
  }
}
'''


def _build_rootfs_boot_cmdline_matrix(
    *,
    root_device_candidates: List[str],
    init_candidates: List[str],
) -> List[Dict[str, Any]]:
    roots = [str(item).strip() for item in root_device_candidates if str(item).strip()] or [
        "/dev/sda",
        "/dev/sda1",
        "/dev/vda",
        "/dev/vda1",
        "/dev/sdb",
        "/dev/sdb1",
    ]
    inits = [str(item).strip() for item in init_candidates if str(item).strip()] or [
        "/usr/local/bin/metisd",
        "/sbin/init",
    ]
    matrix: List[Dict[str, Any]] = []
    for root_device in roots:
        for init_path in inits:
            candidate_id = _safe_filename_part(f"{root_device.strip('/').replace('/', '-')}-{Path(init_path).name}")
            cmdline = (
                f"console=ttyS0 panic=-1 root={root_device} rw "
                f"init={init_path} METIS_DIRECT_VM=1"
            )
            matrix.append(
                {
                    "id": candidate_id,
                    "root_device": root_device,
                    "init": init_path,
                    "kernel_cmdline": cmdline,
                    "expects_metisd_handshake": init_path.endswith("metisd"),
                }
            )
    return matrix


def _build_rootfs_boot_verifier_plan(
    *,
    source_root: Path,
    bundle: Path,
    version: str,
    matrix: List[Dict[str, Any]],
) -> Dict[str, Any]:
    status = _inspect_rootfs_boot_verifier(bundle)
    return {
        "schema": METIS_VM_ROOTFS_BOOT_VERIFIER_SCHEMA,
        "source_root": str(source_root),
        "bundle_path": str(bundle),
        "version": version,
        "status": status,
        "candidate_count": len(matrix),
        "cmdline_matrix": matrix,
        "would_write": [
            {
                "relative_path": rel,
                "path": str(bundle / rel),
                "exists": (bundle / rel).is_file(),
            }
            for rel in [
                "host/rootfs-inspect.ps1",
                "host/rootfs-boot-verifier.ps1",
                "host/boot-cmdline-matrix.json",
                ROOTFS_BOOT_VERIFIER_MANIFEST_NAME,
            ]
        ],
        "notes": [
            "The verifier tries multiple root device and init combinations because direct HCS boot depends on rootfs layout.",
            "The first success criterion is HCS create/start/terminate evidence; runner_ready remains false until guest handshake is implemented.",
            "rootfs-inspect.ps1 can mount the VHDX only when the user explicitly runs it with sufficient Windows privileges.",
        ],
    }


def _write_rootfs_boot_verifier_files(bundle: Path, *, version: str, matrix: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    files = {
        "host/boot-cmdline-matrix.json": json.dumps(
            {
                "schema": "metis.vm_direct.boot_cmdline_matrix.v1",
                "version": version,
                "generated_at": time.time(),
                "candidates": matrix,
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        "host/rootfs-inspect.ps1": _rootfs_inspect_script(),
        "host/rootfs-boot-verifier.ps1": _rootfs_boot_verifier_script(),
    }
    written: List[Dict[str, Any]] = []
    for rel, content in files.items():
        path = bundle / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append({"relative_path": rel, "path": str(path), "size_bytes": path.stat().st_size})
    return written


def _inspect_rootfs_boot_verifier(bundle: Path) -> Dict[str, Any]:
    assets = _inspect_direct_vm_assets(bundle)
    hcs = _inspect_hcs_starter(bundle)
    files = {
        "manifest": bundle / ROOTFS_BOOT_VERIFIER_MANIFEST_NAME,
        "matrix": bundle / "host" / "boot-cmdline-matrix.json",
        "inspect_script": bundle / "host" / "rootfs-inspect.ps1",
        "verifier_script": bundle / "host" / "rootfs-boot-verifier.ps1",
    }
    rows = [
        {
            "name": name,
            "relative_path": _relative_to(path, bundle),
            "path": str(path),
            "exists": path.is_file(),
            "size_bytes": path.stat().st_size if path.is_file() else 0,
        }
        for name, path in files.items()
    ]
    missing = [row["relative_path"] for row in rows if not row["exists"] and row["name"] != "manifest"]
    matrix_count = 0
    matrix_path = bundle / "host" / "boot-cmdline-matrix.json"
    if matrix_path.is_file():
        try:
            data = _read_json_object(matrix_path)
            matrix = data.get("candidates") if isinstance(data.get("candidates"), list) else []
            matrix_count = len(matrix)
        except Exception:
            matrix_count = 0
    return {
        "schema": METIS_VM_ROOTFS_BOOT_VERIFIER_SCHEMA,
        "bundle_path": str(bundle),
        "verifier_ready": not missing,
        "assets_ready": bool(assets.get("assets_ready")),
        "hcs_starter_ready": bool(hcs.get("starter_ready")),
        "hcs_ready": bool(hcs.get("hcs_ready")),
        "missing_assets": assets.get("missing_required") or [],
        "missing_files": missing,
        "candidate_count": matrix_count,
        "files": rows,
        "assets": assets,
        "hcs": hcs,
        "runner_ready": False,
        "runner_ready_reason": "Rootfs boot verifier cannot mark runner_ready until guest handshake succeeds.",
    }


def _build_rootfs_boot_verifier_manifest(
    *,
    source_root: Path,
    bundle: Path,
    version: str,
    matrix: List[Dict[str, Any]],
    status: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "schema": METIS_VM_ROOTFS_BOOT_VERIFIER_SCHEMA,
        "owner": "metis",
        "version": version,
        "created_at": time.time(),
        "source_root": str(source_root),
        "bundle_path": str(bundle),
        "verifier_ready": bool(status.get("verifier_ready")),
        "assets_ready": bool(status.get("assets_ready")),
        "hcs_starter_ready": bool(status.get("hcs_starter_ready")),
        "runner_ready": False,
        "runner_ready_reason": "runner_ready is promoted only after a real guest metisd handshake.",
        "cmdline_matrix": matrix,
        "scripts": {
            "rootfs_inspect": "host/rootfs-inspect.ps1",
            "boot_verifier": "host/rootfs-boot-verifier.ps1",
            "cmdline_matrix": "host/boot-cmdline-matrix.json",
        },
        "success_criteria": [
            "HCS create succeeds",
            "HCS start succeeds",
            "VM can be terminated cleanly unless keep_running=true",
            "future: guest metisd handshake over HCS/vsock succeeds",
        ],
        "notes": [
            "This verifier creates multiple HCS compute documents with different root= and init= values.",
            "It is designed to reveal whether failures are caused by root device, init path, HCS schema, or missing guest daemon.",
            "Current pass can verify HCS start evidence; guest handshake promotion remains a later layer.",
        ],
    }


def _upsert_rootfs_boot_verifier_pack_manifest(bundle: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    data = _load_vm_manifest_data(bundle)
    if not data:
        data = json.loads(_vm_pack_manifest_template())
    data["rootfs_boot_verifier"] = {
        "schema": METIS_VM_ROOTFS_BOOT_VERIFIER_SCHEMA,
        "manifest": ROOTFS_BOOT_VERIFIER_MANIFEST_NAME,
        "verifier_ready": bool(manifest.get("verifier_ready")),
        "assets_ready": bool(manifest.get("assets_ready")),
        "hcs_starter_ready": bool(manifest.get("hcs_starter_ready")),
        "runner_ready": False,
        "candidate_count": len(manifest.get("cmdline_matrix") or []),
        "owner": "metis",
        "version": manifest.get("version"),
    }
    runner = data.setdefault("runner", {})
    if not isinstance(runner, dict):
        runner = {}
        data["runner"] = runner
    runner["boot_verifier_ready"] = bool(manifest.get("verifier_ready"))
    runner["runner_ready"] = False
    runner["runner_ready_reason"] = "rootfs boot verifier is prepared; guest handshake is not yet verified"
    _write_vm_manifest_data(bundle, data)
    return data


def _rootfs_boot_attempt_plan(
    *,
    source_root: Path,
    bundle: Path,
    matrix: List[Dict[str, Any]],
    timeout: int,
    hold_seconds: int,
    enable_experimental_hcs: bool,
) -> List[Dict[str, Any]]:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell"
    attempts: List[Dict[str, Any]] = []
    for item in matrix:
        candidate_id = _safe_filename_part(str(item.get("id") or "candidate"))
        compute_system_id = _safe_compute_system_id(f"metis-boot-{candidate_id}")
        compute_document = bundle / "host" / "boot-candidates" / f"{candidate_id}.hcs-compute-system.json"
        diagnostics = source_root / ".metis" / "diagnostics" / compute_system_id
        lifecycle_log = diagnostics / "hcs-starter-lifecycle.jsonl"
        command = [
            powershell,
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(bundle / "host" / "hcs-starter.ps1"),
            "-Bundle",
            str(bundle),
            "-ComputeSystemId",
            compute_system_id,
            "-ComputeDocument",
            str(compute_document),
            "-LifecycleLog",
            str(lifecycle_log),
            "-TimeoutSeconds",
            str(timeout),
            "-HoldSeconds",
            str(hold_seconds),
        ]
        if enable_experimental_hcs:
            command.append("-EnableExperimentalHcsStart")
        attempts.append(
            {
                "candidate_id": candidate_id,
                "root_device": item.get("root_device"),
                "init": item.get("init"),
                "kernel_cmdline": item.get("kernel_cmdline"),
                "compute_system_id": compute_system_id,
                "compute_document": str(compute_document),
                "diagnostics_dir": str(diagnostics),
                "lifecycle_log": str(lifecycle_log),
                "command": command,
                "enable_experimental_hcs": bool(enable_experimental_hcs),
            }
        )
    return attempts


def _write_rootfs_boot_candidate_compute_document(bundle: Path, *, candidate_id: str, kernel_cmdline: str) -> Path:
    base_path = bundle / "host" / "hcs-compute-system.json"
    if base_path.is_file():
        document = _read_json_object(base_path)
    else:
        document = _hcs_compute_system_document(
            bundle,
            memory_mb=2048,
            processor_count=2,
            kernel_cmdline=_normalize_hcs_kernel_cmdline(kernel_cmdline),
        )
    vm = document.setdefault("VirtualMachine", {})
    if not isinstance(vm, dict):
        vm = {}
        document["VirtualMachine"] = vm
    chipset = vm.setdefault("Chipset", {})
    if not isinstance(chipset, dict):
        chipset = {}
        vm["Chipset"] = chipset
    direct = chipset.setdefault("LinuxKernelDirect", {})
    if not isinstance(direct, dict):
        direct = {}
        chipset["LinuxKernelDirect"] = direct
    direct["KernelCmdLine"] = _normalize_hcs_kernel_cmdline(kernel_cmdline)
    out = bundle / "host" / "boot-candidates" / f"{_safe_filename_part(candidate_id)}.hcs-compute-system.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return out


def _summarize_rootfs_boot_results(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    successes = [item for item in results if item.get("ok")]
    first_success = successes[0] if successes else {}
    return {
        "attempt_count": len(results),
        "hcs_start_succeeded": bool(successes),
        "first_success_candidate": first_success.get("candidate_id", ""),
        "handshake_verified": False,
        "runner_ready": False,
        "failure_codes": [
            {
                "candidate_id": item.get("candidate_id"),
                "code": item.get("code", ""),
                "returncode": item.get("returncode"),
            }
            for item in results
            if not item.get("ok")
        ],
    }


def _write_rootfs_boot_results(bundle: Path, *, results: List[Dict[str, Any]], summary: Dict[str, Any]) -> Path:
    out = bundle / "host" / "boot-results" / f"boot-results-{int(time.time() * 1000)}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "metis.vm_direct.rootfs_boot_results.v1",
        "created_at": time.time(),
        "summary": summary,
        "results": results,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return out


def _normalize_guest_handshake_transport(value: str) -> str:
    text = str(value or "hcs-vsock-jsonl").strip().lower().replace("_", "-")
    if text in {"stdio", "jsonl", "jsonl-stdio", "stdio-jsonl", "host-stdio"}:
        return "jsonl-stdio"
    if text in {"hcs", "vsock", "hcs-vsock", "hcs-jsonl", "hcs-vsock-jsonl", "hyperv-vsock"}:
        return "hcs-vsock-jsonl"
    return "hcs-vsock-jsonl"


def _build_guest_handshake_plan(
    *,
    source_root: Path,
    bundle: Path,
    version: str,
    transport: str,
    timeout: int,
) -> Dict[str, Any]:
    status = _inspect_guest_handshake(bundle)
    return {
        "schema": METIS_VM_GUEST_HANDSHAKE_SCHEMA,
        "source_root": str(source_root),
        "bundle_path": str(bundle),
        "version": version,
        "transport": transport,
        "timeout_seconds": timeout,
        "status": status,
        "expected": {
            "method": "runtime.hello",
            "protocol": "metis.vm.guest.v1",
            "required_response_fields": ["ok", "protocol", "pid", "methods"],
        },
        "transports": {
            "jsonl-stdio": {
                "implemented": True,
                "scope": "host-only protocol smoke",
                "promotes_runner_ready": False,
            },
            "hcs-vsock-jsonl": {
                "implemented": False,
                "scope": "booted HCS guest readiness gate",
                "promotes_runner_ready": True,
            },
        },
        "would_write": [
            {
                "relative_path": rel,
                "path": str(bundle / rel),
                "exists": (bundle / rel).is_file(),
            }
            for rel in [
                "host/guest-handshake.ps1",
                "host/guest-handshake-plan.json",
                GUEST_HANDSHAKE_MANIFEST_NAME,
            ]
        ],
        "readiness_rule": "runner_ready=true only after hcs-vsock-jsonl receives runtime.hello from guest metisd.",
    }


def _write_guest_handshake_files(bundle: Path, *, version: str, transport: str, timeout: int) -> List[Dict[str, Any]]:
    plan = {
        "schema": "metis.vm_direct.guest_handshake_plan.v1",
        "version": version,
        "generated_at": time.time(),
        "transport": transport,
        "timeout_seconds": timeout,
        "expected_method": "runtime.hello",
        "expected_protocol": "metis.vm.guest.v1",
        "runner_ready_gate": "hcs-vsock-jsonl runtime.hello receipt",
        "stdio_note": "jsonl-stdio validates the guest daemon protocol without proving VM boot readiness.",
    }
    files = {
        "host/guest-handshake.ps1": _guest_handshake_script(),
        "host/guest-handshake-plan.json": json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    }
    written: List[Dict[str, Any]] = []
    for rel, content in files.items():
        path = bundle / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append({"relative_path": rel, "path": str(path), "size_bytes": path.stat().st_size})
    return written


def _inspect_guest_handshake(bundle: Path) -> Dict[str, Any]:
    manifest_path = bundle / GUEST_HANDSHAKE_MANIFEST_NAME
    manifest: Dict[str, Any] = {}
    if manifest_path.is_file():
        try:
            manifest = _read_json_object(manifest_path)
        except Exception as exc:
            manifest = {"schema_error": f"{type(exc).__name__}: {exc}"}
    files = {
        "manifest": manifest_path,
        "script": bundle / "host" / "guest-handshake.ps1",
        "plan": bundle / "host" / "guest-handshake-plan.json",
        "guest_daemon": bundle / "guest" / "metisd.py",
    }
    rows = [
        {
            "name": name,
            "relative_path": _relative_to(path, bundle),
            "path": str(path),
            "exists": path.is_file(),
            "size_bytes": path.stat().st_size if path.is_file() else 0,
        }
        for name, path in files.items()
    ]
    missing = [row["relative_path"] for row in rows if not row["exists"] and row["name"] != "manifest"]
    last_receipt_path = str(manifest.get("last_handshake_receipt") or "")
    receipt_path = bundle / last_receipt_path if last_receipt_path and not Path(last_receipt_path).is_absolute() else Path(last_receipt_path)
    receipt_exists = bool(last_receipt_path and receipt_path.is_file())
    hcs_verified = bool(manifest.get("hcs_handshake_verified"))
    runner_ready = bool(manifest.get("runner_ready") and hcs_verified)
    return {
        "schema": METIS_VM_GUEST_HANDSHAKE_SCHEMA,
        "bundle_path": str(bundle),
        "manifest_path": str(manifest_path),
        "verifier_ready": not missing,
        "runner_ready": runner_ready,
        "runner_ready_reason": str(manifest.get("runner_ready_reason") or "runtime.hello from a booted HCS guest has not been verified"),
        "stdio_handshake_verified": bool(manifest.get("stdio_handshake_verified")),
        "hcs_handshake_verified": hcs_verified,
        "last_handshake_receipt": last_receipt_path,
        "last_handshake_receipt_exists": receipt_exists,
        "transport": manifest.get("transport") if isinstance(manifest.get("transport"), dict) else {},
        "missing_files": missing,
        "files": rows,
        "manifest": manifest,
    }


def _build_guest_handshake_manifest(
    *,
    source_root: Path,
    bundle: Path,
    version: str,
    transport: str,
    timeout: int,
    status: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "schema": METIS_VM_GUEST_HANDSHAKE_SCHEMA,
        "owner": "metis",
        "version": version,
        "created_at": time.time(),
        "source_root": str(source_root),
        "bundle_path": str(bundle),
        "prepared": True,
        "verifier_ready": bool(status.get("verifier_ready")),
        "runner_ready": False,
        "runner_ready_reason": "runner_ready requires hcs-vsock-jsonl runtime.hello from a booted guest metisd.",
        "stdio_handshake_verified": False,
        "hcs_handshake_verified": False,
        "last_handshake_receipt": "",
        "transport": {
            "selected": transport,
            "preferred": "hcs-vsock-jsonl",
            "smoke": "jsonl-stdio",
            "frame": "utf8-json-lines",
            "hcs_vsock_implemented": False,
            "stdio_implemented": True,
        },
        "handshake": {
            "method": "runtime.hello",
            "protocol": "metis.vm.guest.v1",
            "timeout_seconds": timeout,
            "expected_methods": _direct_vm_guest_methods(),
        },
        "scripts": {
            "script": "host/guest-handshake.ps1",
            "plan": "host/guest-handshake-plan.json",
        },
        "success_criteria": [
            "HCS ComputeSystem is started and kept alive long enough to connect",
            "host/guest transport connects to guest metisd",
            "guest response to runtime.hello has ok=true and protocol=metis.vm.guest.v1",
            "receipt is written and runner_ready is promoted only for HCS/vsock transport",
        ],
        "notes": [
            "jsonl-stdio validates the same daemon/protocol locally, but it is not a VM readiness proof.",
            "HCS create/start success is insufficient until metisd answers runtime.hello.",
        ],
    }


def _upsert_guest_handshake_pack_manifest(bundle: Path, manifest: Dict[str, Any]) -> Dict[str, Any]:
    data = _load_vm_manifest_data(bundle)
    if not data:
        data = json.loads(_vm_pack_manifest_template())
    runner_ready = bool(manifest.get("runner_ready") and manifest.get("hcs_handshake_verified"))
    data["guest_handshake"] = {
        "schema": METIS_VM_GUEST_HANDSHAKE_SCHEMA,
        "manifest": GUEST_HANDSHAKE_MANIFEST_NAME,
        "prepared": bool(manifest.get("prepared")),
        "verifier_ready": bool(manifest.get("verifier_ready")),
        "runner_ready": runner_ready,
        "runner_ready_reason": manifest.get("runner_ready_reason"),
        "stdio_handshake_verified": bool(manifest.get("stdio_handshake_verified")),
        "hcs_handshake_verified": bool(manifest.get("hcs_handshake_verified")),
        "last_handshake_receipt": manifest.get("last_handshake_receipt") or "",
        "transport": manifest.get("transport") if isinstance(manifest.get("transport"), dict) else {},
        "owner": "metis",
        "version": manifest.get("version"),
    }
    runner = data.setdefault("runner", {})
    if not isinstance(runner, dict):
        runner = {}
        data["runner"] = runner
    runner["guest_handshake_verifier_ready"] = bool(manifest.get("verifier_ready"))
    runner["runner_ready"] = runner_ready
    runner["hcs_direct_ready"] = runner_ready
    runner["runner_ready_reason"] = (
        "HCS guest handshake verified"
        if runner_ready
        else "HCS/vsock runtime.hello from guest metisd is not verified"
    )
    if runner_ready:
        runner["status"] = "hcs-guest-handshake-ready"
        direct_runner = data.get("direct_runner")
        if isinstance(direct_runner, dict):
            direct_runner["runner_ready"] = True
    _write_vm_manifest_data(bundle, data)
    return data


def _guest_handshake_attempt_plan(
    *,
    source_root: Path,
    bundle: Path,
    transport: str,
    compute_system_id: str,
    timeout: int,
    enable_experimental_hcs: bool,
) -> Dict[str, Any]:
    powershell = shutil.which("powershell.exe") or shutil.which("powershell") or "powershell"
    system_id = _safe_compute_system_id(compute_system_id or "metis-handshake")
    diagnostics = source_root / ".metis" / "diagnostics" / system_id
    command = [
        powershell,
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(bundle / "host" / "guest-handshake.ps1"),
        "-Bundle",
        str(bundle),
        "-Transport",
        transport,
        "-ComputeSystemId",
        system_id,
        "-TimeoutSeconds",
        str(timeout),
    ]
    if enable_experimental_hcs:
        command.append("-EnableExperimentalHcsHandshake")
    return {
        "schema": METIS_VM_GUEST_HANDSHAKE_SCHEMA,
        "source_root": str(source_root),
        "bundle_path": str(bundle),
        "transport": transport,
        "compute_system_id": system_id,
        "diagnostics_dir": str(diagnostics),
        "command": command,
        "timeout_seconds": timeout,
        "enable_experimental_hcs": bool(enable_experimental_hcs),
        "expected_method": "runtime.hello",
        "expected_protocol": "metis.vm.guest.v1",
        "runner_ready_on_success": transport == "hcs-vsock-jsonl",
        "transport_implemented": transport == "jsonl-stdio",
    }


def _verify_guest_handshake_stdio(*, source_root: Path, bundle: Path, timeout: int) -> Dict[str, Any]:
    daemon = bundle / "guest" / "metisd.py"
    if not daemon.is_file():
        return {
            "schema": METIS_VM_GUEST_HANDSHAKE_SCHEMA,
            "transport": "jsonl-stdio",
            "handshake_verified": False,
            "code": "METIS_VM_GUEST_DAEMON_MISSING",
            "error": f"guest daemon not found: {daemon}",
        }
    handshake_id = f"guest_handshake_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    handshake_root = source_root / ".metis" / "direct-vm-handshake" / handshake_id
    workspace = handshake_root / "workspace"
    artifacts = handshake_root / "artifacts"
    diagnostics = handshake_root / "diagnostics"
    workspace.mkdir(parents=True, exist_ok=True)
    artifacts.mkdir(parents=True, exist_ok=True)
    diagnostics.mkdir(parents=True, exist_ok=True)
    python_exe = configured_python_executable() or shutil.which("python") or shutil.which("py") or "python"
    messages = [
        {
            "id": "mount",
            "method": "session.mount",
            "params": {
                "workspace": str(workspace),
                "artifacts": str(artifacts),
                "diagnostics": str(diagnostics),
            },
        },
        {"id": "hello", "method": "runtime.hello", "params": {"protocol": "metis.vm.guest.v1"}},
        {"id": "status", "method": "runtime.status", "params": {}},
        {"id": "shutdown", "method": "runtime.shutdown", "params": {}},
    ]
    started = time.time()
    try:
        proc = subprocess.run(
            [python_exe, str(daemon)],
            input="\n".join(json.dumps(item, ensure_ascii=False) for item in messages) + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(5, timeout + 5),
            check=False,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        returncode: Optional[int] = proc.returncode
        timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        returncode = None
        timed_out = True
    responses = _parse_jsonl_responses(stdout)
    by_id = {str(item.get("id") or ""): item for item in responses}
    hello = by_id.get("hello") if isinstance(by_id.get("hello"), dict) else {}
    hello_result = hello.get("result") if isinstance(hello.get("result"), dict) else {}
    verified = (
        returncode == 0
        and not timed_out
        and bool(hello_result.get("ok"))
        and hello_result.get("protocol") == "metis.vm.guest.v1"
        and "runtime.hello" in (hello_result.get("methods") or [])
    )
    receipt = {
        "schema": METIS_VM_GUEST_HANDSHAKE_SCHEMA,
        "created_at": time.time(),
        "duration_ms": int((time.time() - started) * 1000),
        "transport": "jsonl-stdio",
        "proof_scope": "host-only protocol smoke",
        "promotes_runner_ready": False,
        "handshake_id": handshake_id,
        "handshake_verified": verified,
        "method": "runtime.hello",
        "expected_protocol": "metis.vm.guest.v1",
        "hello_result": hello_result if isinstance(hello_result, dict) else {},
        "responses": responses,
        "returncode": returncode,
        "timed_out": timed_out,
        "stdout": _truncate(stdout),
        "stderr": _truncate(stderr),
        "handshake_root": str(handshake_root),
        "workspace": str(workspace),
        "artifacts_dir": str(artifacts),
        "diagnostics_dir": str(diagnostics),
        "lifecycle_log": str(diagnostics / "lifecycle.jsonl"),
    }
    receipt_dir = bundle / "host" / "handshake-receipts"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    receipt_path = receipt_dir / f"{handshake_id}.json"
    receipt["receipt_path"] = str(receipt_path)
    receipt["receipt_relative_path"] = _relative_to(receipt_path, bundle)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return receipt


def _record_guest_handshake_receipt(
    bundle: Path,
    *,
    receipt: Dict[str, Any],
    runner_ready: bool,
    runner_ready_reason: str,
) -> Dict[str, Any]:
    manifest_path = bundle / GUEST_HANDSHAKE_MANIFEST_NAME
    if manifest_path.is_file():
        manifest = _read_json_object(manifest_path)
    else:
        manifest = {
            "schema": METIS_VM_GUEST_HANDSHAKE_SCHEMA,
            "owner": "metis",
            "version": "0.1.0-local",
            "created_at": time.time(),
            "bundle_path": str(bundle),
            "prepared": False,
            "verifier_ready": False,
            "transport": {},
        }
    transport = str(receipt.get("transport") or "")
    verified = bool(receipt.get("handshake_verified"))
    if transport == "jsonl-stdio":
        manifest["stdio_handshake_verified"] = verified
    if transport == "hcs-vsock-jsonl":
        manifest["hcs_handshake_verified"] = verified
    hcs_verified = bool(manifest.get("hcs_handshake_verified"))
    manifest["runner_ready"] = bool(runner_ready and hcs_verified)
    manifest["runner_ready_reason"] = runner_ready_reason
    manifest["last_handshake_receipt"] = receipt.get("receipt_relative_path") or receipt.get("receipt_path") or ""
    manifest["updated_at"] = time.time()
    receipts = manifest.get("receipts")
    if not isinstance(receipts, list):
        receipts = []
    receipts.append(
        {
            "transport": transport,
            "handshake_verified": verified,
            "receipt": manifest["last_handshake_receipt"],
            "created_at": receipt.get("created_at"),
            "promotes_runner_ready": bool(manifest["runner_ready"]),
        }
    )
    manifest["receipts"] = receipts[-20:]
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    _upsert_guest_handshake_pack_manifest(bundle, manifest)
    return manifest


def _guest_handshake_script() -> str:
    return """param(
  [string]$Bundle = "",
  [ValidateSet('jsonl-stdio','hcs-vsock-jsonl')]
  [string]$Transport = 'hcs-vsock-jsonl',
  [string]$ComputeSystemId = "",
  [int]$TimeoutSeconds = 30,
  [switch]$EnableExperimentalHcsHandshake
)

$ErrorActionPreference = 'Stop'
if (-not $Bundle) {
  $Bundle = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}
$Report = @{
  schema = 'metis.vm_direct.guest_handshake_script.v1'
  bundle = $Bundle
  transport = $Transport
  computeSystemId = $ComputeSystemId
  timeoutSeconds = $TimeoutSeconds
  expectedMethod = 'runtime.hello'
  expectedProtocol = 'metis.vm.guest.v1'
  runnerReadyOnSuccess = ($Transport -eq 'hcs-vsock-jsonl')
}
if ($Transport -eq 'jsonl-stdio') {
  $Report.ok = $false
  $Report.code = 'USE_BACKEND_STDIO_VERIFIER'
  $Report.reason = 'Use metis_vm_guest_handshake_verify transport=jsonl-stdio so Python captures the JSONL receipt and lifecycle log.'
  $Report | ConvertTo-Json -Depth 8
  exit 0
}
if (-not $EnableExperimentalHcsHandshake) {
  $Report.ok = $false
  $Report.code = 'METIS_GUEST_HANDSHAKE_EXPERIMENTAL_FLAG_REQUIRED'
  $Report.reason = 'Set -EnableExperimentalHcsHandshake only after reviewing the HCS start and transport plan.'
  $Report | ConvertTo-Json -Depth 8
  exit 0
}
$Report.ok = $false
$Report.code = 'METIS_GUEST_HANDSHAKE_TRANSPORT_UNAVAILABLE'
$Report.reason = 'The HCS/vsock JSONL transport bridge is not implemented yet, so this script cannot receive runtime.hello from metisd.'
$Report | ConvertTo-Json -Depth 8
"""


def _rootfs_inspect_script() -> str:
    return """param(
  [string]$Bundle = "",
  [switch]$Mount,
  [switch]$Dismount
)

$ErrorActionPreference = 'Stop'
if (-not $Bundle) {
  $Bundle = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}
$Rootfs = Join-Path $Bundle 'rootfs.vhdx'
$Report = @{
  schema = 'metis.vm_direct.rootfs_inspect.v1'
  bundle = $Bundle
  rootfs = $Rootfs
  exists = Test-Path $Rootfs
  mounted = $false
  volumes = @()
  hints = @()
}
if (-not (Test-Path $Rootfs)) {
  $Report.hints += 'rootfs.vhdx is missing'
  $Report | ConvertTo-Json -Depth 10
  exit 0
}
if ($Mount) {
  Mount-DiskImage -ImagePath $Rootfs -ErrorAction Stop | Out-Null
}
$image = Get-DiskImage -ImagePath $Rootfs -ErrorAction SilentlyContinue
if ($image) {
  $Report.mounted = [bool]$image.Attached
  if ($image.Attached) {
    $disk = $image | Get-Disk -ErrorAction SilentlyContinue
    if ($disk) {
      $parts = $disk | Get-Partition -ErrorAction SilentlyContinue
      foreach ($part in $parts) {
        $vol = $part | Get-Volume -ErrorAction SilentlyContinue
        if ($vol) {
          $Report.volumes += @{
            driveLetter = $vol.DriveLetter
            fileSystem = $vol.FileSystem
            size = $vol.Size
            sizeRemaining = $vol.SizeRemaining
          }
        }
      }
    }
  }
}
if (-not $Report.mounted) {
  $Report.hints += 'Use -Mount from an elevated PowerShell to inspect files inside rootfs.vhdx.'
}
if ($Dismount) {
  Dismount-DiskImage -ImagePath $Rootfs -ErrorAction SilentlyContinue | Out-Null
}
$Report | ConvertTo-Json -Depth 10
"""


def _rootfs_boot_verifier_script() -> str:
    return """param(
  [string]$Bundle = "",
  [switch]$DryRun,
  [switch]$EnableExperimentalHcsStart,
  [int]$TimeoutSeconds = 120,
  [int]$HoldSeconds = 3
)

$ErrorActionPreference = 'Stop'
if (-not $Bundle) {
  $Bundle = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
}
$MatrixPath = Join-Path $Bundle 'host\\boot-cmdline-matrix.json'
$Starter = Join-Path $Bundle 'host\\hcs-starter.ps1'
if (-not (Test-Path $MatrixPath)) { throw "boot-cmdline-matrix.json not found: $MatrixPath" }
if (-not (Test-Path $Starter)) { throw "hcs-starter.ps1 not found: $Starter" }
$Matrix = Get-Content -LiteralPath $MatrixPath -Raw -Encoding UTF8 | ConvertFrom-Json
$Attempts = @()
foreach ($Candidate in $Matrix.candidates) {
  $Id = $Candidate.id
  $Doc = Join-Path $Bundle "host\\boot-candidates\\$Id.hcs-compute-system.json"
  $SystemId = "metis-boot-$Id"
  $Cmd = @(
    'powershell', '-ExecutionPolicy', 'Bypass', '-File', $Starter,
    '-Bundle', $Bundle,
    '-ComputeSystemId', $SystemId,
    '-ComputeDocument', $Doc,
    '-TimeoutSeconds', "$TimeoutSeconds",
    '-HoldSeconds', "$HoldSeconds"
  )
  if ($EnableExperimentalHcsStart) { $Cmd += '-EnableExperimentalHcsStart' }
  $Attempts += @{
    candidate_id = $Id
    kernel_cmdline = $Candidate.kernel_cmdline
    compute_document = $Doc
    command = $Cmd
  }
}
if ($DryRun -or -not $EnableExperimentalHcsStart) {
  @{
    schema = 'metis.vm_direct.rootfs_boot_verifier_script.v1'
    dry_run = $true
    attempts = $Attempts
    warning = 'No VM was started. Use -EnableExperimentalHcsStart without -DryRun to call HCS.'
  } | ConvertTo-Json -Depth 12
  exit 0
}
throw "Use backend metis_vm_rootfs_boot_verify for execution so results are captured into Metis diagnostics."
"""


def _read_vm_pack_manifest(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {"ok": False, "path": str(path), "reason": "manifest not found"}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"ok": False, "path": str(path), "reason": f"{type(exc).__name__}: {exc}"}
    if not isinstance(data, dict):
        return {"ok": False, "path": str(path), "reason": "manifest root must be an object"}
    required = data.get("required_boot_assets")
    if not isinstance(required, list):
        required = list(VM_REQUIRED_FILES)
    return {
        "ok": True,
        "path": str(path),
        "data": data,
        "required_boot_assets": [str(item) for item in required],
    }


def _load_vm_manifest_data(bundle_dir: Path) -> Dict[str, Any]:
    manifest = _read_vm_pack_manifest(bundle_dir / VM_MANIFEST_NAME)
    data = manifest.get("data") if isinstance(manifest.get("data"), dict) else {}
    if isinstance(data, dict):
        return data
    return {}


def _write_vm_manifest_data(bundle_dir: Path, data: Dict[str, Any]) -> None:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    data.setdefault("schema", VM_PACK_MANIFEST_SCHEMA)
    data.setdefault("name", "metisvm")
    data.setdefault("owner", "metis")
    (bundle_dir / VM_MANIFEST_NAME).write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _resolve_rootfs_source(
    *,
    source_root: Path,
    manifest_url: str = "",
    manifest_path: str = "",
    asset_url: str = "",
    expected_sha256: str = "",
    signature_url: str = "",
) -> Dict[str, Any]:
    manifest: Dict[str, Any] = {}
    assets: List[Dict[str, Any]] = []
    source: Dict[str, Any] = {"kind": "direct", "path": "", "url": ""}
    base_ref = ""

    path_value = str(manifest_path or "").strip()
    url_value = str(manifest_url or "").strip()
    if path_value and url_value:
        raise ValueError("Provide either manifest_path or manifest_url, not both")
    if path_value:
        manifest_file = safe_path_for_read(
            path_value,
            allow_paths_outside_workspace=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        manifest = _read_rootfs_json_file(manifest_file)
        base_ref = str(manifest_file.parent)
        source = {"kind": "manifest_path", "path": str(manifest_file), "url": ""}
        assets = _normalize_rootfs_source_manifest(manifest, base_ref=base_ref)
    elif url_value:
        manifest, final_url = _read_rootfs_json_url(url_value)
        base_ref = final_url
        source = {"kind": "manifest_url", "path": "", "url": final_url}
        assets = _normalize_rootfs_source_manifest(manifest, base_ref=base_ref)

    direct_url = str(asset_url or "").strip()
    explicit_sha = _extract_sha256(expected_sha256)
    explicit_signature = str(signature_url or "").strip()
    if direct_url:
        resolved_url = _resolve_rootfs_asset_reference(direct_url, base_ref=base_ref, source_root=source_root)
        direct_asset = {
            "name": _asset_name_from_url(resolved_url) or "rootfs",
            "url": resolved_url,
            "raw_url": direct_url,
            "sha256": explicit_sha,
            "manifest_sha256": "",
            "signature_url": _resolve_rootfs_asset_reference(
                explicit_signature,
                base_ref=base_ref or resolved_url,
                source_root=source_root,
            )
            if explicit_signature
            else "",
            "size_bytes": 0,
            "source": "direct",
        }
        assets.insert(0, direct_asset)
    elif explicit_sha and assets:
        manifest_sha = str(assets[0].get("sha256") or "")
        assets[0]["manifest_sha256"] = manifest_sha
        assets[0]["sha256"] = explicit_sha
        assets[0]["sha256_conflict"] = bool(manifest_sha and manifest_sha != explicit_sha)
    elif explicit_signature and assets:
        assets[0]["signature_url"] = _resolve_rootfs_asset_reference(
            explicit_signature,
            base_ref=base_ref or str(assets[0].get("url") or ""),
            source_root=source_root,
        )

    selected = next((item for item in assets if str(item.get("url") or "").strip()), {})
    return {
        "source": source,
        "manifest": {
            "provided": bool(manifest),
            "schema": str(manifest.get("schema") or "") if manifest else "",
            "version": str(manifest.get("version") or "") if manifest else "",
            "asset_count": len(assets),
        },
        "assets": assets,
        "selected_asset": selected,
        "sha256_available": bool(selected.get("sha256")) if selected else False,
        "download_required_sha256": True,
    }


def _read_rootfs_json_file(path: Path) -> Dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"failed to read rootfs source manifest: {type(exc).__name__}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("rootfs source manifest must be a JSON object")
    return data


def _read_rootfs_json_url(url: str) -> Tuple[Dict[str, Any], str]:
    parsed = urllib.parse.urlparse(str(url or "").strip())
    if parsed.scheme not in {"http", "https", "file"}:
        local_path = safe_path_for_read(
            str(url or ""),
            allow_paths_outside_workspace=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        return _read_rootfs_json_file(local_path), str(local_path)
    if parsed.scheme == "file":
        local_path = _file_url_to_path(str(url or ""))
        local_path = safe_path_for_read(
            str(local_path),
            allow_paths_outside_workspace=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        return _read_rootfs_json_file(local_path), str(local_path)
    request = urllib.request.Request(
        str(url),
        headers={"User-Agent": "MetisRootfsSource/1.0"},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            final_url = response.geturl() or str(url)
            raw = response.read(2 * 1024 * 1024)
    except Exception as exc:
        raise ValueError(f"failed to fetch rootfs source manifest: {type(exc).__name__}: {exc}") from exc
    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ValueError(f"failed to parse rootfs source manifest JSON: {type(exc).__name__}: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("rootfs source manifest must be a JSON object")
    return data, final_url


def _normalize_rootfs_source_manifest(manifest: Dict[str, Any], *, base_ref: str) -> List[Dict[str, Any]]:
    raw_assets: List[Dict[str, Any]] = []
    assets = manifest.get("assets")
    if isinstance(assets, list):
        raw_assets.extend(item for item in assets if isinstance(item, dict))
    elif isinstance(assets, dict):
        rootfs = assets.get("rootfs")
        if isinstance(rootfs, dict):
            raw_assets.append(rootfs)
        for key, value in assets.items():
            if key == "rootfs":
                continue
            if isinstance(value, dict):
                item = dict(value)
                item.setdefault("name", str(key))
                raw_assets.append(item)
    rootfs = manifest.get("rootfs")
    if isinstance(rootfs, dict):
        raw_assets.append(rootfs)
    if any(key in manifest for key in ("url", "asset_url", "download_url", "path", "file")):
        raw_assets.append(manifest)

    normalized: List[Dict[str, Any]] = []
    for index, raw in enumerate(raw_assets):
        item = _normalize_rootfs_source_asset(raw, base_ref=base_ref, index=index)
        if item.get("url"):
            normalized.append(item)
    return normalized


def _normalize_rootfs_source_asset(raw: Dict[str, Any], *, base_ref: str, index: int) -> Dict[str, Any]:
    url = _first_string(raw, "url", "asset_url", "download_url", "path", "file")
    resolved_url = _resolve_rootfs_asset_reference(url, base_ref=base_ref)
    signature_value = _first_string(raw, "signature_url", "sig_url", "signature_path")
    signature = raw.get("signature")
    if not signature_value and isinstance(signature, dict):
        signature_value = _first_string(signature, "url", "signature_url", "path", "file")
    resolved_signature = (
        _resolve_rootfs_asset_reference(signature_value, base_ref=base_ref) if signature_value else ""
    )
    sha = _extract_sha256(
        _first_string(raw, "sha256", "expected_sha256", "checksum", "digest", "sha256sum")
    )
    return {
        "name": _first_string(raw, "name", "id") or _asset_name_from_url(resolved_url) or f"rootfs-{index + 1}",
        "url": resolved_url,
        "raw_url": url,
        "sha256": sha,
        "manifest_sha256": sha,
        "signature_url": resolved_signature,
        "size_bytes": _coerce_int(raw.get("size_bytes") or raw.get("size") or 0),
        "version": _first_string(raw, "version"),
        "arch": _first_string(raw, "arch", "architecture"),
        "os": _first_string(raw, "os", "base_os"),
        "source": "manifest",
    }


def _first_string(mapping: Dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return ""


def _coerce_int(value: Any) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def _extract_sha256(value: str) -> str:
    text = str(value or "").strip().lower()
    if text.startswith("sha256:"):
        text = text.split(":", 1)[1].strip()
    match = re.search(r"\b[0-9a-f]{64}\b", text)
    return match.group(0) if match else ""


def _resolve_rootfs_asset_reference(value: str, *, base_ref: str = "", source_root: Optional[Path] = None) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme in {"http", "https", "file"}:
        return raw
    if _looks_like_windows_path(raw) or Path(raw).is_absolute():
        return str(Path(raw).expanduser().resolve(strict=False))
    if base_ref:
        base_parsed = urllib.parse.urlparse(base_ref)
        if base_parsed.scheme in {"http", "https"}:
            return urllib.parse.urljoin(base_ref, raw)
        if base_parsed.scheme == "file":
            base_path = _file_url_to_path(base_ref)
            base_dir = base_path if base_path.is_dir() else base_path.parent
            return str((base_dir / raw).resolve(strict=False))
        base_path = Path(base_ref).expanduser().resolve(strict=False)
        base_dir = base_path if base_path.is_dir() else base_path.parent
        return str((base_dir / raw).resolve(strict=False))
    root = source_root or Path.cwd()
    return str((root / raw).resolve(strict=False))


def _looks_like_windows_path(value: str) -> bool:
    return bool(re.match(r"^[A-Za-z]:[\\/]", str(value or ""))) or str(value or "").startswith("\\\\")


def _asset_name_from_url(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""
    parsed = urllib.parse.urlparse(raw)
    if parsed.scheme in {"http", "https", "file"}:
        path_text = urllib.parse.unquote(parsed.path or "")
        return Path(path_text).name
    return Path(raw).name


def _resolve_rootfs_download_target(
    *,
    source_root: Path,
    bundle_dir: Path,
    output_path: str,
    asset: Dict[str, Any],
) -> Path:
    raw = str(output_path or "").strip()
    if raw:
        return safe_path_for_write(raw).resolve(strict=False)
    filename = _asset_name_from_url(str(asset.get("url") or "")) or str(asset.get("name") or "rootfs.tar")
    return (bundle_dir / _canonical_rootfs_asset_name(Path(filename))).resolve(strict=False)


def _download_or_copy_rootfs(url: str, target: Path) -> None:
    value = str(url or "").strip()
    if not value:
        raise ValueError("rootfs source URL/path is required")
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.part-{uuid.uuid4().hex[:8]}")
    try:
        parsed = urllib.parse.urlparse(value)
        if parsed.scheme in {"http", "https"}:
            request = urllib.request.Request(
                value,
                headers={"User-Agent": "MetisRootfsDownloader/1.0"},
                method="GET",
            )
            with urllib.request.urlopen(request, timeout=900) as response, tmp.open("wb") as handle:
                shutil.copyfileobj(response, handle, length=1024 * 1024)
        else:
            local = _file_url_to_path(value) if parsed.scheme == "file" else Path(value).expanduser()
            local = safe_path_for_read(
                str(local),
                allow_paths_outside_workspace=get_effective_sub_allow("allow_paths_outside_workspace"),
            )
            shutil.copy2(local, tmp)
        os.replace(tmp, target)
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _file_url_to_path(url: str) -> Path:
    parsed = urllib.parse.urlparse(str(url or ""))
    if parsed.scheme != "file":
        return Path(url).expanduser().resolve(strict=False)
    path_text = urllib.request.url2pathname(parsed.path or "")
    if parsed.netloc:
        path_text = f"//{parsed.netloc}{path_text}"
    return Path(path_text).expanduser().resolve(strict=False)


def _detect_rootfs_builder(
    *,
    source_root: Path,
    bundle_dir: Path,
    backend: str,
    base_image: str,
    wsl_distro: str,
) -> Dict[str, Any]:
    requested = _normalize_rootfs_build_backend(backend)
    selected_image = _normalize_docker_reference(base_image, default="ubuntu:22.04")
    docker = _detect_docker(docker_image=selected_image)
    docker["base_image"] = selected_image
    docker["base_image_available"] = bool(docker.get("image_available"))
    wsl = _detect_wsl(wsl_distro=wsl_distro)
    builder_dir = bundle_dir / "builder"
    expected_files = [
        builder_dir / "Dockerfile.rootfs",
        builder_dir / "build-rootfs-docker.ps1",
        builder_dir / "build-rootfs-wsl.sh",
        builder_dir / "rootfs-build-plan.json",
        builder_dir / "install-runtime-tools.sh",
        bundle_dir / "guest" / "metisd.py",
    ]
    missing = [str(path) for path in expected_files if not path.is_file()]
    selected = requested
    reason = ""
    if requested == "auto":
        if docker.get("available"):
            selected = "docker"
            reason = "Docker daemon is available and can export a rootfs tar"
        elif wsl.get("available"):
            selected = "wsl_script"
            reason = "WSL is available; v1 writes a debootstrap script for manual/approved execution"
        else:
            selected = "script_only"
            reason = "No Docker/WSL builder backend detected; v1 can still write auditable build scripts"
    elif requested == "docker" and not docker.get("available"):
        selected = "script_only"
        reason = f"Docker requested but unavailable: {docker.get('reason') or 'unknown'}"
    elif requested == "wsl":
        selected = "wsl_script" if wsl.get("available") else "script_only"
        reason = (
            "WSL requested; v1 writes a debootstrap script but does not execute sudo/root build steps automatically"
            if wsl.get("available")
            else f"WSL requested but unavailable: {wsl.get('reason') or 'unknown'}"
        )
    elif requested == "script_only":
        selected = "script_only"
        reason = "script_only requested"
    elif requested == "docker":
        reason = "Docker requested and available"
    return {
        "requested_backend": requested,
        "selected_backend": selected,
        "reason": reason,
        "docker": docker,
        "wsl": wsl,
        "builder_dir": str(builder_dir),
        "script_status": {
            "expected_files": [str(path) for path in expected_files],
            "missing": missing,
            "ready": not missing,
        },
        "supports": {
            "docker_execute": bool(docker.get("available")),
            "wsl_script": bool(wsl.get("available")),
            "script_only": True,
        },
        "notes": [
            "Docker is the first executable rootfs build backend in v1.",
            "WSL/debootstrap script generation is supported, but automatic WSL execution is intentionally deferred.",
            "Build output must still pass SHA256 verification and registration before WSL import.",
        ],
    }


def _normalize_rootfs_build_backend(value: str) -> str:
    text = str(value or "auto").strip().lower().replace("-", "_")
    if text in {"auto", "docker", "wsl", "script_only"}:
        return text
    if text in {"plan", "manual", "scripts"}:
        return "script_only"
    return "auto"


def _normalize_rootfs_build_profile(value: str) -> str:
    text = str(value or "standard").strip().lower().replace("-", "_")
    if text in {"minimal", "smoke"}:
        return "minimal"
    if text in {"office", "full", "documents", "docs"}:
        return "office"
    return "standard"


def _normalize_docker_reference(value: str, *, default: str) -> str:
    text = str(value or "").strip()
    if not text:
        return default
    if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.:/@-]{0,220}", text):
        return text
    return default


def _default_rootfs_image_tag() -> str:
    return f"metis/rootfs:{int(time.time())}-{uuid.uuid4().hex[:8]}"


def _resolve_rootfs_build_output(*, bundle_dir: Path, output_path: str) -> Path:
    raw = str(output_path or "").strip()
    if raw:
        return safe_path_for_write(raw).resolve(strict=False)
    return (bundle_dir / "rootfs.tar").resolve(strict=False)


def _rootfs_build_plan(
    *,
    bundle_dir: Path,
    target: Path,
    backend: str,
    base_image: str,
    image_tag: str,
    profile: str,
    allow_network: bool,
    register: bool,
    keep_image: bool,
) -> Dict[str, Any]:
    container_name = _rootfs_build_container_name(image_tag)
    return {
        "backend": backend,
        "base_image": base_image,
        "image_tag": image_tag,
        "container_name": container_name,
        "profile": profile,
        "allow_network": bool(allow_network),
        "register": bool(register),
        "keep_image": bool(keep_image),
        "bundle_path": str(bundle_dir),
        "target_path": str(target),
        "docker_commands": [
            ["docker", "build", "-f", "builder/Dockerfile.rootfs", "-t", image_tag, "."],
            ["docker", "create", "--name", container_name, image_tag],
            ["docker", "export", container_name, "-o", str(target)],
            ["docker", "rm", "-f", container_name],
        ],
        "wsl_script": str(bundle_dir / "builder" / "build-rootfs-wsl.sh"),
    }


def _rootfs_build_container_name(image_tag: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(image_tag or "metis-rootfs")).strip(".-")
    return f"metis-rootfs-{slug[-48:]}-{uuid.uuid4().hex[:6]}"


def _write_rootfs_builder_files(
    *,
    bundle_dir: Path,
    base_image: str,
    image_tag: str,
    profile: str,
    target: Path,
) -> List[Dict[str, Any]]:
    bundle_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = bundle_dir / VM_MANIFEST_NAME
    guest_path = bundle_dir / "guest" / "metisd.py"
    install_path = bundle_dir / "builder" / "install-runtime-tools.sh"
    readme_path = bundle_dir / "builder" / "README.md"
    if not manifest_path.is_file():
        manifest_path.write_text(_vm_pack_manifest_template(), encoding="utf-8", newline="\n")
    if not guest_path.is_file():
        guest_path.parent.mkdir(parents=True, exist_ok=True)
        guest_path.write_text(_vm_guest_metisd_stub(), encoding="utf-8", newline="\n")
    install_path.parent.mkdir(parents=True, exist_ok=True)
    install_path.write_text(_vm_rootfs_install_tools_script(), encoding="utf-8", newline="\n")
    policy_path = bundle_dir / "builder" / "runtime-policy.json"
    policy_path.write_text(
        json.dumps(_rootfs_runtime_policy(profile=profile), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    if not readme_path.is_file():
        readme_path.parent.mkdir(parents=True, exist_ok=True)
        readme_path.write_text(_vm_rootfs_builder_readme(), encoding="utf-8", newline="\n")
    generated = {
        bundle_dir / "builder" / "Dockerfile.rootfs": _rootfs_dockerfile(
            base_image=base_image,
            profile=profile,
        ),
        bundle_dir / "builder" / "build-rootfs-docker.ps1": _rootfs_docker_build_ps1(
            image_tag=image_tag,
            target=target,
        ),
        bundle_dir / "builder" / "build-rootfs-wsl.sh": _rootfs_wsl_build_script(
            target=target,
        ),
        bundle_dir / "builder" / "rootfs-build-plan.json": json.dumps(
            _rootfs_build_plan(
                bundle_dir=bundle_dir,
                target=target,
                backend="docker",
                base_image=base_image,
                image_tag=image_tag,
                profile=profile,
                allow_network=profile in {"standard", "office"},
                register=True,
                keep_image=True,
            ),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
    }
    written: List[Dict[str, Any]] = []
    for path, content in generated.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append(
            {
                "path": str(path),
                "relative_path": _relative_to(path, bundle_dir),
                "size_bytes": len(content.encode("utf-8")),
            }
        )
    for path in (manifest_path, guest_path, install_path, policy_path, readme_path):
        if path.is_file():
            written.append(
                {
                    "path": str(path),
                    "relative_path": _relative_to(path, bundle_dir),
                    "size_bytes": path.stat().st_size,
                    "ensured": True,
                }
            )
    return written


def _rootfs_dockerfile(*, base_image: str, profile: str) -> str:
    install_block = ""
    if profile in {"standard", "office"}:
        install_block = (
            f"ARG METIS_ROOTFS_PROFILE={profile}\n"
            "ENV METIS_ROOTFS_PROFILE=${METIS_ROOTFS_PROFILE}\n"
            "COPY builder/install-runtime-tools.sh /tmp/install-runtime-tools.sh\n"
            "RUN chmod +x /tmp/install-runtime-tools.sh && /tmp/install-runtime-tools.sh\n"
        )
    return f"""FROM {base_image}
ENV DEBIAN_FRONTEND=noninteractive
{install_block}RUN mkdir -p /etc/metis
COPY guest/metisd.py /usr/local/bin/metisd
COPY builder/runtime-policy.json /etc/metis/runtime-policy.json
RUN chmod +x /usr/local/bin/metisd \\
  && mkdir -p /mnt/.metisfs-root/shared/workspace \\
  && mkdir -p /mnt/.metisfs-root/shared/artifacts \\
  && mkdir -p /mnt/.metisfs-root/shared/diagnostics \\
  && mkdir -p /mnt/.metisfs-root/shared/uploads \\
  && mkdir -p /mnt/.metisfs-root/shared/outputs \\
  && mkdir -p /mnt/.metisfs-root/shared/.metis-perm-req \\
  && mkdir -p /mnt/.metisfs-root/shared/.metis-perm-resp \\
  && mkdir -p /workspace /artifacts /diagnostics /uploads /outputs
LABEL org.metis.rootfs.profile="{profile}"
LABEL org.metis.rootfs.schema="{ROOTFS_BUILD_SCHEMA}"
CMD ["/bin/sh"]
"""


def _rootfs_docker_build_ps1(*, image_tag: str, target: Path) -> str:
    target_text = str(target).replace("'", "''")
    tag_text = str(image_tag).replace("'", "''")
    return f"""param(
  [string]$ImageTag = '{tag_text}',
  [string]$Target = '{target_text}'
)

$ErrorActionPreference = 'Stop'
$Bundle = Split-Path -Parent $PSScriptRoot
$Container = ('metis-rootfs-' + ([guid]::NewGuid().ToString('N')).Substring(0, 12))
docker build -f (Join-Path $PSScriptRoot 'Dockerfile.rootfs') -t $ImageTag $Bundle
docker create --name $Container $ImageTag | Out-Null
try {{
  docker export $Container -o $Target
}} finally {{
  docker rm -f $Container | Out-Null
}}
"""


def _rootfs_wsl_build_script(*, target: Path) -> str:
    target_wsl = _windows_path_to_wsl(target)
    return f"""#!/usr/bin/env bash
set -euo pipefail

TARGET="${{1:-{target_wsl}}}"
WORK="${{METIS_ROOTFS_WORK:-/tmp/metis-rootfs-build}}"
SUITE="${{METIS_ROOTFS_SUITE:-jammy}}"
MIRROR="${{METIS_ROOTFS_MIRROR:-http://archive.ubuntu.com/ubuntu}}"

if ! command -v debootstrap >/dev/null 2>&1; then
  echo "debootstrap is required. Install it with: sudo apt-get install -y debootstrap" >&2
  exit 2
fi

sudo rm -rf "$WORK"
sudo mkdir -p "$WORK"
sudo debootstrap --variant=minbase "$SUITE" "$WORK" "$MIRROR"
sudo cp "$(dirname "$0")/install-runtime-tools.sh" "$WORK/tmp/install-runtime-tools.sh"
sudo cp "$(dirname "$0")/../guest/metisd.py" "$WORK/usr/local/bin/metisd"
sudo mkdir -p "$WORK/etc/metis"
sudo cp "$(dirname "$0")/runtime-policy.json" "$WORK/etc/metis/runtime-policy.json"
sudo chroot "$WORK" /bin/bash -lc "chmod +x /tmp/install-runtime-tools.sh /usr/local/bin/metisd && /tmp/install-runtime-tools.sh"
sudo tar -C "$WORK" -cpf "$TARGET" .
sudo rm -rf "$WORK"
"""


def _run_docker_rootfs_build(
    *,
    docker_exe: str,
    bundle_dir: Path,
    target: Path,
    base_image: str,
    image_tag: str,
    profile: str,
    allow_network: bool,
    keep_image: bool,
    timeout: int,
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    steps: List[Dict[str, Any]] = []
    cleanup_steps: List[Dict[str, Any]] = []
    container = _rootfs_build_container_name(image_tag)
    if allow_network:
        pull = _run_rootfs_builder_step(
            [docker_exe, "pull", base_image],
            cwd=bundle_dir,
            timeout=max(60, min(timeout, 900)),
            step="docker_pull_base_image",
        )
        steps.append(pull)
        _raise_if_step_failed(pull)
    build_args = [
        docker_exe,
        "build",
        "-f",
        "builder/Dockerfile.rootfs",
        "-t",
        image_tag,
    ]
    if allow_network:
        proxy_args = _docker_build_proxy_args()
        if proxy_args:
            build_args.extend(proxy_args)
        build_args.extend(["--build-arg", f"METIS_ROOTFS_PROFILE={profile}"])
    if not allow_network:
        build_args.extend(["--network", "none"])
    build_args.append(".")
    build = _run_rootfs_builder_step(
        build_args,
        cwd=bundle_dir,
        timeout=timeout,
        step=f"docker_build_{profile}",
    )
    steps.append(build)
    _raise_if_step_failed(build)
    create = _run_rootfs_builder_step(
        [docker_exe, "create", "--name", container, image_tag],
        cwd=bundle_dir,
        timeout=120,
        step="docker_create_container",
    )
    steps.append(create)
    _raise_if_step_failed(create)
    try:
        export = _run_rootfs_builder_step(
            [docker_exe, "export", container, "-o", str(target)],
            cwd=bundle_dir,
            timeout=timeout,
            step="docker_export_rootfs",
        )
        steps.append(export)
        _raise_if_step_failed(export)
    finally:
        cleanup_steps.append(
            _run_rootfs_builder_step(
                [docker_exe, "rm", "-f", container],
                cwd=bundle_dir,
                timeout=120,
                step="docker_cleanup_container",
            )
        )
    if not keep_image:
        cleanup_steps.append(
            _run_rootfs_builder_step(
                [docker_exe, "rmi", image_tag],
                cwd=bundle_dir,
                timeout=180,
                step="docker_cleanup_image",
            )
        )
    return steps, cleanup_steps


def _normalize_rootfs_image_backend(value: str) -> str:
    text = str(value or "auto").strip().lower().replace("-", "_")
    if text in {"wsl", "wsl2", "wsl_import", "wslimport"}:
        return "wsl_import"
    if text in {"script", "script_only", "manual", "plan"}:
        return "script_only"
    return "auto"


def _normalize_rootfs_image_distro_name(name: str = "") -> str:
    value = str(name or "").strip()
    if not value:
        value = "MetisRootfsImageBuilder"
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return cleaned or "MetisRootfsImageBuilder"


def _resolve_rootfs_image_output(*, bundle_dir: Path, output_path: str) -> Path:
    raw = str(output_path or "").strip()
    if raw:
        return safe_path_for_write(raw).resolve(strict=False)
    return (bundle_dir / "rootfs.vhdx").resolve(strict=False)


def _resolve_rootfs_image_tar(*, bundle_dir: Path, rootfs_tar_path: str) -> Path:
    raw = str(rootfs_tar_path or "").strip()
    if raw:
        return safe_path_for_write(raw).resolve(strict=False)
    return (bundle_dir / "rootfs.tar").resolve(strict=False)


def _resolve_rootfs_image_install_dir(source_root: Path, install_dir: str, distro: str) -> Path:
    raw = str(install_dir or "").strip()
    if raw:
        return safe_path_for_write(raw).resolve(strict=False)
    return (source_root / ".metis" / "rootfs-image" / "wsl" / distro).resolve(strict=False)


def _detect_rootfs_image_builder(
    *,
    source_root: Path,
    bundle_dir: Path,
    backend: str,
    rootfs_tar_path: str,
    output_path: str,
    temp_distro_name: str,
    install_dir: str,
) -> Dict[str, Any]:
    requested = _normalize_rootfs_image_backend(backend)
    rootfs_tar = _resolve_rootfs_image_tar(bundle_dir=bundle_dir, rootfs_tar_path=rootfs_tar_path)
    target = _resolve_rootfs_image_output(bundle_dir=bundle_dir, output_path=output_path)
    distro = _normalize_rootfs_image_distro_name(temp_distro_name)
    install_path = _resolve_rootfs_image_install_dir(source_root, install_dir, distro)
    wsl_exe = shutil.which("wsl.exe") or shutil.which("wsl") or ""
    features = _detect_wsl_import_features(wsl_exe)
    wsl = {
        "available": bool(wsl_exe and features.get("import_supported")),
        "executable": wsl_exe,
        "features": features,
        "reason": "" if wsl_exe and features.get("import_supported") else (features.get("reason") or "wsl import not available"),
    }
    selected = requested
    reason = ""
    if requested == "auto":
        if wsl.get("available"):
            selected = "wsl_import"
            reason = "WSL import is available and can generate an ext4.vhdx from rootfs.tar"
        else:
            selected = "script_only"
            reason = str(wsl.get("reason") or "WSL import unavailable")
    elif requested == "wsl_import" and not wsl.get("available"):
        selected = "script_only"
        reason = f"WSL import requested but unavailable: {wsl.get('reason') or 'unknown'}"
    elif requested == "wsl_import":
        reason = "WSL import requested and available"
    else:
        selected = "script_only"
        reason = "script_only requested"
    files = {
        "manifest": bundle_dir / ROOTFS_IMAGE_BUILDER_MANIFEST_NAME,
        "script": bundle_dir / "builder" / "build-rootfs-vhdx-wsl.ps1",
        "plan": bundle_dir / "builder" / "rootfs-image-plan.json",
        "layout": bundle_dir / "builder" / "rootfs-image-layout.json",
        "runtime_policy": bundle_dir / "builder" / "runtime-policy.json",
        "guest_daemon": bundle_dir / "guest" / "metisd.py",
    }
    rows = [
        {
            "name": name,
            "relative_path": _relative_to(path, bundle_dir),
            "path": str(path),
            "exists": path.is_file(),
            "size_bytes": path.stat().st_size if path.is_file() else 0,
        }
        for name, path in files.items()
    ]
    missing = [row["relative_path"] for row in rows if not row["exists"] and row["name"] != "manifest"]
    return {
        "requested_backend": requested,
        "selected_backend": selected,
        "reason": reason,
        "rootfs_tar": _inspect_rootfs_import_asset(rootfs_tar),
        "target_vhdx": _inspect_rootfs_import_asset(target),
        "temp_distro_name": distro,
        "install_dir": str(install_path),
        "wsl": wsl,
        "script_status": {
            "expected_files": [str(path) for path in files.values()],
            "missing": missing,
            "ready": not missing,
        },
        "supports": {
            "wsl_import_execute": bool(wsl.get("available")),
            "script_only": True,
            "direct_python_ext4_vhdx": False,
        },
        "notes": [
            "The executable v1 path is rootfs.tar -> WSL2 temporary distro -> ext4.vhdx -> rootfs.vhdx.",
            "Python libraries alone do not reliably create a bootable ext4 VHDX on Windows.",
            "The resulting rootfs.vhdx is Metis-owned and can be registered with metis_rootfs_asset_register.",
        ],
    }


def _rootfs_runtime_policy(*, profile: str) -> Dict[str, Any]:
    return {
        "schema": "metis.rootfs.runtime_policy.v1",
        "profile": profile,
        "default_workspace": "/workspace",
        "artifacts_dir": "/artifacts",
        "diagnostics_dir": "/diagnostics",
        "shared_root": "/mnt/.metisfs-root/shared",
        "permission_request_dir": "/mnt/.metisfs-root/shared/.metis-perm-req",
        "permission_response_dir": "/mnt/.metisfs-root/shared/.metis-perm-resp",
        "filesystem": {
            "default_write_scope": "workspace-copy",
            "deny_read": ["/root/.ssh", "/root/.gnupg", ".env", ".env.*"],
            "deny_write": [".git", ".env", ".env.*"],
            "delete_denied_by_default": True,
        },
        "network": {
            "default": "deny",
            "host_proxy_required": True,
            "allowed_domains": [],
        },
        "tools": _rootfs_image_expected_tools(profile=profile),
    }


def _rootfs_image_expected_tools(*, profile: str) -> List[Dict[str, Any]]:
    base = [
        {"name": "python3", "required": True},
        {"name": "node", "required": True},
        {"name": "npm", "required": True},
        {"name": "git", "required": True},
        {"name": "rg", "required": True},
        {"name": "poppler-utils", "required": True},
        {"name": "metisd", "required": True, "path": "/usr/local/bin/metisd"},
    ]
    if profile in {"standard", "office"}:
        base.extend(
            [
                {"name": "pdfplumber", "required": True},
                {"name": "pypdf", "required": True},
                {"name": "reportlab", "required": True},
                {"name": "python-docx", "required": True},
                {"name": "openpyxl", "required": True},
            ]
        )
    if profile == "office":
        base.extend(
            [
                {"name": "libreoffice", "required": True},
                {"name": "imagemagick", "required": True},
            ]
        )
    return base


def _rootfs_image_layout(*, profile: str) -> Dict[str, Any]:
    return {
        "schema": "metis.rootfs_image.layout.v1",
        "profile": profile,
        "installed_paths": {
            "metisd": "/usr/local/bin/metisd",
            "runtime_policy": "/etc/metis/runtime-policy.json",
            "workspace": "/workspace",
            "artifacts": "/artifacts",
            "diagnostics": "/diagnostics",
            "uploads": "/uploads",
            "outputs": "/outputs",
            "shared_root": "/mnt/.metisfs-root/shared",
            "permission_request_dir": "/mnt/.metisfs-root/shared/.metis-perm-req",
            "permission_response_dir": "/mnt/.metisfs-root/shared/.metis-perm-resp",
        },
        "expected_tools": _rootfs_image_expected_tools(profile=profile),
    }


def _rootfs_image_build_plan(
    *,
    source_root: Path,
    bundle_dir: Path,
    backend: str,
    rootfs_tar: Path,
    target: Path,
    distro: str,
    install_dir: Path,
    profile: str,
    build_rootfs_tar: bool,
    rootfs_backend: str,
    base_image: str,
    image_tag: str,
    allow_network: bool,
    register: bool,
    cleanup: bool,
) -> Dict[str, Any]:
    wsl_exe = shutil.which("wsl.exe") or shutil.which("wsl") or "wsl"
    return {
        "schema": ROOTFS_IMAGE_BUILD_SCHEMA,
        "source_root": str(source_root),
        "bundle_path": str(bundle_dir),
        "backend": backend,
        "profile": profile,
        "rootfs_tar": str(rootfs_tar),
        "target_vhdx": str(target),
        "temp_distro_name": distro,
        "install_dir": str(install_dir),
        "build_rootfs_tar": bool(build_rootfs_tar),
        "rootfs_backend": rootfs_backend,
        "base_image": base_image,
        "image_tag": image_tag,
        "allow_network": bool(allow_network),
        "register": bool(register),
        "cleanup": bool(cleanup),
        "commands": [
            [wsl_exe, "--import", distro, str(install_dir), str(rootfs_tar), "--version", "2"],
            [wsl_exe, "--terminate", distro],
            ["copy", str(install_dir / "ext4.vhdx"), str(target)],
            [wsl_exe, "--unregister", distro] if cleanup else [],
        ],
        "layout": _rootfs_image_layout(profile=profile),
        "runtime_policy": _rootfs_runtime_policy(profile=profile),
    }


def _write_rootfs_image_builder_files(
    *,
    bundle_dir: Path,
    plan: Dict[str, Any],
    profile: str,
) -> List[Dict[str, Any]]:
    rootfs_tar = Path(str(plan.get("rootfs_tar") or bundle_dir / "rootfs.tar"))
    base_image = str(plan.get("base_image") or "ubuntu:22.04")
    image_tag = str(plan.get("image_tag") or _default_rootfs_image_tag())
    written = _write_rootfs_builder_files(
        bundle_dir=bundle_dir,
        base_image=base_image,
        image_tag=image_tag,
        profile=profile,
        target=rootfs_tar,
    )
    generated = {
        bundle_dir / "builder" / "rootfs-image-plan.json": json.dumps(plan, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        bundle_dir / "builder" / "rootfs-image-layout.json": json.dumps(
            _rootfs_image_layout(profile=profile),
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n",
        bundle_dir / "builder" / "build-rootfs-vhdx-wsl.ps1": _rootfs_image_wsl_build_ps1(),
    }
    for path, content in generated.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")
        written.append({"path": str(path), "relative_path": _relative_to(path, bundle_dir), "size_bytes": path.stat().st_size})
    _upsert_rootfs_image_builder_pack_manifest(bundle_dir, plan=plan, manifest={})
    return written


def _run_wsl_rootfs_image_build(
    *,
    wsl_exe: str,
    rootfs_tar: Path,
    target: Path,
    distro: str,
    install_dir: Path,
    cleanup: bool,
    force: bool,
    timeout: int,
) -> Dict[str, Any]:
    steps: List[Dict[str, Any]] = []
    if not rootfs_tar.is_file():
        return {"ok": False, "code": "ROOTFS_IMAGE_SOURCE_TAR_MISSING", "error": f"rootfs.tar not found: {rootfs_tar}", "steps": steps}
    if target.exists() and not force:
        return {"ok": False, "code": "ROOTFS_IMAGE_TARGET_EXISTS", "error": f"target exists: {target}", "steps": steps}
    target.parent.mkdir(parents=True, exist_ok=True)
    install_dir.parent.mkdir(parents=True, exist_ok=True)
    if force:
        steps.append(_run_rootfs_builder_step([wsl_exe, "--unregister", distro], cwd=target.parent, timeout=120, step="wsl_unregister_existing_distro"))
        if install_dir.exists():
            shutil.rmtree(install_dir, ignore_errors=True)
    import_step = _run_rootfs_builder_step(
        [wsl_exe, "--import", distro, str(install_dir), str(rootfs_tar), "--version", "2"],
        cwd=target.parent,
        timeout=timeout,
        step="wsl_import_rootfs_tar",
    )
    steps.append(import_step)
    if int(import_step.get("returncode") or 0) != 0:
        return {"ok": False, "code": "ROOTFS_IMAGE_WSL_IMPORT_FAILED", "error": import_step.get("stderr") or import_step.get("stdout") or "wsl --import failed", "steps": steps}
    terminate = _run_rootfs_builder_step([wsl_exe, "--terminate", distro], cwd=target.parent, timeout=120, step="wsl_terminate_temp_distro")
    steps.append(terminate)
    vhdx_source = install_dir / "ext4.vhdx"
    registry_base = _wsl_distro_base_path(distro)
    if registry_base and (registry_base / "ext4.vhdx").is_file():
        vhdx_source = registry_base / "ext4.vhdx"
    if not vhdx_source.is_file():
        if cleanup:
            steps.append(_run_rootfs_builder_step([wsl_exe, "--unregister", distro], cwd=target.parent, timeout=120, step="wsl_cleanup_missing_vhdx_distro"))
        return {
            "ok": False,
            "code": "ROOTFS_IMAGE_EXT4_VHDX_MISSING",
            "error": f"WSL import succeeded but ext4.vhdx was not found under {install_dir}",
            "steps": steps,
            "vhdx_source": str(vhdx_source),
        }
    tmp = target.with_name(f".{target.name}.part-{uuid.uuid4().hex[:8]}")
    shutil.copy2(vhdx_source, tmp)
    os.replace(tmp, target)
    copied = {"step": "copy_ext4_vhdx_to_rootfs_vhdx", "source": str(vhdx_source), "target": str(target), "returncode": 0}
    steps.append(copied)
    cleanup_step: Dict[str, Any] = {}
    if cleanup:
        cleanup_step = _run_rootfs_builder_step([wsl_exe, "--unregister", distro], cwd=target.parent, timeout=120, step="wsl_unregister_temp_distro")
        steps.append(cleanup_step)
    return {
        "ok": True,
        "steps": steps,
        "vhdx_source": str(vhdx_source),
        "target": str(target),
        "cleanup_step": cleanup_step,
    }


def _write_rootfs_image_builder_manifest(
    bundle: Path,
    *,
    plan: Dict[str, Any],
    target: Path,
    rootfs_tar: Path,
    verification: Dict[str, Any],
    registration: Dict[str, Any],
    result: Dict[str, Any],
    profile: str,
) -> Dict[str, Any]:
    manifest = {
        "schema": ROOTFS_IMAGE_BUILD_SCHEMA,
        "owner": "metis",
        "created_at": time.time(),
        "bundle_path": str(bundle),
        "profile": profile,
        "rootfs_tar": str(rootfs_tar),
        "rootfs_vhdx": str(target),
        "rootfs_vhdx_relative": _relative_to(target, bundle) if _is_relative_to(target, bundle) else str(target),
        "verification": verification,
        "registration": registration,
        "result": result,
        "plan": plan,
        "layout": _rootfs_image_layout(profile=profile),
        "runtime_policy": _rootfs_runtime_policy(profile=profile),
        "runner_ready": False,
        "runner_ready_reason": "rootfs.vhdx is built, but vmlinuz/initrd/HCS boot and guest handshake are still required.",
    }
    manifest_path = bundle / ROOTFS_IMAGE_BUILDER_MANIFEST_NAME
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    _upsert_rootfs_image_builder_pack_manifest(bundle, plan=plan, manifest=manifest)
    return manifest


def _upsert_rootfs_image_builder_pack_manifest(bundle: Path, *, plan: Dict[str, Any], manifest: Dict[str, Any]) -> Dict[str, Any]:
    data = _load_vm_manifest_data(bundle)
    if not data:
        data = json.loads(_vm_pack_manifest_template())
    target = str((manifest or {}).get("rootfs_vhdx") or plan.get("target_vhdx") or "")
    data["rootfs_image_builder"] = {
        "schema": ROOTFS_IMAGE_BUILD_SCHEMA,
        "manifest": ROOTFS_IMAGE_BUILDER_MANIFEST_NAME,
        "prepared": True,
        "built": bool(manifest.get("verification", {}).get("exists")) if isinstance(manifest.get("verification"), dict) else False,
        "rootfs_vhdx": target,
        "profile": str(plan.get("profile") or manifest.get("profile") or ""),
        "backend": str(plan.get("backend") or ""),
        "runner_ready": False,
        "runner_ready_reason": "rootfs image exists only after build; direct VM still needs kernel/initrd/HCS/guest handshake.",
    }
    _write_vm_manifest_data(bundle, data)
    return data


def _rootfs_image_wsl_build_ps1() -> str:
    return """param(
  [string]$Bundle = "",
  [string]$RootfsTar = "",
  [string]$Target = "",
  [string]$DistroName = "MetisRootfsImageBuilder",
  [string]$InstallDir = "",
  [switch]$Force,
  [switch]$NoCleanup
)

$ErrorActionPreference = 'Stop'
if (-not $Bundle) {
  $Bundle = Split-Path -Parent $PSScriptRoot
}
if (-not $RootfsTar) { $RootfsTar = Join-Path $Bundle 'rootfs.tar' }
if (-not $Target) { $Target = Join-Path $Bundle 'rootfs.vhdx' }
if (-not $InstallDir) { $InstallDir = Join-Path $Bundle (Join-Path 'builder\\wsl-image' $DistroName) }
if (-not (Test-Path $RootfsTar)) { throw "rootfs.tar not found: $RootfsTar" }
if ((Test-Path $Target) -and -not $Force) { throw "target exists: $Target" }

if ($Force) {
  wsl.exe --unregister $DistroName 2>$null | Out-Null
  if (Test-Path $InstallDir) { Remove-Item -LiteralPath $InstallDir -Recurse -Force }
}

New-Item -ItemType Directory -Force -Path (Split-Path -Parent $InstallDir) | Out-Null
wsl.exe --import $DistroName $InstallDir $RootfsTar --version 2
wsl.exe --terminate $DistroName 2>$null | Out-Null

$Source = Join-Path $InstallDir 'ext4.vhdx'
if (-not (Test-Path $Source)) { throw "ext4.vhdx not found after import: $Source" }
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Target) | Out-Null
Copy-Item -LiteralPath $Source -Destination $Target -Force

if (-not $NoCleanup) {
  wsl.exe --unregister $DistroName 2>$null | Out-Null
}

@{
  schema = 'metis.rootfs_image_builder.ps1.v1'
  ok = $true
  rootfsTar = $RootfsTar
  target = $Target
  distroName = $DistroName
  installDir = $InstallDir
  cleanup = -not $NoCleanup
} | ConvertTo-Json -Depth 6
"""


def _docker_build_proxy_args() -> List[str]:
    proxy = _detect_docker_build_proxy()
    if not proxy:
        return []
    no_proxy = proxy.get("no_proxy") or "localhost,127.0.0.1,::1"
    values = {
        "HTTP_PROXY": proxy["http_proxy"],
        "HTTPS_PROXY": proxy["https_proxy"],
        "http_proxy": proxy["http_proxy"],
        "https_proxy": proxy["https_proxy"],
        "NO_PROXY": no_proxy,
        "no_proxy": no_proxy,
    }
    args: List[str] = []
    for key, value in values.items():
        args.extend(["--build-arg", f"{key}={value}"])
    return args


def _detect_docker_build_proxy() -> Dict[str, str]:
    explicit = os.environ.get("METIS_DOCKER_BUILD_PROXY", "").strip()
    if explicit:
        normalized = _normalize_proxy_for_docker_build(explicit)
        return {
            "http_proxy": normalized,
            "https_proxy": normalized,
            "no_proxy": os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "",
            "source": "METIS_DOCKER_BUILD_PROXY",
        }

    env_proxy = (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or ""
    ).strip()
    if env_proxy:
        normalized = _normalize_proxy_for_docker_build(env_proxy)
        return {
            "http_proxy": normalized,
            "https_proxy": normalized,
            "no_proxy": os.environ.get("NO_PROXY") or os.environ.get("no_proxy") or "",
            "source": "environment",
        }

    wininet = _read_windows_proxy_server()
    if wininet:
        normalized = _normalize_proxy_for_docker_build(wininet)
        return {
            "http_proxy": normalized,
            "https_proxy": normalized,
            "no_proxy": "",
            "source": "wininet",
        }

    for port in (7897, 7890, 10809, 1080):
        if _tcp_port_open("127.0.0.1", port):
            proxy = f"http://host.docker.internal:{port}"
            return {
                "http_proxy": proxy,
                "https_proxy": proxy,
                "no_proxy": "",
                "source": f"localhost:{port}",
            }
    return {}


def _normalize_proxy_for_docker_build(proxy: str) -> str:
    text = (proxy or "").strip()
    if not text:
        return ""
    if "://" not in text:
        text = f"http://{text}"
    parsed = urllib.parse.urlsplit(text)
    hostname = parsed.hostname or ""
    if not hostname:
        return text
    host_lower = hostname.lower()
    docker_host = (
        host_lower == "localhost"
        or host_lower == "::1"
        or host_lower.startswith("127.")
    )
    if not docker_host:
        return text
    userinfo = ""
    if parsed.username:
        userinfo = urllib.parse.quote(urllib.parse.unquote(parsed.username), safe="")
        if parsed.password:
            userinfo += ":" + urllib.parse.quote(urllib.parse.unquote(parsed.password), safe="")
        userinfo += "@"
    port = f":{parsed.port}" if parsed.port else ""
    netloc = f"{userinfo}host.docker.internal{port}"
    return urllib.parse.urlunsplit((parsed.scheme or "http", netloc, parsed.path, parsed.query, parsed.fragment))


def _read_windows_proxy_server() -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg  # type: ignore

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            proxy_enabled, _ = winreg.QueryValueEx(key, "ProxyEnable")
            if not int(proxy_enabled or 0):
                return ""
            proxy_server, _ = winreg.QueryValueEx(key, "ProxyServer")
    except Exception:
        return ""
    return _select_proxy_from_wininet(str(proxy_server or ""))


def _select_proxy_from_wininet(proxy_server: str) -> str:
    text = (proxy_server or "").strip()
    if not text:
        return ""
    fallback = ""
    for part in text.split(";"):
        item = part.strip()
        if not item:
            continue
        if "=" not in item:
            fallback = fallback or item
            continue
        scheme, value = item.split("=", 1)
        if scheme.strip().lower() in {"https", "http"} and value.strip():
            return value.strip()
    return fallback


def _tcp_port_open(host: str, port: int) -> bool:
    try:
        with socket.create_connection((host, port), timeout=0.25):
            return True
    except OSError:
        return False


def _run_rootfs_builder_step(args: List[str], *, cwd: Path, timeout: int, step: str) -> Dict[str, Any]:
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
        "step": step,
        "command": [str(item) for item in args],
        "returncode": proc.returncode,
        "stdout": _truncate(proc.stdout or ""),
        "stderr": _truncate(proc.stderr or ""),
    }


def _raise_if_step_failed(step: Dict[str, Any]) -> None:
    if int(step.get("returncode") or 0) != 0:
        raise RuntimeError(f"{step.get('step') or 'rootfs build step'} failed: {step.get('stderr') or step.get('stdout')}")


def _select_rootfs_asset_candidate(candidates: List[Dict[str, Any]], explicit_path: str = "") -> Dict[str, Any]:
    if explicit_path:
        explicit = Path(explicit_path).expanduser().resolve(strict=False)
        for item in candidates:
            if Path(str(item.get("path") or "")).resolve(strict=False) == explicit:
                return item
    return next((item for item in candidates if item.get("exists")), {})


def _canonical_rootfs_asset_name(path: Path) -> str:
    name = path.name.lower()
    if name.endswith(".tar.gz"):
        return "rootfs.tar.gz"
    if name.endswith(".tar.zst"):
        return "rootfs.tar.zst"
    if name.endswith(".tgz"):
        return "rootfs.tgz"
    if name.endswith(".vhdx"):
        return "rootfs.vhdx"
    if name.endswith(".vhd"):
        return "rootfs.vhd"
    return "rootfs.tar"


def _rootfs_manifest_entry(bundle_dir: Path, asset_path: Path) -> Dict[str, Any]:
    data = _load_vm_manifest_data(bundle_dir)
    assets = data.get("assets") if isinstance(data.get("assets"), dict) else {}
    rootfs = assets.get("rootfs") if isinstance(assets.get("rootfs"), dict) else {}
    if not rootfs:
        return {}
    rel = str(rootfs.get("path") or "")
    registered = (bundle_dir / rel).resolve(strict=False) if rel and not Path(rel).is_absolute() else Path(rel).resolve(strict=False) if rel else None
    if registered and registered == asset_path.resolve(strict=False):
        return dict(rootfs)
    if rel and Path(rel).name.lower() == asset_path.name.lower():
        return dict(rootfs)
    return {}


def _verify_rootfs_asset(
    path: Path,
    *,
    bundle_dir: Path,
    expected_sha256: str = "",
    signature_path: str = "",
    public_key_path: str = "",
    require_expected: bool = False,
) -> Dict[str, Any]:
    exists = path.is_file()
    manifest_entry = _rootfs_manifest_entry(bundle_dir, path)
    expected = _normalize_sha256(expected_sha256 or str(manifest_entry.get("sha256") or ""))
    actual = _sha256_file(path) if exists else ""
    checksum_verified = bool(expected and actual and expected == actual)
    checksum_status = "not_provided"
    if expected:
        checksum_status = "verified" if checksum_verified else "mismatch"
    elif require_expected:
        checksum_status = "missing"
    signature = _verify_rootfs_signature(path, signature_path=signature_path, public_key_path=public_key_path)
    verified = bool(exists and checksum_verified and signature.get("ok", True))
    if require_expected and not expected:
        verified = False
    return {
        "exists": exists,
        "path": str(path),
        "size_bytes": path.stat().st_size if exists else 0,
        "sha256": actual,
        "expected_sha256": expected,
        "checksum_status": checksum_status,
        "checksum_verified": checksum_verified,
        "manifest_entry": manifest_entry,
        "signature": signature,
        "verified": verified,
    }


def _normalize_sha256(value: str) -> str:
    return _extract_sha256(value)


def _verify_rootfs_signature(path: Path, *, signature_path: str = "", public_key_path: str = "") -> Dict[str, Any]:
    sig = str(signature_path or "").strip()
    pub = str(public_key_path or "").strip()
    if not sig and not pub:
        return {"provided": False, "ok": True, "verified": False, "reason": "signature not provided"}
    if not sig or not pub:
        return {"provided": True, "ok": False, "verified": False, "reason": "signature_path and public_key_path are both required"}
    try:
        sig_path = safe_path_for_read(
            sig,
            allow_paths_outside_workspace=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
        pub_path = safe_path_for_read(
            pub,
            allow_paths_outside_workspace=get_effective_sub_allow("allow_paths_outside_workspace"),
        )
    except Exception as exc:
        return {"provided": True, "ok": False, "verified": False, "reason": f"{type(exc).__name__}: {exc}"}
    openssl = shutil.which("openssl")
    if not openssl:
        return {"provided": True, "ok": False, "verified": False, "reason": "openssl not found on PATH"}
    result = _quick_command(
        [
            openssl,
            "dgst",
            "-sha256",
            "-verify",
            str(pub_path),
            "-signature",
            str(sig_path),
            str(path),
        ],
        timeout=30,
    )
    verified = result.get("returncode") == 0
    return {
        "provided": True,
        "ok": verified,
        "verified": verified,
        "signature_path": str(sig_path),
        "public_key_path": str(pub_path),
        "returncode": result.get("returncode"),
        "stdout": _truncate(str(result.get("stdout") or ""), 1000),
        "stderr": _truncate(str(result.get("stderr") or ""), 1000),
        "reason": "" if verified else "openssl signature verification failed",
    }


def _upsert_rootfs_asset_manifest(
    bundle_dir: Path,
    asset_path: Path,
    *,
    verification: Dict[str, Any],
    source_url: str = "",
    signature_path: str = "",
    public_key_path: str = "",
) -> Dict[str, Any]:
    data = _load_vm_manifest_data(bundle_dir)
    if not data:
        data = json.loads(_vm_pack_manifest_template())
    assets = data.setdefault("assets", {})
    if not isinstance(assets, dict):
        assets = {}
        data["assets"] = assets
    rel_path = _relative_to(asset_path, bundle_dir) if _is_relative_to(asset_path, bundle_dir) else str(asset_path)
    assets["rootfs"] = {
        "path": rel_path,
        "sha256": verification.get("sha256") or "",
        "size_bytes": verification.get("size_bytes") or 0,
        "import_mode": _inspect_rootfs_import_asset(asset_path).get("import_mode"),
        "source_url": str(source_url or ""),
        "registered_at": time.time(),
        "signature": {
            "path": str(signature_path or ""),
            "public_key_path": str(public_key_path or ""),
            "verified": bool((verification.get("signature") or {}).get("verified"))
            if isinstance(verification.get("signature"), dict)
            else False,
        },
    }
    _write_vm_manifest_data(bundle_dir, data)
    return data


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _detect_metis_wsl_runtime(
    *,
    source_root: Path,
    distro_name: str = "",
    install_dir: str = "",
    rootfs_path: str = "",
) -> Dict[str, Any]:
    distro = _normalize_wsl_distro_name(distro_name)
    install_path = _resolve_metis_wsl_install_dir(source_root, install_dir, distro)
    wsl = _detect_wsl()
    executable = str(wsl.get("executable") or shutil.which("wsl.exe") or shutil.which("wsl") or "")
    distros = list(wsl.get("distros") or [])
    installed = distro in distros
    registered_install_path = _wsl_distro_base_path(distro) if installed else Path()
    if registered_install_path and not str(install_dir or "").strip():
        install_path = registered_install_path
    features = _detect_wsl_import_features(executable)
    rootfs_candidates = _metis_rootfs_asset_candidates(source_root, rootfs_path=rootfs_path)
    selected_rootfs = next((item for item in rootfs_candidates if item.get("exists")), {})
    verification = {}
    if selected_rootfs:
        selected_path = Path(str(selected_rootfs.get("path") or ""))
        bundle_dir = _bundle_dir_for_rootfs_asset(source_root, selected_path)
        verification = _verify_rootfs_asset(selected_path, bundle_dir=bundle_dir, require_expected=True)
    available = bool(executable and installed)
    asset_verified = bool(verification.get("verified"))
    ready_to_import = bool(executable and features.get("import_supported") and selected_rootfs and asset_verified and not installed)
    reason = ""
    if available:
        reason = "Metis managed WSL runtime is installed"
    elif not executable:
        reason = "wsl.exe not found on PATH"
    elif installed:
        reason = "Metis managed WSL runtime is installed but not available"
    elif not features.get("import_supported"):
        reason = "WSL import is not supported by this wsl.exe"
    elif not selected_rootfs:
        reason = "Metis rootfs import asset is missing"
    elif not asset_verified:
        reason = "Metis rootfs import asset exists but is not verified with a registered SHA256"
    else:
        reason = "Metis managed WSL runtime can be imported"
    return {
        "available": available,
        "installed": installed,
        "ready_to_import": ready_to_import,
        "reason": reason,
        "distro_name": distro,
        "install_dir": str(install_path),
        "registered_install_dir": str(registered_install_path) if registered_install_path else "",
        "wsl": wsl,
        "features": features,
        "selected_rootfs": selected_rootfs,
        "rootfs_verification": verification,
        "rootfs_candidates": rootfs_candidates,
        "notes": [
            "metis_wsl is the first practical Metis VM runner path: WSL-imported, Metis-owned rootfs.",
            "It is distinct from the generic wsl backend, which uses a user-managed distro.",
            "No import is performed by status checks; use metis_wsl_runtime_import after a Metis rootfs asset exists.",
        ],
    }


def _normalize_wsl_distro_name(name: str = "") -> str:
    value = str(name or "").strip()
    if not value:
        return DEFAULT_METIS_WSL_DISTRO
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip(".-")
    return cleaned or DEFAULT_METIS_WSL_DISTRO


def _resolve_metis_wsl_install_dir(source_root: Path, install_dir: str, distro: str) -> Path:
    raw = str(install_dir or "").strip()
    if raw:
        return safe_path_for_write(raw).resolve(strict=False)
    local_app = os.environ.get("LOCALAPPDATA", "")
    if local_app:
        return (Path(local_app) / "Metis" / "runtime" / "wsl" / distro).resolve(strict=False)
    return (source_root / ".metis" / "wsl" / distro).resolve(strict=False)


def _wsl_distro_base_path(distro: str) -> Path:
    if os.name != "nt":
        return Path()
    try:
        import winreg  # type: ignore

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Lxss",
        ) as lxss:
            count, _, _ = winreg.QueryInfoKey(lxss)
            for index in range(count):
                subkey_name = winreg.EnumKey(lxss, index)
                with winreg.OpenKey(lxss, subkey_name) as subkey:
                    name, _ = winreg.QueryValueEx(subkey, "DistributionName")
                    if str(name or "") != distro:
                        continue
                    base_path, _ = winreg.QueryValueEx(subkey, "BasePath")
                    return Path(_strip_windows_extended_path(str(base_path or ""))).expanduser().resolve(strict=False)
    except Exception:
        return Path()
    return Path()


def _strip_windows_extended_path(path: str) -> str:
    text = str(path or "")
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[8:]
    if text.startswith("\\\\?\\"):
        return text[4:]
    return text


def _detect_wsl_import_features(executable: str = "") -> Dict[str, Any]:
    exe = executable or shutil.which("wsl.exe") or shutil.which("wsl") or ""
    if not exe:
        return {
            "import_supported": False,
            "vhd_import_supported": False,
            "import_in_place_supported": False,
            "reason": "wsl.exe not found",
        }
    help_text = _quick_command([exe, "--help"], timeout=8)
    combined = _clean_command_text(f"{help_text.get('stdout') or ''}\n{help_text.get('stderr') or ''}")
    lower = combined.lower()
    return {
        "import_supported": "--import" in lower,
        "vhd_import_supported": "--vhd" in lower,
        "import_in_place_supported": "--import-in-place" in lower,
        "status_returncode": help_text.get("returncode"),
        "reason": "" if "--import" in lower else "wsl --help did not advertise --import",
    }


def _metis_rootfs_asset_candidates(source_root: Path, rootfs_path: str = "") -> List[Dict[str, Any]]:
    candidates: List[Path] = []
    explicit = str(rootfs_path or "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser().resolve(strict=False))
    for bundle in _vm_bundle_candidates(source_root=source_root):
        manifest = _load_vm_manifest_data(bundle)
        assets = manifest.get("assets") if isinstance(manifest.get("assets"), dict) else {}
        rootfs = assets.get("rootfs") if isinstance(assets.get("rootfs"), dict) else {}
        manifest_path = str(rootfs.get("path") or "").strip()
        if manifest_path:
            path = Path(manifest_path)
            candidates.append((bundle / path).resolve(strict=False) if not path.is_absolute() else path.resolve(strict=False))
        for name in ROOTFS_IMPORT_ASSET_NAMES:
            candidates.append((bundle / name).resolve(strict=False))
    return [_inspect_rootfs_import_asset(path) for path in _dedupe_paths(candidates)]


def _bundle_dir_for_rootfs_asset(source_root: Path, asset_path: Path) -> Path:
    asset = asset_path.resolve(strict=False)
    for bundle in _vm_bundle_candidates(source_root=source_root):
        resolved = bundle.resolve(strict=False)
        if _is_relative_to(asset, resolved):
            return resolved
    return _resolve_vm_bundle_dir(source_root, "")


def _inspect_rootfs_import_asset(path: Path) -> Dict[str, Any]:
    suffixes = "".join(path.suffixes).lower()
    import_mode = "vhd" if suffixes.endswith(".vhdx") or suffixes.endswith(".vhd") else "tar"
    exists = path.is_file()
    return {
        "path": str(path),
        "exists": exists,
        "size_bytes": path.stat().st_size if exists else 0,
        "import_mode": import_mode,
        "is_archive": import_mode == "tar",
        "is_vhd": import_mode == "vhd",
    }


def _build_wsl_import_args(
    *,
    executable: str,
    distro: str,
    install_dir: Path,
    rootfs_asset: Path,
    version: int,
    import_mode: str,
) -> List[str]:
    args = [
        executable,
        "--import",
        distro,
        str(install_dir),
        str(rootfs_asset),
        "--version",
        str(max(1, int(version or 2))),
    ]
    if import_mode == "vhd":
        args.append("--vhd")
    return args


def _detect_wsl(wsl_distro: str = "") -> Dict[str, Any]:
    executable = shutil.which("wsl.exe") or shutil.which("wsl")
    if not executable:
        return {"available": False, "reason": "wsl.exe not found on PATH"}
    version = _quick_command([executable, "--version"], timeout=5)
    listed = _quick_command([executable, "-l", "-q"], timeout=8)
    distro_names = _parse_wsl_distros(listed.get("stdout", ""))
    requested = str(wsl_distro or "").strip()
    selected = requested or (distro_names[0] if distro_names else "")
    if requested and requested not in distro_names:
        return {
            "available": False,
            "executable": executable,
            "requested_distro": requested,
            "distros": distro_names,
            "reason": f"requested WSL distro not found: {requested}",
        }
    if not distro_names and listed.get("returncode") not in (0, None):
        return {
            "available": False,
            "executable": executable,
            "returncode": listed.get("returncode"),
            "stderr": _truncate(str(listed.get("stderr") or ""), 800),
            "reason": "WSL is installed but no usable distro was listed",
        }
    return {
        "available": bool(selected),
        "executable": executable,
        "selected_distro": selected,
        "distros": distro_names,
        "version": _first_nonempty_line(str(version.get("stdout") or version.get("stderr") or "")),
        "reason": "" if selected else "no WSL distro found",
    }


def _detect_docker(docker_image: str = "") -> Dict[str, Any]:
    executable = shutil.which("docker")
    image = str(docker_image or os.environ.get("METIS_DOCKER_RUNTIME_IMAGE") or "python:3.12-slim").strip()
    if not executable:
        return {"available": False, "image": image, "reason": "docker CLI not found on PATH"}
    version = _quick_command([executable, "version", "--format", "{{.Server.Version}}"], timeout=8)
    available = version.get("returncode") == 0 and bool(str(version.get("stdout") or "").strip())
    image_info = _quick_command([executable, "image", "inspect", image], timeout=8) if available else {"returncode": None}
    return {
        "available": available,
        "executable": executable,
        "image": image,
        "server_version": _first_nonempty_line(str(version.get("stdout") or "")),
        "image_available": image_info.get("returncode") == 0,
        "reason": "" if available else _truncate(str(version.get("stderr") or version.get("stdout") or "Docker daemon unavailable"), 800),
    }


def _select_runtime_backend(
    backend: str,
    *,
    docker_image: str = "",
    wsl_distro: str = "",
    vm_bundle_path: str = "",
    metis_wsl_distro: str = "",
    metis_wsl_install_dir: str = "",
    source_root: Optional[Path] = None,
) -> Dict[str, Any]:
    requested = str(backend or "local").strip().lower().replace("-", "_")
    if requested in {"copy", "host"}:
        requested = "local"
    if requested in {"vm_pack", "metis_vm", "metisvm"}:
        requested = "vm"
    if requested in {"wsl_import", "managed_wsl", "metiswsl"}:
        requested = "metis_wsl"
    if requested in {"hcs", "hcs_vm", "hyper_v", "hyperv"}:
        requested = "hcs"
    if requested not in {"auto", "local", "wsl", "docker", "vm", "metis_wsl", "hcs"}:
        requested = "local"
    status = _detect_sandbox_backends(
        docker_image=docker_image,
        wsl_distro=wsl_distro,
        vm_bundle_path=vm_bundle_path,
        metis_wsl_distro=metis_wsl_distro,
        metis_wsl_install_dir=metis_wsl_install_dir,
        source_root=source_root,
    )
    fallback_reason = ""
    selected = requested
    if requested == "auto":
        # Prefer HCS if available, then existing VM/WSL/Docker, then local
        hcs_ok = _hcs_backend_available()
        if hcs_ok:
            selected = "hcs"
            fallback_reason = "auto selected HCS VM sandbox (strongest isolation)"
        else:
            selected = str(status.get("preferred") or "local")
            fallback_reason = "auto selected the strongest available backend"
            if selected == "vm" and not status.get("vm_pack", {}).get("runnable"):
                selected = "local"
                fallback_reason = "VM Pack is detected but not runnable in this build, falling back to local copy"
    elif requested == "hcs":
        if not _hcs_backend_available():
            selected = "local"
            fallback_reason = "HCS VM sandbox unavailable, falling back to local copy"
    elif requested == "metis_wsl" and not status.get("metis_wsl", {}).get("available"):
        selected = "local"
        fallback_reason = f"Metis WSL runtime unavailable, falling back to local copy: {status.get('metis_wsl', {}).get('reason', '')}"
    elif requested == "vm" and not status.get("vm_pack", {}).get("runnable"):
        if status.get("metis_wsl", {}).get("available"):
            selected = "metis_wsl"
            fallback_reason = "VM Pack runner is not runnable; using Metis managed WSL runtime"
        else:
            selected = "local"
            import_hint = ""
            if status.get("metis_wsl", {}).get("ready_to_import"):
                import_hint = "; rootfs asset is available, run metis_wsl_runtime_import first"
            fallback_reason = (
                f"VM Pack unavailable, falling back to local copy: {status.get('vm_pack', {}).get('reason', '')}"
                f"{import_hint}"
            )
    elif requested == "wsl" and not status.get("wsl", {}).get("available"):
        selected = "local"
        fallback_reason = f"WSL unavailable, falling back to local copy: {status.get('wsl', {}).get('reason', '')}"
    elif requested == "docker" and not status.get("docker", {}).get("available"):
        selected = "local"
        fallback_reason = f"Docker unavailable, falling back to local copy: {status.get('docker', {}).get('reason', '')}"
    elif requested == "local":
        selected = "local"
    selected_info = dict(status.get(selected) or {})
    return {
        "requested": requested,
        "selected": selected,
        "fallback_reason": fallback_reason,
        "docker_image": str(selected_info.get("image") or docker_image or os.environ.get("METIS_DOCKER_RUNTIME_IMAGE") or "python:3.12-slim"),
        "wsl_distro": str(selected_info.get("selected_distro") or wsl_distro or ""),
        "metis_wsl_distro": str(
            (status.get("metis_wsl") or {}).get("distro_name")
            or metis_wsl_distro
            or DEFAULT_METIS_WSL_DISTRO
        ),
        "metis_wsl_install_dir": str(
            (status.get("metis_wsl") or {}).get("install_dir")
            or metis_wsl_install_dir
            or ""
        ),
        "vm_bundle_path": str(
            ((status.get("vm_pack") or {}).get("selected_bundle") or {}).get("path")
            or vm_bundle_path
            or ""
        ),
        "status": status,
    }


def _run_runtime_command(
    manifest: RuntimeManifest,
    command_text: str,
    *,
    work_dir: Path,
    timeout: int,
    env_map: Dict[str, str],
    network_allowed: bool,
) -> BackendRunResult:
    backend = str(manifest.backend or "local").lower()
    try:
        if backend == "wsl":
            return _run_wsl_command(
                manifest,
                command_text,
                work_dir=work_dir,
                timeout=timeout,
                env_map=env_map,
                network_allowed=network_allowed,
            )
        if backend == "metis_wsl":
            return _run_metis_wsl_command(
                manifest,
                command_text,
                work_dir=work_dir,
                timeout=timeout,
                env_map=env_map,
                network_allowed=network_allowed,
            )
        if backend == "docker":
            return _run_docker_command(
                manifest,
                command_text,
                work_dir=work_dir,
                timeout=timeout,
                env_map=env_map,
                network_allowed=network_allowed,
            )
        if backend == "vm":
            return _run_vm_guest_protocol_command(
                manifest,
                command_text,
                work_dir=work_dir,
                timeout=timeout,
                env_map=env_map,
                network_allowed=network_allowed,
            )
        if backend == "hcs":
            return _run_hcs_command(
                manifest,
                command_text,
                work_dir=work_dir,
                timeout=timeout,
                env_map=env_map,
                network_allowed=network_allowed,
            )
    except (FileNotFoundError, OSError) as exc:
        if manifest.policy.strict_sandbox:
            return BackendRunResult(
                returncode=126,
                stdout="",
                stderr=f"{backend} backend unavailable at run time and strict sandbox forbids local fallback: {type(exc).__name__}: {exc}",
                timed_out=False,
                executed_command=command_text,
                backend=backend,
                fallback_reason="strict sandbox blocked local fallback",
            )
        local = _run_local_command(
            command_text,
            work_dir=work_dir,
            timeout=timeout,
            env_map=env_map,
        )
        local.fallback_reason = f"{backend} backend unavailable at run time: {type(exc).__name__}: {exc}"
        return local
    return _run_local_command(
        command_text,
        work_dir=work_dir,
        timeout=timeout,
        env_map=env_map,
    )


def _run_vm_guest_protocol_command(
    manifest: RuntimeManifest,
    command_text: str,
    *,
    work_dir: Path,
    timeout: int,
    env_map: Dict[str, str],
    network_allowed: bool,
) -> BackendRunResult:
    bundle = _vm_bundle_path_from_manifest(manifest)
    daemon = bundle / "guest" / "metisd.py"
    if not daemon.is_file():
        raise FileNotFoundError(f"Metis VM guest daemon not found: {daemon}")
    bundle_status = _inspect_vm_bundle_path(bundle, usage="runtime_run")
    if not bundle_status.get("metis_owned"):
        raise OSError(f"Metis VM bundle is not Metis-owned: {bundle}")
    if not bundle_status.get("guest_protocol_ready") and not bundle_status.get("hcs_direct_ready"):
        raise OSError(
            "Metis VM bundle is not runnable: guest protocol bridge or HCS runtime.hello handshake is required"
        )

    python_exe = configured_python_executable() or shutil.which("python") or shutil.which("py") or "python"
    guest_command = shell_command_with_configured_python(command_text)
    protocol_id = f"vm_protocol_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    trace_path = manifest.paths.diagnostics_dir / f"{protocol_id}.json"
    messages = [
        {
            "id": "mount",
            "method": "session.mount",
            "params": {
                "workspace": str(manifest.paths.workspace_dir),
                "artifacts": str(manifest.paths.artifacts_dir),
                "diagnostics": str(manifest.paths.diagnostics_dir),
            },
        },
        {"id": "hello", "method": "runtime.hello", "params": {"protocol": "metis.vm.guest.v1"}},
        {
            "id": "run",
            "method": "process.run",
            "params": {
                "command": guest_command,
                "cwd": str(work_dir),
                "timeout_ms": max(1, int(timeout or 120)) * 1000,
                "network_allowed": bool(network_allowed),
            },
        },
        {
            "id": "collect",
            "method": "artifact.collect",
            "params": {
                "patterns": sorted(ARTIFACT_PATTERNS),
                "max_files": 200,
                "max_bytes_per_file": 20 * 1024 * 1024,
            },
        },
        {"id": "list", "method": "artifact.list", "params": {"limit": 200}},
        {"id": "diagnostics", "method": "diagnostics.export", "params": {}},
        {"id": "shutdown", "method": "runtime.shutdown", "params": {}},
    ]
    vm_env = dict(env_map)
    vm_env.update(
        {
            "METIS_RUNTIME_BACKEND": "vm",
            "METIS_RUNTIME_VM_BUNDLE": str(bundle),
            "METIS_RUNTIME_VM_TRANSPORT": str(bundle_status.get("runner_transport") or "jsonl-stdio"),
            "METIS_RUNTIME_NETWORK_ALLOWED": "1" if network_allowed else "0",
        }
    )
    executed_command = f"metis-vm-jsonl-stdio {daemon}: {command_text}"
    started = time.time()
    try:
        proc = subprocess.run(
            [str(python_exe), str(daemon)],
            input="\n".join(json.dumps(item, ensure_ascii=False) for item in messages) + "\n",
            cwd=str(work_dir),
            env=vm_env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=max(5, int(timeout or 120) + 15),
            check=False,
        )
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
        daemon_returncode: Optional[int] = proc.returncode
        daemon_timed_out = False
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout if isinstance(exc.stdout, str) else ""
        stderr = exc.stderr if isinstance(exc.stderr, str) else ""
        daemon_returncode = None
        daemon_timed_out = True

    responses = _parse_jsonl_responses(stdout)
    by_id = {str(item.get("id") or ""): item for item in responses if isinstance(item, dict)}
    trace = {
        "schema": "metis.vm_runtime.guest_protocol_run.v1",
        "protocol_id": protocol_id,
        "created_at": started,
        "duration_ms": int((time.time() - started) * 1000),
        "bundle_path": str(bundle),
        "daemon": str(daemon),
        "transport": str(bundle_status.get("runner_transport") or "jsonl-stdio"),
        "command": command_text,
        "guest_command": guest_command,
        "cwd": str(work_dir),
        "daemon_returncode": daemon_returncode,
        "daemon_timed_out": daemon_timed_out,
        "daemon_stderr": _truncate(stderr),
        "responses": responses,
    }
    trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")

    if daemon_timed_out:
        return BackendRunResult(
            returncode=None,
            stdout="",
            stderr=(stderr + f"\nVM guest protocol timed out after {timeout}s").strip(),
            timed_out=True,
            executed_command=executed_command,
            backend="vm",
        )
    hello_result = _jsonl_result(by_id, "hello")
    if daemon_returncode != 0 or not hello_result.get("ok"):
        return BackendRunResult(
            returncode=126,
            stdout="",
            stderr=_vm_protocol_failure_message(
                "VM guest protocol handshake failed",
                daemon_returncode=daemon_returncode,
                daemon_stderr=stderr,
                response=by_id.get("hello"),
                trace_path=trace_path,
            ),
            timed_out=False,
            executed_command=executed_command,
            backend="vm",
        )
    run_response = by_id.get("run") if isinstance(by_id.get("run"), dict) else {}
    run_error = run_response.get("error") if isinstance(run_response, dict) else None
    run_result = _jsonl_result(by_id, "run")
    if run_error or not run_result:
        return BackendRunResult(
            returncode=126,
            stdout="",
            stderr=_vm_protocol_failure_message(
                "VM guest process.run response missing or errored",
                daemon_returncode=daemon_returncode,
                daemon_stderr=stderr,
                response=run_response,
                trace_path=trace_path,
            ),
            timed_out=False,
            executed_command=executed_command,
            backend="vm",
        )

    result_stdout = str(run_result.get("stdout") or "")
    result_stderr = str(run_result.get("stderr") or "")
    if stderr.strip():
        result_stderr = (result_stderr + "\n[metisd stderr]\n" + stderr).strip()
    raw_returncode = run_result.get("returncode")
    returncode = int(raw_returncode) if isinstance(raw_returncode, int) else None
    timed_out = bool(run_result.get("timed_out"))
    return BackendRunResult(
        returncode=returncode,
        stdout=result_stdout,
        stderr=result_stderr,
        timed_out=timed_out,
        executed_command=executed_command,
        backend="vm",
    )


def _vm_bundle_path_from_manifest(manifest: RuntimeManifest) -> Path:
    sandbox = manifest.sandbox if isinstance(manifest.sandbox, dict) else {}
    status = sandbox.get("status") if isinstance(sandbox.get("status"), dict) else {}
    vm_pack = status.get("vm_pack") if isinstance(status.get("vm_pack"), dict) else {}
    selected_bundle = vm_pack.get("selected_bundle") if isinstance(vm_pack.get("selected_bundle"), dict) else {}
    raw = str(sandbox.get("vm_bundle_path") or selected_bundle.get("path") or "").strip()
    if not raw:
        raise FileNotFoundError("Metis VM bundle path is missing from runtime manifest")
    return Path(raw).expanduser().resolve(strict=False)


def _jsonl_result(responses: Dict[str, Dict[str, Any]], message_id: str) -> Dict[str, Any]:
    item = responses.get(message_id)
    if not isinstance(item, dict):
        return {}
    result = item.get("result")
    return result if isinstance(result, dict) else {}


def _vm_protocol_failure_message(
    title: str,
    *,
    daemon_returncode: Optional[int],
    daemon_stderr: str,
    response: Any,
    trace_path: Path,
) -> str:
    parts = [
        title,
        f"daemon_returncode={daemon_returncode}",
        f"trace={trace_path}",
    ]
    if response:
        parts.append(f"response={_truncate(json.dumps(response, ensure_ascii=False, sort_keys=True), 1200)}")
    if daemon_stderr.strip():
        parts.append(f"stderr={_truncate(daemon_stderr, 1200)}")
    return "\n".join(parts)


def _hcs_session_key(manifest: RuntimeManifest) -> str:
    """Stable per-session key so the service can reuse a warm VM + /data disk.

    For the HCS backend workspace_dir == the project source root, so hashing
    the source root yields a key that is stable across every runtime job in
    the same project/chat (each job otherwise gets a fresh rt_* manifest id,
    which would never reuse). An explicit METIS_RUNTIME_SESSION_KEY override
    lets the app layer inject a chat-session id later without re-plumbing the
    tool signature.
    """
    override = os.environ.get("METIS_RUNTIME_SESSION_KEY", "").strip()
    if override:
        safe = re.sub(r"[^A-Za-z0-9_.-]", "", override)[:32]
        if safe:
            return f"sk_{safe}"
    try:
        root = str(manifest.paths.source_root or "")
    except Exception:
        root = ""
    if not root:
        return ""
    import hashlib
    digest = hashlib.sha1(os.path.normcase(os.path.abspath(root)).encode("utf-8")).hexdigest()[:16]
    return f"sk_{digest}"


def _hcs_sessiondata_paths(bundle: Optional[Path]) -> tuple[str, str]:
    """Resolve (data_dir, template) for sessiondata persistence.

    data_dir lives under the *user's* LOCALAPPDATA (passed explicitly because
    the LocalSystem service's own LOCALAPPDATA is the systemprofile). The empty
    ext4 template ships in the rich bundle (CI); a METIS_SESSIONDATA_TEMPLATE
    env override is honored for local testing before the bundle repack.
    """
    data_dir = ""
    local_appdata = os.environ.get("LOCALAPPDATA", "")
    if local_appdata:
        data_dir = os.path.join(local_appdata, "Metis", "sessiondata")
    template = ""
    env_tmpl = os.environ.get("METIS_SESSIONDATA_TEMPLATE", "").strip()
    if env_tmpl and os.path.isfile(env_tmpl):
        template = env_tmpl
    elif bundle is not None:
        cand = os.path.join(str(bundle), "sessiondata-template.vhdx")
        if os.path.isfile(cand):
            template = cand
    return data_dir, template


def _run_hcs_command(
    manifest: RuntimeManifest,
    command_text: str,
    *,
    work_dir: Path,
    timeout: int,
    env_map: Dict[str, str],
    network_allowed: bool,
) -> BackendRunResult:
    """Run a command inside an HCS VM.

    Three-tier path:
      1. privileged service (non-elevated, no UAC) via the named pipe
      2. direct hcs_runtime (works only when this process is elevated)
      3. raise -> caller falls back to WSL/local
    """
    # --- Tier 1: privileged service (preferred; no elevation needed) ---
    try:
        from backend.runtime import svc_client
        if svc_client.service_available():
            from backend.runtime.hcs_client import find_metis_bundle
            bundle = find_metis_bundle()
            session_key = _hcs_session_key(manifest)
            data_dir, data_template = _hcs_sessiondata_paths(bundle)
            params = {
                "session_id": session_key,
                "command": command_text,
                "workspace_dir": str(manifest.paths.workspace_dir),
                "artifacts_dir": str(manifest.paths.artifacts_dir),
                "diagnostics_dir": str(manifest.paths.diagnostics_dir),
                "timeout": max(1, int(timeout or 120)),
                "network_allowed": bool(network_allowed),
                "memory_mb": 1024,
                "processors": 2,
                "bundle_dir": str(bundle) if bundle else "",
                # Persistence: per-key writable /data disk (best-effort — the
                # service skips it gracefully when no template/disk exists).
                "session_data_dir": data_dir if session_key else "",
                "session_data_template": data_template if session_key else "",
            }
            result = svc_client.run_job_via_service(params)
            if result is not None and not result.get("error"):
                return BackendRunResult(
                    returncode=result.get("returncode"),
                    stdout=str(result.get("stdout") or ""),
                    stderr=str(result.get("stderr") or ""),
                    timed_out=bool(result.get("timed_out")),
                    executed_command=command_text,
                    backend="hcs",
                    fallback_reason="via metis-vm-service",
                )
    except Exception:
        pass  # fall through to direct / local

    # --- Tier 2: direct hcs_runtime (requires elevated process) ---
    from backend.runtime.hcs_runtime import (
        hcs_runtime_create_session,
        hcs_runtime_run,
        hcs_runtime_destroy,
    )

    session_id = manifest.session_id
    create_result = hcs_runtime_create_session(
        session_id=session_id,
        workspace_dir=manifest.paths.workspace_dir,
        artifacts_dir=manifest.paths.artifacts_dir,
        diagnostics_dir=manifest.paths.diagnostics_dir,
    )
    if not create_result.get("ok"):
        raise OSError(f"HCS session creation failed: {create_result.get('error', 'unknown')}")

    try:
        result = hcs_runtime_run(
            session_id=session_id,
            command=command_text,
            cwd=str(work_dir) if work_dir else "",
            timeout=max(1, int(timeout or 120)),
            env=env_map,
            network_allowed=bool(network_allowed),
        )
        return BackendRunResult(
            returncode=result.get("returncode"),
            stdout=str(result.get("stdout") or ""),
            stderr=str(result.get("stderr") or ""),
            timed_out=bool(result.get("timed_out")),
            executed_command=command_text,
            backend="hcs",
        )
    finally:
        try:
            hcs_runtime_destroy(session_id)
        except Exception:
            pass


def _run_local_command(command_text: str, *, work_dir: Path, timeout: int, env_map: Dict[str, str]) -> BackendRunResult:
    command_to_run = shell_command_with_configured_python(command_text)
    try:
        proc = subprocess.run(
            command_to_run,
            cwd=str(work_dir),
            env=env_map,
            shell=True,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return BackendRunResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            timed_out=False,
            executed_command=command_to_run,
            backend="local",
        )
    except subprocess.TimeoutExpired as exc:
        return BackendRunResult(
            returncode=None,
            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
            stderr=((exc.stderr if isinstance(exc.stderr, str) else "") + f"\nTimeout after {timeout}s").strip(),
            timed_out=True,
            executed_command=command_to_run,
            backend="local",
        )


def _run_wsl_command(
    manifest: RuntimeManifest,
    command_text: str,
    *,
    work_dir: Path,
    timeout: int,
    env_map: Dict[str, str],
    network_allowed: bool,
) -> BackendRunResult:
    sandbox = manifest.sandbox or {}
    status = sandbox.get("status") if isinstance(sandbox.get("status"), dict) else {}
    wsl = status.get("wsl") if isinstance(status.get("wsl"), dict) else {}
    executable = str(wsl.get("executable") or shutil.which("wsl.exe") or shutil.which("wsl") or "wsl.exe")
    distro = str(sandbox.get("wsl_distro") or wsl.get("selected_distro") or "").strip()
    work_dir_wsl = _windows_path_to_wsl(work_dir)
    artifacts_wsl = _windows_path_to_wsl(manifest.paths.artifacts_dir)
    workspace_wsl = _windows_path_to_wsl(manifest.paths.workspace_dir)
    source_wsl = _windows_path_to_wsl(manifest.paths.source_root)
    env_prefix = " ".join(
        f"{key}={shlex.quote(value)}"
        for key, value in {
            **{str(k): str(v) for k, v in env_map.items() if str(k).startswith("METIS_")},
            "METIS_RUNTIME_SESSION_ID": manifest.session_id,
            "METIS_RUNTIME_WORKSPACE": workspace_wsl,
            "METIS_RUNTIME_ARTIFACTS_DIR": artifacts_wsl,
            "METIS_RUNTIME_SOURCE_ROOT": source_wsl,
            "METIS_RUNTIME_BACKEND": "wsl",
            "METIS_RUNTIME_NETWORK_ALLOWED": "1" if network_allowed else "0",
        }.items()
    )
    script = f"cd {shlex.quote(work_dir_wsl)} && {env_prefix} {command_text}"
    args = [executable]
    if distro:
        args.extend(["-d", distro])
    args.extend(["--", "bash", "-lc", script])
    return _run_args(args, timeout=timeout, backend="wsl", executed_command=" ".join(args))


def _run_metis_wsl_command(
    manifest: RuntimeManifest,
    command_text: str,
    *,
    work_dir: Path,
    timeout: int,
    env_map: Dict[str, str],
    network_allowed: bool,
) -> BackendRunResult:
    sandbox = manifest.sandbox or {}
    status = sandbox.get("status") if isinstance(sandbox.get("status"), dict) else {}
    metis_wsl = status.get("metis_wsl") if isinstance(status.get("metis_wsl"), dict) else {}
    wsl = metis_wsl.get("wsl") if isinstance(metis_wsl.get("wsl"), dict) else {}
    executable = str(wsl.get("executable") or shutil.which("wsl.exe") or shutil.which("wsl") or "wsl.exe")
    distro = str(sandbox.get("metis_wsl_distro") or metis_wsl.get("distro_name") or DEFAULT_METIS_WSL_DISTRO).strip()
    if not distro:
        raise FileNotFoundError("Metis WSL distro name is missing")
    work_dir_wsl = _windows_path_to_wsl(work_dir)
    artifacts_wsl = _windows_path_to_wsl(manifest.paths.artifacts_dir)
    workspace_wsl = _windows_path_to_wsl(manifest.paths.workspace_dir)
    source_wsl = _windows_path_to_wsl(manifest.paths.source_root)
    env_prefix = " ".join(
        f"{key}={shlex.quote(value)}"
        for key, value in {
            **{str(k): str(v) for k, v in env_map.items() if str(k).startswith("METIS_")},
            "METIS_RUNTIME_SESSION_ID": manifest.session_id,
            "METIS_RUNTIME_WORKSPACE": workspace_wsl,
            "METIS_RUNTIME_ARTIFACTS_DIR": artifacts_wsl,
            "METIS_RUNTIME_SOURCE_ROOT": source_wsl,
            "METIS_RUNTIME_BACKEND": "metis_wsl",
            "METIS_RUNTIME_NETWORK_ALLOWED": "1" if network_allowed else "0",
        }.items()
    )
    script = f"cd {shlex.quote(work_dir_wsl)} && {env_prefix} {command_text}"
    args = [executable, "-d", distro, "--", "bash", "-lc", script]
    return _run_args(args, timeout=timeout, backend="metis_wsl", executed_command=" ".join(args))


def _run_docker_command(
    manifest: RuntimeManifest,
    command_text: str,
    *,
    work_dir: Path,
    timeout: int,
    env_map: Dict[str, str],
    network_allowed: bool,
) -> BackendRunResult:
    sandbox = manifest.sandbox or {}
    status = sandbox.get("status") if isinstance(sandbox.get("status"), dict) else {}
    docker = status.get("docker") if isinstance(status.get("docker"), dict) else {}
    executable = str(docker.get("executable") or shutil.which("docker") or "docker")
    image = str(sandbox.get("docker_image") or docker.get("image") or os.environ.get("METIS_DOCKER_RUNTIME_IMAGE") or "python:3.12-slim")
    rel_cwd = _relative_to(work_dir, manifest.paths.workspace_dir).replace("\\", "/")
    container_cwd = "/workspace" if rel_cwd in {"", "."} else f"/workspace/{rel_cwd}"
    args = [
        executable,
        "run",
        "--rm",
    ]
    if not network_allowed:
        args.extend(["--network", "none"])
    args.extend(
        [
            "-v",
            f"{manifest.paths.workspace_dir}:/workspace",
            "-v",
            f"{manifest.paths.artifacts_dir}:/artifacts",
            "-w",
            container_cwd,
            "-e",
            f"METIS_RUNTIME_SESSION_ID={manifest.session_id}",
            "-e",
            "METIS_RUNTIME_WORKSPACE=/workspace",
            "-e",
            "METIS_RUNTIME_ARTIFACTS_DIR=/artifacts",
            "-e",
            "METIS_RUNTIME_SOURCE_ROOT=/workspace",
            "-e",
            "METIS_RUNTIME_BACKEND=docker",
        ]
    )
    for key, value in env_map.items():
        if str(key).startswith("METIS_"):
            args.extend(["-e", f"{key}={value}"])
    args.extend([image, "sh", "-lc", command_text])
    return _run_args(args, timeout=timeout, backend="docker", executed_command=" ".join(shlex.quote(str(item)) for item in args))


def _run_args(args: List[str], *, timeout: int, backend: str, executed_command: str) -> BackendRunResult:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return BackendRunResult(
            returncode=proc.returncode,
            stdout=proc.stdout or "",
            stderr=proc.stderr or "",
            timed_out=False,
            executed_command=executed_command,
            backend=backend,
        )
    except subprocess.TimeoutExpired as exc:
        return BackendRunResult(
            returncode=None,
            stdout=exc.stdout if isinstance(exc.stdout, str) else "",
            stderr=((exc.stderr if isinstance(exc.stderr, str) else "") + f"\nTimeout after {timeout}s").strip(),
            timed_out=True,
            executed_command=executed_command,
            backend=backend,
        )


def _quick_command(args: List[str], *, timeout: int = 8) -> Dict[str, Any]:
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
        return {
            "returncode": proc.returncode,
            "stdout": _clean_command_text(proc.stdout or ""),
            "stderr": _clean_command_text(proc.stderr or ""),
        }
    except Exception as exc:
        return {"returncode": None, "stdout": "", "stderr": f"{type(exc).__name__}: {exc}"}


def _parse_wsl_distros(text: str) -> List[str]:
    values: List[str] = []
    for raw in _clean_command_text(text).splitlines():
        cleaned = raw.replace("*", "").strip()
        if not cleaned or cleaned.lower().startswith("windows subsystem"):
            continue
        if cleaned not in values:
            values.append(cleaned)
    return values


def _clean_command_text(text: str) -> str:
    return str(text or "").replace("\x00", "").replace("\r\n", "\n").strip()


def _first_nonempty_line(text: str) -> str:
    for line in _clean_command_text(text).splitlines():
        value = line.strip()
        if value:
            return value[:240]
    return ""


def _windows_path_to_wsl(path: Path) -> str:
    raw = str(path.resolve(strict=False))
    match = re.match(r"^([A-Za-z]):[\\/](.*)$", raw)
    if match:
        drive = match.group(1).lower()
        rest = match.group(2).replace("\\", "/")
        return f"/mnt/{drive}/{rest}"
    return raw.replace("\\", "/")


def _looks_like_network_command(command: str) -> bool:
    return bool(NETWORK_COMMAND_RE.search(str(command or "")))


def _resolve_session_cwd(workspace_dir: Path, cwd: str) -> Path:
    raw = str(cwd or "").strip()
    if not raw:
        return workspace_dir
    candidate = Path(raw)
    if not candidate.is_absolute():
        candidate = workspace_dir / candidate
    resolved = candidate.resolve(strict=False)
    if not _is_within(resolved, workspace_dir):
        raise PathSecurityError(f"runtime command cwd must stay inside the isolated workspace: {resolved}")
    if not resolved.is_dir():
        raise FileNotFoundError(f"runtime command cwd does not exist: {resolved}")
    return resolved


def _append_run(path: Path, row: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _read_runs(path: Path, limit: int = 50) -> List[Dict[str, Any]]:
    if not path.is_file():
        return []
    out: List[Dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines[-max(1, limit) :]:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def _build_patch(manifest: RuntimeManifest) -> Tuple[str, List[Dict[str, Any]]]:
    if (manifest.paths.workspace_dir / ".git").is_dir() and shutil.which("git"):
        patch, changed = _build_git_patch(manifest.paths.workspace_dir)
        if patch.strip() or changed:
            return patch, changed
    return _build_compare_patch(manifest)


def _build_git_patch(workspace_dir: Path) -> Tuple[str, List[Dict[str, Any]]]:
    changed: List[Dict[str, Any]] = []
    patch_parts: List[str] = []
    diff_proc = subprocess.run(
        ["git", "diff", "--no-ext-diff", "--binary", "--", "."],
        cwd=str(workspace_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if diff_proc.returncode == 0 and diff_proc.stdout:
        patch_parts.append(diff_proc.stdout)
    name_proc = subprocess.run(
        ["git", "status", "--porcelain=v1"],
        cwd=str(workspace_dir),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        check=False,
    )
    if name_proc.returncode == 0:
        for line in (name_proc.stdout or "").splitlines():
            if not line.strip():
                continue
            status = line[:2].strip() or line[:2]
            rel = line[3:].strip().strip('"')
            changed.append({"relative_path": rel, "status": status})
            if status == "??":
                path = workspace_dir / rel
                patch_parts.append(_new_file_diff(path, rel))
    return "\n".join(part for part in patch_parts if part), changed


def _build_compare_patch(manifest: RuntimeManifest) -> Tuple[str, List[Dict[str, Any]]]:
    source_files = _file_map(manifest.paths.source_root)
    runtime_files = _file_map(manifest.paths.workspace_dir)
    all_paths = sorted(set(source_files) | set(runtime_files))
    parts: List[str] = []
    changed: List[Dict[str, Any]] = []
    for rel in all_paths:
        source = source_files.get(rel)
        runtime = runtime_files.get(rel)
        if source and runtime and _sha256_file(source) == _sha256_file(runtime):
            continue
        if source and not runtime:
            changed.append({"relative_path": rel, "status": "D"})
            parts.append(_text_diff(source, None, rel))
            continue
        if runtime and not source:
            changed.append({"relative_path": rel, "status": "A"})
            parts.append(_text_diff(None, runtime, rel))
            continue
        if source and runtime:
            changed.append({"relative_path": rel, "status": "M"})
            parts.append(_text_diff(source, runtime, rel))
    return "\n".join(part for part in parts if part), changed


def _file_map(root: Path) -> Dict[str, Path]:
    out: Dict[str, Path] = {}
    for path in _iter_files(root):
        rel = _relative_to(path, root)
        if _is_excluded(rel, include_patterns=[], exclude_patterns=[]):
            continue
        out[rel] = path
    return out


def _new_file_diff(path: Path, rel: str) -> str:
    return _text_diff(None, path, rel)


def _text_diff(old_path: Optional[Path], new_path: Optional[Path], rel: str) -> str:
    old_text = _read_text_for_diff(old_path)
    new_text = _read_text_for_diff(new_path)
    if old_text is None or new_text is None:
        status = "deleted" if new_path is None else "added" if old_path is None else "modified"
        size = (new_path or old_path).stat().st_size if (new_path or old_path) else 0
        return f"Binary file {rel} {status} ({size} bytes)\n"
    old_lines = old_text.splitlines(keepends=True)
    new_lines = new_text.splitlines(keepends=True)
    return "".join(
        difflib.unified_diff(
            old_lines,
            new_lines,
            fromfile=f"a/{rel}",
            tofile=f"b/{rel}",
            lineterm="",
        )
    )


def _read_text_for_diff(path: Optional[Path]) -> Optional[str]:
    if path is None:
        return ""
    try:
        if path.stat().st_size > MAX_TEXT_DIFF_BYTES:
            return None
        data = path.read_bytes()
    except OSError:
        return None
    if b"\x00" in data:
        return None
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            return data.decode("utf-8", errors="replace")
        except Exception:
            return None


def _patch_output_path(manifest: RuntimeManifest, output_path: str) -> Path:
    raw = str(output_path or "").strip()
    if raw:
        return safe_path_for_write(raw)
    return manifest.paths.artifacts_dir / f"{manifest.session_id}.patch"


def _patch_summary(changed_files: List[Dict[str, Any]]) -> str:
    if not changed_files:
        return "No runtime workspace changes detected."
    counts: Dict[str, int] = {}
    for item in changed_files:
        status = str(item.get("status") or "?")
        counts[status] = counts.get(status, 0) + 1
    return ", ".join(f"{status}:{count}" for status, count in sorted(counts.items()))


def _export_diagnostics(manifest: RuntimeManifest) -> Dict[str, Any]:
    zip_path = manifest.paths.diagnostics_dir / f"{manifest.session_id}-diagnostics.zip"
    patch_text, changed_files = ("", [])
    if manifest.mode != "mount":
        try:
            patch_text, changed_files = _build_patch(manifest)
        except Exception as exc:
            patch_text = f"patch export failed: {type(exc).__name__}: {exc}\n"
            changed_files = []
    patch_path = manifest.paths.diagnostics_dir / "changes.patch"
    patch_path.write_text(patch_text, encoding="utf-8", errors="replace")
    artifacts_json = manifest.paths.diagnostics_dir / "artifacts.json"
    artifacts_json.write_text(
        json.dumps(_list_artifacts(manifest.paths.artifacts_dir), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    summary = {
        "schema": RUNTIME_SCHEMA,
        "session_id": manifest.session_id,
        "status": manifest.status,
        "task": manifest.task,
        "backend": manifest.backend,
        "sandbox": manifest.sandbox,
        "source_root": str(manifest.paths.source_root),
        "workspace_dir": str(manifest.paths.workspace_dir),
        "artifacts_dir": str(manifest.paths.artifacts_dir),
        "changed_files": changed_files,
        "runs": _read_runs(manifest.paths.runs_path),
    }
    summary_path = manifest.paths.diagnostics_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        _zip_add_if_exists(archive, manifest.paths.manifest_path, "manifest.json")
        _zip_add_if_exists(archive, manifest.paths.runs_path, "runs.jsonl")
        _zip_add_if_exists(archive, summary_path, "summary.json")
        _zip_add_if_exists(archive, artifacts_json, "artifacts.json")
        _zip_add_if_exists(archive, patch_path, "changes.patch")
        for path in manifest.paths.diagnostics_dir.glob("run_*/*.txt"):
            _zip_add_if_exists(archive, path, str(path.relative_to(manifest.paths.diagnostics_dir)))
    return {
        "ok": True,
        "schema": RUNTIME_SCHEMA,
        "session_id": manifest.session_id,
        "diagnostics_zip": str(zip_path),
        "summary_path": str(summary_path),
        "patch_path": str(patch_path),
        "changed_files": changed_files,
        "artifacts": _list_artifacts(manifest.paths.artifacts_dir),
    }


def _zip_add_if_exists(archive: zipfile.ZipFile, path: Path, arcname: str) -> None:
    if path.is_file():
        archive.write(path, arcname=arcname)


def _list_artifacts(root: Path, limit: int = 200) -> List[Dict[str, Any]]:
    if not root.is_dir():
        return []
    out: List[Dict[str, Any]] = []
    for path in _iter_files(root):
        if len(out) >= limit:
            break
        try:
            size = path.stat().st_size
        except OSError:
            continue
        out.append({"path": str(path), "relative_path": _relative_to(path, root), "size": size})
    return out


def _iter_files(root: Path) -> Iterable[Path]:
    if not root.is_dir():
        return []
    for current, dirs, files in os.walk(root):
        current_path = Path(current)
        dirs[:] = [item for item in dirs if item not in EXCLUDED_DIRS]
        for name in files:
            path = current_path / name
            rel = _relative_to(path, root)
            if _is_excluded(rel, include_patterns=[], exclude_patterns=[]):
                continue
            yield path


def _is_excluded(rel: str, *, include_patterns: List[str], exclude_patterns: List[str]) -> bool:
    normalized = rel.replace("\\", "/")
    parts = normalized.split("/")
    if any(part in EXCLUDED_DIRS for part in parts[:-1]):
        return True
    if _matches_any(normalized, list(EXCLUDED_FILE_PATTERNS)):
        return True
    if exclude_patterns and _matches_any(normalized, exclude_patterns):
        return True
    if include_patterns and not _matches_any(normalized, include_patterns):
        return True
    return False


def _matches_any(value: str, patterns: Iterable[str]) -> bool:
    normalized = str(value or "").replace("\\", "/")
    return any(fnmatch.fnmatch(normalized, str(pattern).replace("\\", "/")) for pattern in patterns)


def _relative_to(path: Path, root: Path) -> str:
    try:
        return str(path.resolve(strict=False).relative_to(root.resolve(strict=False)))
    except ValueError:
        return path.name


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
        return True
    except ValueError:
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_session_id(session_id: str) -> str:
    value = str(session_id or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
        return ""
    return value


def _truncate(text: str, limit: int = MAX_RETURN_CHARS) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    head = limit * 2 // 3
    tail = limit - head
    return value[:head] + f"\n... [truncated {len(value) - limit} chars] ...\n" + value[-tail:]


def _json(payload: Dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)


def _json_error(message: str, *, code: str = "RUNTIME_ERROR", session_id: str = "") -> str:
    return _json(
        {
            "ok": False,
            "schema": RUNTIME_SCHEMA,
            "code": code,
            "session_id": session_id,
            "error": str(message),
        }
    )


__all__ = [
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
    "metis_vm_rootfs_boot_verifier_prepare",
    "metis_vm_rootfs_boot_verify",
    "metis_vm_guest_handshake_prepare",
    "metis_vm_guest_handshake_verify",
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
]

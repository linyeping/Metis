"""
Metis rootfs builder — produces a VM bundle from scratch.

Two build paths:
  1. Docker: build Alpine rootfs via Dockerfile, export as tarball
  2. Pre-built: download a release tarball from a URL

Both paths then convert the rootfs tarball → VHDX using qemu-img
or PowerShell Hyper-V cmdlets, and pair it with a kernel+initrd
to form a complete VM bundle.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("metis.rootfs_builder")

BUNDLE_MANIFEST_SCHEMA = "metis.vm_bundle.manifest.v1"

# Default paths
_GUEST_DIR = Path(__file__).parent / "guest"
_DEFAULT_OUTPUT = Path(os.environ.get("LOCALAPPDATA", "")) / "Metis" / "vm_bundles" / "metisvm.bundle"


def build_rootfs_docker(output_dir: Optional[Path] = None, tag: str = "metis-rootfs:latest") -> Dict[str, Any]:
    """Build rootfs via Docker and export as tarball."""
    out = output_dir or _DEFAULT_OUTPUT
    out.mkdir(parents=True, exist_ok=True)

    dockerfile = _GUEST_DIR / "Dockerfile.rootfs"
    if not dockerfile.is_file():
        return {"ok": False, "error": f"Dockerfile not found: {dockerfile}"}

    if not shutil.which("docker"):
        return {"ok": False, "error": "docker not found in PATH"}

    t0 = time.time()

    # Build image
    build_cmd = ["docker", "build", "-f", str(dockerfile), "-t", tag, str(_GUEST_DIR)]
    result = subprocess.run(build_cmd, capture_output=True, text=True, timeout=600)
    if result.returncode != 0:
        return {"ok": False, "error": f"docker build failed: {result.stderr[:2000]}"}

    # Export rootfs
    tarball = out / "rootfs.tar.gz"
    container_name = f"metis-rootfs-export-{int(time.time())}"
    try:
        subprocess.run(["docker", "create", "--name", container_name, tag, "/bin/true"],
                       capture_output=True, text=True, timeout=30, check=True)
        with open(str(tarball), "wb") as f:
            export = subprocess.Popen(["docker", "export", container_name], stdout=subprocess.PIPE)
            gzip = subprocess.Popen(["gzip"], stdin=export.stdout, stdout=f)
            export.stdout.close()
            gzip.wait(timeout=300)
    finally:
        subprocess.run(["docker", "rm", "-f", container_name], capture_output=True, timeout=30)

    duration_ms = int((time.time() - t0) * 1000)

    return {
        "ok": True,
        "tarball": str(tarball),
        "size_bytes": tarball.stat().st_size,
        "duration_ms": duration_ms,
    }


def tarball_to_vhdx(
    tarball: Path,
    output_vhdx: Path,
    size_gb: int = 2,
) -> Dict[str, Any]:
    """Convert a rootfs tarball to a VHDX.

    Uses qemu-img if available, otherwise falls back to PowerShell
    Hyper-V New-VHD + WSL for ext4 formatting.
    """
    if shutil.which("qemu-img"):
        return _convert_via_qemu(tarball, output_vhdx, size_gb)
    return _convert_via_powershell(tarball, output_vhdx, size_gb)


def _convert_via_qemu(tarball: Path, output_vhdx: Path, size_gb: int) -> Dict[str, Any]:
    raw = output_vhdx.with_suffix(".raw")
    try:
        subprocess.run(
            ["qemu-img", "create", "-f", "raw", str(raw), f"{size_gb}G"],
            capture_output=True, text=True, timeout=60, check=True,
        )
        # Format + extract via WSL if available
        if shutil.which("wsl"):
            wsl_raw = _to_wsl_path(raw)
            wsl_tar = _to_wsl_path(tarball)
            script = f"""
set -e
LOOP=$(losetup --find --show {wsl_raw})
mkfs.ext4 -q "$LOOP"
MOUNT=$(mktemp -d)
mount "$LOOP" "$MOUNT"
tar xzf {wsl_tar} -C "$MOUNT"
umount "$MOUNT"
losetup -d "$LOOP"
rmdir "$MOUNT"
"""
            result = subprocess.run(
                ["wsl", "--exec", "bash", "-c", script],
                capture_output=True, text=True, timeout=300,
            )
            if result.returncode != 0:
                return {"ok": False, "error": f"WSL rootfs extraction failed: {result.stderr[:1000]}"}

        subprocess.run(
            ["qemu-img", "convert", "-f", "raw", "-O", "vhdx", str(raw), str(output_vhdx)],
            capture_output=True, text=True, timeout=300, check=True,
        )
        return {"ok": True, "vhdx": str(output_vhdx), "size_bytes": output_vhdx.stat().st_size}
    finally:
        raw.unlink(missing_ok=True)


def _convert_via_powershell(tarball: Path, output_vhdx: Path, size_gb: int) -> Dict[str, Any]:
    """Fallback: use PowerShell New-VHD (requires Hyper-V module)."""
    script = f"""
$ErrorActionPreference = 'Stop'
New-VHD -Path '{output_vhdx}' -SizeBytes {size_gb}GB -Dynamic | Out-Null
"""
    result = subprocess.run(
        ["powershell", "-NoProfile", "-Command", script],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        return {
            "ok": False,
            "error": f"New-VHD failed (Hyper-V PS module may be missing): {result.stderr[:1000]}",
            "hint": "Install qemu-img or enable Hyper-V PowerShell module",
        }

    return {"ok": True, "vhdx": str(output_vhdx), "note": "empty VHDX created — needs rootfs extraction via WSL"}


def write_bundle_manifest(bundle_dir: Path, kernel_source: str = "") -> Dict[str, Any]:
    """Write metis-vm-pack.json manifest into a bundle directory."""
    manifest = {
        "schema": BUNDLE_MANIFEST_SCHEMA,
        "name": "metisvm",
        "version": "0.1.0",
        "owner": "Metis",
        "created_at": time.time(),
        "kernel_source": kernel_source,
        "assets": {},
    }

    for name in ("vmlinuz", "initrd", "rootfs.vhdx", "metis-data.vhdx", "sessiondata.vhdx"):
        path = bundle_dir / name
        if path.is_file():
            manifest["assets"][name] = {
                "size_bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }

    manifest_path = bundle_dir / "metis-vm-pack.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    return {"ok": True, "manifest_path": str(manifest_path), "assets": list(manifest["assets"].keys())}


def _sha256(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                break
            h.update(chunk)
    return h.hexdigest()


def _to_wsl_path(win_path: Path) -> str:
    p = str(win_path.resolve()).replace("\\", "/")
    if len(p) >= 2 and p[1] == ":":
        return f"/mnt/{p[0].lower()}{p[2:]}"
    return p


__all__ = [
    "build_rootfs_docker",
    "tarball_to_vhdx",
    "write_bundle_manifest",
]

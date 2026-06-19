#!/usr/bin/env python3
r"""
Build a production Metis VM runtime pack (metisvm.bundle).

Assembles an initramfs-only bundle the existing runtime-manager
install/ensure/repair pipeline can consume:

  metisvm.bundle/
    vmlinuz                 (Linux kernel — reused WSL2 kernel, GPL)
    initrd                  (Metis initramfs: python + userland + metisd)
    guest/metisd.py         (agent source, for reference / stdio backend)
    metis-vm-pack.json      (manifest: schema, owner, boot_mode, per-file sha256)
    SHA256SUMS.txt          (checksums for integrity verification)
    KERNEL_SOURCE.txt       (GPL source pointer for the redistributed kernel)

The initramfs is built inside a Linux environment (WSL distro) via
build_initramfs.py.  Everything else is assembled host-side.

Usage:
  python -m backend.runtime.build_runtime_pack [--out DIR] [--wsl-distro NAME]
                                               [--kernel PATH] [--zip] [--install]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
GUEST_DIR = Path(__file__).parent / "guest"

VM_PACK_MANIFEST_SCHEMA = "metis.vm_runtime_pack.manifest.v1"
PACK_VERSION = "0.1.0"
DEFAULT_KERNEL = r"C:\Program Files\WSL\tools\kernel"
DEFAULT_OUT = REPO_ROOT / "desktop" / "resources" / "runtime-pack" / "metisvm.bundle"

METISD_VSOCK_PORT = 5001
PLAN9_FIRST_PORT = 50001

KERNEL_SOURCE_NOTE = """\
Metis VM runtime pack — kernel provenance
=========================================

The `vmlinuz` in this bundle is the Microsoft WSL2 Linux kernel, reused
unmodified.  It is licensed under GPLv2.  Corresponding source is published by
Microsoft at:

  https://github.com/microsoft/WSL2-Linux-Kernel

To obtain the exact source for the kernel version in this bundle, see the
`Linux version` string embedded in `vmlinuz` and check out the matching tag.

A future Metis release will replace this with a self-built kernel (LinuxKit /
buildroot) to remove the external dependency.  See metis-vm-pack.json
`kernel_origin` for the build/source state of this specific pack.
"""


def _sha256(path: Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(chunk), b""):
            h.update(block)
    return h.hexdigest()


def _to_wsl_path(p: Path) -> str:
    s = str(p.resolve()).replace("\\", "/")
    if len(s) >= 2 and s[1] == ":":
        return f"/mnt/{s[0].lower()}{s[2:]}"
    return s


def build_initrd(wsl_distro: str, out_initrd: Path) -> None:
    """Build the initramfs inside a WSL Linux distro."""
    builder = _to_wsl_path(GUEST_DIR / "build_initramfs.py")
    guest = _to_wsl_path(GUEST_DIR)
    target = _to_wsl_path(out_initrd)
    cmd = ["wsl", "-d", wsl_distro, "-u", "root", "--",
           "bash", "-lc", f"python3 {builder} {guest} {target}"]
    print(f"[pack] building initramfs in WSL distro '{wsl_distro}'...")
    result = subprocess.run(cmd, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"initramfs build failed (exit {result.returncode})")
    if not out_initrd.is_file():
        raise RuntimeError(f"initramfs not produced at {out_initrd}")


def assemble_bundle(out: Path, kernel: Path, initrd: Path) -> dict:
    """Assemble the bundle directory with manifest + checksums."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    (out / "guest").mkdir()

    # Copy core assets
    shutil.copy2(kernel, out / "vmlinuz")
    shutil.copy2(initrd, out / "initrd")
    shutil.copy2(GUEST_DIR / "metisd.py", out / "guest" / "metisd.py")
    (out / "KERNEL_SOURCE.txt").write_text(KERNEL_SOURCE_NOTE, encoding="utf-8")

    # Per-asset checksums
    assets = {}
    for rel in ("vmlinuz", "initrd", "guest/metisd.py"):
        fp = out / rel
        assets[rel] = {"size_bytes": fp.stat().st_size, "sha256": _sha256(fp)}

    manifest = {
        "schema": VM_PACK_MANIFEST_SCHEMA,
        "owner": "Metis",
        "name": "metisvm",
        "version": PACK_VERSION,
        "boot_mode": "initramfs",
        "created_at": time.time(),
        "kernel_origin": {
            "kind": "reused",
            "source": "microsoft/WSL2-Linux-Kernel",
            "license": "GPL-2.0",
            "note": "WSL2 kernel reused unmodified; see KERNEL_SOURCE.txt",
        },
        "transport": {
            "metisd_vsock_port": METISD_VSOCK_PORT,
            "plan9_first_port": PLAN9_FIRST_PORT,
            "stdio_smoke_ready": True,
        },
        "assets": assets,
    }
    (out / "metis-vm-pack.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")

    # SHA256SUMS.txt over every file (relative paths, posix)
    lines = []
    for fp in sorted(out.rglob("*")):
        if fp.is_file() and fp.name != "SHA256SUMS.txt":
            rel = fp.relative_to(out).as_posix()
            lines.append(f"{_sha256(fp)}  {rel}")
    (out / "SHA256SUMS.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")

    total = sum(fp.stat().st_size for fp in out.rglob("*") if fp.is_file())
    return {"bundle": str(out), "total_bytes": total, "assets": list(assets.keys())}


def make_zip(bundle: Path, zip_path: Path) -> None:
    zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for fp in sorted(bundle.rglob("*")):
            if fp.is_file():
                zf.write(fp, f"metisvm.bundle/{fp.relative_to(bundle).as_posix()}")
    print(f"[pack] zip: {zip_path} ({zip_path.stat().st_size:,} bytes)")


def install_to_localappdata(bundle: Path) -> Path:
    local = os.environ.get("LOCALAPPDATA", "")
    if not local:
        raise RuntimeError("LOCALAPPDATA not set")
    dest = Path(local) / "Metis" / "vm_bundles" / "metisvm.bundle"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(bundle, dest)
    print(f"[pack] installed to {dest}")
    return dest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--wsl-distro", default="MetisRuntime")
    ap.add_argument("--kernel", default=DEFAULT_KERNEL)
    ap.add_argument("--initrd", default="", help="use a prebuilt initrd instead of building in WSL")
    ap.add_argument("--zip", action="store_true")
    ap.add_argument("--install", action="store_true", help="also install into %%LOCALAPPDATA%%\\Metis")
    args = ap.parse_args()

    out = Path(args.out)
    kernel = Path(args.kernel)
    if not kernel.is_file():
        print(f"ERROR: kernel not found: {kernel}")
        sys.exit(1)

    print("=" * 60)
    print("Metis Runtime Pack Builder")
    print("=" * 60)

    # 1) initramfs
    if args.initrd:
        initrd = Path(args.initrd)
        if not initrd.is_file():
            print(f"ERROR: --initrd not found: {initrd}")
            sys.exit(1)
    else:
        initrd = out.parent / "_build_initrd"
        initrd.parent.mkdir(parents=True, exist_ok=True)
        build_initrd(args.wsl_distro, initrd)

    # 2) assemble
    info = assemble_bundle(out, kernel, initrd)
    print(f"[pack] assembled: {info['bundle']} ({info['total_bytes']:,} bytes)")
    for a in info["assets"]:
        print(f"        - {a}")

    # 3) optional zip + install
    if args.zip:
        make_zip(out, out.parent / "metis-runtime-bundle-v2.zip")
    if args.install:
        install_to_localappdata(out)

    print("DONE")


if __name__ == "__main__":
    main()

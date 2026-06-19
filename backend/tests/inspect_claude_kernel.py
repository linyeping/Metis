"""
Read-only inspection of Claude's VM kernel + initrd (v2: zstd/xz aware).

Determine whether we can REUSE Claude's vmlinuz for a Metis-owned rootfs.
Nothing is copied or modified.
"""
from __future__ import annotations

import gzip
import io
import lzma
import re
import sys
from pathlib import Path

import zstandard as zstd

BUNDLE = Path(r"E:\ClaudeCode\cache\vm_bundles\claudevm.bundle")
VMLINUZ = BUNDLE / "vmlinuz"
INITRD = BUNDLE / "initrd"


def banner(t: str) -> None:
    print("\n" + "=" * 60)
    print(t)
    print("=" * 60)


def find_kernel_version(data: bytes) -> str:
    m = re.search(rb"Linux version [0-9][^\x00\n]{0,200}", data)
    return m.group(0).decode("latin-1") if m else "(version string not found)"


def try_decompress_all(data: bytes, start: int = 0) -> tuple[bytes | None, str]:
    """Try every compression format at a given offset."""
    chunk = data[start:]
    # gzip
    if chunk[:3] == b"\x1f\x8b\x08":
        try:
            return gzip.decompress(chunk), "gzip"
        except Exception:
            pass
    # zstd
    if chunk[:4] == b"\x28\xb5\x2f\xfd":
        try:
            dctx = zstd.ZstdDecompressor()
            return dctx.stream_reader(io.BytesIO(chunk)).read(), "zstd"
        except Exception:
            pass
    # xz
    if chunk[:6] == b"\xfd7zXZ\x00":
        try:
            return lzma.decompress(chunk), "xz"
        except Exception:
            pass
    return None, "none"


def find_and_decompress(data: bytes) -> tuple[bytes | None, str]:
    """Scan for any known compression magic and decompress the payload."""
    magics = [
        (b"\x1f\x8b\x08", "gzip"),
        (b"\x28\xb5\x2f\xfd", "zstd"),
        (b"\xfd7zXZ\x00", "xz"),
    ]
    for magic, name in magics:
        idx = data.find(magic)
        if idx >= 0:
            out, fmt = try_decompress_all(data, idx)
            if out:
                return out, f"{fmt}@0x{idx:x}"
    return None, "none"


def check_drivers(text: bytes, label: str) -> dict:
    checks = {
        "9P filesystem":   [rb"9p2000", rb"v9fs", rb"9pnet"],
        "virtio-9p":       [rb"9pnet_virtio", rb"virtio_9p"],
        "vsock/hv_sock":   [rb"hv_sock", rb"vsock", rb"hvsocket", rb"vmw_vsock"],
        "virtio-scsi":     [rb"virtio_scsi", rb"virtio-scsi", rb"virtscsi"],
        "virtio core":     [rb"virtio_pci", rb"virtio_ring", rb"virtio_blk"],
    }
    results = {}
    print(f"\n  Driver signatures in {label}:")
    for name, pats in checks.items():
        hits = [p.decode() for p in pats if p in text]
        results[name] = bool(hits)
        status = "YES" if hits else "no "
        print(f"    [{status}] {name:18} {hits if hits else ''}")
    return results


def main() -> None:
    banner("Claude VM Kernel + Initrd Inspection v2")

    vmlinuz = VMLINUZ.read_bytes()
    print(f"\nvmlinuz: {len(vmlinuz):,} bytes  bzImage={vmlinuz[0x202:0x206] == b'HdrS'}")

    kern, fmt = find_and_decompress(vmlinuz)
    if kern:
        print(f"  Decompressed kernel: {len(kern):,} bytes via {fmt}")
        print(f"  {find_kernel_version(kern)}")
        check_drivers(kern, "decompressed kernel")
        # Embedded config?
        cfg, cfmt = find_and_decompress(kern[kern.find(b"IKCFG_ST"):]) if b"IKCFG_ST" in kern else (None, "")
        if cfg:
            print(f"\n  Embedded .config found ({len(cfg):,} bytes)")
            for key in ["CONFIG_9P_FS", "CONFIG_NET_9P_VIRTIO", "CONFIG_VSOCKETS",
                        "CONFIG_HYPERV_VSOCKETS", "CONFIG_SCSI_VIRTIO"]:
                m = re.search((key + r"=.").encode(), cfg)
                print(f"    {m.group(0).decode() if m else key + ' (not set)'}")
    else:
        print("  Could not decompress kernel; scanning raw")
        check_drivers(vmlinuz, "raw vmlinuz")
        print(f"  {find_kernel_version(vmlinuz)}")

    banner("Initrd analysis")
    initrd = INITRD.read_bytes()
    print(f"initrd: {len(initrd):,} bytes  magic={initrd[:4].hex()}")
    payload, ifmt = try_decompress_all(initrd)
    if payload:
        print(f"  Decompressed via {ifmt}: {len(payload):,} bytes")
    else:
        payload = initrd
        print("  Could not decompress; scanning raw")

    check_drivers(payload, "initrd")
    print()
    for hint in [b"busybox", b"metisd", b"gcs", b"gcstools", b"opengcs",
                 b"/init", b"/sbin/init", b"python", b"alpine", b"runc"]:
        # count occurrences for stronger signal
        n = payload.count(hint)
        print(f"    {'YES' if n else 'no '}  {hint.decode():14} (x{n})")

    # Look for the gcs binary path more specifically
    for pat in [rb"/bin/gcs", rb"gcs\x00", rb"GuestComputeService", rb"bridge"]:
        m = re.search(pat, payload)
        if m:
            ctx = payload[max(0, m.start()-20):m.start()+40]
            print(f"    gcs ctx: {ctx}")

    banner("Verdict")


if __name__ == "__main__":
    main()

"""Inspect the WSL2 kernel for the drivers Metis needs (read-only)."""
from __future__ import annotations
import gzip, io, lzma, re
from pathlib import Path
import zstandard as zstd

KERNEL = Path(r"C:\Program Files\WSL\tools\kernel")


def _gunzip(c):
    return gzip.decompress(c)

def _zstd(c):
    return zstd.ZstdDecompressor().stream_reader(io.BytesIO(c)).read()

def _xz(c):
    return lzma.decompress(c)

def _lz4_legacy(c):
    # Linux kernel LZ4 legacy frame: magic 0x184C2102, then blocks of [u32 size][data]
    import lz4.block as lb
    pos = 4
    out = bytearray()
    while pos + 4 <= len(c):
        blen = int.from_bytes(c[pos:pos+4], "little")
        pos += 4
        if blen == 0 or blen > len(c) - pos or blen == 0x184C2102:
            break
        try:
            out += lb.decompress(c[pos:pos+blen], uncompressed_size=8 * 1024 * 1024)
        except Exception:
            break
        pos += blen
    return bytes(out)

def _lz4_frame(c):
    import lz4.frame as lf
    return lf.decompress(c)


def decompress_any(data: bytes):
    table = [
        (b"\x1f\x8b\x08", "gzip", _gunzip),
        (b"\x28\xb5\x2f\xfd", "zstd", _zstd),
        (b"\xfd7zXZ\x00", "xz", _xz),
        (b"\x02\x21\x4c\x18", "lz4-legacy", _lz4_legacy),
        (b"\x04\x22\x4d\x18", "lz4-frame", _lz4_frame),
    ]
    # try each magic at every occurrence
    for magic, name, fn in table:
        start = 0
        while True:
            idx = data.find(magic, start)
            if idx < 0:
                break
            try:
                out = fn(data[idx:])
                if out and len(out) > 100_000:
                    return out, f"{name}@0x{idx:x}"
            except Exception:
                pass
            start = idx + 1
    return None, "none"


def check(text: bytes, label: str):
    checks = {
        "9P fs":        [rb"9p2000", rb"v9fs", rb"9pnet"],
        "virtio-9p":    [rb"9pnet_virtio", rb"v9fs"],
        "vsock/hv_sock":[rb"hv_sock", rb"vsock", rb"vmw_vsock", rb"AF_VSOCK"],
        "virtio-scsi":  [rb"virtio_scsi", rb"virtscsi"],
        "virtio core":  [rb"virtio_pci", rb"virtio_ring", rb"virtio_blk"],
        "ext4":         [rb"ext4"],
        "overlayfs":    [rb"overlay"],
    }
    print(f"\n  Drivers in {label}:")
    for n, pats in checks.items():
        hits = [p.decode() for p in pats if p in text]
        print(f"    [{'YES' if hits else 'no '}] {n:14} {hits if hits else ''}")


def main():
    data = KERNEL.read_bytes()
    print(f"WSL kernel: {len(data):,} bytes")
    print(f"  bzImage: {data[0x202:0x206] == b'HdrS'}")
    m = re.search(rb"Linux version [0-9][^\x00\n]{0,160}", data)
    print(f"  {m.group(0).decode('latin-1') if m else '(no plain version)'}")

    kern, fmt = decompress_any(data)
    if kern:
        print(f"  decompressed: {len(kern):,} via {fmt}")
        m = re.search(rb"Linux version [0-9][^\x00\n]{0,160}", kern)
        if m:
            print(f"  {m.group(0).decode('latin-1')}")
        check(kern, "decompressed kernel")
        if b"IKCFG_ST" in kern:
            cfg, _ = decompress_any(kern[kern.find(b"IKCFG_ST"):])
            if cfg:
                print(f"\n  Embedded .config ({len(cfg):,} bytes):")
                for key in ["CONFIG_9P_FS", "CONFIG_NET_9P", "CONFIG_NET_9P_VIRTIO",
                            "CONFIG_VSOCKETS", "CONFIG_HYPERV_VSOCKETS", "CONFIG_VIRTIO_VSOCKETS",
                            "CONFIG_SCSI_VIRTIO", "CONFIG_VIRTIO_PCI", "CONFIG_EXT4_FS",
                            "CONFIG_BLK_DEV_INITRD", "CONFIG_DEVTMPFS"]:
                    mm = re.search((key + r"=.").encode(), cfg)
                    if mm:
                        print(f"    {mm.group(0).decode()}")
                    elif (b"# " + key.encode() + b" is not set") in cfg:
                        print(f"    {key} is NOT set")
                    else:
                        print(f"    {key} (absent)")
    else:
        check(data, "raw kernel")


if __name__ == "__main__":
    main()

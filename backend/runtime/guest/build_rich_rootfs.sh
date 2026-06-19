#!/usr/bin/env bash
# Build the Metis rich VM rootfs in a Linux env (GitHub Actions runner).
#
# Produces (kernel added later on the Windows build):
#   <out>/rootfs.vhdx   ext4 image with python + office libs + metisd (RO base)
#   <out>/initrd        boot initramfs: mount rootfs RO + tmpfs overlay + switch_root
#   <out>/metis-vm-pack.json, SHA256SUMS.txt
#
# Needs: docker, qemu-utils (qemu-img), e2fsprogs (mke2fs), cpio, busybox-static.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
OUT="${1:-$HERE/../vmpack_build_rich}"
SIZE_MB="${ROOTFS_SIZE_MB:-4096}"
mkdir -p "$OUT"

echo "[1/5] docker build rich rootfs image"
docker build -f "$HERE/Dockerfile.rootfs.rich" -t metis-rootfs-rich "$HERE"

echo "[2/5] export container filesystem"
cid="$(docker create metis-rootfs-rich)"
rootdir="$OUT/rootfs"
rm -rf "$rootdir"; mkdir -p "$rootdir"
docker export "$cid" | tar -x -C "$rootdir"
docker rm "$cid" >/dev/null

echo "[3/5] ext4 rootfs.vhdx (mke2fs -d, no privileged loop mount)"
mke2fs -t ext4 -d "$rootdir" -F -q "$OUT/rootfs.ext4" "${SIZE_MB}M"
qemu-img convert -f raw -O vhdx "$OUT/rootfs.ext4" "$OUT/rootfs.vhdx"
rm -f "$OUT/rootfs.ext4"
rm -rf "$rootdir"

echo "[4/5] boot initramfs (busybox + RO-rootfs + tmpfs overlay + switch_root)"
IRD="$OUT/irfs"
rm -rf "$IRD"; mkdir -p "$IRD"/{bin,proc,sys,dev,lower,upper,newroot}
cp "$(command -v busybox)" "$IRD/bin/busybox"
for a in sh mount switch_root mkdir; do ln -sf busybox "$IRD/bin/$a"; done
cat > "$IRD/init" <<'INIT'
#!/bin/sh
/bin/busybox mount -t proc none /proc
/bin/busybox mount -t sysfs none /sys
/bin/busybox mount -t devtmpfs none /dev
for d in /dev/sda /dev/sdb /dev/vda /dev/vdb; do
  /bin/busybox mount -t ext4 -o ro "$d" /lower 2>/dev/null && break
done
/bin/busybox mount -t tmpfs none /upper
/bin/busybox mkdir -p /upper/up /upper/work
/bin/busybox mount -t overlay overlay -o lowerdir=/lower,upperdir=/upper/up,workdir=/upper/work /newroot
exec /bin/busybox switch_root /newroot /sbin/metis-init
INIT
chmod +x "$IRD/init"
( cd "$IRD" && find . | cpio -o -H newc 2>/dev/null | gzip -9 > "$OUT/initrd" )
rm -rf "$IRD"

echo "[5/5] manifest + checksums"
python3 - "$OUT" <<'PY'
import hashlib, json, os, sys, time
out = sys.argv[1]
def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for c in iter(lambda: f.read(1 << 20), b""):
            h.update(c)
    return h.hexdigest()
assets = {}
for n in ("initrd", "rootfs.vhdx"):
    p = os.path.join(out, n)
    if os.path.isfile(p):
        assets[n] = {"size_bytes": os.path.getsize(p), "sha256": sha(p)}
json.dump({
    "schema": "metis.vm_runtime_pack.manifest.v1",
    "owner": "Metis", "name": "metisvm", "version": "0.2.0-rich",
    "boot_mode": "rootfs", "created_at": time.time(),
    "userland": "python3.12 + openpyxl/python-docx/python-pptx/pandas/numpy/pillow/reportlab/pypdf + git + iproute2",
    "assets": assets,
}, open(os.path.join(out, "metis-vm-pack.json"), "w"), indent=2)
with open(os.path.join(out, "SHA256SUMS.txt"), "w") as f:
    for n, a in assets.items():
        f.write(f"{a['sha256']}  {n}\n")
print("  assets:", {n: round(a["size_bytes"]/1024/1024, 1) for n, a in assets.items()})
PY

echo "DONE -> $OUT"
echo "  rootfs.vhdx + initrd + manifest produced. Add vmlinuz (WSL kernel) on the Windows build to complete the bundle."

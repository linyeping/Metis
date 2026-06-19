# Build the Metis rich VM rootfs (office libs + network userland) locally via Docker.
# Two stages, both run in containers (no host Linux tools needed):
#   1. docker build -> docker export   = rootfs filesystem tarball
#   2. debian builder container        = tar -> ext4 -> vhdx + cpio initramfs
# Output: <Out>/rootfs.vhdx + <Out>/initrd + manifest

param(
  [string]$Out = (Join-Path $PSScriptRoot "..\..\backend\runtime\vmpack_build_rich")
)

$ErrorActionPreference = 'Stop'
$env:PATH = "D:\Docker\Docker\resources\bin;$env:PATH"

$guest = Resolve-Path (Join-Path $PSScriptRoot "..\..\backend\runtime\guest")
$Out = (New-Item -ItemType Directory -Force -Path $Out).FullName

Write-Host "[1/4] docker build rich rootfs image"
docker build -f "$guest\Dockerfile.rootfs.rich" -t metis-rootfs-rich "$guest"

Write-Host "[2/4] export container filesystem to tarball"
$tar = Join-Path $Out "rootfs.tar"
if (Test-Path $tar) { Remove-Item $tar -Force }
$cid = (docker create metis-rootfs-rich).Trim()
try {
  docker export -o $tar $cid
} finally {
  docker rm $cid | Out-Null
}
Write-Host "      tarball: $([math]::Round((Get-Item $tar).Length/1MB,1)) MB"

Write-Host "[3/4] convert tarball -> ext4 vhdx + boot initramfs (debian builder)"
$outU = $Out -replace '\\','/' -replace '^([A-Za-z]):','/mnt/$1' -replace '/([A-Z])','/$1'.ToLower()
# Robust drive-letter translation
$drive = $Out.Substring(0,1).ToLower()
$rest  = $Out.Substring(2) -replace '\\','/'
$mnt   = "/mnt/$drive$rest"

docker run --rm `
  -e HTTP_PROXY=http://host.docker.internal:7897 `
  -e HTTPS_PROXY=http://host.docker.internal:7897 `
  -v "${Out}:/work" debian:bookworm bash -c @"
set -euo pipefail
if [ -n "`$HTTP_PROXY" ]; then
  printf 'Acquire::http::Proxy `"%s`";\nAcquire::https::Proxy `"%s`";\n' "`$HTTP_PROXY" "`$HTTPS_PROXY" > /etc/apt/apt.conf.d/00proxy
fi
apt-get update -qq
apt-get install -y -qq --no-install-recommends qemu-utils e2fsprogs cpio busybox-static gzip
cd /work

echo '  unpack tarball to rootdir'
mkdir -p rootdir
tar -xf rootfs.tar -C rootdir
rm -f rootfs.tar

SIZE_MB=`${ROOTFS_SIZE_MB:-4096}
echo '  mke2fs -d (rootfs.ext4 -> rootfs.vhdx)'
mke2fs -t ext4 -d rootdir -F -q rootfs.ext4 `${SIZE_MB}M
qemu-img convert -f raw -O vhdx rootfs.ext4 rootfs.vhdx
rm -f rootfs.ext4
rm -rf rootdir

echo '  boot initramfs (busybox + overlay + switch_root)'
IRD=/tmp/irfs
rm -rf `$IRD; mkdir -p `$IRD/{bin,proc,sys,dev,lower,upper,newroot}
cp /bin/busybox `$IRD/bin/busybox
for a in sh mount switch_root mkdir; do ln -sf busybox `$IRD/bin/`$a; done
cat > `$IRD/init <<'INIT'
#!/bin/sh
/bin/busybox mount -t proc none /proc
/bin/busybox mount -t sysfs none /sys
/bin/busybox mount -t devtmpfs none /dev
for d in /dev/sda /dev/sdb /dev/vda /dev/vdb; do
  /bin/busybox mount -t ext4 -o ro `"`$d`" /lower 2>/dev/null && break
done
/bin/busybox mount -t tmpfs none /upper
/bin/busybox mkdir -p /upper/up /upper/work
/bin/busybox mount -t overlay overlay -o lowerdir=/lower,upperdir=/upper/up,workdir=/upper/work /newroot
exec /bin/busybox switch_root /newroot /sbin/metis-init
INIT
chmod +x `$IRD/init
(cd `$IRD && find . | cpio -o -H newc 2>/dev/null | gzip -9 > /work/initrd)
rm -rf `$IRD

python3 - <<'PY'
import hashlib, json, os, time
def sha(p):
    h = hashlib.sha256()
    with open(p, 'rb') as f:
        for c in iter(lambda: f.read(1<<20), b''):
            h.update(c)
    return h.hexdigest()
assets = {}
for n in ('initrd','rootfs.vhdx'):
    p = os.path.join('/work', n)
    if os.path.isfile(p):
        assets[n] = {'size_bytes': os.path.getsize(p), 'sha256': sha(p)}
manifest = {
    'schema': 'metis.vm_runtime_pack.manifest.v1',
    'owner': 'Metis', 'name': 'metisvm', 'version': '0.2.0-rich',
    'boot_mode': 'rootfs', 'created_at': time.time(),
    'userland': 'python3.12 + openpyxl/python-docx/python-pptx/pandas/numpy/pillow/reportlab/pypdf + git + iproute2',
    'assets': assets,
}
open('/work/metis-vm-pack.json','w').write(json.dumps(manifest, indent=2))
with open('/work/SHA256SUMS.txt','w') as f:
    for n,a in assets.items(): f.write(f\"{a['sha256']}  {n}\n\")
print('  assets MB:', {n: round(a['size_bytes']/1024/1024,1) for n,a in assets.items()})
PY

chmod -R a+rw /work || true
"@

Write-Host "[4/4] artifacts"
Get-ChildItem $Out | Select-Object Name, @{N='MB';E={[math]::Round($_.Length/1MB,2)}} | Format-Table -AutoSize
Write-Host "DONE -> $Out"

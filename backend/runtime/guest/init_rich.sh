#!/bin/bash
# Metis rich-rootfs PID 1 (entered via switch_root from the boot initramfs).
# Mounts pseudo-filesystems and starts metisd on vsock. The host configures
# the NIC on demand via metisd's net.configure (no DHCP dependency).
set +e

mount -t proc     none /proc     2>/dev/null
mount -t sysfs    none /sys      2>/dev/null
mount -t devtmpfs none /dev      2>/dev/null
mount -t tmpfs    none /tmp      2>/dev/null
mount -t tmpfs    none /run      2>/dev/null

ip link set lo up 2>/dev/null

mkdir -p /workspace /artifacts /diagnostics
export HOME=/root
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
export METISD_MODE=vsock METISD_VSOCK_PORT=5001

echo "[metis-init] rich rootfs up; starting metisd on vsock 5001" > /dev/kmsg 2>/dev/null
while true; do
    /usr/local/bin/python3 /usr/local/bin/metisd vsock
    echo "[metis-init] metisd exited; restart in 2s" > /dev/kmsg 2>/dev/null
    sleep 2
done

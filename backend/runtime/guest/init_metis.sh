#!/bin/sh
# Metis VM init script — runs as PID 1 inside the HCS VM.
# Mounts Plan 9 shares, then starts metisd on stdio.
set -e

# Mount Plan 9 shares from the host (HCS passes them as 9p tags)
for tag in workspace artifacts diagnostics; do
    mountpoint="/mnt/$tag"
    mkdir -p "$mountpoint"
    if mount -t 9p "$tag" "$mountpoint" -o trans=virtio,version=9p2000.L,msize=262144 2>/dev/null; then
        echo "[init] mounted $tag → $mountpoint"
    else
        echo "[init] $tag mount skipped (not available)"
    fi
done

# Start metisd
exec /usr/local/bin/metisd

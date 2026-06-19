#!/usr/bin/env bash
# Build rich rootfs entirely through Docker (no WSL apt needed).
# Drives the existing build_rich_rootfs.sh inside a debian:bookworm builder
# that has docker-in-docker access via the mounted docker.sock.
set -euo pipefail

OUT="${1:-/work/out}"
mkdir -p "$OUT"

apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    docker.io qemu-utils e2fsprogs cpio busybox-static python3 ca-certificates

# build_rich_rootfs.sh expects /usr/local/bin/python3, which we just installed.
chmod +x /work/build_rich_rootfs.sh
/work/build_rich_rootfs.sh "$OUT"
chmod -R a+rw "$OUT" || true

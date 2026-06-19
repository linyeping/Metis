#!/usr/bin/env python3
"""
Build a Metis VM initramfs — runs INSIDE a Linux build env (MetisRuntime WSL).

Produces a self-contained cpio.gz initramfs containing:
  - python3 + its shared-lib deps + stdlib
  - metisd guest agent
  - a python /init (PID 1) that mounts proc/sys/dev + 9p shares via
    ctypes mount(), then starts metisd on AF_VSOCK

initramfs-only design: no separate rootfs.vhdx, no busybox, no cpio binary
required.  The whole guest userland lives in the initramfs (~50MB), which
the kernel unpacks to a tmpfs root.

Usage (inside MetisRuntime as root):
  python3 build_initramfs.py <repo_guest_dir> <output_initrd_path>
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import gzip
from pathlib import Path

VSOCK_PORT = 5001
# Must match hcs_client.PLAN9_FIRST_PORT — first vsock port for 9p shares.
PLAN9_FIRST_PORT = 50001


def run(cmd: list) -> str:
    return subprocess.run(cmd, capture_output=True, text=True).stdout


def ldd_deps(binary: str) -> list[str]:
    """Return resolved shared-library paths a binary needs."""
    deps = []
    out = run(["ldd", binary])
    for line in out.splitlines():
        line = line.strip()
        # "libfoo.so => /path/libfoo.so (0x...)"
        if "=>" in line:
            rhs = line.split("=>", 1)[1].strip()
            path = rhs.split(" (", 1)[0].strip()
            if path and path.startswith("/") and os.path.exists(path):
                deps.append(path)
        else:
            # "/lib64/ld-linux-x86-64.so.2 (0x...)"  — the loader
            path = line.split(" (", 1)[0].strip()
            if path.startswith("/") and os.path.exists(path):
                deps.append(path)
    return deps


def copy_into(staging: Path, src: str) -> None:
    """Copy src (absolute path) into staging preserving its absolute path."""
    src_p = Path(src)
    dst = staging / src_p.relative_to("/")
    dst.parent.mkdir(parents=True, exist_ok=True)
    if not dst.exists():
        shutil.copy2(src, dst, follow_symlinks=True)


# Essential commands the sandbox guest should support.
ESSENTIAL_BINS = [
    "sh", "bash", "echo", "cat", "ls", "uname", "mkdir", "rm", "cp", "mv",
    "pwd", "env", "sleep", "head", "tail", "grep", "sed", "awk", "sort",
    "wc", "cut", "tr", "find", "chmod", "chown", "ln", "touch", "tar",
    "gzip", "date", "id", "whoami", "which", "dirname", "basename", "true",
    "false", "test", "expr", "mount", "df", "du", "ps", "kill",
    "git", "python3",
]


def copy_userland(staging: Path) -> None:
    """Copy essential binaries + their shared-lib deps into the initramfs."""
    copied = 0
    for name in ESSENTIAL_BINS:
        path = shutil.which(name)
        if not path:
            # try common absolute locations
            for cand in (f"/bin/{name}", f"/usr/bin/{name}", f"/usr/sbin/{name}", f"/sbin/{name}"):
                if os.path.exists(cand):
                    path = cand
                    break
        if not path:
            continue
        real = os.path.realpath(path)
        copy_into(staging, real)
        # preserve the original (possibly symlinked) name too
        if path != real:
            link = staging / Path(path).relative_to("/")
            link.parent.mkdir(parents=True, exist_ok=True)
            if not link.exists():
                try:
                    link.symlink_to(Path("/") / Path(real).relative_to("/"))
                except OSError:
                    shutil.copy2(real, link)
        for dep in ldd_deps(real):
            copy_into(staging, dep)
        copied += 1

    # /bin/sh must exist for subprocess shell=True
    sh_real = os.path.realpath(shutil.which("dash") or shutil.which("bash") or "/bin/sh")
    sh_link = staging / "bin/sh"
    sh_link.parent.mkdir(parents=True, exist_ok=True)
    if not sh_link.exists():
        try:
            sh_link.symlink_to(Path("/") / Path(sh_real).relative_to("/"))
        except OSError:
            shutil.copy2(sh_real, sh_link)

    # git needs its subcommand binaries in /usr/lib/git-core
    git_core = Path("/usr/lib/git-core")
    if git_core.is_dir():
        for tool in ("git", "git-remote-https", "git-remote-http"):
            t = git_core / tool
            if t.exists():
                real = os.path.realpath(t)
                copy_into(staging, real)
                for dep in ldd_deps(real):
                    copy_into(staging, dep)

    print(f"  userland: copied {copied} essential binaries")


def build_staging(guest_dir: Path, staging: Path) -> None:
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)

    # Base directory skeleton
    for d in ["bin", "usr/bin", "usr/local/bin", "lib", "lib64",
              "lib/x86_64-linux-gnu", "usr/lib/x86_64-linux-gnu",
              "proc", "sys", "dev", "tmp", "root",
              "mnt/workspace", "mnt/artifacts", "mnt/diagnostics"]:
        (staging / d).mkdir(parents=True, exist_ok=True)

    # Locate python
    python_bin = shutil.which("python3.10") or shutil.which("python3") or "/usr/bin/python3"
    python_real = os.path.realpath(python_bin)
    print(f"  python: {python_bin} -> {python_real}")

    # Copy python binary + its deps
    copy_into(staging, python_real)
    for dep in ldd_deps(python_real):
        copy_into(staging, dep)

    # Copy the stdlib tree
    pyver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    stdlib_src = f"/usr/lib/{pyver}"
    if not os.path.isdir(stdlib_src):
        # fall back to detected version of the build python
        import sysconfig
        stdlib_src = sysconfig.get_path("stdlib")
        pyver = Path(stdlib_src).name
    stdlib_dst = staging / "usr/lib" / pyver
    print(f"  stdlib: {stdlib_src} -> {stdlib_dst}")
    shutil.copytree(stdlib_src, stdlib_dst, symlinks=True, dirs_exist_ok=True)

    # lib-dynload .so files have their own deps (ssl, ctypes, etc.)
    dynload = Path(stdlib_src) / "lib-dynload"
    if dynload.is_dir():
        for so in dynload.glob("*.so"):
            for dep in ldd_deps(str(so)):
                copy_into(staging, dep)

    # Symlinks for convenience
    py_in_staging = staging / Path(python_real).relative_to("/")
    for alias in ["usr/bin/python3", "usr/bin/python"]:
        link = staging / alias
        if not link.exists():
            try:
                link.symlink_to(Path("/") / Path(python_real).relative_to("/"))
            except OSError:
                shutil.copy2(python_real, link)

    # Essential userland binaries so process.run can execute real commands.
    copy_userland(staging)

    # metisd agent
    metisd_src = guest_dir / "metisd.py"
    shutil.copy2(metisd_src, staging / "usr/local/bin/metisd")
    os.chmod(staging / "usr/local/bin/metisd", 0o755)

    # /init (PID 1)
    init_path = staging / "init"
    init_path.write_text(_INIT_SCRIPT.format(pyver=pyver, port=VSOCK_PORT, plan9_port=PLAN9_FIRST_PORT))
    os.chmod(init_path, 0o755)
    print(f"  /init written (pyver={pyver}, vsock port={VSOCK_PORT})")


_INIT_SCRIPT = '''#!/usr/bin/python3
# Metis VM init (PID 1) — mounts filesystems then starts metisd on vsock.
import ctypes, os, sys, time

libc = ctypes.CDLL("libc.so.6", use_errno=True)

def mount(source, target, fstype, flags=0, data=None):
    try:
        os.makedirs(target, exist_ok=True)
    except OSError:
        pass
    res = libc.mount(source.encode(), target.encode(), fstype.encode(),
                     ctypes.c_ulong(flags),
                     ctypes.c_char_p(data.encode() if data else None))
    if res != 0:
        e = ctypes.get_errno()
        sys.stderr.write("[init] mount %s -> %s (%s) failed: %s\\n" %
                         (source, target, fstype, os.strerror(e)))
        return False
    return True

sys.stderr.write("[init] Metis VM init starting\\n")
mount("proc", "/proc", "proc")
mount("sysfs", "/sys", "sysfs")
mount("devtmpfs", "/dev", "devtmpfs")
mount("tmpfs", "/tmp", "tmpfs")

# Workspace dirs are local (tmpfs).  Files are pushed/pulled by the host
# over the metisd vsock channel (fs.put / fs.get) — no Plan9/9p needed,
# which keeps us independent of the host GCS bridge.
for d in ("/workspace", "/artifacts", "/diagnostics", "/root"):
    try:
        os.makedirs(d, exist_ok=True)
    except OSError:
        pass

os.environ["PATH"] = "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
os.environ["HOME"] = "/root"
os.environ["METISD_MODE"] = "vsock"
os.environ["METISD_VSOCK_PORT"] = "{port}"

sys.stderr.write("[init] starting metisd on vsock port {port}\\n")
while True:
    pid = os.fork()
    if pid == 0:
        os.execv("/usr/bin/python3", ["/usr/bin/python3", "/usr/local/bin/metisd", "vsock"])
        os._exit(127)
    _, status = os.waitpid(pid, 0)
    sys.stderr.write("[init] metisd exited (status=%d), restarting in 2s\\n" % status)
    time.sleep(2)
'''


# ---------------------------------------------------------------------------
# newc cpio writer (pure python — no cpio binary needed)
# ---------------------------------------------------------------------------

def _field(n: int) -> bytes:
    return b"%08X" % (n & 0xFFFFFFFF)


def write_cpio_gz(staging: Path, output: Path) -> None:
    buf = bytearray()
    ino = [1]

    def emit(name: str, mode: int, data: bytes, nlink: int = 1) -> None:
        namebytes = name.encode() + b"\x00"
        hdr = b"070701"
        hdr += _field(ino[0]); ino[0] += 1
        hdr += _field(mode)
        hdr += _field(0)            # uid
        hdr += _field(0)            # gid
        hdr += _field(nlink)
        hdr += _field(0)            # mtime
        hdr += _field(len(data))
        hdr += _field(0) + _field(0)   # dev maj/min
        hdr += _field(0) + _field(0)   # rdev maj/min
        hdr += _field(len(namebytes))
        hdr += _field(0)            # check
        buf.extend(hdr)
        buf.extend(namebytes)
        while len(buf) % 4:
            buf.append(0)
        buf.extend(data)
        while len(buf) % 4:
            buf.append(0)

    # Walk staging, emit dirs first then contents (os.walk top-down)
    emit(".", stat.S_IFDIR | 0o755, b"", nlink=2)
    for root, dirs, files in os.walk(staging):
        rel_root = os.path.relpath(root, staging)
        for d in sorted(dirs):
            full = os.path.join(root, d)
            rel = os.path.normpath(os.path.join(rel_root, d))
            st = os.lstat(full)
            if stat.S_ISLNK(st.st_mode):
                target = os.readlink(full)
                emit(rel, stat.S_IFLNK | 0o777, target.encode())
            else:
                emit(rel, stat.S_IFDIR | (st.st_mode & 0o777), b"")
        for f in sorted(files):
            full = os.path.join(root, f)
            rel = os.path.normpath(os.path.join(rel_root, f))
            st = os.lstat(full)
            if stat.S_ISLNK(st.st_mode):
                target = os.readlink(full)
                emit(rel, stat.S_IFLNK | 0o777, target.encode())
            else:
                with open(full, "rb") as fh:
                    data = fh.read()
                emit(rel, stat.S_IFREG | (st.st_mode & 0o777), data)

    # Trailer
    emit("TRAILER!!!", 0, b"", nlink=1)

    print(f"  cpio size: {len(buf):,} bytes, gzipping...")
    with gzip.open(output, "wb", compresslevel=6) as gz:
        gz.write(buf)
    print(f"  initrd: {output} ({output.stat().st_size:,} bytes)")


def main() -> None:
    if len(sys.argv) < 3:
        print("usage: build_initramfs.py <repo_guest_dir> <output_initrd>")
        sys.exit(2)
    guest_dir = Path(sys.argv[1])
    output = Path(sys.argv[2])

    print("Building Metis initramfs (initramfs-only design)")
    staging = Path("/root/vmpack/irfs")
    build_staging(guest_dir, staging)
    output.parent.mkdir(parents=True, exist_ok=True)
    write_cpio_gz(staging, output)
    print("DONE")


if __name__ == "__main__":
    main()

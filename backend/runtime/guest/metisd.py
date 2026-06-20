#!/usr/bin/env python3
"""
metisd — Metis guest agent.

Runs inside the VM as a JSONL-over-stdio RPC server.
Handles workspace mounting, command execution, artifact collection,
and diagnostics export for the Metis runtime sandbox.

Zero external dependencies — stdlib only, so it can run on a
bare Alpine rootfs with just python3 installed.

Protocol: one JSON object per line on stdin, one response per line
on stdout.  Every request has {id, method, params}; every response
has {id, ok, ...}.  Unrecognised methods get a generic error reply.
"""
from __future__ import annotations

import fnmatch
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

PROTOCOL_VERSION = "metis.vm.guest.v1"
METISD_VERSION = "0.1.0"

# Paths set by session.mount
_workspace: Optional[Path] = None
_artifacts: Optional[Path] = None
_diagnostics: Optional[Path] = None

_run_log: List[Dict[str, Any]] = []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _reply(req_id: str, ok: bool = True, **fields: Any) -> Dict[str, Any]:
    return {"id": req_id, "ok": ok, **fields}


def _error(req_id: str, code: str, message: str) -> Dict[str, Any]:
    return {"id": req_id, "ok": False, "code": code, "error": message}


def _safe_path(base: Path, relative: str) -> Path:
    """Resolve a path ensuring it stays under base (prevent traversal)."""
    resolved = (base / relative).resolve()
    if not str(resolved).startswith(str(base.resolve())):
        raise ValueError(f"path traversal blocked: {relative}")
    return resolved


# ---------------------------------------------------------------------------
# Method handlers
# ---------------------------------------------------------------------------

def handle_session_mount(req_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    global _workspace, _artifacts, _diagnostics

    workspace = params.get("workspace", "")
    artifacts = params.get("artifacts", "")
    diagnostics = params.get("diagnostics", "")

    if workspace:
        _workspace = Path(workspace)
    if artifacts:
        _artifacts = Path(artifacts)
        _artifacts.mkdir(parents=True, exist_ok=True)
    if diagnostics:
        _diagnostics = Path(diagnostics)
        _diagnostics.mkdir(parents=True, exist_ok=True)

    return _reply(req_id,
        workspace=str(_workspace or ""),
        artifacts=str(_artifacts or ""),
        diagnostics=str(_diagnostics or ""),
    )


def handle_runtime_hello(req_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    client_protocol = params.get("protocol", "")
    return _reply(req_id,
        protocol=PROTOCOL_VERSION,
        version=METISD_VERSION,
        compatible=client_protocol == PROTOCOL_VERSION or client_protocol == "",
        pid=os.getpid(),
        hostname=os.uname().nodename if hasattr(os, "uname") else "unknown",
    )


def handle_process_run(req_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    command = params.get("command", "")
    if not command:
        return _error(req_id, "COMMAND_REQUIRED", "command is required")

    cwd = params.get("cwd", "")
    timeout_ms = params.get("timeout_ms", 120_000)
    timeout_s = max(1, timeout_ms // 1000)
    network_allowed = params.get("network_allowed", False)

    work_dir = cwd or str(_workspace or "/tmp")
    if not Path(work_dir).is_dir():
        work_dir = str(_workspace or "/tmp")

    env = dict(os.environ)
    env["METIS_RUNTIME"] = "1"
    env["METIS_RUNTIME_WORKSPACE"] = str(_workspace or "")
    env["METIS_RUNTIME_ARTIFACTS_DIR"] = str(_artifacts or "")
    env["METIS_RUNTIME_DIAGNOSTICS_DIR"] = str(_diagnostics or "")
    if not network_allowed:
        env["METIS_NETWORK_BLOCKED"] = "1"

    started = time.time()
    timed_out = False
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=work_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
        returncode = proc.returncode
        stdout = proc.stdout or ""
        stderr = proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = -1
        stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
    except Exception as exc:
        returncode = 127
        stdout = ""
        stderr = f"{type(exc).__name__}: {exc}"

    duration_ms = int((time.time() - started) * 1000)

    entry = {
        "command": command,
        "returncode": returncode,
        "timed_out": timed_out,
        "duration_ms": duration_ms,
        "stdout_len": len(stdout),
        "stderr_len": len(stderr),
    }
    _run_log.append(entry)

    # Truncate large output for the response
    max_chars = 60_000
    return _reply(req_id,
        returncode=returncode,
        timed_out=timed_out,
        duration_ms=duration_ms,
        stdout=stdout[:max_chars],
        stderr=stderr[:max_chars],
        stdout_truncated=len(stdout) > max_chars,
        stderr_truncated=len(stderr) > max_chars,
    )


def handle_artifact_collect(req_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if not _workspace:
        return _error(req_id, "NOT_MOUNTED", "workspace not mounted")
    if not _artifacts:
        return _error(req_id, "NOT_MOUNTED", "artifacts dir not set")

    patterns = params.get("patterns", ["*.py", "*.txt", "*.json", "*.md", "*.csv"])
    max_files = params.get("max_files", 200)
    max_bytes = params.get("max_bytes_per_file", 20 * 1024 * 1024)

    collected: List[Dict[str, Any]] = []
    count = 0

    for root_dir, dirs, files in os.walk(str(_workspace)):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "node_modules", ".metis"}]
        for fname in files:
            if count >= max_files:
                break
            if not any(fnmatch.fnmatch(fname, p) for p in patterns):
                continue
            src = Path(root_dir) / fname
            try:
                size = src.stat().st_size
            except OSError:
                continue
            if size > max_bytes or size == 0:
                continue
            rel = src.relative_to(_workspace)
            dst = _artifacts / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(str(src), str(dst))
                collected.append({
                    "path": str(rel),
                    "size": size,
                })
                count += 1
            except OSError:
                continue

    return _reply(req_id, collected=collected, count=len(collected))


def handle_artifact_list(req_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if not _artifacts:
        return _reply(req_id, artifacts=[], count=0)

    limit = params.get("limit", 200)
    artifacts: List[Dict[str, Any]] = []

    for root_dir, _dirs, files in os.walk(str(_artifacts)):
        for fname in files:
            if len(artifacts) >= limit:
                break
            fpath = Path(root_dir) / fname
            try:
                st = fpath.stat()
                artifacts.append({
                    "path": str(fpath.relative_to(_artifacts)),
                    "size": st.st_size,
                    "mtime": st.st_mtime,
                })
            except OSError:
                continue

    return _reply(req_id, artifacts=artifacts, count=len(artifacts))


def handle_diagnostics_export(req_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if not _diagnostics:
        return _reply(req_id, exported=False, reason="diagnostics dir not set")

    diag: Dict[str, Any] = {
        "protocol": PROTOCOL_VERSION,
        "version": METISD_VERSION,
        "pid": os.getpid(),
        "workspace": str(_workspace or ""),
        "artifacts": str(_artifacts or ""),
        "diagnostics": str(_diagnostics or ""),
        "run_log": _run_log[-50:],
        "env_keys": sorted(os.environ.keys()),
        "timestamp": time.time(),
    }

    diag_path = _diagnostics / "metisd_diagnostics.json"
    try:
        diag_path.write_text(json.dumps(diag, indent=2, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass

    return _reply(req_id, exported=True, path=str(diag_path), summary=diag)


def handle_fs_put(req_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Write a file into the guest from base64 content (host -> guest)."""
    import base64
    path = params.get("path", "")
    if not path:
        return _error(req_id, "PATH_REQUIRED", "path is required")
    try:
        data = base64.b64decode(params.get("content_b64", ""))
        os.makedirs(os.path.dirname(path) or "/", exist_ok=True)
        with open(path, "wb") as f:
            f.write(data)
        mode = params.get("mode")
        if mode:
            os.chmod(path, int(mode))
        return _reply(req_id, path=path, written=len(data))
    except Exception as exc:
        return _error(req_id, "FS_PUT_FAILED", f"{type(exc).__name__}: {exc}")


def handle_fs_get(req_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Read a file from the guest as base64 content (guest -> host)."""
    import base64
    path = params.get("path", "")
    if not path:
        return _error(req_id, "PATH_REQUIRED", "path is required")
    try:
        with open(path, "rb") as f:
            data = f.read()
        return _reply(req_id, path=path, size=len(data),
                      content_b64=base64.b64encode(data).decode("ascii"))
    except FileNotFoundError:
        return _error(req_id, "NOT_FOUND", f"file not found: {path}")
    except Exception as exc:
        return _error(req_id, "FS_GET_FAILED", f"{type(exc).__name__}: {exc}")


def handle_fs_list(req_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """List files under a root, optionally only those modified since a time."""
    root = params.get("root", "/workspace")
    since = float(params.get("since_mtime", 0) or 0)
    limit = int(params.get("limit", 5000))
    out: List[Dict[str, Any]] = []
    for r, dirs, files in os.walk(root):
        dirs[:] = [d for d in dirs if d not in {".git", "__pycache__", "node_modules", ".metis"}]
        for fn in files:
            if len(out) >= limit:
                break
            fp = os.path.join(r, fn)
            try:
                st = os.lstat(fp)
            except OSError:
                continue
            if st.st_mtime >= since:
                out.append({"path": os.path.relpath(fp, root),
                            "size": st.st_size, "mtime": st.st_mtime})
    return _reply(req_id, root=root, files=out, count=len(out))


def handle_net_configure(req_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Configure the guest NIC from host-supplied HCN endpoint info.

    Needs `ip` (iproute2) in the guest — present in the rich rootfs.
    params: {ip, prefix, gateway, dns:[...], iface}
    """
    ip = params.get("ip", "")
    prefix = int(params.get("prefix", 24) or 24)
    gateway = params.get("gateway", "")
    dns = params.get("dns") or []
    iface = params.get("iface", "eth0")
    if not ip:
        return _error(req_id, "IP_REQUIRED", "ip is required")

    steps = [
        ["ip", "link", "set", iface, "up"],
        ["ip", "addr", "add", f"{ip}/{prefix}", "dev", iface],
    ]
    if gateway:
        steps.append(["ip", "route", "replace", "default", "via", gateway, "dev", iface])
    results = []
    for cmd in steps:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            results.append({"cmd": " ".join(cmd), "rc": r.returncode, "err": r.stderr.strip()[:200]})
        except Exception as exc:
            results.append({"cmd": " ".join(cmd), "rc": -1, "err": str(exc)})
    if dns:
        try:
            with open("/etc/resolv.conf", "w") as f:
                for d in dns:
                    f.write(f"nameserver {d}\n")
        except OSError as exc:
            results.append({"cmd": "resolv.conf", "rc": -1, "err": str(exc)})

    ok = all(s.get("rc") == 0 for s in results if s["cmd"].startswith("ip "))
    return _reply(req_id, configured=ok, iface=iface, ip=ip, steps=results)


def handle_data_mount(req_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    """Mount the writable sessiondata disk to /data and point pip --user at it.

    The host attaches a per-session writable ext4 vhdx labelled METISDATA.
    We mount it by label (robust to SCSI lun ordering), create the pip user
    base, and export PYTHONUSERBASE so `pip install --user` and the user
    site-packages persist across runs. Needs `mount` in the guest (present in
    the rich rootfs); on the minimal initramfs this degrades to mounted=False.
    """
    import glob

    mountpoint = params.get("mountpoint", "/data")
    label = params.get("label", "METISDATA")
    pyuserbase = params.get("pythonuserbase", "/data/pyuser")
    steps: List[Dict[str, Any]] = []

    try:
        os.makedirs(mountpoint, exist_ok=True)
    except OSError as exc:
        return _reply(req_id, mounted=False, error=f"mkdir {mountpoint}: {exc}")

    def _try_mount(cmd: List[str]) -> bool:
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
            steps.append({"cmd": " ".join(cmd), "rc": r.returncode, "err": (r.stderr or "").strip()[:200]})
            return r.returncode == 0
        except Exception as exc:  # mount binary missing on minimal pack, etc.
            steps.append({"cmd": " ".join(cmd), "rc": -1, "err": str(exc)})
            return False

    mounted = os.path.ismount(mountpoint)
    if not mounted:
        # Preferred: mount by filesystem label.
        mounted = _try_mount(["mount", "-L", label, mountpoint])
    if not mounted:
        # Fallback: try each non-rootfs block device until one mounts.
        for dev in sorted(glob.glob("/dev/sd*")):
            if _try_mount(["mount", dev, mountpoint]):
                mounted = True
                break

    if mounted:
        try:
            os.makedirs(pyuserbase, exist_ok=True)
            # Inherited by handle_process_run, which copies os.environ.
            os.environ["PYTHONUSERBASE"] = pyuserbase
        except OSError as exc:
            steps.append({"cmd": "mkdir pyuserbase", "rc": -1, "err": str(exc)})

    return _reply(req_id,
        mounted=bool(mounted),
        mountpoint=mountpoint,
        pythonuserbase=pyuserbase if mounted else "",
        steps=steps,
    )


def handle_runtime_shutdown(req_id: str, params: Dict[str, Any]) -> Dict[str, Any]:
    return _reply(req_id, message="shutting down", _shutdown=True)


# ---------------------------------------------------------------------------
# Dispatch
# ---------------------------------------------------------------------------

HANDLERS = {
    "session.mount": handle_session_mount,
    "runtime.hello": handle_runtime_hello,
    "process.run": handle_process_run,
    "artifact.collect": handle_artifact_collect,
    "artifact.list": handle_artifact_list,
    "fs.put": handle_fs_put,
    "fs.get": handle_fs_get,
    "fs.list": handle_fs_list,
    "net.configure": handle_net_configure,
    "data.mount": handle_data_mount,
    "diagnostics.export": handle_diagnostics_export,
    "runtime.shutdown": handle_runtime_shutdown,
}


def dispatch(line: str) -> Optional[Dict[str, Any]]:
    try:
        msg = json.loads(line)
    except json.JSONDecodeError:
        return {"id": "", "ok": False, "code": "PARSE_ERROR", "error": "invalid JSON"}

    req_id = str(msg.get("id", ""))
    method = str(msg.get("method", ""))
    params = msg.get("params") or {}

    handler = HANDLERS.get(method)
    if not handler:
        return _error(req_id, "UNKNOWN_METHOD", f"unknown method: {method}")

    try:
        return handler(req_id, params)
    except Exception as exc:
        return _error(req_id, "INTERNAL_ERROR", f"{type(exc).__name__}: {exc}")


# ---------------------------------------------------------------------------
# Connection handling
# ---------------------------------------------------------------------------

def _serve_lines(read_line, write_line) -> None:
    """Process JSONL requests until shutdown. Generic over transport."""
    while True:
        line = read_line()
        if line is None:
            break
        line = line.strip()
        if not line:
            continue

        response = dispatch(line)
        shutdown = bool(response and response.pop("_shutdown", False))
        if response is not None:
            write_line(json.dumps(response, ensure_ascii=False) + "\n")

        if shutdown:
            break


def serve_stdio() -> None:
    """Serve the JSONL protocol over stdin/stdout."""
    def read_line():
        line = sys.stdin.readline()
        return line if line else None

    def write_line(text):
        sys.stdout.write(text)
        sys.stdout.flush()

    _serve_lines(read_line, write_line)


def serve_vsock(port: int) -> None:
    """Serve the JSONL protocol over an AF_VSOCK listening socket.

    This is the production transport inside the HCS VM: the host
    connects via HvSocket to the matching service GUID.
    """
    import socket as _socket

    VMADDR_CID_ANY = 0xFFFFFFFF
    sock = _socket.socket(_socket.AF_VSOCK, _socket.SOCK_STREAM)
    sock.setsockopt(_socket.SOL_SOCKET, _socket.SO_REUSEADDR, 1)
    sock.bind((VMADDR_CID_ANY, port))
    sock.listen(4)
    sys.stderr.write(f"[metisd] listening on vsock port {port}\n")
    sys.stderr.flush()

    while True:
        conn, _addr = sock.accept()
        try:
            buf = b""

            def read_line():
                nonlocal buf
                while b"\n" not in buf:
                    chunk = conn.recv(65536)
                    if not chunk:
                        if buf:
                            line, buf = buf, b""
                            return line.decode("utf-8", errors="replace")
                        return None
                    buf += chunk
                line, buf = buf.split(b"\n", 1)
                return line.decode("utf-8", errors="replace")

            def write_line(text):
                conn.sendall(text.encode("utf-8"))

            _serve_lines(read_line, write_line)
        except Exception as exc:
            sys.stderr.write(f"[metisd] connection error: {exc}\n")
        finally:
            try:
                conn.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

# Default vsock port for metisd. Host connects via HvSocket GUID
# "00001389-facb-11e6-bd58-64006a7986d3" (0x1389 == 5001).
DEFAULT_VSOCK_PORT = 5001


def main() -> None:
    mode = os.environ.get("METISD_MODE", "")
    if not mode and len(sys.argv) > 1:
        mode = sys.argv[1]

    if mode in ("vsock", "--vsock"):
        port = int(os.environ.get("METISD_VSOCK_PORT", DEFAULT_VSOCK_PORT))
        serve_vsock(port)
    else:
        serve_stdio()


if __name__ == "__main__":
    main()

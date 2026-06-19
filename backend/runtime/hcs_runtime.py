"""
HCS runtime backend for Metis isolated execution.

Boots a lightweight HCS VM via hcs_client, shares the workspace
through Plan 9 filesystem, and communicates with the metisd guest
agent via HvSocket (vsock).  Provides the same interface contract
as the local/WSL/Docker backends in isolated_runtime.py.

Communication modes (tried in order):
  1. HvSocket + metisd  — full JSONL protocol over vsock
  2. HcsCreateProcess   — direct guest process exec (needs GCS agent)
  3. Fallback           — raise so caller can fall back to local
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from backend.runtime.hcs_client import (
    ConsoleReader,
    HcsAccessDenied,
    HcsError,
    HcsNotAvailable,
    HcsVm,
    enumerate_compute_systems,
    find_any_bundle,
    find_metis_bundle,
    force_terminate_vm_by_id,
    is_hcs_available,
)

log = logging.getLogger("metis.hcs_runtime")

# Owners we recognise as Metis-managed VMs (for orphan reaping).
METIS_VM_OWNERS = {"Metis", "MetisPoC"}
# Cap simultaneous sandbox VMs to bound host RAM/CPU use.
MAX_CONCURRENT_SESSIONS = 4

# Guest dirs (no Plan 9; copy model via vsock). Match init_rich.sh and the
# initramfs init that create /workspace, /artifacts, /diagnostics directly.
GUEST_WORKSPACE = "/workspace"
GUEST_ARTIFACTS = "/artifacts"
GUEST_DIAGNOSTICS = "/diagnostics"

# metisd listens on AF_VSOCK port 5001 inside the guest.  HCS bridges
# a host AF_HYPERV connection to a guest vsock port via the Linux VSOCK
# template GUID: "<port-hex>-facb-11e6-bd58-64006a7986d3".
# 5001 == 0x1389 → 00001389-...
METISD_VSOCK_PORT = 5001
METISD_SERVICE_GUID = f"{METISD_VSOCK_PORT:08x}-facb-11e6-bd58-64006a7986d3"

_BOOT_TIMEOUT_MS = 60_000
_METISD_CONNECT_TIMEOUT_S = 15.0


# ---------------------------------------------------------------------------
# Session registry (in-process)
# ---------------------------------------------------------------------------

class HcsSession:
    """Tracks a running HCS VM tied to a runtime session."""

    def __init__(
        self,
        session_id: str,
        vm: HcsVm,
        workspace_dir: Path,
        artifacts_dir: Path,
        diagnostics_dir: Path,
    ):
        self.session_id = session_id
        self.vm = vm
        self.workspace_dir = workspace_dir
        self.artifacts_dir = artifacts_dir
        self.diagnostics_dir = diagnostics_dir
        self.boot_ms: int = 0
        self.console: Optional[ConsoleReader] = None
        self._exec_mode: str = ""  # "hvsocket", "hcs_process", or "unsupported"

    def detect_exec_mode(self, wait_s: float = 20.0) -> str:
        """Probe which execution mode the guest supports.

        metisd needs a few seconds to come up after boot (init + python
        startup), so we poll the HvSocket channel for up to `wait_s`.
        """
        if self._exec_mode:
            return self._exec_mode

        # Preferred path: HvSocket -> metisd. Poll until the guest agent
        # is listening (it binds vsock once /init finishes booting).
        deadline = time.time() + wait_s
        while time.time() < deadline:
            try:
                sock = self.vm.connect_hvsocket(METISD_SERVICE_GUID, timeout_s=2.0)
                sock.close()
                self._exec_mode = "hvsocket"
                log.info("Guest supports HvSocket (metisd)")
                return self._exec_mode
            except Exception:
                time.sleep(1.0)

        # Fallback: HcsCreateProcess (only works with a GCS-enabled guest).
        try:
            result = self.vm.exec_process(["echo", "probe"], timeout_ms=5000)
            if result.exit_code == 0:
                self._exec_mode = "hcs_process"
                log.info("Guest supports HcsCreateProcess")
                return self._exec_mode
        except HcsError:
            pass

        self._exec_mode = "unsupported"
        log.warning("Guest does not support HvSocket (metisd) or HcsCreateProcess — command execution unavailable")
        return self._exec_mode

    def destroy(self) -> None:
        try:
            self.vm.destroy()
        except Exception as exc:
            log.debug("HCS session destroy: %s", exc)
        if self.console:
            try:
                self.console.stop()
            except Exception:
                pass


_sessions: Dict[str, HcsSession] = {}


def _get_session(session_id: str) -> HcsSession:
    session = _sessions.get(session_id)
    if not session:
        raise HcsError("hcs_runtime", 0, f"no HCS session: {session_id}")
    return session


def cleanup_orphan_vms() -> Dict[str, Any]:
    """Reap Metis-owned compute systems that no live in-process session tracks.

    Handles VMs leaked by a previous crash (ShouldTerminateOnLastHandleClosed
    usually reaps them, but a hard kill can leave them running).
    """
    live_ids = {s.vm.vm_id for s in _sessions.values()}
    reaped: List[str] = []
    errors: List[str] = []
    try:
        systems = enumerate_compute_systems()
    except Exception as exc:
        return {"ok": False, "error": str(exc), "reaped": [], "errors": []}
    for vm in systems:
        owner = str(vm.get("Owner") or "")
        vid = str(vm.get("Id") or "")
        if owner in METIS_VM_OWNERS and vid and vid not in live_ids:
            try:
                force_terminate_vm_by_id(vid)
                reaped.append(vid)
            except Exception as exc:
                errors.append(f"{vid}: {exc}")
    if reaped:
        log.info("reaped %d orphan Metis VM(s)", len(reaped))
    return {"ok": not errors, "reaped": reaped, "errors": errors}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def hcs_runtime_available() -> Tuple[bool, str]:
    """Check if HCS runtime can be used."""
    available, reason = is_hcs_available()
    if not available:
        return False, reason
    bundle = find_any_bundle()
    if not bundle:
        return False, "no VM bundle found"
    return True, "ok"


def hcs_runtime_create_session(
    session_id: str,
    workspace_dir: Path,
    artifacts_dir: Path,
    diagnostics_dir: Path,
    *,
    bundle_path: str = "",
    memory_mb: int = 1024,
    processors: int = 2,
) -> Dict[str, Any]:
    """Boot an HCS VM and bind it to a runtime session."""
    # Reuse a live session if one already exists for this id.
    existing = _sessions.get(session_id)
    if existing is not None and existing.vm.state == "running":
        return {
            "ok": True, "session_id": session_id, "vm_id": existing.vm.vm_id,
            "boot_ms": existing.boot_ms, "reused": True,
            "exec_mode": existing.detect_exec_mode(), "bundle": str(existing.vm.bundle),
        }

    # Bound concurrent VMs; reap orphans first in case the cap is hit by leaks.
    if len(_sessions) >= MAX_CONCURRENT_SESSIONS:
        cleanup_orphan_vms()
        if len(_sessions) >= MAX_CONCURRENT_SESSIONS:
            return {
                "ok": False, "code": "MAX_SESSIONS",
                "error": f"too many concurrent sandbox VMs ({len(_sessions)}/{MAX_CONCURRENT_SESSIONS})",
            }

    bundle: Optional[Path] = None
    if bundle_path:
        bundle = Path(bundle_path)
    else:
        bundle = find_any_bundle()

    if not bundle:
        return {"ok": False, "error": "no VM bundle found", "code": "BUNDLE_NOT_FOUND"}

    # The compute system id is also the AF_HYPERV VmId for vsock, so it MUST
    # be a GUID. Derive a stable UUID from the session id.
    vm_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, f"metis-runtime.{session_id}"))

    # Always attach a drained serial console: an undrained ttyS0 can block the
    # guest /init before metisd starts, and this captures boot logs for free.
    console: Optional[ConsoleReader] = None
    console_pipe = ""
    try:
        console = ConsoleReader(log_path=diagnostics_dir / "vm_console.log")
        console_pipe = console.start()
    except Exception as exc:
        log.warning("console reader unavailable: %s", exc)
        console = None

    vm = HcsVm(
        bundle,
        vm_id=vm_id,
        memory_mb=memory_mb,
        processors=processors,
        console_pipe=console_pipe,
    )

    try:
        vm.create()
        boot_ms = vm.start(timeout_ms=_BOOT_TIMEOUT_MS)
    except HcsAccessDenied as exc:
        vm.destroy()
        if console:
            console.stop()
        return {
            "ok": False,
            "error": str(exc),
            "code": "HCS_ACCESS_DENIED",
            "hint": "Run as Administrator or add user to Hyper-V Administrators group",
        }
    except HcsError as exc:
        vm.destroy()
        if console:
            console.stop()
        return {"ok": False, "error": str(exc), "code": "HCS_ERROR"}

    session = HcsSession(
        session_id=session_id,
        vm=vm,
        workspace_dir=workspace_dir,
        artifacts_dir=artifacts_dir,
        diagnostics_dir=diagnostics_dir,
    )
    session.boot_ms = boot_ms
    session.console = console
    _sessions[session_id] = session

    # Detect what execution modes the guest supports
    exec_mode = session.detect_exec_mode()

    log.info("HCS session created: %s (VM %s, boot %dms, exec=%s)",
             session_id, vm_id, boot_ms, exec_mode)
    return {
        "ok": True,
        "session_id": session_id,
        "vm_id": vm_id,
        "boot_ms": boot_ms,
        "bundle": str(bundle),
        "exec_mode": exec_mode,
    }


def hcs_runtime_run(
    session_id: str,
    command: str,
    *,
    cwd: str = "",
    timeout: int = 120,
    env: Optional[Dict[str, str]] = None,
    network_allowed: bool = False,
) -> Dict[str, Any]:
    """Execute a command inside the HCS VM guest."""
    session = _get_session(session_id)
    exec_mode = session.detect_exec_mode()

    if exec_mode == "hvsocket":
        return _run_via_hvsocket(session, command, cwd=cwd, timeout=timeout,
                                 env=env, network_allowed=network_allowed)
    elif exec_mode == "hcs_process":
        return _run_via_hcs_process(session, command, cwd=cwd, timeout=timeout,
                                    env=env, network_allowed=network_allowed)
    else:
        return {
            "ok": False,
            "session_id": session_id,
            "error": "VM guest does not support command execution (no GCS agent or metisd)",
            "code": "EXEC_NOT_SUPPORTED",
            "returncode": 126,
            "stdout": "",
            "stderr": "VM booted but no execution channel available. Build a Metis rootfs with metisd for full support.",
            "timed_out": False,
            "duration_ms": 0,
            "backend": "hcs",
            "executed_command": command,
        }


def hcs_runtime_destroy(session_id: str) -> Dict[str, Any]:
    """Terminate the HCS VM and clean up."""
    session = _sessions.pop(session_id, None)
    if not session:
        return {"ok": True, "message": "no session to destroy"}
    session.destroy()
    log.info("HCS session destroyed: %s", session_id)
    return {"ok": True, "session_id": session_id}


def hcs_runtime_destroy_all() -> int:
    """Terminate all running HCS sessions."""
    ids = list(_sessions.keys())
    for sid in ids:
        hcs_runtime_destroy(sid)
    return len(ids)


# ---------------------------------------------------------------------------
# Execution backends
# ---------------------------------------------------------------------------

_SKIP_DIRS = {".git", "__pycache__", "node_modules", ".metis", ".pytest_cache", ".ruff_cache"}
_MAX_PUSH_FILE_BYTES = 16 * 1024 * 1024


def _send(session: HcsSession, messages: list, timeout_s: float) -> Dict[str, Any]:
    """Open one HvSocket connection, exchange JSONL, return responses by id."""
    sock = session.vm.connect_hvsocket(METISD_SERVICE_GUID, timeout_s=_METISD_CONNECT_TIMEOUT_S)
    try:
        responses = session.vm.send_jsonl(sock, messages, timeout_s=timeout_s)
    finally:
        try:
            sock.close()
        except Exception:
            pass
    return {str(r.get("id", "")): r for r in responses if isinstance(r, dict)}


def _run_via_hvsocket(
    session: HcsSession,
    command: str,
    *,
    cwd: str,
    timeout: int,
    env: Optional[Dict[str, str]],
    network_allowed: bool,
) -> Dict[str, Any]:
    """Run a command in the guest via metisd over HvSocket using the copy model:
    push workspace files in, run, then pull new/changed files back to artifacts.
    """
    import base64

    started = time.time()
    workspace = session.workspace_dir

    # 1) Push: mount + upload every workspace file, then run + list.
    pushed: set[str] = set()
    msgs: list = [
        {"id": "hello", "method": "runtime.hello", "params": {"protocol": "metis.vm.guest.v1"}},
        {"id": "mount", "method": "session.mount",
         "params": {"workspace": GUEST_WORKSPACE, "artifacts": GUEST_ARTIFACTS, "diagnostics": GUEST_DIAGNOSTICS}},
    ]
    try:
        if workspace and workspace.is_dir():
            for f in workspace.rglob("*"):
                if not f.is_file():
                    continue
                if any(part in _SKIP_DIRS for part in f.relative_to(workspace).parts):
                    continue
                try:
                    if f.stat().st_size > _MAX_PUSH_FILE_BYTES:
                        continue
                    data = f.read_bytes()
                except OSError:
                    continue
                rel = f.relative_to(workspace).as_posix()
                pushed.add(rel)
                msgs.append({"id": f"put:{rel}", "method": "fs.put",
                             "params": {"path": f"{GUEST_WORKSPACE}/{rel}",
                                        "content_b64": base64.b64encode(data).decode("ascii")}})
    except Exception:
        pass

    msgs.append({"id": "run", "method": "process.run",
                 "params": {"command": command, "cwd": cwd or GUEST_WORKSPACE,
                            "timeout_ms": max(1, timeout) * 1000,
                            "network_allowed": bool(network_allowed)}})
    msgs.append({"id": "list", "method": "fs.list", "params": {"root": GUEST_WORKSPACE}})

    try:
        by_id = _send(session, msgs, timeout_s=float(timeout + 60))
    except Exception as exc:
        return _hvsocket_error(session, command, started, exc)

    run_resp = by_id.get("run", {})
    list_resp = by_id.get("list", {})

    # 2) Pull: fetch files the guest created/changed (new relpaths) into artifacts.
    pulled = 0
    try:
        new_files = [item for item in (list_resp.get("files") or [])
                     if isinstance(item, dict) and item.get("path") not in pushed]
        if new_files:
            get_msgs = [{"id": f"get:{it['path']}", "method": "fs.get",
                         "params": {"path": f"{GUEST_WORKSPACE}/{it['path']}"}} for it in new_files[:500]]
            got = _send(session, get_msgs, timeout_s=120.0)
            for rid, resp in got.items():
                if not rid.startswith("get:") or not resp.get("ok"):
                    continue
                rel = rid[4:]
                content = resp.get("content_b64")
                if content is None:
                    continue
                dst = session.artifacts_dir / rel
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(base64.b64decode(content))
                pulled += 1
    except Exception as exc:
        log.warning("artifact pull failed: %s", exc)

    duration_ms = int((time.time() - started) * 1000)
    return {
        "ok": run_resp.get("ok", False),
        "session_id": session.session_id,
        "returncode": run_resp.get("returncode", -1),
        "stdout": str(run_resp.get("stdout", "")),
        "stderr": str(run_resp.get("stderr", "")),
        "timed_out": bool(run_resp.get("timed_out")),
        "duration_ms": duration_ms,
        "backend": "hcs",
        "executed_command": command,
        "files_pushed": len(pushed),
        "files_pulled": pulled,
    }


def _hvsocket_error(session: HcsSession, command: str, started: float, exc: Exception) -> Dict[str, Any]:
    return {
        "ok": False,
        "session_id": session.session_id,
        "error": f"HvSocket communication failed: {exc}",
        "code": "HVSOCKET_ERROR",
        "returncode": 126,
        "stdout": "",
        "stderr": str(exc),
        "timed_out": False,
        "duration_ms": int((time.time() - started) * 1000),
        "backend": "hcs",
        "executed_command": command,
    }


def _run_via_hcs_process(
    session: HcsSession,
    command: str,
    *,
    cwd: str,
    timeout: int,
    env: Optional[Dict[str, str]],
    network_allowed: bool,
) -> Dict[str, Any]:
    """Run a command via HcsCreateProcess (requires GCS agent in guest)."""
    started = time.time()

    guest_env: Dict[str, str] = {
        "METIS_RUNTIME": "1",
        "HOME": "/root",
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
    }
    if not network_allowed:
        guest_env["METIS_NETWORK_BLOCKED"] = "1"
    if env:
        guest_env.update(env)

    result = session.vm.exec_process(
        ["sh", "-c", command],
        working_dir=cwd or "/",
        env=guest_env,
        timeout_ms=max(5000, timeout * 1000 + 10_000),
    )

    duration_ms = int((time.time() - started) * 1000)

    return {
        "ok": result.exit_code == 0 and not result.timed_out,
        "session_id": session.session_id,
        "returncode": result.exit_code,
        "stdout": "",
        "stderr": "",
        "timed_out": result.timed_out,
        "duration_ms": duration_ms,
        "backend": "hcs",
        "executed_command": command,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _read_host_file(path: Path, max_bytes: int = 512_000) -> str:
    try:
        if not path.is_file():
            return ""
        data = path.read_bytes()[:max_bytes]
        return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


__all__ = [
    "hcs_runtime_available",
    "hcs_runtime_create_session",
    "hcs_runtime_run",
    "hcs_runtime_destroy",
    "hcs_runtime_destroy_all",
    "cleanup_orphan_vms",
    "HcsSession",
]

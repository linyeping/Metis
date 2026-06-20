"""
HCS (Host Compute Service) client for Metis VM sandbox.

Thin Python wrapper over computecore.dll that manages lightweight
Hyper-V Linux VMs via the HCS V2 API.  No Go binary, no Docker,
no WSL — just ctypes calls to the same DLL that Docker Desktop and
Claude Code Desktop use.

Requirements:
  - Windows 10/11 with Virtual Machine Platform enabled
  - User in Hyper-V Administrators group  (or running as admin)
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import json
import logging
import os
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("metis.hcs")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

INFINITE = 0xFFFFFFFF

# First vsock port for Plan9 9p shares (each share gets a sequential port).
PLAN9_FIRST_PORT = 50001

# Well-known HRESULTs
S_OK = 0
HCS_E_ACCESS_DENIED = 0x8037011B
HCS_E_HYPERV_NOT_INSTALLED = 0x80370102
HCS_E_SYSTEM_ALREADY_EXISTS = 0xC0370100
HCS_E_INVALID_STATE = 0xC0370105
HCS_E_SYSTEM_NOT_FOUND = 0xC0370109

HRESULT_NAMES = {
    HCS_E_ACCESS_DENIED: "HCS_E_ACCESS_DENIED",
    HCS_E_HYPERV_NOT_INSTALLED: "HCS_E_HYPERV_NOT_INSTALLED",
    HCS_E_SYSTEM_ALREADY_EXISTS: "HCS_E_SYSTEM_ALREADY_EXISTS",
    HCS_E_INVALID_STATE: "HCS_E_INVALID_STATE",
    HCS_E_SYSTEM_NOT_FOUND: "HCS_E_SYSTEM_NOT_FOUND",
}

# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HcsError(Exception):
    """An HCS API call returned a failing HRESULT."""

    def __init__(self, function: str, hr: int, detail: str = ""):
        self.hr = hr & 0xFFFFFFFF
        self.function = function
        self.detail = detail
        name = HRESULT_NAMES.get(self.hr, "")
        tag = f" ({name})" if name else ""
        super().__init__(f"{function} failed: 0x{self.hr:08X}{tag} {detail}".rstrip())


class HcsNotAvailable(HcsError):
    """computecore.dll could not be loaded — VM Platform likely not enabled."""
    pass


class HcsAccessDenied(HcsError):
    """Caller lacks Hyper-V Administrators membership or admin elevation."""
    pass


# ---------------------------------------------------------------------------
# DLL singleton
# ---------------------------------------------------------------------------

_hcs: Optional[ctypes.WinDLL] = None

HCS_OPERATION = ctypes.c_void_p
HCS_SYSTEM = ctypes.c_void_p
HCS_PROCESS = ctypes.c_void_p
HRESULT = ctypes.c_long  # raw c_long, not ctypes.HRESULT (which auto-raises OSError)
PCWSTR = ctypes.c_wchar_p


def _load_hcs() -> ctypes.WinDLL:
    global _hcs
    if _hcs is not None:
        return _hcs
    try:
        dll = ctypes.WinDLL("computecore")
    except OSError as exc:
        raise HcsNotAvailable("LoadLibrary", 0, f"computecore.dll not found: {exc}") from exc
    _declare_bindings(dll)
    _hcs = dll
    return dll


def _declare_bindings(dll: ctypes.WinDLL) -> None:
    # Operation lifecycle
    dll.HcsCreateOperation.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    dll.HcsCreateOperation.restype = HCS_OPERATION

    dll.HcsWaitForOperationResult.argtypes = [HCS_OPERATION, wintypes.DWORD, ctypes.POINTER(PCWSTR)]
    dll.HcsWaitForOperationResult.restype = HRESULT

    dll.HcsCloseOperation.argtypes = [HCS_OPERATION]
    dll.HcsCloseOperation.restype = None

    # Compute system lifecycle
    dll.HcsCreateComputeSystem.argtypes = [PCWSTR, PCWSTR, HCS_OPERATION, ctypes.c_void_p, ctypes.POINTER(HCS_SYSTEM)]
    dll.HcsCreateComputeSystem.restype = HRESULT

    dll.HcsOpenComputeSystem.argtypes = [PCWSTR, wintypes.DWORD, ctypes.POINTER(HCS_SYSTEM)]
    dll.HcsOpenComputeSystem.restype = HRESULT

    dll.HcsStartComputeSystem.argtypes = [HCS_SYSTEM, HCS_OPERATION, PCWSTR]
    dll.HcsStartComputeSystem.restype = HRESULT

    dll.HcsShutDownComputeSystem.argtypes = [HCS_SYSTEM, HCS_OPERATION, PCWSTR]
    dll.HcsShutDownComputeSystem.restype = HRESULT

    dll.HcsTerminateComputeSystem.argtypes = [HCS_SYSTEM, HCS_OPERATION, PCWSTR]
    dll.HcsTerminateComputeSystem.restype = HRESULT

    dll.HcsCloseComputeSystem.argtypes = [HCS_SYSTEM]
    dll.HcsCloseComputeSystem.restype = None

    dll.HcsGetComputeSystemProperties.argtypes = [HCS_SYSTEM, HCS_OPERATION, PCWSTR]
    dll.HcsGetComputeSystemProperties.restype = HRESULT

    # Enumeration
    dll.HcsEnumerateComputeSystems.argtypes = [PCWSTR, HCS_OPERATION]
    dll.HcsEnumerateComputeSystems.restype = HRESULT

    # Process in guest
    dll.HcsCreateProcess.argtypes = [HCS_SYSTEM, PCWSTR, HCS_OPERATION, ctypes.c_void_p, ctypes.POINTER(HCS_PROCESS)]
    dll.HcsCreateProcess.restype = HRESULT

    dll.HcsTerminateProcess.argtypes = [HCS_PROCESS, HCS_OPERATION, PCWSTR]
    dll.HcsTerminateProcess.restype = HRESULT

    dll.HcsGetProcessProperties.argtypes = [HCS_PROCESS, HCS_OPERATION, PCWSTR]
    dll.HcsGetProcessProperties.restype = HRESULT

    dll.HcsCloseProcess.argtypes = [HCS_PROCESS]
    dll.HcsCloseProcess.restype = None

    dll.HcsWaitForProcessExit.argtypes = [HCS_PROCESS, wintypes.DWORD, ctypes.POINTER(PCWSTR)]
    dll.HcsWaitForProcessExit.restype = HRESULT


# ---------------------------------------------------------------------------
# Low-level helpers
# ---------------------------------------------------------------------------

_OP = object()  # sentinel for operation-handle position


def _new_op() -> HCS_OPERATION:
    dll = _load_hcs()
    op = dll.HcsCreateOperation(None, None)
    if not op:
        raise HcsError("HcsCreateOperation", 0, "returned NULL")
    return op


def _wait_op(op: HCS_OPERATION, timeout_ms: int = INFINITE) -> Optional[str]:
    dll = _load_hcs()
    result_doc = PCWSTR()
    hr = dll.HcsWaitForOperationResult(op, timeout_ms, ctypes.byref(result_doc))
    text = result_doc.value if result_doc.value else None
    if hr != 0:
        if (hr & 0xFFFFFFFF) == HCS_E_ACCESS_DENIED:
            raise HcsAccessDenied("HcsWaitForOperationResult", hr, text or "")
        raise HcsError("HcsWaitForOperationResult", hr, text or "")
    return text


def _hcs_call(fn_name: str, args: list, timeout_ms: int = 30_000) -> Optional[str]:
    """Call an HCS function, inserting the operation handle where _OP appears."""
    dll = _load_hcs()
    op = _new_op()
    resolved = [op if a is _OP else a for a in args]
    try:
        fn = getattr(dll, fn_name)
        hr = fn(*resolved)
        if hr != 0:
            if (hr & 0xFFFFFFFF) == HCS_E_ACCESS_DENIED:
                raise HcsAccessDenied(fn_name, hr)
            raise HcsError(fn_name, hr)
        return _wait_op(op, timeout_ms)
    finally:
        dll.HcsCloseOperation(op)


# ---------------------------------------------------------------------------
# Compute document builder
# ---------------------------------------------------------------------------

def build_vm_document(
    bundle: Path,
    *,
    memory_mb: int = 1024,
    processors: int = 2,
    owner: str = "Metis",
    kernel_cmdline: str = "console=ttyS0 quiet",
    enable_hvsocket: bool = True,
    extra_vhds: Optional[Dict[str, str]] = None,
    plan9_shares: Optional[Dict[str, Tuple[str, bool]]] = None,
    console_pipe: str = "",
) -> Dict[str, Any]:
    """Build an HCS V2 compute document for a Linux direct-boot VM.

    If the bundle has no rootfs.vhdx, an initramfs-only VM is built
    (the initrd is the whole root filesystem).  `console_pipe` adds a
    COM1 → named-pipe serial console for boot debugging.
    """
    vmlinuz = bundle / "vmlinuz"
    initrd = bundle / "initrd"
    rootfs = bundle / "rootfs.vhdx"

    for f in (vmlinuz, initrd):
        if not f.is_file():
            raise FileNotFoundError(f"VM asset missing: {f}")

    attachments: Dict[str, Any] = {}
    slot = 0
    if rootfs.is_file():
        attachments["0"] = {"Type": "VirtualDisk", "Path": str(rootfs.resolve()), "ReadOnly": True}
        slot = 1
        # Auto-detect standard companion VHDs
        for name, read_only in [("sessiondata.vhdx", False), ("metis-data.vhdx", False), ("smol-bin.vhdx", True)]:
            vhd = bundle / name
            if vhd.is_file():
                attachments[str(slot)] = {"Type": "VirtualDisk", "Path": str(vhd.resolve()), "ReadOnly": read_only}
                slot += 1

    if extra_vhds:
        for path_str, mode in extra_vhds.items():
            attachments[str(slot)] = {
                "Type": "VirtualDisk",
                "Path": str(Path(path_str).resolve()),
                "ReadOnly": mode == "ro",
            }
            slot += 1

    devices: Dict[str, Any] = {}
    if attachments:
        devices["Scsi"] = {"primary": {"Attachments": attachments}}

    if console_pipe:
        devices["ComPorts"] = {"0": {"NamedPipe": console_pipe}}

    doc: Dict[str, Any] = {
        "Owner": owner,
        "SchemaVersion": {"Major": 2, "Minor": 1},
        "ShouldTerminateOnLastHandleClosed": True,
        "VirtualMachine": {
            "StopOnReset": True,
            "Chipset": {
                "LinuxKernelDirect": {
                    "KernelFilePath": str(vmlinuz.resolve()),
                    "InitRdPath": str(initrd.resolve()),
                    "KernelCmdLine": kernel_cmdline,
                },
            },
            "ComputeTopology": {
                "Memory": {"SizeInMB": memory_mb, "AllowOvercommit": True},
                "Processor": {"Count": processors},
            },
            "Devices": devices,
        },
    }

    if plan9_shares:
        # HCS Plan9 schema: Shares is an ARRAY; each share needs a vsock Port.
        # The host runs a 9p server on that port; the guest dials vsock
        # (CID=2) and mounts via trans=fd. Ports are assigned sequentially.
        shares_list: List[Dict[str, Any]] = []
        port = PLAN9_FIRST_PORT
        for tag, (host_path, read_only) in plan9_shares.items():
            # Flags: LinuxMetadata (0x4) | CaseSensitive (0x8) — required for
            # the 9p server to expose POSIX metadata to a Linux guest.
            flags = 0x4 | 0x8
            if read_only:
                flags |= 0x1  # ReadOnly flag
            shares_list.append({
                "Name": tag,
                "AccessName": tag,
                "Path": str(Path(host_path).resolve()),
                "Port": port,
                "Flags": flags,
                "ReadOnly": read_only,
            })
            port += 1
        doc["VirtualMachine"]["Devices"]["Plan9"] = {"Shares": shares_list}

    if enable_hvsocket:
        doc["VirtualMachine"]["Devices"]["HvSocket"] = {
            "HvSocketConfig": {
                "DefaultBindSecurityDescriptor": "D:P(A;;FA;;;WD)",
                "DefaultConnectSecurityDescriptor": "D:P(A;;FA;;;WD)",
            },
        }

    return doc


# ---------------------------------------------------------------------------
# Bundle discovery
# ---------------------------------------------------------------------------

_METIS_BUNDLE_NAME = "metisvm.bundle"

_METIS_BUNDLE_SEARCH_PATHS = [
    # Explicit dev / CI override always wins (point at vmpack_build in dev)
    lambda: Path(os.environ.get("METIS_VM_BUNDLE_PATH", "")) if os.environ.get("METIS_VM_BUNDLE_PATH") else None,
    # Installed by the Metis runtime pack (production)
    lambda: Path(os.environ.get("LOCALAPPDATA", "")) / "Metis" / "vm_bundles" / _METIS_BUNDLE_NAME,
]


def _is_bundle(p: Optional[Path]) -> bool:
    """A usable bundle has a kernel + an initrd (rootfs.vhdx is optional —
    initramfs-only bundles carry the whole userland in the initrd)."""
    if not p:
        return False
    try:
        return (p / "vmlinuz").is_file() and (p / "initrd").is_file()
    except Exception:
        return False


def find_metis_bundle() -> Optional[Path]:
    """Locate Metis's own VM bundle."""
    for resolver in _METIS_BUNDLE_SEARCH_PATHS:
        try:
            p = resolver()
            if _is_bundle(p):
                return p
        except Exception:
            continue
    return None


def find_any_bundle() -> Optional[Path]:
    """Find any usable VM bundle — Metis first, then Claude as dev fallback."""
    bundle = find_metis_bundle()
    if bundle:
        return bundle
    # Dev fallback: Claude Code Desktop bundle (never shipped to users)
    claude_paths = [
        Path(r"E:\ClaudeCode\cache\vm_bundles\claudevm.bundle"),
        Path.home() / "AppData/Local/Packages/Claude_pzs8sxrjxfjjc/LocalCache/Roaming/Claude/vm_bundles/claudevm.bundle",
    ]
    for p in claude_paths:
        if _is_bundle(p):
            return p
    return None


# ---------------------------------------------------------------------------
# Serial console capture (named pipe → drain thread)
# ---------------------------------------------------------------------------

class ConsoleReader:
    """Creates a Windows named pipe for the VM's COM1 and drains it.

    HCS connects to the pipe as a client; if nobody drains it the guest's
    writes to ttyS0 can block early boot.  This also captures boot logs.
    """

    _PIPE_ACCESS_DUPLEX = 0x3
    _ERROR_PIPE_CONNECTED = 535

    def __init__(self, name: str = "", log_path: Optional[Path] = None):
        import threading
        self.pipe_name = name or rf"\\.\pipe\metis-console-{uuid.uuid4().hex[:12]}"
        self.log_path = log_path
        self.buffer = bytearray()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._handle = None
        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32.CreateNamedPipeW.restype = wintypes.HANDLE
        self._k32.CreateNamedPipeW.argtypes = [
            wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
            wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p]
        self._k32.ConnectNamedPipe.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
        self._k32.ReadFile.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p]

    def start(self) -> str:
        import threading
        self._handle = self._k32.CreateNamedPipeW(
            self.pipe_name, self._PIPE_ACCESS_DUPLEX, 0, 1, 65536, 65536, 0, None)
        if self._handle == wintypes.HANDLE(-1).value:
            raise HcsError("CreateNamedPipe", 0, f"err={ctypes.get_last_error()}")
        self._thread = threading.Thread(target=self._drain, daemon=True)
        self._thread.start()
        return self.pipe_name

    def _drain(self) -> None:
        if not self._k32.ConnectNamedPipe(self._handle, None):
            err = ctypes.get_last_error()
            if err != self._ERROR_PIPE_CONNECTED:
                return
        buf = ctypes.create_string_buffer(8192)
        nread = wintypes.DWORD(0)
        while not self._stop.is_set():
            ok = self._k32.ReadFile(self._handle, buf, 8192, ctypes.byref(nread), None)
            if not ok or nread.value == 0:
                if ctypes.get_last_error() in (109, 233):
                    break
                time.sleep(0.05)
                continue
            self.buffer.extend(buf.raw[:nread.value])
            if self.log_path:
                try:
                    with open(self.log_path, "ab") as f:
                        f.write(buf.raw[:nread.value])
                except OSError:
                    pass

    def text(self) -> str:
        return self.buffer.decode("utf-8", errors="replace")

    def stop(self) -> None:
        self._stop.set()
        if self._handle is not None:
            try:
                ctypes.WinDLL("kernel32").CloseHandle(self._handle)
            except Exception:
                pass
            self._handle = None


# ---------------------------------------------------------------------------
# Process execution result
# ---------------------------------------------------------------------------

@dataclass
class GuestProcessResult:
    exit_code: int = -1
    timed_out: bool = False
    duration_ms: int = 0
    process_id: int = 0


# ---------------------------------------------------------------------------
# HcsVm — main class
# ---------------------------------------------------------------------------

class HcsVm:
    """Manages the lifecycle of a single HCS lightweight Linux VM."""

    def __init__(
        self,
        bundle: Path,
        *,
        vm_id: str = "",
        memory_mb: int = 1024,
        processors: int = 2,
        owner: str = "Metis",
        kernel_cmdline: str = "console=ttyS0 quiet",
        plan9_shares: Optional[Dict[str, Tuple[str, bool]]] = None,
        console_pipe: str = "",
    ):
        self.bundle = Path(bundle)
        self.vm_id = vm_id or str(uuid.uuid4())
        self.memory_mb = memory_mb
        self.processors = processors
        self.owner = owner
        self.kernel_cmdline = kernel_cmdline
        self.plan9_shares = plan9_shares
        self.console_pipe = console_pipe

        self._handle: Optional[HCS_SYSTEM] = None
        self._state: str = "idle"  # idle → created → running → stopped

    # -- Context manager --

    def __enter__(self) -> "HcsVm":
        self.create()
        self.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self.destroy()

    # -- Lifecycle --

    @property
    def state(self) -> str:
        return self._state

    @property
    def handle(self) -> HCS_SYSTEM:
        if self._handle is None:
            raise HcsError("HcsVm", 0, "VM not created yet")
        return self._handle

    def create(self) -> None:
        """Create the compute system (does not start it)."""
        if self._state != "idle":
            raise HcsError("HcsVm.create", 0, f"invalid state: {self._state}")

        dll = _load_hcs()
        doc = build_vm_document(
            self.bundle,
            memory_mb=self.memory_mb,
            processors=self.processors,
            owner=self.owner,
            kernel_cmdline=self.kernel_cmdline,
            plan9_shares=self.plan9_shares,
            console_pipe=self.console_pipe,
        )
        config_json = json.dumps(doc, ensure_ascii=False)

        system = HCS_SYSTEM()
        op = _new_op()
        try:
            hr = dll.HcsCreateComputeSystem(
                PCWSTR(self.vm_id),
                PCWSTR(config_json),
                op,
                None,
                ctypes.byref(system),
            )
            if hr != 0:
                if (hr & 0xFFFFFFFF) == HCS_E_ACCESS_DENIED:
                    raise HcsAccessDenied("HcsCreateComputeSystem", hr)
                raise HcsError("HcsCreateComputeSystem", hr)
            _wait_op(op, 30_000)
        finally:
            dll.HcsCloseOperation(op)

        self._handle = system
        self._state = "created"
        log.info("HCS VM created: %s", self.vm_id)

    def start(self, timeout_ms: int = 60_000) -> int:
        """Start the VM. Returns boot time in ms."""
        if self._state != "created":
            raise HcsError("HcsVm.start", 0, f"invalid state: {self._state}")

        t0 = time.perf_counter()
        _hcs_call("HcsStartComputeSystem", [self.handle, _OP, None], timeout_ms=timeout_ms)
        boot_ms = int((time.perf_counter() - t0) * 1000)

        self._state = "running"
        log.info("HCS VM started: %s (%dms)", self.vm_id, boot_ms)
        return boot_ms

    def shutdown(self, timeout_ms: int = 30_000) -> None:
        """Request graceful guest shutdown."""
        if self._state != "running":
            return
        try:
            _hcs_call("HcsShutDownComputeSystem", [self.handle, _OP, None], timeout_ms=timeout_ms)
            self._state = "stopped"
            log.info("HCS VM shut down: %s", self.vm_id)
        except HcsError as exc:
            if exc.hr == HCS_E_INVALID_STATE:
                self._state = "stopped"
            else:
                raise

    def terminate(self, timeout_ms: int = 10_000) -> None:
        """Force-kill the VM."""
        if self._handle is None:
            return
        if self._state not in ("running", "created"):
            return
        try:
            _hcs_call("HcsTerminateComputeSystem", [self.handle, _OP, None], timeout_ms=timeout_ms)
            log.info("HCS VM terminated: %s", self.vm_id)
        except HcsError as exc:
            if exc.hr != HCS_E_INVALID_STATE:
                log.warning("HCS terminate failed: %s", exc)
        self._state = "stopped"

    def close(self) -> None:
        """Release the VM handle."""
        if self._handle is not None:
            dll = _load_hcs()
            dll.HcsCloseComputeSystem(self._handle)
            self._handle = None
            self._state = "idle"
            log.debug("HCS VM handle closed: %s", self.vm_id)

    def destroy(self) -> None:
        """Terminate + close in one shot. Safe to call multiple times."""
        try:
            self.terminate()
        except Exception as exc:
            log.debug("terminate during destroy: %s", exc)
        self.close()

    # -- Properties --

    def properties(self, property_types: Optional[List[str]] = None) -> Dict[str, Any]:
        """Query VM runtime properties (state, memory, statistics)."""
        query = json.dumps({"PropertyTypes": property_types or ["Statistics", "Memory"]})
        result = _hcs_call(
            "HcsGetComputeSystemProperties",
            [self.handle, _OP, PCWSTR(query)],
            timeout_ms=5_000,
        )
        return json.loads(result) if result else {}

    # -- Guest execution --

    def exec_process(
        self,
        command: List[str],
        *,
        working_dir: str = "/",
        env: Optional[Dict[str, str]] = None,
        timeout_ms: int = 120_000,
        redirect_stdout: str = "",
        redirect_stderr: str = "",
    ) -> GuestProcessResult:
        """Run a process inside the guest VM and wait for it to exit.

        Uses HCS guest process execution — the guest OS must be booted
        and have an init that supports the HCS process launch protocol
        (Linux utility VM / GCS agent).
        """
        if self._state != "running":
            raise HcsError("exec_process", 0, f"VM not running (state={self._state})")

        dll = _load_hcs()

        params: Dict[str, Any] = {
            "CommandLine": command[0] if len(command) == 1 else " ".join(command),
            "WorkingDirectory": working_dir,
            "CreateStdInPipe": False,
            "CreateStdOutPipe": bool(redirect_stdout),
            "CreateStdErrPipe": bool(redirect_stderr),
        }
        if env:
            params["Environment"] = env
        if redirect_stdout:
            params["StdOutPath"] = redirect_stdout
        if redirect_stderr:
            params["StdErrPath"] = redirect_stderr

        params_json = json.dumps(params, ensure_ascii=False)
        process = HCS_PROCESS()
        op = _new_op()
        t0 = time.perf_counter()

        try:
            hr = dll.HcsCreateProcess(
                self.handle,
                PCWSTR(params_json),
                op,
                None,
                ctypes.byref(process),
            )
            if hr != 0:
                raise HcsError("HcsCreateProcess", hr)
            _wait_op(op, 30_000)
        finally:
            dll.HcsCloseOperation(op)

        result = GuestProcessResult()

        try:
            exit_doc = PCWSTR()
            hr = dll.HcsWaitForProcessExit(process, timeout_ms, ctypes.byref(exit_doc))
            result.duration_ms = int((time.perf_counter() - t0) * 1000)

            if hr != 0:
                result.timed_out = True
                try:
                    _hcs_call("HcsTerminateProcess", [process, _OP, None], timeout_ms=5_000)
                except HcsError:
                    pass
            elif exit_doc.value:
                info = json.loads(exit_doc.value)
                result.exit_code = info.get("ExitCode", -1)
                result.process_id = info.get("ProcessId", 0)
        finally:
            dll.HcsCloseProcess(process)

        return result

    # -- HvSocket (vsock) communication --

    def connect_hvsocket(self, service_id: str, timeout_s: float = 10.0) -> "socket.socket":
        """Connect to a service inside the guest via HvSocket (AF_HYPERV).

        service_id is a GUID like "00000001-facb-11e6-bd58-64006a7986d3".
        Returns a connected socket. Caller owns the socket and must close it.
        """
        import socket as _socket

        if self._state != "running":
            raise HcsError("connect_hvsocket", 0, f"VM not running (state={self._state})")

        sock = _socket.socket(_socket.AF_HYPERV, _socket.SOCK_STREAM, _socket.HV_PROTOCOL_RAW)
        sock.settimeout(timeout_s)
        sock.connect((self.vm_id, service_id))
        return sock

    def send_jsonl(self, sock: "socket.socket", messages: list, timeout_s: float = 120.0) -> list:
        """Send JSONL messages over an HvSocket and collect responses.

        Each message is a dict; one JSON line per message.
        Returns a list of parsed response dicts.
        """
        import socket as _socket

        payload = ""
        for msg in messages:
            payload += json.dumps(msg, ensure_ascii=False) + "\n"

        sock.settimeout(timeout_s)
        sock.sendall(payload.encode("utf-8"))
        sock.shutdown(_socket.SHUT_WR)

        chunks = []
        while True:
            try:
                chunk = sock.recv(65536)
                if not chunk:
                    break
                chunks.append(chunk)
            except _socket.timeout:
                break

        data = b"".join(chunks).decode("utf-8", errors="replace")
        responses = []
        for line in data.splitlines():
            line = line.strip()
            if line:
                try:
                    responses.append(json.loads(line))
                except json.JSONDecodeError:
                    responses.append({"raw": line, "ok": False})
        return responses

    # -- repr --

    def __repr__(self) -> str:
        return f"<HcsVm id={self.vm_id!r} state={self._state}>"


# ---------------------------------------------------------------------------
# Module-level utilities
# ---------------------------------------------------------------------------

def enumerate_compute_systems(query: str = "{}") -> List[Dict[str, Any]]:
    """List running HCS compute systems."""
    result = _hcs_call("HcsEnumerateComputeSystems", [PCWSTR(query), _OP], timeout_ms=5_000)
    return json.loads(result) if result else []


def force_terminate_vm_by_id(vm_id: str, timeout_ms: int = 10_000) -> bool:
    """Open an existing compute system by id and force-terminate it.

    Used to reap orphaned VMs that outlived their owning process. Returns
    True if the VM was terminated (or was already gone).
    """
    dll = _load_hcs()
    system = HCS_SYSTEM()
    GENERIC_ALL = 0x10000000
    hr = dll.HcsOpenComputeSystem(PCWSTR(vm_id), GENERIC_ALL, ctypes.byref(system))
    if hr != 0:
        # Not found / already gone is success for cleanup purposes.
        if (hr & 0xFFFFFFFF) == HCS_E_SYSTEM_NOT_FOUND:
            return True
        raise HcsError("HcsOpenComputeSystem", hr)
    try:
        _hcs_call("HcsTerminateComputeSystem", [system, _OP, None], timeout_ms=timeout_ms)
        return True
    except HcsError as exc:
        if exc.hr == HCS_E_INVALID_STATE:
            return True
        raise
    finally:
        dll.HcsCloseComputeSystem(system)


def is_hcs_available() -> Tuple[bool, str]:
    """Check whether HCS is usable. Returns (available, reason)."""
    try:
        _load_hcs()
    except HcsNotAvailable as exc:
        return False, f"computecore.dll not found — enable Virtual Machine Platform in Windows Features ({exc})"
    except Exception as exc:
        return False, str(exc)

    try:
        enumerate_compute_systems()
        return True, "ok"
    except HcsAccessDenied:
        return False, "access denied — run as Administrator or add user to Hyper-V Administrators group"
    except HcsError as exc:
        return False, str(exc)


def hcs_status_summary() -> Dict[str, Any]:
    """Return a status dict suitable for the Runtime Settings UI."""
    available, reason = is_hcs_available()
    bundle = find_metis_bundle()
    dev_bundle = find_any_bundle() if not bundle else bundle

    running_vms: List[Dict[str, str]] = []
    if available:
        try:
            for vm in enumerate_compute_systems():
                if vm.get("Owner") in ("Metis", "MetisPoC"):
                    running_vms.append({"id": vm.get("Id", ""), "owner": vm.get("Owner", "")})
        except Exception:
            pass

    return {
        "hcs_available": available,
        "hcs_reason": reason,
        "metis_bundle_found": bundle is not None,
        "metis_bundle_path": str(bundle) if bundle else "",
        "dev_bundle_found": dev_bundle is not None,
        "dev_bundle_path": str(dev_bundle) if dev_bundle else "",
        "running_metis_vms": running_vms,
    }


__all__ = [
    "HcsError",
    "HcsNotAvailable",
    "HcsAccessDenied",
    "HcsVm",
    "GuestProcessResult",
    "build_vm_document",
    "find_metis_bundle",
    "find_any_bundle",
    "enumerate_compute_systems",
    "force_terminate_vm_by_id",
    "is_hcs_available",
    "hcs_status_summary",
]

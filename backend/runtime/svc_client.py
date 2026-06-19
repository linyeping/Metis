r"""
Client for the Metis privileged VM service (metis-vm-svc).

The non-elevated Metis backend talks to the LocalSystem service over the
named pipe \\.\pipe\metis-vm-service using the JSONL RPC protocol. When the
service is installed + running, the app can use the HCS sandbox without any
per-call UAC elevation.

Pure ctypes (no pywin32 dependency).
"""
from __future__ import annotations

import ctypes
import json
import sys
from ctypes import wintypes
from typing import Any, Dict, List, Optional

PIPE_NAME = r"\\.\pipe\metis-vm-service"
PROTOCOL = "metis.vm.svc.v1"

_GENERIC_READ = 0x80000000
_GENERIC_WRITE = 0x40000000
_OPEN_EXISTING = 3
_ERROR_PIPE_BUSY = 231
_INVALID_HANDLE = wintypes.HANDLE(-1).value

if sys.platform == "win32":
    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _k32.CreateFileW.restype = wintypes.HANDLE
    _k32.CreateFileW.argtypes = [
        wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
        ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE,
    ]
    _k32.WaitNamedPipeW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD]
    _k32.WriteFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    _k32.ReadFile.argtypes = [
        wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
    ]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]
else:  # pragma: no cover
    _k32 = None


class _PipeConn:
    def __init__(self, handle: int):
        self._h = handle

    def write(self, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = wintypes.DWORD(0)
            ok = _k32.WriteFile(self._h, ctypes.c_char_p(bytes(view)), len(view), ctypes.byref(written), None)
            if not ok or written.value == 0:
                raise OSError(f"WriteFile failed: {ctypes.get_last_error()}")
            view = view[written.value:]

    def read_chunk(self, size: int = 65536) -> bytes:
        buf = ctypes.create_string_buffer(size)
        nread = wintypes.DWORD(0)
        ok = _k32.ReadFile(self._h, buf, size, ctypes.byref(nread), None)
        if not ok or nread.value == 0:
            return b""
        return buf.raw[: nread.value]

    def close(self) -> None:
        if self._h:
            _k32.CloseHandle(self._h)
            self._h = None


def _connect(timeout_ms: int = 2000) -> Optional[_PipeConn]:
    if _k32 is None:
        return None
    for _ in range(3):
        h = _k32.CreateFileW(PIPE_NAME, _GENERIC_READ | _GENERIC_WRITE, 0, None, _OPEN_EXISTING, 0, None)
        if h != _INVALID_HANDLE:
            return _PipeConn(h)
        if ctypes.get_last_error() == _ERROR_PIPE_BUSY:
            _k32.WaitNamedPipeW(PIPE_NAME, timeout_ms)
            continue
        return None
    return None


def _rpc(messages: List[Dict[str, Any]], read_budget: int = 4_000_000) -> List[Dict[str, Any]]:
    """Send each request line; read one response line per request."""
    conn = _connect()
    if conn is None:
        raise OSError("metis-vm-service pipe unavailable")
    try:
        payload = "".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages).encode("utf-8")
        conn.write(payload)
        resps: List[Dict[str, Any]] = []
        buf = b""
        total = 0
        while len(resps) < len(messages):
            chunk = conn.read_chunk()
            if not chunk:
                break
            total += len(chunk)
            if total > read_budget:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                try:
                    resps.append(json.loads(line.decode("utf-8", errors="replace")))
                except json.JSONDecodeError:
                    continue
        return resps
    finally:
        conn.close()


def service_available() -> bool:
    """True if the service is running and answers the handshake."""
    try:
        resps = _rpc([{"seq": 1, "method": "svc.hello", "params": {"protocol": PROTOCOL}}])
        return bool(resps and resps[0].get("ok"))
    except Exception:
        return False


def service_status() -> Dict[str, Any]:
    try:
        resps = _rpc([{"seq": 1, "method": "svc.status", "params": {}}])
        if resps and isinstance(resps[0].get("result"), dict):
            return {"ok": True, **resps[0]["result"]}
        return {"ok": False, "error": "no status"}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def run_job_via_service(params: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Run one job through the service. Returns the run_job result dict, or None."""
    try:
        resps = _rpc([{"seq": 1, "method": "vm.run_job", "params": params}])
    except Exception:
        return None
    for r in resps:
        if r.get("seq") == 1 and isinstance(r.get("result"), dict):
            return r["result"]
    return None


__all__ = ["service_available", "service_status", "run_job_via_service", "PIPE_NAME"]

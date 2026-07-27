"""Shared operating-system credential storage for Metis secrets.

The desktop app and the standalone CLI are separate processes, so Electron's
``safeStorage`` blob cannot be consumed by the CLI.  On Windows both surfaces
use the current user's Windows Credential Manager instead.  The functions in
this module deliberately return ``False``/``None`` when the platform store is
unavailable, but raise :class:`CredentialStoreError` for an actual Windows API
failure so callers can preserve a safe compatibility fallback.
"""

from __future__ import annotations

import ctypes
import os
from ctypes import wintypes
from functools import lru_cache
from typing import Any, Optional

LLM_API_KEY_TARGET = "Metis/LLM/API-Key"

_CRED_TYPE_GENERIC = 1
_CRED_PERSIST_LOCAL_MACHINE = 2
_ERROR_NOT_FOUND = 1168


class CredentialStoreError(OSError):
    """A Windows Credential Manager operation failed."""


class _FILETIME(ctypes.Structure):
    _fields_ = [
        ("dwLowDateTime", wintypes.DWORD),
        ("dwHighDateTime", wintypes.DWORD),
    ]


class _CREDENTIALW(ctypes.Structure):
    _fields_ = [
        ("Flags", wintypes.DWORD),
        ("Type", wintypes.DWORD),
        ("TargetName", wintypes.LPWSTR),
        ("Comment", wintypes.LPWSTR),
        ("LastWritten", _FILETIME),
        ("CredentialBlobSize", wintypes.DWORD),
        ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
        ("Persist", wintypes.DWORD),
        ("AttributeCount", wintypes.DWORD),
        ("Attributes", ctypes.c_void_p),
        ("TargetAlias", wintypes.LPWSTR),
        ("UserName", wintypes.LPWSTR),
    ]


@lru_cache(maxsize=1)
def _windows_api() -> Optional[Any]:
    if os.name != "nt":
        return None
    try:
        api = ctypes.WinDLL("Advapi32.dll", use_last_error=True)
    except (AttributeError, OSError):
        return None
    credential_pointer = ctypes.POINTER(_CREDENTIALW)
    api.CredWriteW.argtypes = [credential_pointer, wintypes.DWORD]
    api.CredWriteW.restype = wintypes.BOOL
    api.CredReadW.argtypes = [
        wintypes.LPCWSTR,
        wintypes.DWORD,
        wintypes.DWORD,
        ctypes.POINTER(credential_pointer),
    ]
    api.CredReadW.restype = wintypes.BOOL
    api.CredDeleteW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
    api.CredDeleteW.restype = wintypes.BOOL
    api.CredFree.argtypes = [ctypes.c_void_p]
    api.CredFree.restype = None
    return api


def is_available() -> bool:
    """Return whether Windows Credential Manager can be called."""
    return _windows_api() is not None


def read_api_key() -> Optional[str]:
    """Read the shared Metis LLM API key for the current Windows user."""
    api = _windows_api()
    if api is None:
        return None
    pointer = ctypes.POINTER(_CREDENTIALW)()
    if not api.CredReadW(LLM_API_KEY_TARGET, _CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
        error = ctypes.get_last_error()
        if error == _ERROR_NOT_FOUND:
            return None
        raise _operation_error("read", error)
    try:
        credential = pointer.contents
        if not credential.CredentialBlob or not credential.CredentialBlobSize:
            return ""
        raw = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        try:
            return raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise CredentialStoreError("stored Metis API key is not valid UTF-8") from exc
    finally:
        api.CredFree(pointer)


def write_api_key(api_key: str) -> bool:
    """Write ``api_key`` to Windows Credential Manager.

    ``False`` means the platform store is unavailable.  An empty key deletes
    the existing credential.
    """
    api = _windows_api()
    if api is None:
        return False
    value = str(api_key or "")
    if not value:
        delete_api_key()
        return True
    encoded = value.encode("utf-8")
    blob = (ctypes.c_ubyte * len(encoded)).from_buffer_copy(encoded)
    credential = _CREDENTIALW()
    credential.Type = _CRED_TYPE_GENERIC
    credential.TargetName = LLM_API_KEY_TARGET
    credential.CredentialBlobSize = len(encoded)
    credential.CredentialBlob = ctypes.cast(blob, ctypes.POINTER(ctypes.c_ubyte))
    credential.Persist = _CRED_PERSIST_LOCAL_MACHINE
    credential.UserName = "Metis"
    if not api.CredWriteW(ctypes.byref(credential), 0):
        raise _operation_error("write", ctypes.get_last_error())
    return True


def delete_api_key() -> bool:
    """Delete the shared API key; missing credentials count as success."""
    api = _windows_api()
    if api is None:
        return False
    if api.CredDeleteW(LLM_API_KEY_TARGET, _CRED_TYPE_GENERIC, 0):
        return True
    error = ctypes.get_last_error()
    if error == _ERROR_NOT_FOUND:
        return True
    raise _operation_error("delete", error)


def _operation_error(operation: str, error: int) -> CredentialStoreError:
    message = ctypes.FormatError(error).strip() if error else "unknown Windows error"
    return CredentialStoreError(error, f"could not {operation} Metis API key: {message}")

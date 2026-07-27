from __future__ import annotations

import ctypes

from backend.core import credential_store


class _FakeCredentialApi:
    def __init__(self) -> None:
        self.value = b""
        self._blob = None
        self._credential = None
        self.freed = False

    def CredWriteW(self, credential_pointer, _flags):
        credential = ctypes.cast(
            credential_pointer,
            ctypes.POINTER(credential_store._CREDENTIALW),
        ).contents
        self.value = ctypes.string_at(credential.CredentialBlob, credential.CredentialBlobSize)
        return 1

    def CredReadW(self, _target, _credential_type, _flags, output_pointer):
        if not self.value:
            return 0
        self._blob = (ctypes.c_ubyte * len(self.value)).from_buffer_copy(self.value)
        self._credential = credential_store._CREDENTIALW()
        self._credential.CredentialBlobSize = len(self.value)
        self._credential.CredentialBlob = ctypes.cast(
            self._blob,
            ctypes.POINTER(ctypes.c_ubyte),
        )
        pointer = ctypes.pointer(self._credential)
        ctypes.cast(
            output_pointer,
            ctypes.POINTER(ctypes.POINTER(credential_store._CREDENTIALW)),
        )[0] = pointer
        return 1

    def CredDeleteW(self, _target, _credential_type, _flags):
        self.value = b""
        return 1

    def CredFree(self, _pointer):
        self.freed = True


def test_windows_credential_round_trip_uses_utf8(monkeypatch) -> None:
    api = _FakeCredentialApi()
    monkeypatch.setattr(credential_store, "_windows_api", lambda: api)

    assert credential_store.write_api_key("sk-密钥") is True
    assert api.value == "sk-密钥".encode("utf-8")
    assert credential_store.read_api_key() == "sk-密钥"
    assert api.freed is True
    assert credential_store.delete_api_key() is True


def test_unavailable_credential_store_is_explicit_fallback(monkeypatch) -> None:
    monkeypatch.setattr(credential_store, "_windows_api", lambda: None)

    assert credential_store.is_available() is False
    assert credential_store.read_api_key() is None
    assert credential_store.write_api_key("sk-test") is False
    assert credential_store.delete_api_key() is False

from __future__ import annotations

import json
from typing import Any, Iterable

import pytest

from backend.runtime.llm_backends import openai_compat
from backend.runtime.llm_backends.openai_compat import OpenAICompatBackend


# --- 缺口1: atomic write retries transient Windows file locks ----------------

def test_replace_with_retry_recovers_from_transient_lock(monkeypatch):
    from backend.web import llm_state

    calls = {"n": 0}
    real_replace = llm_state.os.replace

    def flaky_replace(src, dst):
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError("transiently locked by antivirus")
        return real_replace(src, dst)

    monkeypatch.setattr(llm_state.os, "replace", flaky_replace)
    monkeypatch.setattr(llm_state, "time", __import__("time"))  # ensure real sleep stub ok

    import tempfile, os as _os

    d = tempfile.mkdtemp()
    src = _os.path.join(d, "src.tmp")
    dst = _os.path.join(d, "dst.json")
    with open(src, "w") as f:
        f.write("{}")

    llm_state._replace_with_retry(src, dst)
    assert calls["n"] == 3  # retried twice then succeeded
    assert _os.path.exists(dst)


def test_replace_with_retry_raises_after_persistent_lock(monkeypatch):
    from backend.web import llm_state

    def always_locked(src, dst):
        raise PermissionError("permanently locked")

    monkeypatch.setattr(llm_state.os, "replace", always_locked)
    with pytest.raises(PermissionError):
        llm_state._replace_with_retry("a", "b", attempts=3)


# --- 缺口2: half-streamed tool-call protection -------------------------------

class _FakeStream:
    status_code = 200
    encoding = "utf-8"

    def __init__(self, lines: Iterable[bytes]) -> None:
        self._lines = list(lines)

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self, decode_unicode: bool = False) -> Iterable[bytes]:
        return iter(self._lines)


def _tool_chunk(name: str, args: str, *, finish: str | None = None) -> bytes:
    payload = {
        "choices": [{
            "delta": {"tool_calls": [{"index": 0, "id": "c1", "function": {"name": name, "arguments": args}}]},
            "finish_reason": finish,
        }]
    }
    return f"data: {json.dumps(payload)}".encode("utf-8")


def _drain(stream):
    while True:
        try:
            next(stream)
        except StopIteration as stop:
            return stop.value


def _backend(monkeypatch, lines):
    monkeypatch.setattr(openai_compat, "post_with_retries", lambda *a, **k: _FakeStream(lines))
    return OpenAICompatBackend(base_url="https://relay.example/v1", api_key="k", model="gpt-5.5")


def test_complete_tool_call_args_are_kept(monkeypatch):
    backend = _backend(monkeypatch, [
        _tool_chunk("read_file", '{"path": "a.py"}', finish="tool_calls"),
        b"data: [DONE]",
    ])
    resp = _drain(backend.chat_stream([{"role": "user", "content": "x"}]))
    assert len(resp.tool_calls) == 1
    assert resp.tool_calls[0].name == "read_file"


def test_half_streamed_truncated_args_are_dropped(monkeypatch):
    # arguments JSON truncated mid-stream (connection broke) → must be dropped.
    backend = _backend(monkeypatch, [
        _tool_chunk("read_file", '{"path": "a.p'),  # no [DONE], truncated JSON
    ])
    resp = _drain(backend.chat_stream([{"role": "user", "content": "x"}]))
    assert resp.tool_calls == []


def test_empty_args_no_param_tool_is_kept(monkeypatch):
    backend = _backend(monkeypatch, [
        _tool_chunk("desktop_screenshot", "", finish="tool_calls"),
        b"data: [DONE]",
    ])
    resp = _drain(backend.chat_stream([{"role": "user", "content": "x"}]))
    assert len(resp.tool_calls) == 1


# --- 缺口3: atexit cleanup of background processes --------------------------

class _FakeProc:
    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        self.killed = True


def test_atexit_cleanup_terminates_registered_processes(monkeypatch):
    from backend.tools.coding.foundation.cli import manage_long_running as mlr

    proc = _FakeProc()
    mlr.background_processes.clear()
    mlr.background_processes[proc.pid] = {"process": proc, "name": "vite", "command": "npm run dev", "start_time": 0}
    # force the cross-platform fallback path (no real taskkill/killpg on fake pid)
    monkeypatch.setattr(mlr, "_terminate_process_tree", lambda p: p.terminate())

    mlr._cleanup_all_background_processes()

    assert proc.terminated is True
    assert mlr.background_processes == {}


def test_external_registered_process_skipped_on_cleanup():
    from backend.tools.coding.foundation.cli import manage_long_running as mlr

    mlr.background_processes.clear()
    mlr.background_processes[999] = {"process": None, "name": "ext", "command": "x", "start_time": 0}
    mlr._cleanup_all_background_processes()  # must not crash on process=None
    assert mlr.background_processes == {}

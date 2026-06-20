"""Tests for the metisd guest agent — JSONL protocol handling."""
from __future__ import annotations

import json

from backend.runtime.guest.metisd import dispatch, PROTOCOL_VERSION


def _dispatch(method: str, params: dict = None, req_id: str = "test") -> dict:
    msg = json.dumps({"id": req_id, "method": method, "params": params or {}})
    return dispatch(msg)


class TestProtocol:
    def test_unknown_method(self):
        r = _dispatch("nonexistent.method")
        assert r["ok"] is False
        assert r["code"] == "UNKNOWN_METHOD"

    def test_invalid_json(self):
        r = dispatch("not json at all")
        assert r["ok"] is False
        assert r["code"] == "PARSE_ERROR"


class TestSessionMount:
    def test_mount_sets_paths(self, tmp_path):
        ws = tmp_path / "ws"
        art = tmp_path / "art"
        diag = tmp_path / "diag"
        r = _dispatch("session.mount", {
            "workspace": str(ws),
            "artifacts": str(art),
            "diagnostics": str(diag),
        })
        assert r["ok"] is True
        assert r["workspace"] == str(ws)


class TestRuntimeHello:
    def test_hello_returns_version(self):
        r = _dispatch("runtime.hello", {"protocol": PROTOCOL_VERSION})
        assert r["ok"] is True
        assert r["protocol"] == PROTOCOL_VERSION
        assert r["compatible"] is True

    def test_hello_empty_protocol_is_compatible(self):
        r = _dispatch("runtime.hello", {"protocol": ""})
        assert r["ok"] is True
        assert r["compatible"] is True


class TestProcessRun:
    def test_echo_command(self, tmp_path):
        _dispatch("session.mount", {"workspace": str(tmp_path)})
        r = _dispatch("process.run", {
            "command": "echo hello-metis",
            "timeout_ms": 5000,
        })
        assert r["ok"] is True
        assert r["returncode"] == 0
        assert "hello-metis" in r["stdout"]

    def test_missing_command(self):
        r = _dispatch("process.run", {"command": ""})
        assert r["ok"] is False
        assert r["code"] == "COMMAND_REQUIRED"

    def test_failing_command(self, tmp_path):
        _dispatch("session.mount", {"workspace": str(tmp_path)})
        r = _dispatch("process.run", {
            "command": "exit 42",
            "timeout_ms": 5000,
        })
        assert r["returncode"] == 42


class TestArtifacts:
    def test_collect_and_list(self, tmp_path):
        ws = tmp_path / "ws"
        art = tmp_path / "art"
        ws.mkdir()
        art.mkdir()
        (ws / "output.txt").write_text("result")
        (ws / "data.csv").write_text("a,b\n1,2")

        _dispatch("session.mount", {
            "workspace": str(ws),
            "artifacts": str(art),
        })

        r = _dispatch("artifact.collect", {
            "patterns": ["*.txt", "*.csv"],
            "max_files": 10,
        })
        assert r["ok"] is True
        assert r["count"] == 2

        r2 = _dispatch("artifact.list", {"limit": 10})
        assert r2["ok"] is True
        assert r2["count"] == 2


class TestDiagnostics:
    def test_export(self, tmp_path):
        diag = tmp_path / "diag"
        diag.mkdir()
        _dispatch("session.mount", {"diagnostics": str(diag)})
        r = _dispatch("diagnostics.export")
        assert r["ok"] is True
        assert r["exported"] is True
        assert (diag / "metisd_diagnostics.json").is_file()


class TestShutdown:
    def test_shutdown_reply(self):
        r = _dispatch("runtime.shutdown")
        assert r["ok"] is True

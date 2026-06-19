"""Tests for runtime storage usage + retention GC (incl Windows read-only removal)."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from backend.runtime.isolated_runtime import (
    metis_runtime_storage_usage,
    metis_runtime_gc,
)


def _make_session(metis: Path, sid: str, size_bytes: int = 1024, read_only: bool = False) -> None:
    for kind in ("runtime", "artifacts", "diagnostics"):
        d = metis / kind / sid
        d.mkdir(parents=True, exist_ok=True)
        f = d / "data.bin"
        f.write_bytes(b"x" * size_bytes)
        if read_only:
            os.chmod(f, stat.S_IREAD)


def test_storage_usage_reports_sizes(tmp_path):
    metis = tmp_path / ".metis"
    _make_session(metis, "rt_1", 2048)
    _make_session(metis, "rt_2", 4096)
    data = json.loads(metis_runtime_storage_usage(root=str(tmp_path)))
    assert data["ok"] is True
    assert data["session_count"] == 2
    assert data["total_bytes"] >= 2048 + 4096


def test_gc_keep_recent_caps_sessions(tmp_path):
    metis = tmp_path / ".metis"
    import time
    for i in range(6):
        _make_session(metis, f"rt_{i}", 1024)
        # stagger mtimes so ordering is deterministic
        os.utime(metis / "runtime" / f"rt_{i}", (time.time() + i, time.time() + i))
    out = json.loads(metis_runtime_gc(root=str(tmp_path), keep_recent=3, max_age_days=3650))
    assert out["ok"] is True
    assert out["removed_session_count"] == 3
    remaining = [p.name for p in (metis / "runtime").iterdir() if p.is_dir()]
    assert len(remaining) == 3


def test_gc_removes_read_only_files(tmp_path):
    """Windows: read-only files (e.g. snapshotted .git) must still be removed."""
    metis = tmp_path / ".metis"
    _make_session(metis, "rt_ro", 1024, read_only=True)
    _make_session(metis, "rt_keep", 1024)
    out = json.loads(metis_runtime_gc(root=str(tmp_path), keep_recent=1, max_age_days=3650, aggressive=True))
    assert out["ok"] is True
    # aggressive removes everything; the read-only dir must be gone
    assert not (metis / "runtime" / "rt_ro").exists()
    assert not (metis / "artifacts" / "rt_ro").exists()


def test_gc_never_touches_wsl_dir(tmp_path):
    """Regression: GC must NOT delete .metis/runtime/wsl (managed WSL distro)."""
    metis = tmp_path / ".metis"
    _make_session(metis, "rt_old", 1024)
    # Simulate the managed WSL distro storage living under runtime/.
    wsl = metis / "runtime" / "wsl" / "MetisRuntime"
    wsl.mkdir(parents=True)
    (wsl / "ext4.vhdx").write_bytes(b"important user data")
    # Even aggressive GC must leave the wsl dir intact.
    out = json.loads(metis_runtime_gc(root=str(tmp_path), aggressive=True))
    assert out["ok"] is True
    assert (metis / "runtime" / "wsl" / "MetisRuntime" / "ext4.vhdx").exists()
    assert "wsl" not in out["removed_sessions"]


def test_gc_aggressive_clears_all(tmp_path):
    metis = tmp_path / ".metis"
    for i in range(4):
        _make_session(metis, f"rt_{i}", 1024)
    out = json.loads(metis_runtime_gc(root=str(tmp_path), aggressive=True))
    assert out["ok"] is True
    assert out["removed_session_count"] == 4
    assert not list((metis / "runtime").iterdir())

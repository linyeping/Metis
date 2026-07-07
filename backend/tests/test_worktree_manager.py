from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from backend.runtime import worktree_manager as wm


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise AssertionError(f"git {' '.join(args)} failed: {result.stderr or result.stdout}")
    return result.stdout


def _init_repo(path: Path) -> None:
    if not shutil.which("git"):
        pytest.skip("git is not available")
    path.mkdir()
    _git(path, "init")
    _git(path, "config", "user.email", "metis-test@example.local")
    _git(path, "config", "user.name", "Metis Test")
    (path / "app.txt").write_text("hello\n", encoding="utf-8")
    _git(path, "add", "app.txt")
    _git(path, "commit", "-m", "initial")


def test_worktree_manager_create_diff_promote_archive_remove(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.setenv("METIS_WORKTREE_BASE", str(tmp_path / "worktrees"))

    record = wm.create_worktree(str(repo), run_id="runabcdef1234", session_id="session-1", label="Code Run")

    assert record.status == "active"
    assert record.worktree_id.startswith("wt_")
    assert Path(record.worktree_path).is_dir()
    assert Path(record.worktree_workspace_root) == Path(record.worktree_path)
    assert not wm._is_within(Path(record.worktree_path), repo)

    (Path(record.worktree_path) / "app.txt").write_text("hello from worktree\n", encoding="utf-8")
    (Path(record.worktree_path) / "new-report.md").write_text("# New report\n", encoding="utf-8")
    diff = wm.diff_worktree(str(repo), record.worktree_id)

    assert diff["worktree"]["worktree_id"] == record.worktree_id
    assert "app.txt" in diff["stat"]
    assert "new-report.md" in diff["stat"]
    assert "hello from worktree" in diff["patch"]
    assert "new-report.md" in diff["patch"]

    dry_run = wm.promote_worktree(str(repo), record.worktree_id, dry_run=True)
    assert dry_run["ok"] is True
    assert dry_run["dry_run"] is True
    assert (repo / "app.txt").read_text(encoding="utf-8") == "hello\n"

    promoted = wm.promote_worktree(str(repo), record.worktree_id)
    assert promoted["ok"] is True
    assert (repo / "app.txt").read_text(encoding="utf-8") == "hello from worktree\n"
    assert (repo / "new-report.md").read_text(encoding="utf-8") == "# New report\n"

    archived = wm.archive_worktree(str(repo), record.worktree_id)
    assert archived.status == "archived"

    removed = wm.remove_worktree(str(repo), record.worktree_id, force=True)
    assert removed.status == "removed"
    assert not Path(record.worktree_path).exists()


def test_worktree_promote_review_selective_promote_and_rollback(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.setenv("METIS_WORKTREE_BASE", str(tmp_path / "worktrees"))

    record = wm.create_worktree(str(repo), run_id="runselective123", session_id="session-1", label="Code Run")
    worktree = Path(record.worktree_path)
    (worktree / "app.txt").write_text("hello changed\n", encoding="utf-8")
    (worktree / "new-report.md").write_text("# New report\n", encoding="utf-8")

    review = wm.review_worktree_promote(str(repo), record.worktree_id, paths=["new-report.md"])
    assert review["schema"] == wm.WORKTREE_PROMOTE_REVIEW_SCHEMA
    assert review["ok"] is True
    assert review["can_apply"] is True
    assert review["paths"] == ["new-report.md"]
    assert [item["path"] for item in review["files"]] == ["new-report.md"]
    assert "new-report.md" in review["patch"]
    assert "app.txt" not in review["patch"]

    promoted = wm.promote_worktree(str(repo), record.worktree_id, paths=["new-report.md"])
    assert promoted["ok"] is True
    assert promoted["rollback_available"] is True
    assert promoted["promotion_id"].startswith("promo_")
    assert (repo / "app.txt").read_text(encoding="utf-8") == "hello\n"
    assert (repo / "new-report.md").read_text(encoding="utf-8") == "# New report\n"

    rollback_check = wm.rollback_worktree_promotion(str(repo), record.worktree_id, dry_run=True)
    assert rollback_check["ok"] is True
    rolled_back = wm.rollback_worktree_promotion(str(repo), record.worktree_id)
    assert rolled_back["ok"] is True
    assert rolled_back["promotion_id"] == promoted["promotion_id"]
    assert not (repo / "new-report.md").exists()
    assert (repo / "app.txt").read_text(encoding="utf-8") == "hello\n"


def test_worktree_promote_review_explains_conflict(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repo"
    _init_repo(repo)
    monkeypatch.setenv("METIS_WORKTREE_BASE", str(tmp_path / "worktrees"))

    record = wm.create_worktree(str(repo), run_id="runconflict123", session_id="session-1", label="Code Run")
    worktree = Path(record.worktree_path)
    (worktree / "app.txt").write_text("hello from worktree\n", encoding="utf-8")
    (repo / "app.txt").write_text("hello from source\n", encoding="utf-8")

    review = wm.review_worktree_promote(str(repo), record.worktree_id, paths=["app.txt"])
    assert review["ok"] is False
    assert review["can_apply"] is False
    assert review["conflicts"]["ok"] is False
    assert review["conflicts"]["files"][0]["path"] == "app.txt"
    assert "source workspace already has changes" in review["conflicts"]["files"][0]["reason"]


def test_create_worktree_supports_non_git_snapshot_workspace(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "plain"
    workspace.mkdir()
    (workspace / "notes.txt").write_text("original\n", encoding="utf-8")
    monkeypatch.setenv("METIS_WORKTREE_BASE", str(tmp_path / "worktrees"))

    record = wm.create_worktree(str(workspace), run_id="run-1")
    worktree = Path(record.worktree_path)

    assert record.metadata["worktree_kind"] == "snapshot"
    assert worktree.is_dir()
    assert not wm._is_within(worktree, workspace)

    (worktree / "notes.txt").write_text("changed\n", encoding="utf-8")
    (worktree / "report.md").write_text("# Report\n", encoding="utf-8")
    diff = wm.diff_worktree(str(workspace), record.worktree_id)
    assert "notes.txt" in diff["status"]
    assert "report.md" in diff["status"]

    promoted = wm.promote_worktree(str(workspace), record.worktree_id)
    assert promoted["ok"] is True
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "changed\n"
    assert (workspace / "report.md").read_text(encoding="utf-8") == "# Report\n"

    rolled_back = wm.rollback_worktree_promotion(str(workspace), record.worktree_id)
    assert rolled_back["ok"] is True
    assert (workspace / "notes.txt").read_text(encoding="utf-8") == "original\n"
    assert not (workspace / "report.md").exists()

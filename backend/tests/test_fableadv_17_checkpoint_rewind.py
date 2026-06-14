from __future__ import annotations

from pathlib import Path

import pytest

from backend.runtime import checkpoints


@pytest.fixture()
def metis_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "metis-home"
    monkeypatch.setenv("METIS_HOME", str(home))
    from backend.core import paths as metis_paths

    metis_paths.clear_metis_home_cache()
    yield home
    metis_paths.clear_metis_home_cache()


def test_checkpoint_restores_file_to_before_snapshot(metis_home: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "app.txt"
    target.parent.mkdir(parents=True)
    target.write_text("before", encoding="utf-8")

    recorder = checkpoints.create_checkpoint(
        session_id="session-a",
        workspace_root=str(workspace),
        anchor_index=2,
        user_message_id="msg-user",
        history_snapshot=[{"role": "user", "content": "edit"}],
        compact_state_snapshot={"summary": "old"},
    )
    recorder.capture_path("src/app.txt")
    recorder.finalize("done")
    target.write_text("after", encoding="utf-8")

    restored = checkpoints.restore_files_from_checkpoint("session-a", recorder.checkpoint_id, str(workspace))

    assert target.read_text(encoding="utf-8") == "before"
    assert restored["restored"] == ["src/app.txt"]
    manifest = checkpoints.get_checkpoint("session-a", recorder.checkpoint_id)
    assert manifest is not None
    assert checkpoints.load_history_snapshot("session-a", manifest) == [{"role": "user", "content": "edit"}]
    assert checkpoints.load_compact_state_snapshot("session-a", manifest) == {"summary": "old"}


def test_checkpoint_rewind_deletes_file_created_after_anchor(metis_home: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    recorder = checkpoints.create_checkpoint(
        session_id="session-b",
        workspace_root=str(workspace),
        anchor_index=0,
        user_message_id="msg-user",
    )
    recorder.capture_path("generated.txt")
    recorder.finalize("done")

    created = workspace / "generated.txt"
    created.write_text("new", encoding="utf-8")

    restored = checkpoints.restore_files_from_checkpoint("session-b", recorder.checkpoint_id, str(workspace))

    assert not created.exists()
    assert restored["restored"] == ["generated.txt"]


def test_checkpoint_captures_apply_patch_paths(metis_home: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "src" / "patched.txt"
    target.parent.mkdir(parents=True)
    target.write_text("before", encoding="utf-8")

    recorder = checkpoints.create_checkpoint(
        session_id="session-patch",
        workspace_root=str(workspace),
        anchor_index=0,
        user_message_id="msg-user",
    )
    recorder.capture_tool_call(
        "apply_patch",
        {
            "patch_text": "*** Begin Patch\n*** Update File: src/patched.txt\n@@\n-before\n+after\n*** End Patch\n",
        },
    )
    recorder.finalize("done")
    target.write_text("after", encoding="utf-8")

    restored = checkpoints.restore_files_from_checkpoint("session-patch", recorder.checkpoint_id, str(workspace))

    assert target.read_text(encoding="utf-8") == "before"
    assert restored["restored"] == ["src/patched.txt"]


def test_checkpoint_captures_files_under_deleted_directory(metis_home: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    target = workspace / "folder" / "child.txt"
    target.parent.mkdir(parents=True)
    target.write_text("before", encoding="utf-8")

    recorder = checkpoints.create_checkpoint(
        session_id="session-dir",
        workspace_root=str(workspace),
        anchor_index=0,
        user_message_id="msg-user",
    )
    recorder.capture_tool_call("delete_directory", {"path": "folder"})
    recorder.finalize("done")
    target.unlink()
    target.parent.rmdir()

    restored = checkpoints.restore_files_from_checkpoint("session-dir", recorder.checkpoint_id, str(workspace))

    assert target.read_text(encoding="utf-8") == "before"
    assert restored["restored"] == ["folder/child.txt"]


def test_rewind_safety_checkpoint_can_delay_pruning(metis_home: Path, tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    for index in range(checkpoints.MAX_CHECKPOINTS_PER_SESSION):
        recorder = checkpoints.create_checkpoint(
            session_id="session-c",
            workspace_root=str(workspace),
            anchor_index=index,
            user_message_id=f"msg-{index}",
        )
        recorder.finalize("done")

    first = checkpoints.list_checkpoints("session-c")[0]
    safety = checkpoints.create_checkpoint(
        session_id="session-c",
        workspace_root=str(workspace),
        anchor_index=99,
        reason="rewind_safety",
        prune=False,
    )
    safety.finalize("rewind_safety", prune=False)

    assert checkpoints.get_checkpoint("session-c", str(first["checkpoint_id"])) is not None
    assert len(checkpoints.list_checkpoints("session-c")) == checkpoints.MAX_CHECKPOINTS_PER_SESSION + 1

    checkpoints.prune_checkpoints("session-c")
    assert len(checkpoints.list_checkpoints("session-c")) == checkpoints.MAX_CHECKPOINTS_PER_SESSION

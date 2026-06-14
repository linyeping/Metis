from __future__ import annotations

import base64
import json
import os
import shutil
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from backend.core.paths import metis_dir

MAX_CHECKPOINTS_PER_SESSION = 20
MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024
MAX_DIRECTORY_SNAPSHOT_FILES = 200

WRITE_LIKE_TOOLS = {
    "append_to_file",
    "apply_patch",
    "create_file",
    "delete_file",
    "delete_directory",
    "editCode",
    "edit_code_ast",
    "rename_file_update_refs",
    "robust_replace_in_file",
    "write_file",
}


def checkpoints_root() -> Path:
    return metis_dir("checkpoints")


def session_checkpoint_dir(session_id: str) -> Path:
    safe = _slug(session_id or "session")
    path = checkpoints_root() / safe
    path.mkdir(parents=True, exist_ok=True)
    return path


def create_checkpoint(
    *,
    session_id: str,
    workspace_root: str,
    anchor_index: int,
    user_message_id: str = "",
    reason: str = "user_turn",
    history_snapshot: Optional[List[Dict[str, Any]]] = None,
    compact_state_snapshot: Optional[Dict[str, Any]] = None,
    prune: bool = True,
) -> "CheckpointRecorder":
    recorder = CheckpointRecorder(
        session_id=session_id,
        workspace_root=workspace_root,
        anchor_index=anchor_index,
        user_message_id=user_message_id,
        reason=reason,
        history_snapshot=history_snapshot,
        compact_state_snapshot=compact_state_snapshot,
    )
    recorder.write_manifest()
    if prune:
        prune_checkpoints(session_id)
    return recorder


def list_checkpoints(session_id: str) -> List[Dict[str, Any]]:
    items: List[Dict[str, Any]] = []
    root = session_checkpoint_dir(session_id)
    for manifest_path in root.glob("*/manifest.json"):
        manifest = _read_json(manifest_path)
        if not manifest:
            continue
        manifest["checkpoint_id"] = str(manifest.get("checkpoint_id") or manifest_path.parent.name)
        manifest["file_count"] = len(manifest.get("files") or [])
        items.append(manifest)
    items.sort(key=lambda item: float(item.get("created_at") or 0))
    return items


def get_checkpoint(session_id: str, checkpoint_id: str) -> Optional[Dict[str, Any]]:
    safe = _slug(checkpoint_id or "")
    if not safe:
        return None
    manifest = _read_json(session_checkpoint_dir(session_id) / safe / "manifest.json")
    if not manifest:
        return None
    manifest["checkpoint_id"] = str(manifest.get("checkpoint_id") or safe)
    manifest["file_count"] = len(manifest.get("files") or [])
    return manifest


def find_checkpoint(
    session_id: str,
    *,
    checkpoint_id: str = "",
    user_message_id: str = "",
    anchor_index: Optional[int] = None,
) -> Optional[Dict[str, Any]]:
    if checkpoint_id:
        return get_checkpoint(session_id, checkpoint_id)
    checkpoints = list_checkpoints(session_id)
    if user_message_id:
        for item in reversed(checkpoints):
            if str(item.get("user_message_id") or "") == user_message_id:
                return item
    if anchor_index is not None:
        try:
            wanted = int(anchor_index)
        except (TypeError, ValueError):
            wanted = -1
        for item in reversed(checkpoints):
            if int(item.get("anchor_index") or -1) == wanted:
                return item
    return checkpoints[-1] if checkpoints else None


def restore_files_from_checkpoint(session_id: str, checkpoint_id: str, workspace_root: str = "") -> Dict[str, Any]:
    checkpoints = list_checkpoints(session_id)
    start = next((index for index, item in enumerate(checkpoints) if item.get("checkpoint_id") == checkpoint_id), -1)
    if start < 0:
        raise ValueError("checkpoint not found")

    restore_entries: Dict[str, Dict[str, Any]] = {}
    for manifest in checkpoints[start:]:
        for entry in manifest.get("files") or []:
            if not isinstance(entry, dict):
                continue
            rel_path = str(entry.get("relative_path") or "")
            if rel_path and rel_path not in restore_entries:
                restore_entries[rel_path] = {**entry, "_checkpoint_id": manifest.get("checkpoint_id")}

    root = _workspace_root(workspace_root or str(checkpoints[start].get("workspace_root") or ""))
    restored: List[str] = []
    skipped: List[Dict[str, str]] = []
    for rel_path, entry in restore_entries.items():
        target = _safe_target(root, rel_path)
        if target is None:
            skipped.append({"path": rel_path, "reason": "outside workspace"})
            continue
        if bool(entry.get("existed")):
            snapshot_rel = str(entry.get("snapshot") or "")
            snapshot_path = session_checkpoint_dir(session_id) / str(entry.get("_checkpoint_id") or "") / snapshot_rel
            if not snapshot_path.is_file():
                skipped.append({"path": rel_path, "reason": "snapshot missing"})
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(snapshot_path, target)
        else:
            if target.is_file():
                target.unlink()
        restored.append(rel_path)
    return {"restored": restored, "skipped": skipped}


def prune_checkpoints(session_id: str, keep: int = MAX_CHECKPOINTS_PER_SESSION) -> None:
    checkpoints = list_checkpoints(session_id)
    extra = len(checkpoints) - keep
    if extra <= 0:
        return
    root = session_checkpoint_dir(session_id)
    for item in checkpoints[:extra]:
        path = root / _slug(str(item.get("checkpoint_id") or ""))
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)


class CheckpointRecorder:
    def __init__(
        self,
        *,
        session_id: str,
        workspace_root: str,
        anchor_index: int,
        user_message_id: str = "",
        reason: str = "user_turn",
        history_snapshot: Optional[List[Dict[str, Any]]] = None,
        compact_state_snapshot: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.session_id = str(session_id or "")
        self.workspace_root = str(_workspace_root(workspace_root))
        self.checkpoint_id = f"ckpt_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        self.anchor_index = max(0, int(anchor_index or 0))
        self.user_message_id = str(user_message_id or "")
        self.reason = str(reason or "user_turn")
        self.created_at = time.time()
        self.completed_at = 0.0
        self.status = "open"
        self.files: Dict[str, Dict[str, Any]] = {}
        self.history_snapshot = history_snapshot
        self.compact_state_snapshot = compact_state_snapshot if compact_state_snapshot is not None else None
        self.path = session_checkpoint_dir(self.session_id) / self.checkpoint_id
        self.files_dir = self.path / "files"
        self.files_dir.mkdir(parents=True, exist_ok=True)

    def capture_tool_call(self, tool_name: str, arguments: Dict[str, Any]) -> None:
        canonical = str(tool_name or "").strip()
        if canonical not in WRITE_LIKE_TOOLS:
            return
        for rel_path in _tool_paths(canonical, arguments):
            self.capture_path(rel_path)

    def capture_path(self, path: str) -> None:
        rel_path, target = _relative_target(self.workspace_root, path)
        if not rel_path or target is None or rel_path in self.files:
            return
        if target.is_dir():
            captured = 0
            for child in sorted(target.rglob("*")):
                if captured >= MAX_DIRECTORY_SNAPSHOT_FILES:
                    break
                if child.is_file():
                    self.capture_path(str(child))
                    captured += 1
            return
        entry: Dict[str, Any] = {
            "relative_path": rel_path,
            "absolute_path": str(target),
            "existed": target.is_file(),
            "snapshot": "",
            "size": 0,
            "skipped": "",
        }
        if target.is_file():
            try:
                size = target.stat().st_size
                entry["size"] = int(size)
                if size <= MAX_SNAPSHOT_BYTES:
                    snapshot_name = f"{_path_token(rel_path)}.bin"
                    snapshot_path = self.files_dir / snapshot_name
                    snapshot_path.write_bytes(target.read_bytes())
                    entry["snapshot"] = f"files/{snapshot_name}"
                else:
                    entry["skipped"] = "file too large"
            except OSError as exc:
                entry["skipped"] = str(exc)[:240]
        self.files[rel_path] = entry
        self.write_manifest()

    def finalize(self, status: str = "done", *, prune: bool = True) -> None:
        self.status = status or "done"
        self.completed_at = time.time()
        self.write_manifest()
        if prune:
            prune_checkpoints(self.session_id)

    def write_manifest(self) -> None:
        self.path.mkdir(parents=True, exist_ok=True)
        payload: Dict[str, Any] = {
            "version": 1,
            "checkpoint_id": self.checkpoint_id,
            "session_id": self.session_id,
            "workspace_root": self.workspace_root,
            "anchor_index": self.anchor_index,
            "user_message_id": self.user_message_id,
            "reason": self.reason,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "status": self.status,
            "files": list(self.files.values()),
        }
        if self.history_snapshot is not None:
            history_path = self.path / "history.json"
            history_path.write_text(json.dumps(self.history_snapshot, ensure_ascii=False), encoding="utf-8")
            payload["history_snapshot"] = "history.json"
        if self.compact_state_snapshot is not None:
            compact_path = self.path / "compact_state.json"
            compact_path.write_text(json.dumps(self.compact_state_snapshot, ensure_ascii=False), encoding="utf-8")
            payload["compact_state_snapshot"] = "compact_state.json"
        (self.path / "manifest.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True),
            encoding="utf-8",
        )


def load_history_snapshot(session_id: str, checkpoint: Dict[str, Any]) -> Optional[List[Dict[str, Any]]]:
    snapshot = str(checkpoint.get("history_snapshot") or "")
    if not snapshot:
        return None
    path = session_checkpoint_dir(session_id) / str(checkpoint.get("checkpoint_id") or "") / snapshot
    data = _read_json(path)
    return data if isinstance(data, list) else None


def load_compact_state_snapshot(session_id: str, checkpoint: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    snapshot = str(checkpoint.get("compact_state_snapshot") or "")
    if not snapshot:
        return None
    path = session_checkpoint_dir(session_id) / str(checkpoint.get("checkpoint_id") or "") / snapshot
    data = _read_json(path)
    return data if isinstance(data, dict) else None


def _tool_paths(tool_name: str, arguments: Dict[str, Any]) -> Iterable[str]:
    if not isinstance(arguments, dict):
        return []
    if tool_name in {"write_file", "create_file", "append_to_file", "robust_replace_in_file", "editCode", "edit_code_ast"}:
        value = str(arguments.get("file_path") or arguments.get("path") or "").strip()
        return [value] if value else []
    if tool_name in {"delete_file", "delete_directory"}:
        value = str(arguments.get("path") or arguments.get("file_path") or "").strip()
        return [value] if value else []
    if tool_name == "rename_file_update_refs":
        paths = [
            str(arguments.get("old_path") or "").strip(),
            str(arguments.get("new_path") or "").strip(),
        ]
        return [path for path in paths if path]
    if tool_name == "apply_patch":
        return _patch_paths(str(arguments.get("patch_text") or ""), str(arguments.get("base_dir") or ""))
    return []


def _patch_paths(patch_text: str, base_dir: str = "") -> List[str]:
    paths: List[str] = []
    for raw_line in patch_text.splitlines():
        line = raw_line.strip()
        if line.startswith(("*** Update File: ", "*** Add File: ", "*** Delete File: ")):
            _append_unique(paths, _with_patch_base(line.split(": ", 1)[1], base_dir))
            continue
        if line.startswith("*** Rename from: "):
            _append_unique(paths, _with_patch_base(line.split(": ", 1)[1], base_dir))
            continue
        if line.startswith("*** Rename to: "):
            _append_unique(paths, _with_patch_base(line.split(": ", 1)[1], base_dir))
            continue
        if line.startswith("*** Move to: "):
            _append_unique(paths, _with_patch_base(line.split(": ", 1)[1], base_dir))
            continue
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                _append_unique(paths, _with_patch_base(_strip_diff_prefix(parts[2]), base_dir))
                _append_unique(paths, _with_patch_base(_strip_diff_prefix(parts[3]), base_dir))
            continue
        if line.startswith(("--- ", "+++ ")):
            value = line[4:].split("\t", 1)[0].strip()
            _append_unique(paths, _with_patch_base(_strip_diff_prefix(value), base_dir))
    return paths


def _append_unique(paths: List[str], value: str) -> None:
    cleaned = value.strip()
    if cleaned and cleaned != "/dev/null" and cleaned not in paths:
        paths.append(cleaned)


def _with_patch_base(path: str, base_dir: str) -> str:
    value = str(path or "").strip().strip('"')
    if not value or value == "/dev/null":
        return ""
    base = str(base_dir or "").strip()
    if not base or base == "." or Path(value).is_absolute():
        return value
    return str(Path(base) / value)


def _strip_diff_prefix(path: str) -> str:
    value = str(path or "").strip().strip('"')
    if value in {"", "/dev/null"}:
        return value
    if value.startswith("a/") or value.startswith("b/"):
        return value[2:]
    return value


def _relative_target(workspace_root: str, path: str) -> tuple[str, Optional[Path]]:
    root = _workspace_root(workspace_root)
    raw = Path(os.path.expanduser(str(path or "")))
    if not raw.is_absolute():
        raw = root / raw
    target = raw.resolve(strict=False)
    try:
        rel = target.relative_to(root)
    except ValueError:
        return "", None
    return str(rel).replace("\\", "/"), target


def _safe_target(root: Path, rel_path: str) -> Optional[Path]:
    target = (root / rel_path).resolve(strict=False)
    try:
        target.relative_to(root)
    except ValueError:
        return None
    return target


def _workspace_root(value: str) -> Path:
    return Path(value or os.getcwd()).expanduser().resolve(strict=False)


def _path_token(value: str) -> str:
    encoded = base64.urlsafe_b64encode(value.encode("utf-8")).decode("ascii")
    return encoded.rstrip("=") or "path"


def _slug(value: str) -> str:
    text = "".join(ch if ch.isalnum() or ch in "._-" else "-" for ch in str(value or ""))
    return text.strip(".-")[:160] or "default"


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None

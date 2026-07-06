from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

WORKTREE_SCHEMA = "metis.worktree.v1"
WORKTREE_REGISTRY_SCHEMA = "metis.worktree_registry.v1"
WORKTREE_PROMOTE_REVIEW_SCHEMA = "metis.worktree_promote_review.v1"
WORKTREE_PROMOTE_SCHEMA = "metis.worktree_promote.v1"
WORKTREE_PROMOTE_ROLLBACK_SCHEMA = "metis.worktree_promote_rollback.v1"
WORKTREE_STATUSES = {"active", "archived", "removed", "promoted", "failed"}
_MAX_DIFF_CHARS = int(os.environ.get("METIS_WORKTREE_MAX_DIFF_CHARS", "200000"))


class WorktreeError(RuntimeError):
    pass


@dataclass
class WorktreeRecord:
    worktree_id: str
    workspace_root: str
    repo_root: str
    worktree_path: str
    worktree_workspace_root: str
    branch: str
    base_ref: str
    status: str = "active"
    run_id: str = ""
    session_id: str = ""
    label: str = "run"
    source_profile: str = "local_worktree"
    dirty_at_creation: bool = False
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    archived_at: float = 0.0
    removed_at: float = 0.0
    promoted_at: float = 0.0
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    schema: str = WORKTREE_SCHEMA

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "worktree_id": self.worktree_id,
            "workspace_root": self.workspace_root,
            "repo_root": self.repo_root,
            "worktree_path": self.worktree_path,
            "worktree_workspace_root": self.worktree_workspace_root,
            "branch": self.branch,
            "base_ref": self.base_ref,
            "status": self.status,
            "run_id": self.run_id,
            "session_id": self.session_id,
            "label": self.label,
            "source_profile": self.source_profile,
            "dirty_at_creation": self.dirty_at_creation,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "archived_at": self.archived_at,
            "removed_at": self.removed_at,
            "promoted_at": self.promoted_at,
            "error": self.error,
            "metadata": dict(self.metadata or {}),
        }

    @classmethod
    def from_dict(cls, row: Dict[str, Any]) -> "WorktreeRecord":
        return cls(
            worktree_id=str(row.get("worktree_id") or row.get("id") or ""),
            workspace_root=str(row.get("workspace_root") or ""),
            repo_root=str(row.get("repo_root") or ""),
            worktree_path=str(row.get("worktree_path") or ""),
            worktree_workspace_root=str(row.get("worktree_workspace_root") or row.get("worktree_path") or ""),
            branch=str(row.get("branch") or ""),
            base_ref=str(row.get("base_ref") or "HEAD"),
            status=_normalize_status(row.get("status")),
            run_id=str(row.get("run_id") or ""),
            session_id=str(row.get("session_id") or ""),
            label=str(row.get("label") or "run"),
            source_profile=str(row.get("source_profile") or "local_worktree"),
            dirty_at_creation=bool(row.get("dirty_at_creation")),
            created_at=float(row.get("created_at") or 0.0),
            updated_at=float(row.get("updated_at") or 0.0),
            archived_at=float(row.get("archived_at") or 0.0),
            removed_at=float(row.get("removed_at") or 0.0),
            promoted_at=float(row.get("promoted_at") or 0.0),
            error=str(row.get("error") or ""),
            metadata=dict(row.get("metadata") if isinstance(row.get("metadata"), dict) else {}),
            schema=str(row.get("schema") or WORKTREE_SCHEMA),
        )


def create_worktree(
    workspace_root: str,
    *,
    run_id: str = "",
    session_id: str = "",
    label: str = "run",
) -> WorktreeRecord:
    workspace = _resolve_existing_dir(workspace_root)
    if workspace is None:
        raise WorktreeError(f"workspace_root is not an existing directory: {workspace_root!r}")

    repo_root = _require_git_repo(workspace)
    base_ref = _git_stdout(["rev-parse", "--verify", "HEAD"], cwd=repo_root).strip()
    if not base_ref:
        raise WorktreeError("git repository has no HEAD commit")

    base_dir = _worktree_base_dir(repo_root)
    if _is_within(base_dir, repo_root):
        raise WorktreeError(f"worktree base is inside source repository: {base_dir}")
    base_dir.mkdir(parents=True, exist_ok=True)

    suffix = (run_id or uuid.uuid4().hex)[:10]
    safe_label = _slug(label or "run")
    worktree_id = f"wt_{uuid.uuid4().hex[:12]}"
    branch = f"metis/run/{safe_label}-{suffix}"
    worktree_path = base_dir / f"{safe_label}-{suffix}"
    if worktree_path.exists():
        worktree_path = base_dir / f"{safe_label}-{suffix}-{uuid.uuid4().hex[:6]}"

    created = _run_git(["worktree", "add", "-b", branch, str(worktree_path), base_ref], cwd=repo_root, timeout=90)
    if created.returncode != 0:
        raise WorktreeError(_git_error("failed to create git worktree", created))

    worktree_workspace_root = _corresponding_worktree_workspace(workspace, repo_root, worktree_path)
    record = WorktreeRecord(
        worktree_id=worktree_id,
        workspace_root=str(workspace),
        repo_root=str(repo_root),
        worktree_path=str(worktree_path.resolve()),
        worktree_workspace_root=str(worktree_workspace_root),
        branch=branch,
        base_ref=base_ref.strip(),
        run_id=run_id,
        session_id=session_id,
        label=safe_label,
        dirty_at_creation=_is_dirty(repo_root),
        metadata={
            "base_dir": str(base_dir),
            "source_branch": _current_branch(repo_root),
        },
    )
    _upsert_record(record.workspace_root, record)
    return record


def list_worktrees(workspace_root: str) -> List[WorktreeRecord]:
    return [WorktreeRecord.from_dict(row) for row in _load_registry(workspace_root).get("worktrees", []) if isinstance(row, dict)]


def get_worktree(workspace_root: str, worktree_id: str) -> WorktreeRecord:
    for record in list_worktrees(workspace_root):
        if record.worktree_id == worktree_id:
            return record
    raise WorktreeError(f"worktree not found: {worktree_id}")


def archive_worktree(workspace_root: str, worktree_id: str) -> WorktreeRecord:
    record = get_worktree(workspace_root, worktree_id)
    record.status = "archived"
    record.archived_at = time.time()
    record.updated_at = record.archived_at
    _upsert_record(workspace_root, record)
    return record


def remove_worktree(workspace_root: str, worktree_id: str, *, force: bool = False) -> WorktreeRecord:
    record = get_worktree(workspace_root, worktree_id)
    base_dir = _worktree_base_dir(Path(record.repo_root))
    worktree_path = Path(record.worktree_path).resolve()
    if not _is_within(worktree_path, base_dir):
        raise WorktreeError(f"refusing to remove worktree outside managed base: {worktree_path}")
    args = ["worktree", "remove"]
    if force:
        args.append("--force")
    args.append(str(worktree_path))
    removed = _run_git(args, cwd=Path(record.repo_root), timeout=90)
    if removed.returncode != 0:
        raise WorktreeError(_git_error("failed to remove git worktree", removed))
    record.status = "removed"
    record.removed_at = time.time()
    record.updated_at = record.removed_at
    _upsert_record(workspace_root, record)
    return record


def diff_worktree(workspace_root: str, worktree_id: str, *, max_chars: int = _MAX_DIFF_CHARS) -> Dict[str, Any]:
    record = get_worktree(workspace_root, worktree_id)
    worktree_path = Path(record.worktree_path)
    if not worktree_path.is_dir():
        raise WorktreeError(f"worktree path no longer exists: {worktree_path}")
    status = _git_stdout(["status", "--short"], cwd=worktree_path)
    stat = _diff_worktree_with_untracked(worktree_path, record.base_ref, stat=True)
    patch = _diff_worktree_with_untracked(worktree_path, record.base_ref, stat=False)
    truncated = len(patch) > max_chars
    if truncated:
        patch = patch[:max_chars] + "\n[diff truncated]"
    return {
        "schema": "metis.worktree_diff.v1",
        "worktree": record.to_dict(),
        "status": status,
        "stat": stat,
        "patch": patch,
        "files": _changed_files_from_status(status),
        "truncated": truncated,
        "base_ref": record.base_ref,
    }


def review_worktree_promote(
    workspace_root: str,
    worktree_id: str,
    *,
    paths: Optional[List[str]] = None,
    max_chars: int = _MAX_DIFF_CHARS,
) -> Dict[str, Any]:
    record = get_worktree(workspace_root, worktree_id)
    source_repo = Path(record.repo_root)
    worktree_path = Path(record.worktree_path)
    if not source_repo.is_dir() or not worktree_path.is_dir():
        raise WorktreeError("source repo or worktree path no longer exists")

    selected_paths = _normalize_promote_paths(paths)
    status = _git_stdout(["status", "--short", "--", *selected_paths] if selected_paths else ["status", "--short"], cwd=worktree_path)
    stat = _diff_worktree_with_untracked(worktree_path, record.base_ref, stat=True, paths=selected_paths)
    patch = _diff_worktree_with_untracked(worktree_path, record.base_ref, stat=False, paths=selected_paths)
    files = _changed_files_from_status(status)
    patch_preview = patch
    truncated = len(patch_preview) > max_chars
    if truncated:
        patch_preview = patch_preview[:max_chars] + "\n[diff truncated]"

    check = _check_patch_applies(source_repo, patch)
    conflicts = _conflict_payload(check, files=files, source_repo=source_repo)
    return {
        "schema": WORKTREE_PROMOTE_REVIEW_SCHEMA,
        "ok": bool(check.returncode == 0),
        "worktree": record.to_dict(),
        "paths": selected_paths,
        "files": files,
        "status": status,
        "stat": stat,
        "patch": patch_preview,
        "truncated": truncated,
        "base_ref": record.base_ref,
        "can_apply": bool(check.returncode == 0),
        "conflicts": conflicts,
        "message": "patch applies cleanly" if check.returncode == 0 else conflicts.get("summary", "patch does not apply cleanly"),
    }


def promote_worktree(
    workspace_root: str,
    worktree_id: str,
    *,
    dry_run: bool = False,
    paths: Optional[List[str]] = None,
) -> Dict[str, Any]:
    record = get_worktree(workspace_root, worktree_id)
    source_repo = Path(record.repo_root)
    worktree_path = Path(record.worktree_path)
    if not source_repo.is_dir() or not worktree_path.is_dir():
        raise WorktreeError("source repo or worktree path no longer exists")

    selected_paths = _normalize_promote_paths(paths)
    status = _git_stdout(["status", "--short", "--", *selected_paths] if selected_paths else ["status", "--short"], cwd=worktree_path)
    stat = _diff_worktree_with_untracked(worktree_path, record.base_ref, stat=True, paths=selected_paths)
    patch = _diff_worktree_with_untracked(worktree_path, record.base_ref, stat=False, paths=selected_paths)
    files = _changed_files_from_status(status)
    if not patch.strip():
        return {
            "schema": WORKTREE_PROMOTE_SCHEMA,
            "ok": True,
            "dry_run": dry_run,
            "worktree": record.to_dict(),
            "paths": selected_paths,
            "files": files,
            "message": "no changes to promote",
        }
    check = _check_patch_applies(source_repo, patch)
    conflicts = _conflict_payload(check, files=files, source_repo=source_repo)
    if check.returncode != 0:
        return {
            "schema": WORKTREE_PROMOTE_SCHEMA,
            "ok": False,
            "dry_run": dry_run,
            "worktree": record.to_dict(),
            "paths": selected_paths,
            "files": files,
            "conflicts": conflicts,
            "error": _git_error("patch does not apply cleanly", check),
        }
    if dry_run:
        return {
            "schema": WORKTREE_PROMOTE_SCHEMA,
            "ok": True,
            "dry_run": True,
            "worktree": record.to_dict(),
            "paths": selected_paths,
            "files": files,
            "stat": stat,
            "can_apply": True,
            "conflicts": conflicts,
            "message": "patch applies cleanly",
        }
    promotion_id = f"promo_{uuid.uuid4().hex[:12]}"
    patch_path = _write_promotion_patch(source_repo, promotion_id, patch)
    applied = _run_git_with_input(["apply", "-"], cwd=source_repo, input_text=patch, timeout=60)
    if applied.returncode != 0:
        return {
            "schema": WORKTREE_PROMOTE_SCHEMA,
            "ok": False,
            "dry_run": False,
            "worktree": record.to_dict(),
            "paths": selected_paths,
            "files": files,
            "conflicts": _conflict_payload(applied, files=files, source_repo=source_repo),
            "error": _git_error("failed to apply patch", applied),
        }
    record.status = "promoted"
    record.promoted_at = time.time()
    record.updated_at = record.promoted_at
    _record_promotion(
        record,
        promotion_id=promotion_id,
        patch_path=patch_path,
        paths=selected_paths,
        files=files,
        stat=stat,
    )
    _upsert_record(workspace_root, record)
    return {
        "schema": WORKTREE_PROMOTE_SCHEMA,
        "ok": True,
        "dry_run": False,
        "worktree": record.to_dict(),
        "promotion_id": promotion_id,
        "rollback_available": True,
        "rollback_patch_path": str(patch_path),
        "paths": selected_paths,
        "files": files,
        "stat": stat,
        "message": "patch promoted to source workspace",
    }


def rollback_worktree_promotion(
    workspace_root: str,
    worktree_id: str,
    *,
    promotion_id: str = "",
    dry_run: bool = False,
) -> Dict[str, Any]:
    record = get_worktree(workspace_root, worktree_id)
    source_repo = Path(record.repo_root)
    if not source_repo.is_dir():
        raise WorktreeError("source repo no longer exists")
    promotion = _find_rollback_promotion(record, promotion_id)
    if not promotion:
        return {
            "schema": WORKTREE_PROMOTE_ROLLBACK_SCHEMA,
            "ok": False,
            "dry_run": dry_run,
            "worktree": record.to_dict(),
            "error": "no rollback promotion found",
        }
    patch_path = Path(str(promotion.get("patch_path") or "")).expanduser()
    if not patch_path.is_file():
        return {
            "schema": WORKTREE_PROMOTE_ROLLBACK_SCHEMA,
            "ok": False,
            "dry_run": dry_run,
            "worktree": record.to_dict(),
            "promotion_id": str(promotion.get("promotion_id") or ""),
            "error": f"rollback patch not found: {patch_path}",
        }
    patch = patch_path.read_text(encoding="utf-8", errors="replace")
    check = _run_git_with_input(["apply", "-R", "--check", "-"], cwd=source_repo, input_text=patch, timeout=60)
    files = promotion.get("files") if isinstance(promotion.get("files"), list) else []
    conflicts = _conflict_payload(check, files=[item for item in files if isinstance(item, dict)], source_repo=source_repo)
    if check.returncode != 0:
        return {
            "schema": WORKTREE_PROMOTE_ROLLBACK_SCHEMA,
            "ok": False,
            "dry_run": dry_run,
            "worktree": record.to_dict(),
            "promotion_id": str(promotion.get("promotion_id") or ""),
            "conflicts": conflicts,
            "error": _git_error("rollback patch does not apply cleanly", check),
        }
    if dry_run:
        return {
            "schema": WORKTREE_PROMOTE_ROLLBACK_SCHEMA,
            "ok": True,
            "dry_run": True,
            "worktree": record.to_dict(),
            "promotion_id": str(promotion.get("promotion_id") or ""),
            "paths": promotion.get("paths") if isinstance(promotion.get("paths"), list) else [],
            "files": files,
            "message": "rollback patch applies cleanly",
        }
    applied = _run_git_with_input(["apply", "-R", "-"], cwd=source_repo, input_text=patch, timeout=60)
    if applied.returncode != 0:
        return {
            "schema": WORKTREE_PROMOTE_ROLLBACK_SCHEMA,
            "ok": False,
            "dry_run": False,
            "worktree": record.to_dict(),
            "promotion_id": str(promotion.get("promotion_id") or ""),
            "conflicts": _conflict_payload(applied, files=[item for item in files if isinstance(item, dict)], source_repo=source_repo),
            "error": _git_error("failed to apply rollback patch", applied),
        }
    promotion["rolled_back"] = True
    promotion["rolled_back_at"] = time.time()
    record.status = "active"
    record.updated_at = time.time()
    record.metadata["last_rollback"] = {
        "promotion_id": str(promotion.get("promotion_id") or ""),
        "rolled_back_at": promotion["rolled_back_at"],
    }
    _upsert_record(workspace_root, record)
    return {
        "schema": WORKTREE_PROMOTE_ROLLBACK_SCHEMA,
        "ok": True,
        "dry_run": False,
        "worktree": record.to_dict(),
        "promotion_id": str(promotion.get("promotion_id") or ""),
        "paths": promotion.get("paths") if isinstance(promotion.get("paths"), list) else [],
        "files": files,
        "message": "promotion rolled back from source workspace",
    }


def _diff_worktree_with_untracked(
    worktree_path: Path,
    base_ref: str,
    *,
    stat: bool,
    paths: Optional[List[str]] = None,
) -> str:
    selected_paths = _normalize_promote_paths(paths)
    untracked = _untracked_files(worktree_path, paths=selected_paths)
    if untracked:
        _git_stdout(["add", "-N", "--", *untracked], cwd=worktree_path, timeout=60)
    try:
        args = ["diff", "--stat" if stat else "--binary", base_ref]
        if selected_paths:
            args.extend(["--", *selected_paths])
        return _git_stdout(args, cwd=worktree_path, timeout=60)
    finally:
        if untracked:
            _git_stdout(["reset", "--", *untracked], cwd=worktree_path, timeout=60)


def _untracked_files(worktree_path: Path, *, paths: Optional[List[str]] = None) -> List[str]:
    selected_paths = _normalize_promote_paths(paths)
    args = ["ls-files", "--others", "--exclude-standard"]
    if selected_paths:
        args.extend(["--", *selected_paths])
    out = _git_stdout(args, cwd=worktree_path)
    return [line.strip() for line in out.splitlines() if line.strip()]


def _normalize_promote_paths(paths: Optional[List[str]]) -> List[str]:
    if not paths:
        return []
    out: List[str] = []
    seen: set[str] = set()
    for item in paths:
        text = str(item or "").replace("\\", "/").strip().strip('"')
        if not text or text.startswith("/") or re.match(r"^[A-Za-z]:", text):
            continue
        parts = [part for part in text.split("/") if part not in {"", "."}]
        if not parts or any(part == ".." for part in parts):
            continue
        normalized = "/".join(parts)
        if normalized in seen:
            continue
        seen.add(normalized)
        out.append(normalized)
    return out[:200]


def _changed_files_from_status(status: str) -> List[Dict[str, str]]:
    files: List[Dict[str, str]] = []
    for line in str(status or "").splitlines():
        if not line.strip():
            continue
        marker = line[:2].strip() or line[:1].strip() or "?"
        path = line[3:].strip() if len(line) >= 4 else line.strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1].strip()
        path = path.strip('"').replace("\\", "/")
        if path:
            files.append({"path": path, "status": marker})
        if len(files) >= 200:
            break
    return files


def _check_patch_applies(source_repo: Path, patch: str) -> subprocess.CompletedProcess[str]:
    if not str(patch or "").strip():
        return subprocess.CompletedProcess(args=["git", "apply", "--check", "-"], returncode=0, stdout="", stderr="")
    return _run_git_with_input(["apply", "--check", "-"], cwd=source_repo, input_text=patch, timeout=60)


def _conflict_payload(
    result: subprocess.CompletedProcess[str],
    *,
    files: List[Dict[str, str]],
    source_repo: Path,
) -> Dict[str, Any]:
    raw = "\n".join(part for part in [result.stderr, result.stdout] if part).strip()
    if result.returncode == 0:
        return {
            "ok": True,
            "summary": "No conflicts detected.",
            "files": [],
            "raw": "",
        }
    conflict_paths = _paths_from_git_apply_error(raw)
    if not conflict_paths:
        conflict_paths = [str(item.get("path") or "") for item in files if isinstance(item, dict) and item.get("path")]
    rows: List[Dict[str, str]] = []
    for path in conflict_paths:
        source_status = _source_status_for_path(source_repo, path)
        reason = "patch context does not match the source workspace"
        if source_status:
            reason = f"source workspace already has changes for this path ({source_status})"
        rows.append({"path": path, "reason": reason, "source_status": source_status})
    return {
        "ok": False,
        "summary": "Patch does not apply cleanly. Review conflicting files before promoting.",
        "files": rows,
        "raw": raw,
    }


def _paths_from_git_apply_error(text: str) -> List[str]:
    paths: List[str] = []
    seen: set[str] = set()
    patterns = [
        r"error: patch failed: (.+?):\d+",
        r"error: (.+?): patch does not apply",
        r"error: (.+?): already exists in working directory",
        r"error: (.+?): No such file or directory",
    ]
    for line in str(text or "").splitlines():
        for pattern in patterns:
            match = re.search(pattern, line)
            if not match:
                continue
            path = match.group(1).strip().strip('"').replace("\\", "/")
            if path and path not in seen:
                seen.add(path)
                paths.append(path)
    return paths[:200]


def _source_status_for_path(source_repo: Path, path: str) -> str:
    try:
        out = _git_stdout(["status", "--short", "--", path], cwd=source_repo, timeout=20)
    except Exception:
        return ""
    return " ".join(line.strip() for line in out.splitlines() if line.strip())


def _write_promotion_patch(source_repo: Path, promotion_id: str, patch: str) -> Path:
    out_dir = source_repo / ".metis" / "worktrees" / "promotions"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{promotion_id}.patch"
    path.write_text(patch, encoding="utf-8", newline="\n")
    return path


def _record_promotion(
    record: WorktreeRecord,
    *,
    promotion_id: str,
    patch_path: Path,
    paths: List[str],
    files: List[Dict[str, str]],
    stat: str,
) -> None:
    promotions = record.metadata.get("promotions") if isinstance(record.metadata.get("promotions"), list) else []
    row = {
        "schema": WORKTREE_PROMOTE_SCHEMA,
        "promotion_id": promotion_id,
        "patch_path": str(patch_path),
        "paths": list(paths),
        "files": list(files),
        "stat": stat,
        "created_at": time.time(),
        "rolled_back": False,
        "rolled_back_at": 0.0,
    }
    promotions.append(row)
    record.metadata["promotions"] = promotions[-50:]
    record.metadata["last_promotion"] = row


def _find_rollback_promotion(record: WorktreeRecord, promotion_id: str) -> Optional[Dict[str, Any]]:
    promotions = record.metadata.get("promotions") if isinstance(record.metadata.get("promotions"), list) else []
    candidates = [item for item in promotions if isinstance(item, dict) and not bool(item.get("rolled_back"))]
    if promotion_id:
        for item in candidates:
            if str(item.get("promotion_id") or "") == promotion_id:
                return item
        return None
    return candidates[-1] if candidates else None


def registry_payload(workspace_root: str) -> Dict[str, Any]:
    registry = _load_registry(workspace_root)
    registry["worktrees"] = [record.to_dict() for record in list_worktrees(workspace_root)]
    return registry


def _load_registry(workspace_root: str) -> Dict[str, Any]:
    path = _registry_path(workspace_root)
    if not path.exists():
        return {"schema": WORKTREE_REGISTRY_SCHEMA, "workspace_root": str(Path(workspace_root).resolve()), "worktrees": []}
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        data = {}
    worktrees = data.get("worktrees") if isinstance(data.get("worktrees"), list) else []
    return {
        "schema": str(data.get("schema") or WORKTREE_REGISTRY_SCHEMA),
        "workspace_root": str(data.get("workspace_root") or Path(workspace_root).resolve()),
        "worktrees": worktrees,
    }


def _write_registry(workspace_root: str, registry: Dict[str, Any]) -> None:
    path = _registry_path(workspace_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    with temp.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(registry, handle, ensure_ascii=False, indent=2, sort_keys=True)
        handle.write("\n")
    os.replace(temp, path)


def _upsert_record(workspace_root: str, record: WorktreeRecord) -> None:
    registry = _load_registry(workspace_root)
    rows = [row for row in registry.get("worktrees", []) if isinstance(row, dict) and row.get("worktree_id") != record.worktree_id]
    rows.append(record.to_dict())
    rows.sort(key=lambda row: float(row.get("created_at") or 0.0), reverse=True)
    registry["worktrees"] = rows
    _write_registry(workspace_root, registry)


def _registry_path(workspace_root: str) -> Path:
    workspace = Path(workspace_root or ".").expanduser().resolve()
    return workspace / ".metis" / "worktrees" / "registry.json"


def _resolve_existing_dir(path: str) -> Optional[Path]:
    try:
        resolved = Path(path or ".").expanduser().resolve()
    except OSError:
        return None
    return resolved if resolved.is_dir() else None


def _require_git_repo(cwd: Path) -> Path:
    result = _run_git(["rev-parse", "--show-toplevel"], cwd=cwd)
    if result.returncode != 0:
        raise WorktreeError(_git_error("workspace is not inside a git repository", result))
    root = (result.stdout or "").strip().splitlines()
    if not root:
        raise WorktreeError("git did not return a repository root")
    return Path(root[-1]).resolve()


def _worktree_base_dir(repo_root: Path) -> Path:
    configured = os.environ.get("METIS_WORKTREE_BASE", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    digest = hashlib.sha1(str(repo_root).lower().encode("utf-8")).hexdigest()[:8]
    return (repo_root.parent / ".metis-worktrees" / f"{repo_root.name}-{digest}").resolve()


def _corresponding_worktree_workspace(workspace: Path, repo_root: Path, worktree_path: Path) -> Path:
    try:
        relative = workspace.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return worktree_path.resolve()
    return (worktree_path / relative).resolve()


def _slug(text: str, *, default: str = "run", limit: int = 38) -> str:
    lowered = str(text or "").strip().lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    return (slug[:limit].strip("-") or default)


def _normalize_status(value: Any) -> str:
    status = str(value or "active").strip().lower()
    return status if status in WORKTREE_STATUSES else "active"


def _current_branch(repo_root: Path) -> str:
    return _git_stdout(["rev-parse", "--abbrev-ref", "HEAD"], cwd=repo_root)


def _is_dirty(repo_root: Path) -> bool:
    return bool(_git_stdout(["status", "--porcelain"], cwd=repo_root).strip())


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def _git_stdout(args: List[str], *, cwd: Path, timeout: int = 20) -> str:
    result = _run_git(args, cwd=cwd, timeout=timeout)
    if result.returncode != 0:
        raise WorktreeError(_git_error("git command failed", result))
    return result.stdout or ""


def _git_error(prefix: str, result: subprocess.CompletedProcess[str]) -> str:
    detail = (result.stderr or result.stdout or "").strip()
    return prefix if not detail else f"{prefix}: {detail[:1200]}"


def _run_git(args: List[str], *, cwd: Path, timeout: int = 20) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(["git", *args], 1, "", str(exc))


def _run_git_with_input(
    args: List[str],
    *,
    cwd: Path,
    input_text: str,
    timeout: int = 20,
) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return subprocess.CompletedProcess(["git", *args], 1, "", str(exc))


__all__ = [
    "WORKTREE_PROMOTE_REVIEW_SCHEMA",
    "WORKTREE_PROMOTE_ROLLBACK_SCHEMA",
    "WORKTREE_PROMOTE_SCHEMA",
    "WORKTREE_REGISTRY_SCHEMA",
    "WORKTREE_SCHEMA",
    "WorktreeError",
    "WorktreeRecord",
    "archive_worktree",
    "create_worktree",
    "diff_worktree",
    "get_worktree",
    "list_worktrees",
    "promote_worktree",
    "registry_payload",
    "remove_worktree",
    "review_worktree_promote",
    "rollback_worktree_promotion",
]

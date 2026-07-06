from __future__ import annotations

import json
import mimetypes
import os
import tempfile
import threading
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urlparse

from backend.core.paths import metis_dir, metis_home, metis_path
from backend.runtime import office_artifact_validation

ARTIFACT_SCHEMA = "metis.artifact.v1"
ARTIFACT_VERSION = 1
ARTIFACT_KINDS = {
    "file_change",
    "diff",
    "report",
    "document",
    "preview_evidence",
    "download",
    "workspace_file",
}

_LOCK = threading.RLock()
_MAX_LIMIT = 500


@dataclass(frozen=True)
class ArtifactFilters:
    session_id: str = ""
    run_id: str = ""
    kind: str = ""
    limit: int = 100


class ArtifactRegistryError(ValueError):
    """Raised when an artifact record cannot be safely registered."""


def registry_path() -> Path:
    return metis_path("artifacts", "registry.jsonl")


def register_artifact(
    *,
    kind: str,
    title: str,
    path: str = "",
    url: str = "",
    mime: str = "",
    run_id: str = "",
    session_id: str = "",
    turn_id: str = "",
    source_event_id: str = "",
    source_tool_call_id: str = "",
    metadata: dict[str, Any] | None = None,
    workspace_root: str = "",
) -> dict[str, Any]:
    """Append or update an artifact reference in the durable registry."""
    normalized_kind = _normalize_kind(kind)
    normalized_path = _normalize_path(path, workspace_root=workspace_root)
    normalized_url = _normalize_url(url)
    if not normalized_path and not normalized_url:
        raise ArtifactRegistryError("path or url is required")
    if normalized_path:
        _assert_path_allowed(normalized_path, workspace_root=workspace_root)

    clean_metadata = dict(metadata or {}) if isinstance(metadata, dict) else {}
    if _requires_office_validation(normalized_kind, normalized_path):
        validation = office_artifact_validation.validate_office_artifact(normalized_path)
        if not validation.get("ok"):
            raise ArtifactRegistryError(
                f"office artifact validation failed: {validation.get('summary') or validation.get('error') or 'unknown error'}"
            )
        clean_metadata = {
            **clean_metadata,
            "office_validation": validation,
            "artifact_state": "complete",
            "validated": True,
        }
    now = _utc_now()
    with _LOCK:
        current = _read_registry_unlocked()
        existing = _find_existing(current, kind=normalized_kind, path=normalized_path, url=normalized_url, source_event_id=source_event_id, source_tool_call_id=source_tool_call_id)
        artifact_id = str(existing.get("artifact_id") or f"art_{uuid.uuid4().hex}") if existing else f"art_{uuid.uuid4().hex}"
        created_at = str(existing.get("created_at") or now) if existing else now
        record = {
            "schema": ARTIFACT_SCHEMA,
            "version": ARTIFACT_VERSION,
            "artifact_id": artifact_id,
            "run_id": str(run_id or existing.get("run_id") if existing else run_id or ""),
            "session_id": str(session_id or existing.get("session_id") if existing else session_id or ""),
            "turn_id": str(turn_id or existing.get("turn_id") if existing else turn_id or ""),
            "kind": normalized_kind,
            "title": _clean_title(title, normalized_path, normalized_url),
            "path": normalized_path,
            "url": normalized_url,
            "mime": str(mime or existing.get("mime") if existing else mime or _guess_mime(normalized_path, normalized_url)),
            "created_at": created_at,
            "updated_at": now,
            "source_event_id": str(source_event_id or existing.get("source_event_id") if existing else source_event_id or ""),
            "source_tool_call_id": str(source_tool_call_id or existing.get("source_tool_call_id") if existing else source_tool_call_id or ""),
            "metadata": {
                **(existing.get("metadata") if isinstance(existing.get("metadata"), dict) else {}),
                **clean_metadata,
            } if existing else clean_metadata,
        }
        _append_record_unlocked(record)
        return record


def list_artifacts(filters: ArtifactFilters | None = None) -> list[dict[str, Any]]:
    filters = filters or ArtifactFilters()
    limit = max(1, min(int(filters.limit or 100), _MAX_LIMIT))
    with _LOCK:
        rows = list(_read_registry_unlocked().values())
    if filters.session_id:
        rows = [row for row in rows if str(row.get("session_id") or "") == filters.session_id]
    if filters.run_id:
        rows = [row for row in rows if str(row.get("run_id") or "") == filters.run_id]
    if filters.kind:
        rows = [row for row in rows if str(row.get("kind") or "") == filters.kind]
    rows.sort(key=lambda row: str(row.get("updated_at") or row.get("created_at") or ""), reverse=True)
    return rows[:limit]


def get_artifact(artifact_id: str) -> dict[str, Any] | None:
    clean_id = str(artifact_id or "").strip()
    if not clean_id:
        return None
    with _LOCK:
        return _read_registry_unlocked().get(clean_id)


def reindex_artifacts(*, workspace_root: str = "") -> dict[str, Any]:
    """Rebuild basic registry references from known durable artifact locations."""
    created: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    def add(**kwargs: Any) -> None:
        try:
            created.append(register_artifact(**kwargs, workspace_root=workspace_root))
        except Exception as exc:
            errors.append({"path": str(kwargs.get("path") or kwargs.get("url") or ""), "error": str(exc)})

    for report_path in _iter_files(metis_dir("research", "reports"), suffixes={".md", ".markdown"}):
        add(
            kind="report",
            title=report_path.stem,
            path=str(report_path),
            mime="text/markdown",
            metadata={"reindexed": True, "source": "research_reports"},
        )

    for evidence_path in _iter_preview_evidence_dirs():
        for path_item in _iter_files(evidence_path, suffixes={".json"}):
            title = "Preview evidence"
            url = ""
            try:
                parsed = json.loads(path_item.read_text(encoding="utf-8"))
                result = parsed.get("result") if isinstance(parsed, dict) else {}
                if isinstance(result, dict):
                    title = str(result.get("title") or title)
                    url = str(result.get("url") or "")
            except Exception:
                pass
            add(
                kind="preview_evidence",
                title=title,
                path=str(path_item),
                url=url,
                mime="application/json",
                metadata={"reindexed": True, "source": "preview_evidence"},
            )

    if workspace_root:
        audit_path = Path(workspace_root).resolve(strict=False) / ".metis" / "audit" / "file-change-transactions.jsonl"
        if audit_path.is_file():
            add(
                kind="file_change",
                title="File change audit",
                path=str(audit_path),
                mime="application/jsonl",
                metadata={"reindexed": True, "source": "file_change_audit"},
            )
        workspace_artifacts = Path(workspace_root).resolve(strict=False) / ".metis" / "artifacts"
        for path_item in _iter_files(workspace_artifacts):
            add(
                kind=_kind_for_file(path_item),
                title=path_item.name,
                path=str(path_item),
                mime=_guess_mime(str(path_item), ""),
                metadata={"reindexed": True, "source": "workspace_artifacts"},
            )

    return {"ok": True, "count": len(created), "artifacts": created, "errors": errors}


def _read_registry_unlocked() -> dict[str, dict[str, Any]]:
    path = registry_path()
    if not path.is_file():
        return {}
    rows: dict[str, dict[str, Any]] = {}
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return rows
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(row, dict):
            continue
        normalized = _coerce_record(row)
        if normalized:
            rows[normalized["artifact_id"]] = normalized
    return rows


def _append_record_unlocked(record: dict[str, Any]) -> None:
    path = registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _coerce_record(row: dict[str, Any]) -> dict[str, Any] | None:
    artifact_id = str(row.get("artifact_id") or row.get("artifactId") or "").strip()
    kind = _normalize_kind(str(row.get("kind") or ""))
    title = str(row.get("title") or "").strip()
    created_at = str(row.get("created_at") or row.get("createdAt") or "").strip()
    path = str(row.get("path") or "").strip()
    url = str(row.get("url") or "").strip()
    if not artifact_id or not title or not created_at or (not path and not url):
        return None
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    return {
        "schema": ARTIFACT_SCHEMA,
        "version": ARTIFACT_VERSION,
        "artifact_id": artifact_id,
        "run_id": str(row.get("run_id") or row.get("runId") or ""),
        "session_id": str(row.get("session_id") or row.get("sessionId") or ""),
        "turn_id": str(row.get("turn_id") or row.get("turnId") or ""),
        "kind": kind,
        "title": title,
        "path": path,
        "url": url,
        "mime": str(row.get("mime") or ""),
        "created_at": created_at,
        "updated_at": str(row.get("updated_at") or row.get("updatedAt") or created_at),
        "source_event_id": str(row.get("source_event_id") or row.get("sourceEventId") or ""),
        "source_tool_call_id": str(row.get("source_tool_call_id") or row.get("sourceToolCallId") or ""),
        "metadata": dict(metadata),
    }


def _find_existing(
    rows: dict[str, dict[str, Any]],
    *,
    kind: str,
    path: str,
    url: str,
    source_event_id: str,
    source_tool_call_id: str,
) -> dict[str, Any] | None:
    for row in rows.values():
        if str(row.get("kind") or "") != kind:
            continue
        if source_event_id and str(row.get("source_event_id") or "") != str(source_event_id):
            continue
        if source_tool_call_id and str(row.get("source_tool_call_id") or "") != str(source_tool_call_id):
            continue
        if path and str(row.get("path") or "") == path:
            return row
        if url and str(row.get("url") or "") == url:
            return row
    for row in rows.values():
        if str(row.get("kind") or "") != kind:
            continue
        if path and str(row.get("path") or "") == path:
            return row
        if url and str(row.get("url") or "") == url:
            return row
    return None


def _normalize_kind(value: str) -> str:
    kind = str(value or "").strip().lower()
    if not kind:
        raise ArtifactRegistryError("kind is required")
    if kind not in ARTIFACT_KINDS:
        raise ArtifactRegistryError(f"unsupported artifact kind: {kind}")
    return kind


def _normalize_path(value: str, *, workspace_root: str = "") -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    path = Path(text).expanduser()
    if not path.is_absolute():
        base = Path(workspace_root or os.getcwd())
        path = base / path
    return str(path.resolve(strict=False))


def _normalize_url(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    parsed = urlparse(text)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise ArtifactRegistryError("artifact url must be http(s)")
    return text


def _clean_title(value: str, path: str, url: str) -> str:
    title = str(value or "").strip()
    if title:
        return title[:240]
    if path:
        return Path(path).name or "Artifact"
    return urlparse(url).netloc or "Artifact"


def _guess_mime(path: str, url: str) -> str:
    value = path or urlparse(url).path
    return mimetypes.guess_type(value)[0] or ""


def _kind_for_file(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".md", ".markdown", ".txt", ".pdf", ".docx", ".xlsx", ".pptx", ".csv"}:
        return "document"
    if ext in {".diff", ".patch"}:
        return "diff"
    return "workspace_file"


def _requires_office_validation(kind: str, path: str) -> bool:
    if kind not in {"document", "report"}:
        return False
    return bool(path and office_artifact_validation.is_office_artifact_path(path))


def _assert_path_allowed(path: str, *, workspace_root: str = "") -> None:
    target = os.path.abspath(path)
    roots = _allowed_roots(workspace_root)
    if not any(_path_within(target, root) for root in roots):
        raise ArtifactRegistryError("artifact path is outside allowed roots")


def _allowed_roots(workspace_root: str = "") -> list[str]:
    roots = [
        str(metis_home()),
        tempfile.gettempdir(),
    ]
    data_root = os.environ.get("METIS_DATA_ROOT", "").strip()
    if data_root:
        roots.append(data_root)
    if workspace_root:
        roots.append(workspace_root)
    return [os.path.abspath(root) for root in roots if str(root or "").strip()]


def _path_within(path: str, root: str) -> bool:
    try:
        target = os.path.normcase(os.path.abspath(path))
        base = os.path.normcase(os.path.abspath(root))
        return os.path.commonpath([target, base]) == base
    except ValueError:
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _iter_files(root: Path, suffixes: set[str] | None = None) -> Iterable[Path]:
    if not root.is_dir():
        return []
    blocked_names = {"registry.jsonl"}
    files: list[Path] = []
    for item in root.rglob("*"):
        if not item.is_file() or item.name in blocked_names:
            continue
        if suffixes and item.suffix.lower() not in suffixes:
            continue
        files.append(item)
    return files[:300]


def _iter_preview_evidence_dirs() -> list[Path]:
    roots: list[Path] = []
    data_root = os.environ.get("METIS_DATA_ROOT", "").strip()
    if data_root:
        roots.append(Path(data_root).expanduser().resolve(strict=False) / "electron" / "preview-evidence")
    roots.append(metis_home() / "electron" / "preview-evidence")
    roots.append(metis_home() / "preview-evidence")
    seen: set[str] = set()
    output: list[Path] = []
    for root in roots:
        key = os.path.normcase(os.path.abspath(str(root)))
        if key in seen:
            continue
        seen.add(key)
        output.append(root)
    return output

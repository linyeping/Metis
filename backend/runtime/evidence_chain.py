from __future__ import annotations

import json
import re
from typing import Any, Dict, Iterable, Mapping


EVIDENCE_CHAIN_SCHEMA = "metis.verifier.evidence_chain.v2"
EVIDENCE_ENTRY_SCHEMA = "metis.verifier.evidence.v2"


def build_verifier_evidence_payload(
    *,
    surface: str,
    assertion: str = "",
    checks: Mapping[str, Any] | None = None,
    check_details: Mapping[str, Any] | None = None,
    evidence: Iterable[Mapping[str, Any]] | None = None,
    subject: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Build a consistent verifier verdict and evidence chain.

    The verifier tools historically returned ad-hoc `checks` and sometimes an
    `evidence_chain`. v2 keeps those fields useful while adding a common
    verdict and normalized chain that the UI and future compaction can read.
    """

    clean_checks = {str(key): bool(value) for key, value in (checks or {}).items()}
    details = {str(key): value for key, value in (check_details or {}).items()}
    subject_payload = _compact_value(dict(subject or {}), max_chars=2000)
    chain: list[dict[str, Any]] = []

    for item in evidence or []:
        if not isinstance(item, Mapping):
            continue
        chain.append(_normalize_evidence_entry(item, surface=surface, index=len(chain) + 1))

    for name, ok in clean_checks.items():
        detail = details.get(name, {})
        chain.append(
            _normalize_evidence_entry(
                {
                    "kind": "check",
                    "check": name,
                    "ok": ok,
                    "summary": _check_summary(name, ok),
                    "detail": detail,
                },
                surface=surface,
                index=len(chain) + 1,
            )
        )

    verdict = build_verdict(
        checks=clean_checks,
        assertion=assertion,
        surface=surface,
        subject=subject_payload if isinstance(subject_payload, Mapping) else {},
    )
    return {
        "evidence_schema": EVIDENCE_CHAIN_SCHEMA,
        "verdict": verdict,
        "evidence_chain_v2": chain,
    }


def build_verdict(
    *,
    checks: Mapping[str, Any],
    assertion: str = "",
    surface: str = "",
    subject: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    normalized = {str(key): bool(value) for key, value in checks.items()}
    total = len(normalized)
    passed = sum(1 for value in normalized.values() if value)
    failed_checks = [name for name, ok in normalized.items() if not ok]
    ok = total == 0 or passed == total
    subject_text = _subject_text(subject or {})
    summary = _verdict_summary(
        ok=ok,
        passed=passed,
        total=total,
        assertion=assertion,
        subject=subject_text,
    )
    return {
        "ok": ok,
        "status": "verified" if ok else "failed",
        "surface": str(surface or "verifier"),
        "assertion": str(assertion or "").strip(),
        "summary": summary,
        "passed": passed,
        "failed": len(failed_checks),
        "total": total,
        "failed_checks": failed_checks,
        "subject": dict(subject or {}),
    }


def _normalize_evidence_entry(item: Mapping[str, Any], *, surface: str, index: int) -> Dict[str, Any]:
    kind = str(item.get("kind") or item.get("source") or "evidence").strip() or "evidence"
    ok = bool(item.get("ok", item.get("matched", True)))
    check = str(item.get("check") or item.get("id") or "").strip()
    summary = str(item.get("summary") or "").strip() or _evidence_summary(kind, item)
    entry: Dict[str, Any] = {
        "schema": EVIDENCE_ENTRY_SCHEMA,
        "id": f"ev-{index:02d}-{_slug(kind if not check else check)}",
        "surface": str(surface or "verifier"),
        "kind": kind,
        "ok": ok,
        "summary": _truncate(summary, 260),
    }
    if check:
        entry["check"] = check
    for key in ("query", "path", "url", "title", "exe", "hwnd", "source"):
        value = item.get(key)
        if value not in (None, ""):
            entry[key] = _compact_value(value, max_chars=400)
    detail = item.get("detail")
    if detail is None:
        detail = {
            key: value
            for key, value in item.items()
            if key
            not in {
                "schema",
                "id",
                "surface",
                "kind",
                "ok",
                "matched",
                "summary",
                "check",
                "query",
                "path",
                "url",
                "title",
                "exe",
                "hwnd",
                "source",
            }
        }
    compact_detail = _compact_value(detail, max_chars=2400)
    if compact_detail not in ({}, [], "", None):
        entry["detail"] = compact_detail
    return entry


def _check_summary(name: str, ok: bool) -> str:
    label = str(name or "check").replace("_", " ")
    return f"{label}: {'passed' if ok else 'failed'}"


def _evidence_summary(kind: str, item: Mapping[str, Any]) -> str:
    if kind == "window":
        return _join_parts("Window", item.get("title"), item.get("exe"))
    if kind == "screenshot":
        return _join_parts("Screenshot", item.get("path"), _size_text(item))
    if kind == "text_match":
        query = str(item.get("query") or "").strip()
        matched = bool(item.get("matched", item.get("ok", False)))
        return f"Text match {query!r}: {'matched' if matched else 'not matched'}"
    if kind == "diagnostics":
        return "Diagnostics captured"
    if kind == "page":
        return _join_parts("Page", item.get("url"), item.get("title"))
    return _join_parts(kind, item.get("summary"), item.get("text"), item.get("path"))


def _verdict_summary(*, ok: bool, passed: int, total: int, assertion: str, subject: str) -> str:
    prefix = "Verified" if ok else "Verification failed"
    count = f"{passed}/{total} checks passed" if total else "no explicit checks"
    target = str(assertion or subject or "").strip()
    if target:
        return _truncate(f"{prefix}: {count}. {target}", 260)
    return f"{prefix}: {count}."


def _subject_text(subject: Mapping[str, Any]) -> str:
    for key in ("title", "url", "path", "exe", "hwnd"):
        value = str(subject.get(key) or "").strip()
        if value:
            return value
    return ""


def _size_text(item: Mapping[str, Any]) -> str:
    width = item.get("width")
    height = item.get("height")
    if width and height:
        return f"{width}x{height}"
    return ""


def _join_parts(*parts: Any) -> str:
    return " · ".join(str(part).strip() for part in parts if str(part or "").strip())


def _compact_value(value: Any, *, max_chars: int) -> Any:
    if isinstance(value, Mapping):
        compacted: Dict[str, Any] = {}
        for key, item in value.items():
            if len(compacted) >= 30:
                break
            compacted[str(key)] = _compact_value(item, max_chars=max(120, max_chars // 3))
        return compacted
    if isinstance(value, list):
        return [_compact_value(item, max_chars=max(120, max_chars // 4)) for item in value[:20]]
    if isinstance(value, tuple):
        return [_compact_value(item, max_chars=max(120, max_chars // 4)) for item in value[:20]]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return _truncate(str(value), max_chars) if isinstance(value, str) else value
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = str(value)
    return _truncate(text, max_chars)


def _truncate(text: str, limit: int) -> str:
    value = str(text or "")
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "-", str(value or "").strip().lower()).strip("-")
    return slug[:40] or "evidence"

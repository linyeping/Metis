from __future__ import annotations

import json
import re
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse, urlunparse

from backend.core.paths import metis_dir
from backend.runtime.artifact_registry import ArtifactRegistryError, register_artifact

_SCHEMA = "metis.research_job.v1"
_LOCK = threading.RLock()
_MAX_JOBS = 120
_TRUNCATED_MARKER_RE = re.compile(r"\[\s*\.{3}\s*truncated\s+\d+\s+chars\s*\.{3}\s*\]", flags=re.IGNORECASE)
_REPORT_NOISE_LINE_PATTERNS = [
    r"^\s*(?:On This Page|In this article|Table of contents|Back to Blog|Sign In|Log In|Subscribe|Newsletter|Previous|Next|Top)\s*$",
    r"^\s*(?:Jump to content|Main menu|Navigation|Contribute|Personal tools|Appearance|Search|Share|Copy link|Mail)\s*$",
    r"^\s*(?:Get API key|Cookbook|Community|Docs|API reference|Overview|Get Started|Pricing|Coding agent setup)\s*$",
    r"^\s*(?:Use your Google Account|Email or phone|Forgot email\?|Not your computer\?|Create account|Next)\s*$",
    r"^\s*\d+\s+sections?\s*$",
    r"^\s*(?:x|go|open|read|more)\s*$",
    r"^\s*(?:首页\s*[»>]|Tags?:|标签[:：])",
    r"^[^\n]{0,120}(?:Gemini|Claude|OpenAI|Google|谷歌|模型|教程|创作|新闻)[^\n]{0,120}[：:]\s*(?:Go|Open|Read|More)\s*$",
    r"^\s*[-*]?\s*(?:Status|Provider|Providers|Query|Search query|Report status|Chat output policy|Search results|Evidence pages opened|Read failures|Partial evidence|URL)\s*[:：]",
    r"Afrikaans.+English.+Español.+Français",
    r"MCP ServersMCP Servers|Agent SkillsAgent Skills|DocumentationDocumentation",
    r"(?:\[.{2,80}\]\([^)]+\)\s*){2,}",
    r"\bConnection(?:Aborted)?Error\b",
    r"\bHTTPS?ConnectionPool\b",
    r"\bMax retries exceeded\b",
    r"\bSSLError\b",
    r"\bUNEXPECTED_EOF_WHILE_READING\b",
    r"\bCERTIFICATE_VERIFY_FAILED\b",
    r"页面读取失败",
    r"你的主机中的软件中止了一个已建立的连接",
    r"会员\s*充值|客服\s*微信|微信号|请添加客服|代充|出售\s*账号|账号\s*出售|gpthuiyuan",
]


def save_research_activity_job(
    activity: dict[str, Any],
    *,
    report: str = "",
    status: str = "complete",
    evidence: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Persist a web/search/research activity as a durable research job."""
    with _LOCK:
        payload = dict(activity or {})
        existing_id = str(payload.get("job_id") or "").strip()
        job_id = existing_id or _new_job_id(payload.get("kind"))
        now = int(time.time() * 1000)
        previous = get_research_job(job_id) if existing_id else None
        payload["sources"] = _sanitize_sources(payload.get("sources") or (previous or {}).get("sources"))
        payload["failures"] = _sanitize_sources(payload.get("failures") or (previous or {}).get("failures"), keep_status=True)
        payload["attempts"] = _sanitize_attempts(payload.get("attempts") or (previous or {}).get("attempts"))
        clean_report = _sanitize_report_links(report or str((previous or {}).get("report") or ""))
        job = {
            **(previous or {}),
            "schema": _SCHEMA,
            "id": job_id,
            "kind": str(payload.get("kind") or (previous or {}).get("kind") or "research"),
            "title": str(payload.get("title") or payload.get("query") or (previous or {}).get("title") or "Untitled research"),
            "query": str(payload.get("query") or (previous or {}).get("query") or ""),
            "status": _normalize_status(status),
            "providers": _list_of_strings(payload.get("providers") or (previous or {}).get("providers")),
            "plan": _list_of_dicts(payload.get("phases") or (previous or {}).get("plan")),
            "queries": _queries_from_activity(payload, previous or {}),
            "sources": _list_of_dicts(payload.get("sources")),
            "attempts": _list_of_dicts(payload.get("attempts")),
            "failures": _list_of_dicts(payload.get("failures")),
            "evidence": evidence if evidence is not None else _evidence_from_activity(payload, previous or {}),
            "report": clean_report,
            "stats": _stats_from_activity(payload, previous or {}),
            "created_at": int((previous or {}).get("created_at") or now),
            "updated_at": now,
        }
        job["report"] = _sanitize_report_body(str(job.get("report") or ""), job)
        if str(job.get("report") or "").strip():
            job.update(_write_report_markdown_file(job))
            job.update(_register_report_artifact(job, payload))
        path = _job_path(job_id)
        path.write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
        _prune_old_jobs()
        return job


def list_research_jobs(limit: int = 40) -> list[dict[str, Any]]:
    with _LOCK:
        rows = [_compact_job(job) for job in _read_all_jobs()]
    rows.sort(key=lambda item: int(item.get("updated_at") or 0), reverse=True)
    return rows[: max(1, min(int(limit or 40), _MAX_JOBS))]


def get_research_job(job_id: str) -> dict[str, Any] | None:
    safe_id = _safe_job_id(job_id)
    if not safe_id:
        return None
    path = _job_path(safe_id)
    if not path.is_file():
        return None
    try:
        row = json.loads(path.read_text(encoding="utf-8"))
        return _sanitize_job(row) if isinstance(row, dict) else None
    except Exception:
        return None


def export_research_job_markdown(job_id: str) -> str:
    job = get_research_job(job_id)
    if not job:
        return ""
    return _job_markdown(job)


def _job_markdown(job: dict[str, Any]) -> str:
    title = str(job.get("title") or job.get("query") or "Research Report").strip()
    report = str(job.get("report") or "").strip()
    lines: list[str] = []
    if report:
        if re.match(r"^\s*#\s+", report):
            lines.extend([report, ""])
        else:
            lines.extend([f"# {title}", "", report, ""])
    else:
        lines.extend([f"# {title}", ""])
    sources = _list_of_dicts(job.get("sources"))
    if sources:
        lines.extend(["## 来源", ""])
        for source in sources:
            title = source.get("title") or source.get("domain") or source.get("url") or "Untitled"
            url = source.get("url") or ""
            lines.append(f"- [{title}]({url})" if url else f"- {title}")
        lines.append("")
    return "\n".join(lines).strip() + "\n"


def _read_all_jobs() -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    for path in _jobs_dir().glob("*.json"):
        try:
            row = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(row, dict):
            jobs.append(_sanitize_job(row))
    return jobs


def _compact_job(job: dict[str, Any]) -> dict[str, Any]:
    stats = job.get("stats") if isinstance(job.get("stats"), dict) else {}
    return {
        "schema": job.get("schema") or _SCHEMA,
        "id": job.get("id") or "",
        "kind": job.get("kind") or "research",
        "title": job.get("title") or "",
        "query": job.get("query") or "",
        "status": job.get("status") or "complete",
        "providers": _list_of_strings(job.get("providers")),
        "stats": stats,
        "report_filename": job.get("report_filename") or "",
        "report_path": job.get("report_path") or "",
        "artifact_id": job.get("artifact_id") or "",
        "created_at": int(job.get("created_at") or 0),
        "updated_at": int(job.get("updated_at") or 0),
    }


def _jobs_dir() -> Path:
    return metis_dir("research", "jobs")


def _reports_dir() -> Path:
    return metis_dir("research", "reports")


def _job_path(job_id: str) -> Path:
    return _jobs_dir() / f"{_safe_job_id(job_id)}.json"


def _new_job_id(kind: Any) -> str:
    prefix = re.sub(r"[^a-z0-9_]+", "_", str(kind or "research").lower()).strip("_") or "research"
    return f"{prefix}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"


def _safe_job_id(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip())[:120]


def _normalize_status(status: str) -> str:
    value = str(status or "").strip().lower()
    if value in {"running", "queued", "error", "failed", "partial", "complete", "done"}:
        return "complete" if value == "done" else value
    return "complete"


def _list_of_strings(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item or "").strip()]


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _queries_from_activity(activity: dict[str, Any], previous: dict[str, Any]) -> list[dict[str, Any]]:
    previous_queries = _list_of_dicts(previous.get("queries"))
    query = str(activity.get("query") or "").strip()
    if not query:
        return previous_queries
    row = {
        "query": query,
        "providers": _list_of_strings(activity.get("providers")),
        "kind": activity.get("kind") or "research",
        "at": int(time.time() * 1000),
    }
    if previous_queries:
        last = previous_queries[-1]
        if str(last.get("query") or "") == query and str(last.get("kind") or "") == str(row["kind"]):
            return [*previous_queries[:-1], {**last, **row}]
    return [*previous_queries, row][-20:]


def _evidence_from_activity(activity: dict[str, Any], previous: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = _list_of_dicts(previous.get("evidence"))
    for source in _list_of_dicts(activity.get("sources")):
        status = str(source.get("status") or "")
        if status not in {"opened", "search_result"}:
            continue
        item = {
            "title": _display_source_title(source.get("title"), source.get("url") or source.get("domain")),
            "url": clean_user_source_url(str(source.get("url") or "")),
            "snippet": source.get("snippet") or "",
            "status": source.get("evidence_status") or status,
            "chars": source.get("chars") or 0,
        }
        key = _evidence_key(item)
        for index, existing in enumerate(evidence):
            if _evidence_key(existing) == key:
                evidence[index] = {**existing, **item}
                break
        else:
            evidence.append(item)
    return evidence[-80:]


def _evidence_key(item: dict[str, Any]) -> str:
    return str(item.get("url") or item.get("title") or "").strip().lower()


def _stats_from_activity(activity: dict[str, Any], previous: dict[str, Any]) -> dict[str, Any]:
    current = activity.get("stats") if isinstance(activity.get("stats"), dict) else {}
    if current:
        return dict(current)
    previous_stats = previous.get("stats") if isinstance(previous.get("stats"), dict) else {}
    return dict(previous_stats)


def _prune_old_jobs() -> None:
    jobs = _read_all_jobs()
    if len(jobs) <= _MAX_JOBS:
        return
    jobs.sort(key=lambda item: int(item.get("updated_at") or 0), reverse=True)
    keep = {str(item.get("id") or "") for item in jobs[:_MAX_JOBS]}
    for path in _jobs_dir().glob("*.json"):
        if path.stem not in keep:
            try:
                path.unlink()
            except OSError:
                pass


def _sanitize_job(job: dict[str, Any]) -> dict[str, Any]:
    row = dict(job or {})
    row["sources"] = _sanitize_sources(row.get("sources"))
    row["failures"] = _sanitize_sources(row.get("failures"), keep_status=True)
    row["attempts"] = _sanitize_attempts(row.get("attempts"))
    row["evidence"] = _sanitize_evidence(row.get("evidence"))
    row["report"] = _sanitize_report_body(_sanitize_report_links(str(row.get("report") or "")), row)
    if str(row.get("title") or "").strip().lower() in {"", "(untitled)", "untitled", "untitled research"}:
        row["title"] = str(row.get("query") or "Research Report")
    row["report_filename"] = str((row.get("report_filename") or _report_filename(row)) if row.get("report") else "")
    row["report_path"] = str(row.get("report_path") or "")
    row["artifact_id"] = str(row.get("artifact_id") or "")
    if str(row.get("report") or "").strip() and _report_file_missing(row):
        row.update(_write_report_markdown_file(row))
        row.update(_register_report_artifact(row, row))
    return row


def _report_file_missing(job: dict[str, Any]) -> bool:
    path = str(job.get("report_path") or "").strip()
    if not path:
        return True
    try:
        return not Path(path).is_file()
    except OSError:
        return True


def _sanitize_sources(value: Any, *, keep_status: bool = False) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for source in _list_of_dicts(value):
        row = dict(source)
        url = clean_user_source_url(str(row.get("url") or row.get("final_url") or row.get("href") or ""))
        domain = _clean_domain(row.get("domain"), url)
        row["url"] = url
        row["domain"] = domain
        row["title"] = _display_source_title(row.get("title"), url or domain)
        if row.get("snippet"):
            row["snippet"] = str(row.get("snippet") or "").strip()
        if not keep_status and str(row.get("status") or "").strip().lower() in {"read", "已读"}:
            row["status"] = "opened"
        rows.append(row)
    return rows


def _sanitize_attempts(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for attempt in _list_of_dicts(value):
        row = dict(attempt)
        if row.get("url"):
            row["url"] = clean_user_source_url(str(row.get("url") or ""))
        rows.append(row)
    return rows


def _sanitize_evidence(value: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in _list_of_dicts(value):
        row = dict(item)
        row["url"] = clean_user_source_url(str(row.get("url") or ""))
        row["title"] = _display_source_title(row.get("title"), row.get("url"))
        rows.append(row)
    return rows


def clean_user_source_url(value: str) -> str:
    """Return the user-facing URL, unwrapping internal Jina reader URLs."""
    text = unquote(str(value or "").strip())
    if not text:
        return ""
    for _ in range(8):
        repaired = re.sub(r"^http://(?=https?://)", "", text, flags=re.IGNORECASE)
        if repaired != text:
            text = repaired
            continue
        parsed = urlparse(text)
        if parsed.netloc.lower() in {"r.jina.ai", "www.r.jina.ai"}:
            rest = unquote((parsed.path or "").lstrip("/"))
            if parsed.query:
                rest = f"{rest}?{parsed.query}" if rest else parsed.query
            if rest and rest != text:
                text = rest
                continue
        match = re.match(r"^https?://r\.jina\.ai/(.+)$", text, flags=re.IGNORECASE)
        if match:
            text = unquote(match.group(1))
            continue
        break
    text = re.sub(r"^http://(?=https?://)", "", text, flags=re.IGNORECASE)
    parsed = urlparse(text)
    if parsed.scheme in {"http", "https"} and parsed.netloc:
        return urlunparse(parsed._replace(fragment=""))
    return text


def _display_source_title(title: Any, url_or_domain: Any = "") -> str:
    value = str(title or "").strip()
    lowered = value.lower()
    if value and lowered not in {"(untitled)", "untitled", "未命名来源"} and "r.jina.ai" not in lowered:
        return value
    url = clean_user_source_url(str(url_or_domain or ""))
    parsed = urlparse(url)
    host = parsed.netloc or str(url_or_domain or "").strip()
    host = re.sub(r"^www\.", "", host, flags=re.IGNORECASE)
    if host:
        path_tail = unquote((parsed.path or "").rstrip("/").rsplit("/", 1)[-1]) if parsed.path else ""
        if path_tail and "." not in path_tail[:8] and len(path_tail) <= 80:
            return f"{host} / {path_tail}"
        return host
    return "来源"


def _clean_domain(domain: Any, url: str) -> str:
    value = str(domain or "").strip()
    if value and "r.jina.ai" not in value.lower() and not value.lower().startswith(("http://", "https://")):
        return re.sub(r"^www\.", "", value, flags=re.IGNORECASE)
    parsed = urlparse(clean_user_source_url(url))
    return re.sub(r"^www\.", "", parsed.netloc, flags=re.IGNORECASE)


def _sanitize_report_links(report: str) -> str:
    text = str(report or "")
    if "r.jina.ai" not in text:
        return text
    return re.sub(r"https?://r\.jina\.ai/[^\s)>\]]+", lambda match: clean_user_source_url(match.group(0)), text)


def _sanitize_report_body(report: str, job: dict[str, Any] | None = None) -> str:
    """Keep durable reports user-facing; strip crawler/tool logs and legacy raw evidence dumps."""
    original = str(report or "")
    text = _TRUNCATED_MARKER_RE.sub("", original)
    if not text.strip():
        return ""
    output: list[str] = []
    skip_level = 0
    removed_raw_section = False
    for line in text.splitlines():
        stripped = line.strip()
        heading = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", stripped)
        if heading and skip_level:
            level = len(heading.group(1))
            if level <= skip_level:
                skip_level = 0
        if skip_level:
            continue
        if heading and _is_report_noise_heading(heading.group(2)):
            removed_raw_section = True
            skip_level = len(heading.group(1))
            continue
        if _is_report_noise_line(stripped):
            removed_raw_section = True
            continue
        output.append(line)
    cleaned = _normalize_markdown_spacing("\n".join(output))
    if _report_body_too_thin(cleaned) and (removed_raw_section or _looks_like_legacy_raw_report(original)):
        fallback = _fallback_report_body(job or {})
        if fallback:
            return fallback
    return cleaned


def _is_report_noise_heading(value: str) -> bool:
    text = str(value or "").strip()
    if re.fullmatch(r"(?:目录|目錄|table\s+of\s+contents|contents|toc|report|evidence\s+opened|raw\s+evidence)", text, flags=re.IGNORECASE):
        return True
    return bool(re.search(r"read\s+failures|读取失败|连接错误|错误来源|抓取失败|failed\s+reads?", text, flags=re.IGNORECASE))


def _is_report_noise_line(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _REPORT_NOISE_LINE_PATTERNS)


def _looks_like_legacy_raw_report(value: str) -> bool:
    text = str(value or "")
    return bool(re.search(r"Evidence Opened|Read Failures|^\s*-\s*Providers?\s*:", text, flags=re.IGNORECASE | re.MULTILINE))


def _report_body_too_thin(value: str) -> bool:
    body = "\n".join(line for line in str(value or "").splitlines() if line.strip() and not line.lstrip().startswith("#"))
    return len(body.strip()) < 80


def _normalize_markdown_spacing(value: str) -> str:
    lines = [line.rstrip() for line in str(value or "").splitlines()]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _fallback_report_body(job: dict[str, Any]) -> str:
    title = str(job.get("title") or job.get("query") or "研究报告").strip()
    sources = _list_of_dicts(job.get("sources"))
    evidence = _list_of_dicts(job.get("evidence"))
    if not sources and not evidence:
        return ""
    opened = len(sources) or len(evidence)
    lines = [
        f"# {title}",
        "",
        "## 摘要",
        "",
        f"本次研究已整理 {opened} 个可复查来源。旧版原始抓取日志已被清理，避免把网页导航、登录页、工具状态和抓取错误混入报告正文。",
        "",
        "## 初步证据",
        "",
    ]
    evidence_rows = evidence or sources
    for item in evidence_rows[:8]:
        label = _display_source_title(item.get("title"), item.get("url") or item.get("domain"))
        snippet = str(item.get("snippet") or item.get("text") or "").strip()
        snippet = re.sub(r"\s+", " ", snippet)[:220].strip()
        if snippet:
            lines.append(f"- **{label}**：{snippet}")
        else:
            lines.append(f"- **{label}**：该来源已收录，可在来源列表中打开复查。")
    lines.extend(["", "## 后续处理", "", "建议重新运行 Deep Research 以生成完整长文报告；当前文档保留为干净的来源索引和复查入口。"])
    return "\n".join(lines).strip()


def _write_report_markdown_file(job: dict[str, Any]) -> dict[str, str]:
    filename = _report_filename(job)
    path = _reports_dir() / filename
    try:
        path.write_text(_job_markdown(job), encoding="utf-8")
    except OSError:
        return {"report_filename": filename, "report_path": ""}
    return {"report_filename": filename, "report_path": str(path)}


def _register_report_artifact(job: dict[str, Any], context: dict[str, Any]) -> dict[str, str]:
    path = str(job.get("report_path") or "").strip()
    if not path:
        return {}
    metadata = {
        "job_id": str(job.get("id") or ""),
        "research_kind": str(job.get("kind") or "research"),
        "query": str(job.get("query") or ""),
        "providers": _list_of_strings(job.get("providers")),
        "source_count": len(_list_of_dicts(job.get("sources"))),
    }
    try:
        artifact = register_artifact(
            kind="report",
            title=str(job.get("title") or job.get("query") or job.get("report_filename") or "Research report"),
            path=path,
            mime="text/markdown",
            run_id=str(context.get("run_id") or context.get("runId") or ""),
            session_id=str(context.get("session_id") or context.get("sessionId") or ""),
            turn_id=str(context.get("turn_id") or context.get("turnId") or ""),
            source_event_id=str(context.get("source_event_id") or context.get("sourceEventId") or ""),
            source_tool_call_id=str(context.get("source_tool_call_id") or context.get("sourceToolCallId") or ""),
            metadata=metadata,
        )
    except (ArtifactRegistryError, OSError, ValueError):
        return {"artifact_id": str(job.get("artifact_id") or "")}
    return {"artifact_id": str(artifact.get("artifact_id") or "")}


def _report_filename(job: dict[str, Any]) -> str:
    base = str(job.get("title") or job.get("query") or "研究报告").strip()
    base = re.sub(r"[\\/:*?\"<>|\r\n\t]+", " ", base)
    base = re.sub(r"\s+", " ", base).strip(" .")
    if not base:
        base = "研究报告"
    if len(base) > 72:
        base = base[:72].rstrip()
    suffix = str(job.get("id") or "")[-8:]
    suffix = re.sub(r"[^A-Za-z0-9]+", "", suffix)
    return f"{base}{('-' + suffix) if suffix else ''}.md"

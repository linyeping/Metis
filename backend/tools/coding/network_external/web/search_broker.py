from __future__ import annotations

import concurrent.futures
import re
import json
import os
from html import unescape
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse, urlunparse

from backend.tools.coding.network_external.web.research_jobs import clean_user_source_url, save_research_activity_job
from backend.tools.coding.network_external.web.web_content import extract_html_markdown
from backend.tools.coding.network_external.web.web_fetch import _blocked_host

_USER_AGENT = "Mozilla/5.0 (compatible; Metis/3.0; +https://metis.local) AppleWebKit/537.36"
_TEXT_CONTENT_TYPES = ("text/", "json", "xml", "javascript")
_RESEARCH_PAYLOAD_SCHEMA = "metis.research_activity.v1"


def metis_search_query(
    query: str,
    max_results: int = 5,
    region: str = "",
    timelimit: str = "",
    provider: str = "auto",
) -> dict[str, Any]:
    value = str(query or "").strip()
    limit = _clamp_int(max_results, 1, 10, default=5)
    provider_chain = _provider_chain_for_request(provider)
    selected_provider = provider_chain[0]
    if not value:
        return {"ok": False, "query": value, "provider": selected_provider, "results": [], "error": "web_search 需要非空查询词。"}
    activity = _search_activity_payload(
        value,
        selected_provider,
        [],
        provider_chain=provider_chain,
        search_status="running",
        phase_summary="正在检索网络来源",
    )
    _persist_research_activity(activity, status="running")
    search_job_id = str(activity.get("job_id") or "")
    provider_errors: list[str] = []
    search_attempts: list[dict[str, Any]] = []
    raw_results: list[dict[str, Any]] = []
    effective_query = value
    try:
        for candidate in provider_chain:
            selected_provider = candidate
            hard_error = False
            for candidate_query in _search_query_variants(value):
                effective_query = candidate_query
                try:
                    raw_results = _search_provider_text(candidate, candidate_query, max_results=limit, region=region, timelimit=timelimit)
                except ImportError:
                    raise
                except Exception as exc:
                    if _is_no_results_error(exc):
                        search_attempts.append(
                            _search_attempt(candidate, candidate_query, status="no_results", error=str(exc)[:160])
                        )
                        raw_results = []
                        continue
                    provider_errors.append(f"{candidate}: {type(exc).__name__}: {str(exc)[:160]}")
                    raw_results = []
                    hard_error = True
                    break
                search_attempts.append(
                    _search_attempt(
                        candidate,
                        candidate_query,
                        status="complete" if raw_results else "no_results",
                        results=len(raw_results),
                    )
                )
                if raw_results:
                    break
            if raw_results or not hard_error:
                break
        else:
            if not search_attempts:
                raise RuntimeError("; ".join(provider_errors) or "no provider available")
    except ImportError:
        activity = _search_activity_payload(
            value,
            selected_provider,
            [],
            provider_chain=provider_chain,
            provider_errors=provider_errors,
            attempts=search_attempts,
            search_status="error",
            phase_summary="缺少 ddgs 依赖",
        )
        if search_job_id:
            activity["job_id"] = search_job_id
        activity["error"] = "web_search 需要安装 ddgs: pip install ddgs"
        _persist_research_activity(activity, report=_search_report(value, selected_provider, []), status="error")
        return {
            "ok": False,
            "query": value,
            "provider": selected_provider,
            "provider_chain": provider_chain,
            "results": [],
            "error": "web_search 需要安装 ddgs: pip install ddgs",
            "research_activity": activity,
        }
    except Exception as exc:
        error = f"web_search 查询失败: {type(exc).__name__}: {str(exc)[:240]}"
        activity = _search_activity_payload(
            value,
            selected_provider,
            [],
            provider_chain=provider_chain,
            provider_errors=provider_errors,
            attempts=search_attempts,
            search_status="error",
            phase_summary=error,
        )
        if search_job_id:
            activity["job_id"] = search_job_id
        _persist_research_activity(activity, report=error, status="error")
        return {
            "ok": False,
            "query": value,
            "provider": selected_provider,
            "provider_chain": provider_chain,
            "results": [],
            "error": error,
            "research_activity": activity,
        }

    results = _normalize_search_results(raw_results, limit)
    relaxed_query = effective_query != value
    if results:
        phase_summary = f"{selected_provider} 返回 {len(results)} 条结果"
        if relaxed_query:
            phase_summary = f"已放宽检索，{phase_summary}"
    else:
        phase_summary = "未找到匹配来源，已尝试放宽查询" if relaxed_query else "未找到匹配来源"
    activity = _search_activity_payload(
        value,
        selected_provider,
        results,
        provider_chain=provider_chain,
        provider_errors=provider_errors,
        attempts=search_attempts,
        search_status="complete",
        phase_summary=phase_summary,
    )
    if search_job_id:
        activity["job_id"] = search_job_id
    _persist_research_activity(activity, report=_search_report(value, selected_provider, results))
    return {
        "ok": True,
        "query": value,
        "provider": selected_provider,
        "provider_chain": provider_chain,
        "provider_errors": provider_errors,
        "attempts": search_attempts,
        "effective_query": effective_query,
        "results": results,
        "count": len(results),
        "research_activity": activity,
    }


_PARTIAL_TEXT_THRESHOLD = 200
_BLOCKED_HTTP_STATUS = {401, 403, 405, 429, 451}


def metis_page_read(url: str, max_chars: int = 4000) -> dict[str, Any]:
    target = str(url or "").strip()
    limit = _clamp_int(max_chars, 1000, 12000, default=4000)
    parsed = urlparse(target)
    if parsed.scheme.lower() != "https":
        return {"ok": False, "status": "blocked", "url": target, "error": "只允许读取 https:// URL。"}
    if not parsed.hostname:
        return {"ok": False, "status": "blocked", "url": target, "error": "URL 缺少合法主机名。"}
    reason = _blocked_host(parsed.hostname)
    if reason:
        return {
            "ok": False,
            "status": "blocked",
            "url": target,
            "error": f"禁止访问该主机（SSRF 防护: {reason}）。",
        }

    try:
        import requests

        response = requests.get(target, timeout=25, allow_redirects=True, headers={"User-Agent": _USER_AGENT})
        final_url = response.url
        final = urlparse(final_url)
        if final.scheme.lower() != "https":
            return {
                "ok": False,
                "status": "blocked",
                "url": target,
                "final_url": final_url,
                "error": "重定向到了非 HTTPS，已中止。",
            }
        if final.hostname:
            final_reason = _blocked_host(final.hostname)
            if final_reason:
                return {
                    "ok": False,
                    "status": "blocked",
                    "url": target,
                    "final_url": final_url,
                    "error": f"重定向目标主机被拒绝（SSRF: {final_reason}）。",
                }
        status_code = getattr(response, "status_code", None)
        if status_code in _BLOCKED_HTTP_STATUS:
            return {
                "ok": False,
                "status": "blocked",
                "url": target,
                "final_url": final_url,
                "error": f"目标站点拒绝访问（HTTP {status_code}，可能限流或需要登录）。",
            }
        response.raise_for_status()
    except ModuleNotFoundError:
        return {
            "ok": False,
            "status": "error",
            "url": target,
            "error": "页面读取需要安装 requests: pip install requests",
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "error",
            "url": target,
            "error": f"页面读取失败: {type(exc).__name__}: {str(exc)[:240]}",
        }

    body = _decode_body(response.content)
    content_type = response.headers.get("content-type", "")
    title = _extract_title(body) if _is_html(content_type, body) else ""
    text = extract_html_markdown(body, base_url=response.url) if _is_html(content_type, body) else body
    if not _is_text_content(content_type) and not _is_html(content_type, body):
        text = f"Binary content omitted ({len(response.content)} bytes, content-type: {content_type or 'unknown'})."
    cleaned = _normalize_page_text(text)
    status = "ok" if len(cleaned) >= _PARTIAL_TEXT_THRESHOLD else "partial"
    result = {
        "ok": True,
        "status": status,
        "url": target,
        "final_url": response.url,
        "title": title,
        "content_type": content_type or "text/html",
        "text": _truncate(cleaned, limit),
        "chars": len(cleaned),
        "truncated": len(cleaned) > limit,
    }
    if status == "partial":
        result["note"] = "提取内容过短，可能是动态/JS 渲染页面，证据不完整，引用时需谨慎。"
    return result


def metis_search_research(
    question: str,
    max_results: int = 5,
    max_pages: int = 3,
    max_chars_per_page: int = 1800,
    provider: str = "auto",
) -> dict[str, Any]:
    question_value = str(question or "").strip()
    page_limit = _clamp_int(max_pages, 1, 5, default=3)
    activity = _research_activity_payload(question_value, {}, [], [], stage="searching", page_limit=page_limit)
    _persist_research_activity(activity, status="running")
    research_job_id = str(activity.get("job_id") or "")

    search = metis_search_query(question_value, max_results=max_results, provider=provider)
    if not search.get("ok"):
        activity = _research_activity_payload(question_value, search, [], [], stage="error", page_limit=page_limit)
        if research_job_id:
            activity["job_id"] = research_job_id
        _persist_research_activity(activity, report=f"Research failed: {search.get('error') or 'unknown error'}", status="error")
        return {"ok": False, "question": question_value, "search": search, "pages": [], "failures": [], "research_activity": activity}

    pages: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    activity = _research_activity_payload(question_value, search, pages, failures, stage="reading", page_limit=page_limit)
    if research_job_id:
        activity["job_id"] = research_job_id
    _persist_research_activity(activity, status="running")

    candidates = _research_page_candidates(search.get("results") if isinstance(search.get("results"), list) else [])
    batch_size = max(1, page_limit)
    max_workers = max(1, min(4, page_limit))
    char_limit = _clamp_int(max_chars_per_page, 800, 2500, default=1800)
    for offset in range(0, len(candidates), batch_size):
        if len(pages) >= page_limit:
            break
        batch = candidates[offset : offset + batch_size]
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, len(batch))) as executor:
            futures = [
                (index, result, url, executor.submit(metis_page_read, url, max_chars=char_limit))
                for index, result, url in batch
            ]
            rows: list[tuple[int, dict[str, Any], str, dict[str, Any]]] = []
            for index, result, url, future in futures:
                try:
                    page = future.result()
                except Exception as exc:
                    page = {"ok": False, "status": "error", "url": url, "error": f"{type(exc).__name__}: {str(exc)[:180]}"}
                rows.append((index, result, url, page))
        for _index, result, url, page in sorted(rows, key=lambda item: item[0]):
            if len(pages) >= page_limit:
                break
            if page.get("ok"):
                pages.append({"search_result": result, **page})
            else:
                failures.append(
                    {
                        "url": url,
                        "status": str(page.get("status") or "error"),
                        "error": str(page.get("error") or "unknown error"),
                    }
                )
        activity = _research_activity_payload(question_value, search, pages, failures, stage="reading", page_limit=page_limit)
        if research_job_id:
            activity["job_id"] = research_job_id
        _persist_research_activity(activity, status="running")

    activity = _research_activity_payload(question_value, search, pages, failures, stage="complete", page_limit=page_limit)
    if research_job_id:
        activity["job_id"] = research_job_id
    final_status = "complete" if pages and not failures else "partial" if pages or failures else "partial"
    _persist_research_activity(activity, report=_research_report(question_value, pages, failures), status=final_status)
    return {
        "ok": True,
        "question": question_value,
        "search": search,
        "pages": pages,
        "failures": failures,
        "research_activity": activity,
    }


def _research_page_candidates(results: list[Any]) -> list[tuple[int, dict[str, Any], str]]:
    seen: set[str] = set()
    candidates: list[tuple[int, dict[str, Any], str]] = []
    for index, result in enumerate(results):
        if not isinstance(result, dict):
            continue
        url = str(result.get("url") or "")
        key = _canonical_url_key(url)
        if not url or key in seen:
            continue
        seen.add(key)
        candidates.append((index, result, url))
    return candidates


def format_search_response(search: dict[str, Any]) -> str:
    query = str(search.get("query") or "")
    if not search.get("ok"):
        return f"❌ web_search 失败: {search.get('error') or 'unknown error'}"
    lines = [f"=== Web Search: {query!r} ===", f"Provider: {search.get('provider') or 'unknown'}"]
    results = search.get("results") or []
    if not results:
        lines.append("未找到可用结果。")
        return _attach_research_payload("\n".join(lines), search.get("research_activity"))
    for item in results:
        snippet = str(item.get("snippet") or "").strip()
        url = _source_url(item.get("url"))
        lines.append(
            f"[{item.get('rank')}] {_source_title(item.get('title'), url)}\n"
            f"URL: {url}\n"
            f"Snippet: {snippet or '(no snippet)'}"
        )
    return _attach_research_payload("\n\n".join(lines), search.get("research_activity"))


def format_research_response(research: dict[str, Any]) -> str:
    question = str(research.get("question") or "")
    search = research.get("search") or {}
    if not research.get("ok"):
        return f"❌ web_research 失败: {(search or {}).get('error') or 'unknown error'}"
    if not isinstance(research.get("research_activity"), dict):
        pages_for_payload = research.get("pages") if isinstance(research.get("pages"), list) else []
        failures_for_payload = research.get("failures") if isinstance(research.get("failures"), list) else []
        research = {
            **research,
            "research_activity": _research_activity_payload(question, search if isinstance(search, dict) else {}, pages_for_payload, failures_for_payload),
        }

    lines = [
        f"=== Web Research: {question!r} ===",
        f"Provider: {search.get('provider') or 'unknown'}",
        "Report status: saved to Metis Research job store.",
        "Chat output policy: answer briefly in chat; do not paste a full research report or long source list. "
        "Use the saved report view for the durable report, outline, sources, copy, and export.",
    ]
    results = search.get("results") or []
    lines.append(f"Search results: {len(results)}")

    pages = research.get("pages") or []
    failures = research.get("failures") or []
    partial_pages = [page for page in pages if page.get("status") == "partial"]
    lines.append(f"Evidence pages opened: {len(pages)}")
    if failures:
        lines.append(f"Read failures: {len(failures)}")
    if partial_pages:
        lines.append(f"Partial evidence: {len(partial_pages)}")
    if not pages:
        lines.append("No evidence pages could be read. Use browse_web for dynamic/blocked pages.")
    for index, page in enumerate(pages[:5], start=1):
        source = page.get("search_result") or {}
        final_url = _source_url(page.get("final_url") or page.get("url") or source.get("url"))
        title = _source_title(page.get("title") or source.get("title"), final_url)
        text = str(page.get("text") or "").strip()
        status = str(page.get("status") or "ok")
        status_tag = " [PARTIAL EVIDENCE - 内容过短/可能不完整，引用前要谨慎]" if status == "partial" else ""
        excerpt = _truncate(_normalize_page_text(text), 600)
        lines.append(
            f"\n[{index}] {title}{status_tag}\n"
            f"URL: {final_url}\n"
            f"Excerpt:\n{excerpt or '(empty extracted text)'}"
        )
    if failures:
        lines.append("\nRead failures:")
        for failure in failures:
            status = str(failure.get("status") or "error")
            label = "blocked/rate-limited" if status == "blocked" else "error"
            lines.append(f"- {_source_url(failure.get('url'))} [{label}]: {failure.get('error')}")
        lines.append(
            "\nDo not silently drop blocked/rate-limited sources — if a claim depends on one of "
            "them, tell the user it could not be verified instead of citing it anyway."
        )
    lines.append(
        "\nUse the evidence URLs above for a concise answer. Do not cite search snippets as final proof. "
        "Never cite a [PARTIAL EVIDENCE] page as if it were complete."
    )
    return _attach_research_payload("\n".join(lines), research.get("research_activity"))


def research_payload_comment(payload: dict[str, Any] | None) -> str:
    if not payload:
        return ""
    try:
        return f"<!-- METIS_RESEARCH_JSON {json.dumps(payload, ensure_ascii=False, separators=(',', ':'))} -->"
    except Exception:
        return ""


def _attach_research_payload(text: str, payload: Any) -> str:
    comment = research_payload_comment(payload if isinstance(payload, dict) else None)
    return f"{comment}\n{text}" if comment else text


def _persist_research_activity(payload: dict[str, Any], *, report: str = "", status: str = "complete", evidence: list[dict[str, Any]] | None = None) -> None:
    try:
        job = save_research_activity_job(payload, report=report, status=status, evidence=evidence)
        payload["job_id"] = job.get("id") or ""
        payload["job_status"] = job.get("status") or status
        payload["report_filename"] = job.get("report_filename") or ""
        payload["report_path"] = job.get("report_path") or ""
    except Exception as exc:
        payload["job_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"


def _normalize_search_provider(provider: str) -> str:
    value = str(provider or "auto").strip().lower()
    if value in {"", "auto"}:
        return "ddgs"
    if value in {"duckduckgo", "duckduckgo-search"}:
        return "ddgs"
    return value


def _provider_chain_for_request(provider: str) -> list[str]:
    value = str(provider or "auto").strip().lower()
    if value in {"", "auto"}:
        configured = [
            _normalize_search_provider(item)
            for item in re.split(r"[,;\s]+", os.environ.get("METIS_WEB_SEARCH_PROVIDERS", "ddgs"))
            if item.strip()
        ]
        return _dedupe_provider_chain(configured or ["ddgs"])
    return [_normalize_search_provider(value)]


def _dedupe_provider_chain(items: list[str]) -> list[str]:
    seen: set[str] = set()
    chain: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        chain.append(item)
    return chain or ["ddgs"]


def _search_provider_text(provider: str, query: str, *, max_results: int, region: str = "", timelimit: str = "") -> list[dict[str, Any]]:
    if provider == "ddgs":
        return _ddgs_text(query, max_results=max_results, region=region, timelimit=timelimit)
    raise ValueError(f"unsupported search provider: {provider}")


def _search_query_variants(query: str) -> list[str]:
    value = " ".join(str(query or "").split())
    if not value:
        return []
    site_matches = re.findall(r"(?i)(?:^|\s)site:([^\s]+)", value)
    if not site_matches:
        return [value]

    stripped = " ".join(re.sub(r"(?i)(?:^|\s)site:[^\s]+", " ", value).split())
    variants = [value]
    for domain in site_matches:
        clean_domain = re.sub(r"^[\"'(<\[]+|[\"')>\],.]+$", "", domain.strip().lower())
        if not clean_domain:
            continue
        if stripped:
            variants.append(f"{clean_domain} {stripped}")
            relaxed_domain = _relaxed_site_domain(clean_domain)
            if relaxed_domain and relaxed_domain != clean_domain:
                variants.append(f"{relaxed_domain} {stripped}")
        else:
            variants.append(clean_domain)
    if stripped:
        variants.append(stripped)
    return _dedupe_text_items(variants)


def _relaxed_site_domain(domain: str) -> str:
    parts = [part for part in domain.split(".") if part]
    if len(parts) <= 2:
        return domain
    if domain.endswith(".gov.cn") and len(parts) >= 3:
        return ".".join(parts[-3:])
    if domain.startswith("m.") or domain.startswith("www."):
        return ".".join(parts[1:])
    return ".".join(parts[-2:])


def _dedupe_text_items(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        value = " ".join(str(item or "").split())
        key = value.lower()
        if not value or key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


def _is_no_results_error(exc: Exception) -> bool:
    message = f"{type(exc).__name__}: {str(exc)}".lower()
    return "no results" in message or "no result" in message


def _search_attempt(
    provider: str,
    query: str,
    *,
    status: str,
    results: int = 0,
    error: str = "",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "provider": provider,
        "query": query,
        "status": status,
        "results": max(0, int(results or 0)),
    }
    if error:
        row["error"] = error
    return row


def _search_report(query: str, provider: str, results: list[dict[str, str | int]]) -> str:
    lines = [f"Search query: {query}", f"Provider: {provider}", ""]
    for item in results:
        url = _source_url(item.get("url"))
        lines.append(f"- {_source_title(item.get('title'), url)}")
        lines.append(f"  {url}")
        snippet = str(item.get("snippet") or "").strip()
        if snippet:
            lines.append(f"  {snippet}")
    return "\n".join(lines).strip()


def _research_report(question: str, pages: list[dict[str, Any]], failures: list[dict[str, str]]) -> str:
    lines = [f"# {question or 'Research Report'}", "", "## Evidence Opened", ""]
    if pages:
        for page in pages:
            title = _source_title(page.get("title") or (page.get("search_result") or {}).get("title"), page.get("final_url") or page.get("url"))
            url = _source_url(page.get("final_url") or page.get("url"))
            status = page.get("status") or "ok"
            lines.append(f"### {title}")
            lines.append("")
            lines.append(f"- URL: {url}")
            lines.append(f"- Status: {status}")
            text = str(page.get("text") or "").strip()
            if text:
                lines.extend(["", _truncate(_normalize_page_text(text), 1600), ""])
    else:
        lines.append("No readable evidence pages were opened.")
    if failures:
        lines.extend(["", "## Read Failures", ""])
        for failure in failures:
            lines.append(f"- {_source_url(failure.get('url'))} [{failure.get('status')}]: {failure.get('error')}")
    return "\n".join(lines).strip()


def _search_activity_payload(
    query: str,
    provider: str,
    results: list[dict[str, str | int]],
    *,
    provider_chain: list[str] | None = None,
    provider_errors: list[str] | None = None,
    attempts: list[dict[str, Any]] | None = None,
    search_status: str = "complete",
    phase_summary: str = "",
) -> dict[str, Any]:
    sources = [
        {
            "id": f"s{index}",
            "rank": item.get("rank"),
            "title": _source_title(item.get("title"), item.get("url")),
            "url": _source_url(item.get("url")),
            "domain": _source_domain(item.get("source"), item.get("url")),
            "snippet": item.get("snippet") or "",
            "status": "search_result",
        }
        for index, item in enumerate(results, start=1)
    ]
    return {
        "schema": _RESEARCH_PAYLOAD_SCHEMA,
        "kind": "search",
        "title": str(query or "").strip(),
        "query": str(query or "").strip(),
        "providers": provider_chain or [provider],
        "stats": {"search_results": len(results), "sources": len(sources), "opened": 0, "failures": 0},
        "phases": [
            {
                "id": "search",
                "label": "搜索网络",
                "status": search_status,
                "count": len(results),
                "summary": phase_summary or f"{provider} 返回 {len(results)} 条结果",
            },
        ],
        "sources": sources,
        "attempts": attempts or [],
        "provider_errors": provider_errors or [],
    }


def _research_activity_payload(
    question: str,
    search: dict[str, Any],
    pages: list[dict[str, Any]],
    failures: list[dict[str, str]],
    *,
    stage: str = "complete",
    page_limit: int = 0,
) -> dict[str, Any]:
    results = search.get("results") if isinstance(search, dict) else []
    results = results if isinstance(results, list) else []
    opened_by_url: dict[str, dict[str, Any]] = {}
    for page in pages:
        for candidate in (page.get("url"), page.get("final_url")):
            key = _canonical_url_key(_source_url(candidate))
            if key:
                opened_by_url[key] = page
    failed_by_url = {_canonical_url_key(_source_url(failure.get("url"))): failure for failure in failures}
    sources: list[dict[str, Any]] = []
    for index, item in enumerate(results, start=1):
        if not isinstance(item, dict):
            continue
        url = _source_url(item.get("url"))
        key = _canonical_url_key(url)
        page = opened_by_url.get(key)
        failure = failed_by_url.get(key)
        page_url = _source_url((page or {}).get("final_url") or (page or {}).get("url") or url)
        sources.append(
            {
                "id": f"s{index}",
                "rank": item.get("rank") or index,
                "title": _source_title((page or {}).get("title") or item.get("title"), page_url),
                "url": page_url,
                "domain": _source_domain(item.get("source"), page_url),
                "snippet": item.get("snippet") or "",
                "status": "opened" if page else "failed" if failure else "search_result",
                "evidence_status": (page or {}).get("status") or (failure or {}).get("status") or "",
                "chars": (page or {}).get("chars") or 0,
                "error": (failure or {}).get("error") or "",
            }
        )
    return {
        "schema": _RESEARCH_PAYLOAD_SCHEMA,
        "kind": "research",
        "title": str(question or "").strip(),
        "query": str(question or "").strip(),
        "providers": search.get("provider_chain") or [search.get("provider") or "unknown"],
        "stats": {
            "search_results": len(results),
            "sources": len(sources),
            "opened": len(pages),
            "failures": len(failures),
            "partial": sum(1 for page in pages if page.get("status") == "partial"),
        },
        "phases": _research_phases(str(question or "").strip(), search, results, pages, failures, stage=stage, page_limit=page_limit),
        "sources": sources,
        "failures": failures,
    }


def _research_phases(
    question: str,
    search: dict[str, Any],
    results: list[Any],
    pages: list[dict[str, Any]],
    failures: list[dict[str, str]],
    *,
    stage: str,
    page_limit: int,
) -> list[dict[str, Any]]:
    total_results = len(results)
    target_pages = min(page_limit or total_results, total_results) if total_results else page_limit
    opened = len(pages)
    failed = len(failures)
    if stage == "searching":
        return [
            {"id": "plan", "label": "明确检索方向", "status": "complete", "summary": question},
            {"id": "search", "label": "搜索网络", "status": "running", "summary": "正在检索网络来源"},
            {"id": "browse", "label": "浏览来源", "status": "queued", "summary": "等待搜索结果"},
            {"id": "evidence", "label": "整理证据", "status": "queued", "summary": "等待可读来源"},
        ]
    if stage == "reading":
        return [
            {"id": "plan", "label": "明确检索方向", "status": "complete", "summary": question},
            {"id": "search", "label": "搜索网络", "status": "complete", "count": total_results},
            {
                "id": "browse",
                "label": "浏览来源",
                "status": "running",
                "count": opened,
                "failed": failed,
                "summary": f"已读取 {opened}/{target_pages or total_results or 0} 个来源",
            },
            {
                "id": "evidence",
                "label": "整理证据",
                "status": "running" if opened else "queued",
                "count": opened,
                "summary": "正在整理可引用证据" if opened else "等待页面内容",
            },
        ]
    if stage == "error":
        return [
            {"id": "plan", "label": "明确检索方向", "status": "complete", "summary": question},
            {"id": "search", "label": "搜索网络", "status": "error", "summary": str(search.get("error") or "搜索失败")},
            {"id": "browse", "label": "浏览来源", "status": "skipped"},
            {"id": "evidence", "label": "整理证据", "status": "skipped"},
        ]
    browse_status = "complete" if opened else "partial" if failed or total_results else "partial"
    evidence_status = "complete" if opened and not failed else "partial" if opened or failed or total_results else "partial"
    return [
        {"id": "plan", "label": "明确检索方向", "status": "complete", "summary": question},
        {"id": "search", "label": "搜索网络", "status": "complete" if search.get("ok", True) else "error", "count": total_results},
        {"id": "browse", "label": "浏览来源", "status": browse_status, "count": opened, "failed": failed},
        {"id": "evidence", "label": "整理证据", "status": evidence_status, "count": opened},
    ]


def _ddgs_text(query: str, *, max_results: int, region: str = "", timelimit: str = "") -> list[dict[str, Any]]:
    from ddgs import DDGS

    kwargs: dict[str, Any] = {"max_results": max_results}
    if region:
        kwargs["region"] = region
    if timelimit:
        kwargs["timelimit"] = timelimit
    try:
        with DDGS() as client:
            return list(client.text(query, **kwargs) or [])
    except TypeError:
        with DDGS() as client:
            return list(client.text(query, max_results=max_results) or [])


def _normalize_search_results(raw_results: list[dict[str, Any]], limit: int) -> list[dict[str, str | int]]:
    normalized: list[dict[str, str | int]] = []
    seen: set[str] = set()
    for raw in raw_results:
        if not isinstance(raw, dict):
            continue
        url = _source_url(_clean_result_url(str(raw.get("href") or raw.get("url") or "")))
        if not url:
            continue
        key = _canonical_url_key(url)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "rank": len(normalized) + 1,
                "title": _source_title(_clean_text(raw.get("title") or raw.get("heading") or ""), url),
                "url": url,
                "snippet": _clean_text(raw.get("body") or raw.get("snippet") or raw.get("description") or ""),
                "source": urlparse(url).netloc,
            }
        )
        if len(normalized) >= limit:
            break
    return normalized


def _clean_result_url(url: str) -> str:
    value = unescape(str(url or "").strip())
    if not value:
        return ""
    parsed = urlparse(value)
    if "duckduckgo.com" in parsed.netloc and parsed.query:
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            value = unquote(target)
            parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"}:
        return ""
    return urlunparse(parsed._replace(fragment=""))


def _canonical_url_key(url: str) -> str:
    parsed = urlparse(str(url or ""))
    return urlunparse(parsed._replace(fragment="", scheme=parsed.scheme.lower(), netloc=parsed.netloc.lower()))


def _clean_text(value: Any) -> str:
    return _normalize_text(unescape(str(value or "")))


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _source_url(value: Any) -> str:
    return clean_user_source_url(str(value or ""))


def _source_domain(domain: Any, url: Any) -> str:
    value = str(domain or "").strip()
    if value and "r.jina.ai" not in value.lower() and not value.lower().startswith(("http://", "https://")):
        return re.sub(r"^www\.", "", value, flags=re.IGNORECASE)
    parsed = urlparse(_source_url(url))
    return re.sub(r"^www\.", "", parsed.netloc, flags=re.IGNORECASE)


def _source_title(title: Any, url_or_domain: Any = "") -> str:
    value = _clean_text(title)
    lowered = value.lower()
    if value and lowered not in {"(untitled)", "untitled", "未命名来源"} and "r.jina.ai" not in lowered:
        return value
    url = _source_url(url_or_domain)
    parsed = urlparse(url)
    host = re.sub(r"^www\.", "", parsed.netloc, flags=re.IGNORECASE)
    if host:
        tail = unquote((parsed.path or "").rstrip("/").rsplit("/", 1)[-1]) if parsed.path else ""
        if tail and len(tail) <= 80 and not re.match(r"^[a-z0-9_-]+\.(html?|shtml|php|aspx?)$", tail, re.IGNORECASE):
            return f"{host} / {tail}"
        return host
    return "来源"


def _normalize_page_text(value: str) -> str:
    text = re.sub(r"[ \t]+", " ", str(value or ""))
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip() + f"\n\n[... truncated {len(text) - max_chars} chars ...]"


def _extract_title(html_text: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", html_text, flags=re.IGNORECASE | re.DOTALL)
    return _clean_text(match.group(1)) if match else ""


def _is_html(content_type: str, text: str) -> bool:
    lowered = (content_type or "").lower()
    return "html" in lowered or "<html" in text[:500].lower() or "<!doctype html" in text[:500].lower()


def _is_text_content(content_type: str) -> bool:
    lowered = (content_type or "").lower()
    return any(marker in lowered for marker in _TEXT_CONTENT_TYPES)


def _decode_body(body: bytes) -> str:
    for encoding in ("utf-8", "utf-16", "gb18030", "latin-1"):
        try:
            return body.decode(encoding)
        except UnicodeDecodeError:
            continue
    return body.decode("utf-8", errors="replace")


def _clamp_int(value: Any, lower: int, upper: int, *, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(lower, min(upper, parsed))

from __future__ import annotations

import re
from html import unescape
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse, urlunparse

from backend.tools.coding.network_external.web.web_content import extract_html_markdown
from backend.tools.coding.network_external.web.web_fetch import _blocked_host

_USER_AGENT = "Mozilla/5.0 (compatible; Metis/3.0; +https://metis.local) AppleWebKit/537.36"
_TEXT_CONTENT_TYPES = ("text/", "json", "xml", "javascript")


def metis_search_query(
    query: str,
    max_results: int = 5,
    region: str = "",
    timelimit: str = "",
) -> dict[str, Any]:
    value = str(query or "").strip()
    limit = _clamp_int(max_results, 1, 10, default=5)
    if not value:
        return {"ok": False, "query": value, "results": [], "error": "web_search 需要非空查询词。"}
    try:
        raw_results = _ddgs_text(value, max_results=limit, region=region, timelimit=timelimit)
    except ImportError:
        return {
            "ok": False,
            "query": value,
            "results": [],
            "error": "web_search 需要安装 ddgs: pip install ddgs",
        }
    except Exception as exc:
        return {
            "ok": False,
            "query": value,
            "results": [],
            "error": f"web_search 查询失败: {type(exc).__name__}: {str(exc)[:240]}",
        }

    results = _normalize_search_results(raw_results, limit)
    return {"ok": True, "query": value, "results": results, "count": len(results)}


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
    max_chars_per_page: int = 3000,
) -> dict[str, Any]:
    search = metis_search_query(question, max_results=max_results)
    if not search.get("ok"):
        return {"ok": False, "question": question, "search": search, "pages": [], "failures": []}

    seen: set[str] = set()
    pages: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    page_limit = _clamp_int(max_pages, 1, 5, default=3)
    for result in search.get("results", []):
        url = str(result.get("url") or "")
        key = _canonical_url_key(url)
        if not url or key in seen:
            continue
        seen.add(key)
        page = metis_page_read(url, max_chars=max_chars_per_page)
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
        if len(pages) >= page_limit:
            break

    return {
        "ok": True,
        "question": str(question or "").strip(),
        "search": search,
        "pages": pages,
        "failures": failures,
    }


def format_search_response(search: dict[str, Any]) -> str:
    query = str(search.get("query") or "")
    if not search.get("ok"):
        return f"❌ web_search 失败: {search.get('error') or 'unknown error'}"
    lines = [f"=== Web Search: {query!r} ==="]
    results = search.get("results") or []
    if not results:
        lines.append("未找到可用结果。")
        return "\n".join(lines)
    for item in results:
        snippet = str(item.get("snippet") or "").strip()
        lines.append(
            f"[{item.get('rank')}] {item.get('title') or '(untitled)'}\n"
            f"URL: {item.get('url')}\n"
            f"Snippet: {snippet or '(no snippet)'}"
        )
    return "\n\n".join(lines)


def format_research_response(research: dict[str, Any]) -> str:
    question = str(research.get("question") or "")
    search = research.get("search") or {}
    if not research.get("ok"):
        return f"❌ web_research 失败: {(search or {}).get('error') or 'unknown error'}"

    lines = [f"=== Web Research: {question!r} ==="]
    results = search.get("results") or []
    lines.append(f"Search results: {len(results)}")
    for item in results:
        lines.append(f"- [{item.get('rank')}] {item.get('title') or '(untitled)'} — {item.get('url')}")

    pages = research.get("pages") or []
    lines.append(f"\nEvidence pages opened: {len(pages)}")
    if not pages:
        lines.append("No evidence pages could be read. Use browse_web for dynamic/blocked pages.")
    for index, page in enumerate(pages, start=1):
        source = page.get("search_result") or {}
        title = page.get("title") or source.get("title") or "(untitled)"
        final_url = page.get("final_url") or page.get("url")
        text = str(page.get("text") or "").strip()
        status = str(page.get("status") or "ok")
        status_tag = " [PARTIAL EVIDENCE - 内容过短/可能不完整，引用前要谨慎]" if status == "partial" else ""
        lines.append(
            f"\n[{index}] {title}{status_tag}\n"
            f"URL: {final_url}\n"
            f"Content type: {page.get('content_type')}\n"
            f"Excerpt:\n{text or '(empty extracted text)'}"
        )

    failures = research.get("failures") or []
    if failures:
        lines.append("\nRead failures:")
        for failure in failures:
            status = str(failure.get("status") or "error")
            label = "blocked/rate-limited" if status == "blocked" else "error"
            lines.append(f"- {failure.get('url')} [{label}]: {failure.get('error')}")
        lines.append(
            "\nDo not silently drop blocked/rate-limited sources — if a claim depends on one of "
            "them, tell the user it could not be verified instead of citing it anyway."
        )
    lines.append("\nUse the evidence URLs above for citations; do not cite search snippets as final proof. "
                 "Never cite a [PARTIAL EVIDENCE] page as if it were complete.")
    return "\n".join(lines)


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
        url = _clean_result_url(str(raw.get("href") or raw.get("url") or ""))
        if not url:
            continue
        key = _canonical_url_key(url)
        if key in seen:
            continue
        seen.add(key)
        normalized.append(
            {
                "rank": len(normalized) + 1,
                "title": _clean_text(raw.get("title") or raw.get("heading") or url),
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

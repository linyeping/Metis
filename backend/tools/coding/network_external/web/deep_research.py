"""Deep Research pipeline — plan → query → gather → grade → outline → synthesize.

对话驱动的两阶段深度研究：
  1. deep_research_plan(question)  → 生成研究计划（brief + 子问题），在聊天里给用户确认
  2. deep_research_run(question, plan_json) → 执行完整 6 阶段流水线并落地 research job

设计要点（对齐 Gemini Deep Research / LangChain Open Deep Research 的经验）：
  - 研究阶段并行（每个子问题一个 worker，隔离上下文）
  - 写作阶段单次 LLM 调用（避免分节并行导致风格割裂）
  - 任意 LLM 阶段失败 → 降级到 metis_search_research，保证不劣化
"""
from __future__ import annotations

import concurrent.futures
import json
import re
import time
from typing import Any, Callable
from urllib.parse import unquote, urlparse

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution
from backend.tools.coding.network_external.web.evidence_review import review_evidence_item
from backend.tools.coding.network_external.web.question_type import classify_question
from backend.tools.coding.network_external.web.report_review import build_official_absence_report, review_report
from backend.tools.coding.network_external.web.research_jobs import save_research_activity_job
from backend.tools.coding.network_external.web.search_broker import (
    _canonical_url_key,
    _clamp_int,
    _normalize_page_text,
    _provider_chain_for_request,
    _source_domain,
    _source_title,
    _source_url,
    format_research_response,
    metis_page_read,
    metis_search_query,
    metis_search_research,
)
from backend.tools.coding.network_external.web.source_policy import (
    classify_source,
    domain_hint_matches_host as policy_domain_hint_matches_host,
    host_for_url as policy_host_for_url,
    official_domains_for_question,
    spam_hit_count as policy_spam_hit_count,
    url_matches_domain_hint as policy_url_matches_domain_hint,
)

_SCHEMA = "metis.research_activity.v1"
_MAX_SUBQUESTIONS = 6
_MIN_SUBQUESTIONS = 2
_MAX_QUERIES_PER_SUB = 3
_GRADE_KEEP_THRESHOLD = 0.35
_MIN_EVIDENCE_CHARS = 120

_NETWORK_ERROR_PATTERNS = [
    r"\bConnection(?:Aborted)?Error\b",
    r"\bHTTPS?ConnectionPool\b",
    r"\bMax retries exceeded\b",
    r"\bSSLError\b",
    r"\bSSLCertVerificationError\b",
    r"\bUNEXPECTED_EOF_WHILE_READING\b",
    r"\bCERTIFICATE_VERIFY_FAILED\b",
    r"\bReadTimeout\b",
    r"\bProxyError\b",
    r"\bErrno\s*\d+\b",
    r"页面读取失败",
    r"你的主机中的软件中止了一个已建立的连接",
]

_SPAM_PATTERNS = [
    r"教程网",
    r"下载",
    r"安装包",
    r"破解",
    r"激活码",
    r"镜像站",
    r"会员\s*充值",
    r"代充",
    r"出售\s*账号",
    r"账号\s*出售",
    r"客服\s*微信",
    r"微信号",
    r"请添加客服",
    r"点击可跳转",
    r"海外账号",
    r"低价(?:充值|代充|账号)",
    r"gpthuiyuan",
    r"谷歌\s*Gemini\s*教程",
    r"Google\s+Gemini\s+常见问题",
]

_OFFICIAL_SOURCE_HINTS: list[tuple[str, tuple[str, ...]]] = [
    (
        r"\b(gemini|gemma|deepmind|google\s+ai|google)\b|谷歌|谷歌\s*gemini",
        (
            "blog.google",
            "deepmind.google",
            "ai.google.dev",
            "ai.google",
            "gemini.google.com",
            "developers.googleblog.com",
            "cloud.google.com",
            "google.com",
        ),
    ),
    (
        r"\b(claude|anthropic)\b",
        (
            "anthropic.com",
            "docs.anthropic.com",
            "support.claude.com",
            "platform.claude.com",
        ),
    ),
    (
        r"\b(openai|chatgpt|gpt|codex|sora)\b",
        (
            "openai.com",
            "platform.openai.com",
            "help.openai.com",
            "cookbook.openai.com",
            "github.com/openai",
        ),
    ),
    (
        r"\b(microsoft|azure|copilot)\b|微软",
        (
            "microsoft.com",
            "azure.microsoft.com",
            "learn.microsoft.com",
            "blogs.microsoft.com",
        ),
    ),
    (
        r"\b(meta|llama)\b",
        (
            "ai.meta.com",
            "meta.com",
            "research.facebook.com",
            "github.com/meta-llama",
        ),
    ),
    (
        r"\b(xai|grok)\b",
        (
            "x.ai",
            "docs.x.ai",
            "blog.x.ai",
        ),
    ),
]

_AUTHORITATIVE_DOMAIN_SUFFIXES = {
    "arxiv.org",
    "nature.com",
    "science.org",
    "acm.org",
    "ieee.org",
    "theverge.com",
    "techcrunch.com",
    "wired.com",
    "reuters.com",
    "apnews.com",
    "bloomberg.com",
    "ft.com",
    "github.com",
}

_SUSPICIOUS_BRAND_TLDS = (".cc", ".top", ".xyz", ".vip", ".icu", ".shop", ".site", ".online")
_BRAND_TERMS = r"(gemini|google|claude|anthropic|openai|chatgpt|gpt|codex|sora|copilot|llama|grok)"
_TRUNCATED_MARKER_RE = re.compile(r"\[\s*\.{3}\s*truncated\s+\d+\s+chars\s*\.{3}\s*\]", flags=re.IGNORECASE)
_NAVIGATION_NOISE_PATTERNS = [
    r"^(?:On This Page|In this article|Table of contents|Back to Blog|Sign In|Log In|Subscribe|Newsletter|Previous|Next|Top)$",
    r"^(?:Jump to content|Main menu|Navigation|Contribute|Personal tools|Appearance|Search|Share|Copy link|Mail)$",
    r"^(?:Get API key|Cookbook|Community|Docs|API reference|Overview|Get Started|Pricing|Coding agent setup)$",
    r"^(?:Use your Google Account|Email or phone|Forgot email\?|Not your computer\?|Create account|Next)$",
    r"^\d+\s+sections?$",
    r"^\s*(?:x|go|open|read|more)\s*$",
    r"^\s*(?:首页\s*[»>]|Tags?:|标签[:：])",
    r"^[^\n]{0,120}(?:Gemini|Claude|OpenAI|Google|谷歌|模型|教程|创作|新闻)[^\n]{0,120}[：:]\s*(?:Go|Open|Read|More)\s*$",
    r"^\s*[-*]?\s*(?:URL|Status|Provider|Providers|Query|Search query)\s*[:：]",
    r"Afrikaans.+English.+Español.+Français",
    r"MCP ServersMCP Servers|Agent SkillsAgent Skills|DocumentationDocumentation",
    r"(?:\[.{2,80}\]\([^)]+\)\s*){2,}",
]


# ─────────────────────────────────────────────────────────────────────────────
# LLM 访问（延迟 import 防循环依赖）
# ─────────────────────────────────────────────────────────────────────────────
def _llm_chat(messages: list[dict[str, Any]], *, temperature: float = 0.3, max_tokens: int = 2048, timeout: float = 60.0) -> str:
    """Single LLM call returning plain text. Raises on failure (caller handles fallback)."""
    from backend.runtime.agent_loop import _create_backend
    from backend.web.app import _load_config_for_workspace, _active_workspace_root

    config = _load_config_for_workspace(_active_workspace_root())
    backend = _create_backend(config)
    response = backend.chat(messages, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
    return str(response.content or "").strip()


def _extract_json(text: str) -> Any:
    """Best-effort JSON extraction from an LLM response (handles ```json fences)."""
    value = str(text or "").strip()
    if not value:
        return None
    # strip code fences
    fence = re.search(r"```(?:json)?\s*(.+?)```", value, flags=re.DOTALL)
    if fence:
        value = fence.group(1).strip()
    # try direct parse
    try:
        return json.loads(value)
    except Exception:
        pass
    # try to locate the first {...} or [...] block
    for opener, closer in (("[", "]"), ("{", "}")):
        start = value.find(opener)
        end = value.rfind(closer)
        if start >= 0 and end > start:
            try:
                return json.loads(value[start : end + 1])
            except Exception:
                continue
    return None


# ─────────────────────────────────────────────────────────────────────────────
# 阶段 1：PLAN — 生成研究计划
# ─────────────────────────────────────────────────────────────────────────────
def _generate_plan(question: str) -> dict[str, Any]:
    """Produce a research brief + sub-questions. Raises on LLM failure."""
    prompt = (
        "你是一个深度研究规划助手。针对用户的研究问题，生成一个结构化研究计划。\n"
        "输出严格的 JSON，格式：\n"
        '{\n'
        '  "brief": "一句话研究目标（作为整个研究的北极星）",\n'
        '  "subquestions": ["子问题1", "子问题2", "子问题3"]\n'
        '}\n'
        f"子问题应彼此独立、覆盖问题的不同维度，数量 {_MIN_SUBQUESTIONS}~{_MAX_SUBQUESTIONS} 个。\n"
        "只输出 JSON，不要解释。"
    )
    raw = _llm_chat(
        [
            {"role": "system", "content": prompt},
            {"role": "user", "content": f"研究问题：{question}"},
        ],
        temperature=0.4,
        max_tokens=1024,
    )
    data = _extract_json(raw)
    if not isinstance(data, dict):
        raise ValueError("plan generation returned non-dict")
    brief = str(data.get("brief") or question).strip()
    subs_raw = data.get("subquestions")
    subs = [str(item).strip() for item in subs_raw if str(item or "").strip()] if isinstance(subs_raw, list) else []
    if not subs:
        raise ValueError("plan generation returned no subquestions")
    subs = subs[:_MAX_SUBQUESTIONS]
    return {"brief": brief, "subquestions": subs}


# ─────────────────────────────────────────────────────────────────────────────
# 阶段 2：QUERY — 每个子问题扩展搜索词
# ─────────────────────────────────────────────────────────────────────────────
def _expand_queries(subquestion: str) -> list[str]:
    """Expand a sub-question into 2~3 search query variants. Falls back to the sub-question itself."""
    try:
        raw = _llm_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "把研究子问题转换成 2~3 条高质量搜索引擎查询词。\n"
                        "查询词应包含关键实体、年份或版本等具体限定。\n"
                        '只输出 JSON 数组，如 ["查询1", "查询2"]。'
                    ),
                },
                {"role": "user", "content": subquestion},
            ],
            temperature=0.3,
            max_tokens=512,
        )
        data = _extract_json(raw)
        if isinstance(data, list):
            queries = [str(item).strip() for item in data if str(item or "").strip()]
            if queries:
                return queries[:_MAX_QUERIES_PER_SUB]
    except Exception:
        pass
    return [subquestion]


def _official_domains_for_question(question: str) -> list[str]:
    return official_domains_for_question(question)


def _with_official_query_variants(queries: list[str], question: str, question_type: dict[str, Any] | None = None) -> list[str]:
    """Put official-domain searches first when the subject has known primary sources."""
    official_domains = _official_domains_for_question(question)
    if not official_domains:
        return queries[:_MAX_QUERIES_PER_SUB]
    qtype = str((question_type or {}).get("type") or "")
    entities = [str(item).strip() for item in (question_type or {}).get("entities", []) if str(item or "").strip()]
    variants: list[str] = []
    for query in queries[:_MAX_QUERIES_PER_SUB]:
        domain_limit = 6 if qtype == "official_release_check" else 4
        for domain in official_domains[:domain_limit]:
            variants.append(f"{query} site:{domain}")
        if qtype == "official_release_check":
            variants.append(f"{query} official release announcement")
            variants.append(f"{query} official docs changelog")
        else:
            variants.append(f"{query} official source")
        variants.append(query)
    if qtype == "official_release_check":
        for entity in entities:
            if not re.search(r"\d", entity):
                continue
            for domain in official_domains[:4]:
                variants.append(f'"{entity}" site:{domain}')
    deduped: list[str] = []
    seen: set[str] = set()
    limit = 12 if qtype == "official_release_check" else 8
    for value in variants:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(value)
        if len(deduped) >= limit:
            break
    return deduped or queries[:_MAX_QUERIES_PER_SUB]


# ─────────────────────────────────────────────────────────────────────────────
# 阶段 3：GATHER — 并行搜索 + 抓取
# ─────────────────────────────────────────────────────────────────────────────
def _gather_for_subquestion(
    subquestion: str,
    *,
    max_results: int,
    max_pages: int,
    max_chars_per_page: int,
    provider: str,
    question: str = "",
    question_type: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Search + read pages for a single sub-question. Returns evidence + sources."""
    research_question = str(question or subquestion)
    queries = _with_official_query_variants(_expand_queries(subquestion), research_question, question_type)
    seen: set[str] = set()
    candidates: list[dict[str, Any]] = []
    for query in queries:
        search = metis_search_query(query, max_results=max_results, provider=provider)
        if not search.get("ok"):
            continue
        for item in search.get("results") or []:
            if not isinstance(item, dict):
                continue
            url = _source_url(item.get("url"))
            key = _canonical_url_key(url)
            if not url or key in seen:
                continue
            seen.add(key)
            if _should_drop_search_candidate(item, research_question, question_type):
                continue
            enriched = dict(item)
            quality = _source_quality_for_search_result(item, research_question, question_type)
            enriched["_metis_source_quality"] = quality["label"]
            enriched["_metis_source_score"] = quality["score"]
            enriched["_metis_source_reason"] = quality["reason"]
            enriched["_metis_source_core_allowed"] = quality.get("core_allowed", True)
            enriched["_metis_source_role"] = quality.get("role") or ""
            candidates.append(enriched)

    pages: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    char_limit = _clamp_int(max_chars_per_page, 800, 4000, default=1800)
    to_read = _rank_research_candidates(candidates, research_question, question_type)[: max(1, max_pages)]
    if to_read:
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, len(to_read))) as executor:
            futures = [(item, executor.submit(metis_page_read, _source_url(item.get("url")), char_limit)) for item in to_read]
            for item, future in futures:
                url = _source_url(item.get("url"))
                try:
                    page = future.result()
                except Exception as exc:
                    page = {"ok": False, "status": "error", "url": url, "error": f"{type(exc).__name__}: {str(exc)[:160]}"}
                if page.get("ok"):
                    pages.append({"search_result": item, **page})
                else:
                    failures.append({"url": url, "status": str(page.get("status") or "error"), "error": str(page.get("error") or "")})
    return {"subquestion": subquestion, "queries": queries, "pages": pages, "failures": failures, "candidates": candidates}


def _gather_parallel(
    subquestions: list[str],
    *,
    max_results: int,
    max_pages: int,
    max_chars_per_page: int,
    provider: str,
    question: str = "",
    question_type: dict[str, Any] | None = None,
    on_progress: Callable[[list[dict[str, Any]], int, int], None] | None = None,
) -> list[dict[str, Any]]:
    """Run all sub-questions in parallel, one worker each.

    on_progress(partial_evidence, done_count, total) is invoked each time a
    sub-question completes, carrying evidence accumulated so far — this drives
    the live progress feed (running job incremental persistence)."""
    results: list[dict[str, Any]] = [{} for _ in subquestions]
    total = len(subquestions)
    done = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=min(4, max(1, len(subquestions)))) as executor:
        future_map = {
            executor.submit(
                _gather_for_subquestion,
                sub,
                max_results=max_results,
                max_pages=max_pages,
                max_chars_per_page=max_chars_per_page,
                provider=provider,
                question=question,
                question_type=question_type,
            ): index
            for index, sub in enumerate(subquestions)
        }
        for future in concurrent.futures.as_completed(future_map):
            index = future_map[future]
            try:
                results[index] = future.result()
            except Exception as exc:
                results[index] = {
                    "subquestion": subquestions[index],
                    "queries": [],
                    "pages": [],
                    "failures": [{"url": "", "status": "error", "error": f"{type(exc).__name__}: {str(exc)[:160]}"}],
                    "candidates": [],
                }
            done += 1
            if on_progress is not None:
                try:
                    on_progress(_collect_evidence(results, question=question, question_type=question_type), done, total)
                except Exception:
                    pass
    return results


def _rank_research_candidates(candidates: list[dict[str, Any]], question: str, question_type: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    return sorted(
        candidates,
        key=lambda item: (
            float(item.get("_metis_source_score") or _source_quality_for_search_result(item, question, question_type)["score"]),
            -int(item.get("rank") or 999),
        ),
        reverse=True,
    )


def _should_drop_search_candidate(item: dict[str, Any], question: str, question_type: dict[str, Any] | None = None) -> bool:
    quality = _source_quality_for_search_result(item, question, question_type)
    return quality["label"] in {"blocked", "rejected"}


def _source_quality_for_search_result(item: dict[str, Any], question: str, question_type: dict[str, Any] | None = None) -> dict[str, Any]:
    policy = classify_source(item, question, question_type)
    label = str(policy.get("label") or "normal")
    if label == "rejected":
        label = "blocked"
    return {
        "label": label,
        "score": policy.get("score", 0),
        "reason": policy.get("reason") or "",
        "core_allowed": bool(policy.get("core_allowed", True)),
        "role": policy.get("role") or "",
    }


def _is_low_quality_search_result(*, haystack: str, host: str, official_domains: list[str]) -> bool:
    if _spam_hit_count(haystack) > 0:
        return True
    if re.search(r"\baccounts\.google\.com\b|/signin|/login|ServiceLogin|WebLiteSignIn|/auth/", haystack, flags=re.IGNORECASE):
        return True
    official_like_query = bool(official_domains)
    brand_hit = bool(re.search(_BRAND_TERMS, haystack, flags=re.IGNORECASE))
    if not official_like_query and not brand_hit:
        return False
    if re.search(r"教程网|中文网|下载站|资源站|镜像站|非官方|官网入口|最新网址|国内入口|网页版入口", haystack, flags=re.IGNORECASE):
        return True
    if brand_hit and host.endswith(_SUSPICIOUS_BRAND_TLDS):
        return True
    if brand_hit and re.search(rf"{_BRAND_TERMS}[-_.]", host, flags=re.IGNORECASE):
        return True
    if official_like_query and re.search(r"官网|官方", haystack) and not any(_domain_hint_matches_host(host, domain) for domain in official_domains):
        return True
    return False


def _host_for_url(url: str) -> str:
    return policy_host_for_url(url)


def _url_matches_domain_hint(url: str, domain_hint: str) -> bool:
    return policy_url_matches_domain_hint(url, domain_hint)


def _domain_hint_matches_host(host: str, domain_hint: str) -> bool:
    return policy_domain_hint_matches_host(host, domain_hint)


def _prefer_primary_evidence(question: str, evidence: list[dict[str, Any]], question_type: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    qtype = str((question_type or {}).get("type") or "")
    if qtype == "official_release_check":
        official = [item for item in evidence if str(item.get("source_quality") or "") == "official"]
        if not official:
            return evidence
        context = [
            item
            for item in evidence
            if item not in official and str(item.get("source_quality") or "") == "authoritative" and float(item.get("review_relevance") or 0) >= 0.45
        ][:2]
        return official + context
    official_domains = _official_domains_for_question(question)
    if not official_domains:
        return evidence
    primary = [
        item
        for item in evidence
        if str(item.get("source_quality") or "") in {"official", "authoritative"}
        or any(_url_matches_domain_hint(str(item.get("url") or ""), domain) for domain in official_domains)
    ]
    if len(primary) >= 2:
        return primary
    return evidence


# ─────────────────────────────────────────────────────────────────────────────
# 阶段 4：GRADE — 证据打分与过滤
# ─────────────────────────────────────────────────────────────────────────────
def _collect_evidence(
    gathered: list[dict[str, Any]],
    question: str = "",
    question_type: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Flatten gathered pages into a deduplicated evidence list."""
    evidence: list[dict[str, Any]] = []
    seen: set[str] = set()
    for bucket in gathered:
        for page in bucket.get("pages") or []:
            source = page.get("search_result") or {}
            url = _source_url(_unwrap_reader_url(page.get("final_url") or page.get("url") or source.get("url")))
            key = _canonical_url_key(url)
            if not url or key in seen:
                continue
            seen.add(key)
            title = _source_title(page.get("title") or source.get("title"), url)
            domain = _source_domain(source.get("source"), url)
            snippet = str(source.get("snippet") or "")
            raw_text = _normalize_page_text(str(page.get("text") or ""))
            text = _sanitize_research_text(raw_text)
            if _should_drop_research_evidence(title=title, url=url, domain=domain, snippet=snippet, raw_text=raw_text, clean_text=text):
                continue
            candidate = {
                "title": title,
                "url": url,
                "domain": domain,
                "snippet": snippet,
                "text": text,
                "chars": len(text),
                "status": str(page.get("status") or "opened"),
                "source_quality": source.get("_metis_source_quality") or "normal",
                "source_score": source.get("_metis_source_score") or 0,
                "source_reason": source.get("_metis_source_reason") or "",
                "source_role": source.get("_metis_source_role") or "",
                "core_allowed": bool(source.get("_metis_source_core_allowed", True)),
                "subquestion": bucket.get("subquestion") or "",
            }
            review = review_evidence_item(candidate, question, question_type)
            if not review.get("accepted"):
                continue
            review_label = str(review.get("authority") or candidate["source_quality"] or "normal")
            if review_label == "rejected":
                continue
            candidate.update(
                {
                    "review_relevance": review.get("relevance", 0),
                    "review_authority": review_label,
                    "source_quality": review_label,
                    "source_score": review.get("authority_score", candidate.get("source_score") or 0),
                    "source_reason": review.get("authority_reason") or candidate.get("source_reason") or "",
                    "source_role": review.get("source_role") or candidate.get("source_role") or "",
                    "core_allowed": bool(review.get("core_allowed", candidate.get("core_allowed", True))),
                    "boilerplate_score": review.get("boilerplate_score", 0),
                    "spam_hits": review.get("spam_hits", 0),
                }
            )
            evidence.append(candidate)
    return evidence


def _sanitize_research_text(value: str) -> str:
    """Remove obvious crawler/network noise and spammy ad lines before grading/synthesis."""
    text = _normalize_page_text(_strip_research_noise_fragments(str(value or "")))
    if not text:
        return ""
    kept: list[str] = []
    previous_bad = False
    for line in text.splitlines():
        cleaned = line.strip()
        if not cleaned:
            if kept and kept[-1] != "":
                kept.append("")
            previous_bad = False
            continue
        bad = _is_network_error_text(cleaned) or _spam_hit_count(cleaned) > 0 or _is_navigation_noise_text(cleaned)
        if bad or (previous_bad and len(cleaned) <= 4):
            previous_bad = True
            continue
        kept.append(cleaned)
        previous_bad = False
    return _normalize_page_text("\n".join(kept))


def _sanitize_report_text(value: str) -> str:
    """Display/landing safety net for already synthesized report text."""
    text = _normalize_page_text(_strip_research_noise_fragments(str(value or "")))
    if not text:
        return ""
    lines = text.splitlines()
    output: list[str] = []
    skip_level = 0
    for line in lines:
        heading = re.match(r"^(#{1,6})\s+(.+)$", line.strip())
        if heading and skip_level:
            level = len(heading.group(1))
            if level <= skip_level:
                skip_level = 0
        if skip_level:
            continue
        if heading and _is_report_noise_heading(heading.group(2)):
            skip_level = len(heading.group(1))
            continue
        if _is_network_error_text(line) or _spam_hit_count(line) > 0 or _is_navigation_noise_text(line):
            continue
        output.append(line)
    return _normalize_page_text("\n".join(output))


def _should_drop_research_evidence(*, title: str, url: str, domain: str, snippet: str, raw_text: str, clean_text: str) -> bool:
    haystack = "\n".join([title, url, domain, snippet, raw_text])
    if _is_network_error_text(haystack):
        return True
    spam_hits = _spam_hit_count(haystack)
    if spam_hits >= 2:
        return True
    if len(clean_text) < _MIN_EVIDENCE_CHARS:
        return True
    return False


def _is_network_error_text(value: str) -> bool:
    text = str(value or "")
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _NETWORK_ERROR_PATTERNS)


def _strip_research_noise_fragments(value: str) -> str:
    return _TRUNCATED_MARKER_RE.sub("", str(value or ""))


def _is_navigation_noise_text(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _NAVIGATION_NOISE_PATTERNS)


def _spam_hit_count(value: str) -> int:
    text = str(value or "")
    local_hits = sum(1 for pattern in _SPAM_PATTERNS if re.search(pattern, text, flags=re.IGNORECASE))
    return max(local_hits, policy_spam_hit_count(text))


def _is_report_noise_heading(value: str) -> bool:
    text = str(value or "").strip()
    return bool(re.fullmatch(r"(?:目录|目錄|table\s+of\s+contents|contents|toc|report|evidence\s+opened|raw\s+evidence)", text, flags=re.IGNORECASE)) or bool(
        re.search(r"read\s+failures|读取失败|连接错误|错误来源|抓取失败|failed\s+reads?", text, flags=re.IGNORECASE)
    )


def _unwrap_reader_url(value: Any) -> str:
    current = str(value or "").strip()
    for _ in range(4):
        parsed = urlparse(current)
        if parsed.netloc.lower() != "r.jina.ai":
            break
        next_value = unquote((parsed.path or "").lstrip("/")).strip()
        next_value = re.sub(r"^https?://(https?://)", r"\1", next_value, flags=re.IGNORECASE)
        if not re.match(r"^https?://", next_value, flags=re.IGNORECASE) or next_value == current:
            break
        current = next_value
    return current


def _plain_excerpt(value: str, max_chars: int) -> str:
    text = _strip_research_noise_fragments(str(value or "")).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rstrip()


def _grade_evidence(question: str, evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """LLM-score each evidence item 0~1 for relevance+credibility; filter low quality.
    Falls back to keeping all evidence if grading fails."""
    if not evidence:
        return evidence
    try:
        catalog = [
            {
                "i": index,
                "title": item["title"],
                "domain": item["domain"],
                "quality": item.get("source_quality") or "normal",
                "excerpt": _plain_excerpt(item["text"], 300),
            }
            for index, item in enumerate(evidence)
        ]
        raw = _llm_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "你是研究证据评估助手。针对研究问题，给每条证据打分（0~1）：\n"
                        "综合考虑相关性和来源可信度。官方/一手来源和权威研究来源优先；SEO、教程站、搬运站、论坛内容要显著降分。\n"
                        '只输出 JSON 数组：[{"i": 序号, "score": 0.85}, ...]。'
                    ),
                },
                {"role": "user", "content": f"研究问题：{question}\n\n证据列表：\n{json.dumps(catalog, ensure_ascii=False)}"},
            ],
            temperature=0.2,
            max_tokens=1024,
        )
        data = _extract_json(raw)
        if isinstance(data, list):
            score_map: dict[int, float] = {}
            for row in data:
                if isinstance(row, dict) and "i" in row:
                    try:
                        score_map[int(row["i"])] = max(0.0, min(1.0, float(row.get("score", 0))))
                    except Exception:
                        continue
            if score_map:
                for index, item in enumerate(evidence):
                    item["score"] = score_map.get(index, _GRADE_KEEP_THRESHOLD)
                graded = [item for item in evidence if item.get("score", 0) >= _GRADE_KEEP_THRESHOLD]
                graded.sort(key=lambda item: item.get("score", 0), reverse=True)
                return graded or evidence
    except Exception:
        pass
    # fallback: keep all, no scores
    for item in evidence:
        item.setdefault("score", _GRADE_KEEP_THRESHOLD)
    return evidence


# ─────────────────────────────────────────────────────────────────────────────
# 阶段 5：OUTLINE — 生成报告目录
# ─────────────────────────────────────────────────────────────────────────────
def _build_outline(question: str, brief: str, subquestions: list[str]) -> list[str]:
    """Generate report section headings. Falls back to sub-questions as headings."""
    try:
        raw = _llm_chat(
            [
                {
                    "role": "system",
                    "content": (
                        "根据研究目标和子问题，规划研究报告的章节标题（3~6 个）。\n"
                        '只输出 JSON 数组：["章节1", "章节2", ...]。'
                    ),
                },
                {"role": "user", "content": f"研究目标：{brief}\n子问题：{json.dumps(subquestions, ensure_ascii=False)}"},
            ],
            temperature=0.4,
            max_tokens=512,
        )
        data = _extract_json(raw)
        if isinstance(data, list):
            headings = [str(item).strip() for item in data if str(item or "").strip()]
            if headings:
                return headings[:6]
    except Exception:
        pass
    return subquestions


# ─────────────────────────────────────────────────────────────────────────────
# 阶段 6：SYNTHESIZE — 单次生成带引用的完整报告
# ─────────────────────────────────────────────────────────────────────────────
def _synthesize_report(
    question: str,
    brief: str,
    outline: list[str],
    evidence: list[dict[str, Any]],
) -> str:
    """One-shot report generation with [n] citations bound to evidence rank."""
    numbered = []
    for rank, item in enumerate(evidence, start=1):
        numbered.append(
            f"[{rank}] {item['title']} ({item['domain']} · {item.get('source_quality') or 'normal'})\n{_plain_excerpt(item['text'], 800)}"
        )
    evidence_block = "\n\n".join(numbered)
    outline_block = "\n".join(f"- {heading}" for heading in outline)

    raw = _llm_chat(
        [
            {
                "role": "system",
                "content": (
                    "你是资深研究分析师和专业长文编辑。基于给定证据，撰写一篇接近 Gemini Deep Research 成品质量的中文研究报告。\n"
                    "要求：\n"
                    "1. 使用 Markdown，开头必须是一个 # 主标题；随后写 ## 引言，用 2~4 段交代背景、为什么重要、报告覆盖范围。\n"
                    "2. 在引言后写 ## 核心摘要，用 4~6 条要点给出高密度结论，不要泛泛而谈。\n"
                    "3. 主体章节使用编号标题，例如 ## 1. 核心发现、### 1.1 细分议题；每个章节必须有成段分析、因果解释和必要的时间/版本/主体信息。\n"
                    "4. 必须至少包含一个 Markdown 表格，用于时间线、模型/产品矩阵、优劣对比或影响评估；表格前后要有解释，不要堆砌。\n"
                    "5. 对争议、限制、风险和不确定性单独成节，不要只写单向宣传稿。\n"
                    "6. 关键论断后用 [n] 标注来源（n 对应证据编号）。\n"
                    "7. 优先使用 official / authoritative 证据；普通来源只能作为辅助，不允许用低质量来源支撑核心结论。\n"
                    "8. 只使用证据中的信息，不要编造。证据不足处如实说明。\n"
                    "9. 结尾写 ## 总结，提炼结论、后续观察点和用户可以继续追踪的指标。\n"
                    "10. 不要输出“目录 / Table of Contents / TOC”，产品界面会自动生成目录。\n"
                    "11. 不要输出 Evidence Opened、Read Failures、Status、Providers、URL 清单、抓取错误或任何工具日志；来源会由产品界面单独附上。\n"
                    "12. 严禁把网页导航、登录页、按钮词、语言切换、站点边栏、SEO 推荐卡片转写进正文；例如 On This Page、Sign in、Get API key、Go、Open、首页、Tags 这类内容全部忽略。\n"
                    "13. 每一个目录级标题都必须在正文中真实存在，并且标题下必须有实质分析；不要生成空标题或虚假目录。"
                ),
            },
            {
                "role": "user",
                "content": (
                    f"研究问题：{question}\n研究目标：{brief}\n\n"
                    f"报告大纲：\n{outline_block}\n\n"
                    f"证据（带编号）：\n{evidence_block}"
                ),
            },
        ],
        temperature=0.5,
        max_tokens=4096,
        timeout=120.0,
    )
    return str(raw or "").strip()


# ─────────────────────────────────────────────────────────────────────────────
# Job 持久化
# ─────────────────────────────────────────────────────────────────────────────
# 流水线阶段顺序（用于阶段性 phase 状态推导）
_STAGE_ORDER = ["plan", "query", "gather", "grade", "outline", "synthesize"]
_STAGE_META = {
    "plan": ("plan", "生成研究计划"),
    "query": ("query", "扩展搜索词"),
    "gather": ("gather", "并行搜索抓取"),
    "grade": ("grade", "证据分级"),
    "outline": ("outline", "规划大纲"),
    "synthesize": ("synthesize", "撰写报告"),
}


def _sources_from_evidence(evidence: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert graded/ungraded evidence into research_job source rows."""
    sources: list[dict[str, Any]] = []
    for rank, item in enumerate(evidence, start=1):
        sources.append(
            {
                "id": f"s{rank}",
                "rank": rank,
                "title": item.get("title") or item.get("url") or "",
                "url": item.get("url") or "",
                "domain": item.get("domain") or "",
                "snippet": item.get("snippet") or "",
                "status": "opened",
                "evidence_status": item.get("status") or "opened",
                "chars": item.get("chars") or 0,
                "score": round(float(item.get("score", 0)), 2),
                "quality": item.get("source_quality") or "normal",
                "quality_reason": item.get("source_reason") or "",
            }
        )
    return sources


def _phase_rows(
    stage: str,
    *,
    brief: str,
    gathered: list[dict[str, Any]],
    opened: int,
    failures: int,
    outline: list[str],
    gather_done: int = 0,
    gather_total: int = 0,
) -> list[dict[str, Any]]:
    """Derive per-phase status (complete / running / queued) from the current stage."""
    current_index = _STAGE_ORDER.index(stage) if stage in _STAGE_ORDER else len(_STAGE_ORDER)

    def status_for(name: str) -> str:
        idx = _STAGE_ORDER.index(name)
        if idx < current_index:
            return "complete"
        if idx == current_index:
            return "complete" if stage == "synthesize" else "running"
        return "queued"

    query_count = sum(len(bucket.get("queries") or []) for bucket in gathered)
    gather_row: dict[str, Any] = {
        "id": "gather",
        "label": _STAGE_META["gather"][1],
        "status": status_for("gather"),
        "count": opened,
        "failed": failures,
    }
    # 采集阶段进行中时展示 x/total 子问题进度
    if status_for("gather") == "running" and gather_total:
        gather_row["summary"] = f"{gather_done}/{gather_total} 子问题"

    return [
        {"id": "plan", "label": _STAGE_META["plan"][1], "status": status_for("plan"), "summary": brief},
        {"id": "query", "label": _STAGE_META["query"][1], "status": status_for("query"), "count": query_count},
        gather_row,
        {"id": "grade", "label": _STAGE_META["grade"][1], "status": status_for("grade"), "count": opened},
        {"id": "outline", "label": _STAGE_META["outline"][1], "status": status_for("outline"), "count": len(outline)},
        {"id": "synthesize", "label": _STAGE_META["synthesize"][1], "status": status_for("synthesize")},
    ]


def _build_activity_payload(
    question: str,
    brief: str,
    plan: dict[str, Any],
    gathered: list[dict[str, Any]],
    evidence: list[dict[str, Any]],
    outline: list[str],
    provider_chain: list[str],
    *,
    stage: str = "synthesize",
    job_id: str = "",
    gather_done: int = 0,
    gather_total: int = 0,
    question_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a research_activity payload compatible with research_jobs schema.

    stage 决定 phases 的状态推导：running 阶段生成 complete/running/queued 混合，
    synthesize 阶段全部 complete。传入 job_id 以复用稳定 id 增量落盘。
    """
    sources = _sources_from_evidence(evidence)
    failures: list[dict[str, Any]] = []
    for bucket in gathered:
        for failure in bucket.get("failures") or []:
            if failure.get("url"):
                failures.append(failure)

    total_opened = len(evidence)
    total_failures = len(failures)
    phases = _phase_rows(
        stage,
        brief=brief,
        gathered=gathered,
        opened=total_opened,
        failures=total_failures,
        outline=outline,
        gather_done=gather_done,
        gather_total=gather_total,
    )
    queries_flat: list[dict[str, Any]] = []
    for bucket in gathered:
        for query in bucket.get("queries") or []:
            queries_flat.append({"query": query, "subquestion": bucket.get("subquestion") or "", "at": int(time.time() * 1000)})

    payload: dict[str, Any] = {
        "schema": _SCHEMA,
        "kind": "deep_research",
        "title": question,
        "query": question,
        "brief": brief,
        "question_type": (question_profile or {}).get("type") or "general_explainer",
        "question_profile": question_profile or {},
        "providers": provider_chain,
        "stats": {
            "subquestions": len(plan.get("subquestions") or []),
            "queries": len(queries_flat),
            "sources": len(sources),
            "opened": total_opened,
            "failures": total_failures,
        },
        "phases": phases,
        "plan_subquestions": plan.get("subquestions") or [],
        "queries_detail": queries_flat,
        "outline": outline,
        "sources": sources,
        "failures": failures,
    }
    if job_id:
        payload["job_id"] = job_id
    return payload


# ─────────────────────────────────────────────────────────────────────────────
# 降级
# ─────────────────────────────────────────────────────────────────────────────
def _fallback_to_simple(question: str, *, max_results: int, max_pages: int, max_chars_per_page: int, provider: str) -> str:
    """When deep pipeline can't run (LLM unavailable), degrade to the existing simple research."""
    return format_research_response(
        metis_search_research(
            question=question,
            max_results=max_results,
            max_pages=max_pages,
            max_chars_per_page=max_chars_per_page,
            provider=provider,
        )
    )


def _deterministic_plan_for_question(question: str, question_profile: dict[str, Any]) -> dict[str, Any] | None:
    """Fallback plan for high-risk question types where simple search would be unsafe."""
    qtype = str((question_profile or {}).get("type") or "")
    if qtype != "official_release_check":
        return None
    return {
        "brief": f"核验“{question}”的官方发布状态、可确认事实和传闻来源。",
        "subquestions": [
            f"{question} 官方公告 官方文档 变更日志",
            f"{question} 官方模型列表 API 文档 release notes",
            f"{question} 第三方报道 传闻 误称 来源",
        ],
    }


def _format_deep_research_completion(
    *,
    question: str,
    brief: str,
    subquestions: list[str],
    evidence: list[dict[str, Any]],
    outline: list[str],
    job_id: str,
    report_path: str,
) -> str:
    embed = {
        "schema": _SCHEMA,
        "kind": "deep_research",
        "job_id": job_id,
        "job_status": "complete",
        "title": question,
        "query": question,
        "report_path": report_path,
        "sources": _sources_from_evidence(evidence),
        "stats": {"sources": len(evidence), "subquestions": len(subquestions), "outline": len(outline)},
    }

    lines = [
        f"=== Deep Research 完成：{question} ===",
        f"研究目标：{brief}",
        f"子问题：{len(subquestions)} 个 | 证据来源：{len(evidence)} 条 | 大纲章节：{len(outline)} 个",
        f"报告已保存到 Research job store（job_id={job_id or 'n/a'}）。",
    ]
    if report_path:
        lines.append(f"报告文件：{report_path}")
    lines.extend(
        [
            "",
            "chat 输出策略：用自然语言给用户一段简短总结即可；完整报告、大纲、来源和导出请引导用户到 Research 报告视图查看，不要在聊天里粘贴整篇报告或长来源列表。",
            f"<!-- METIS_RESEARCH_JSON {json.dumps(embed, ensure_ascii=False, separators=(',', ':'))} -->",
        ]
    )
    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# 公共工具入口
# ─────────────────────────────────────────────────────────────────────────────
@trace_execution
def deep_research_plan(question: str, reason: str = "") -> str:
    """阶段一：生成研究计划供用户确认。"""
    _ = reason
    question_value = str(question or "").strip()
    if not question_value:
        return "❌ deep_research_plan 需要非空研究问题。"
    try:
        plan = _generate_plan(question_value)
    except Exception as exc:
        return (
            f"⚠️ 计划生成失败（{type(exc).__name__}），可直接调用 deep_research_run 让其即时规划，"
            "或降级用 web_research。"
        )
    plan_json = json.dumps(plan, ensure_ascii=False)
    lines = [
        f"=== 研究计划：{question_value} ===",
        f"研究目标：{plan['brief']}",
        "",
        "子问题：",
    ]
    for index, sub in enumerate(plan["subquestions"], start=1):
        lines.append(f"  {index}. {sub}")
    lines.extend(
        [
            "",
            "请把以上计划展示给用户确认或修改。用户确认后，调用 deep_research_run 并传入 plan_json 执行研究。",
            "chat 输出策略：把计划要点用自然语言呈现给用户，不要粘贴 JSON。",
            f"<!-- METIS_RESEARCH_PLAN {plan_json} -->",
        ]
    )
    return "\n".join(lines)


@trace_execution
def deep_research_run(
    question: str,
    plan_json: str = "",
    max_results: int = 5,
    max_pages: int = 3,
    max_chars_per_page: int = 1800,
    reason: str = "",
    provider: str = "auto",
) -> str:
    """阶段二：执行完整 6 阶段深度研究流水线（增量落盘 running job 以支持实时进度）。"""
    _ = reason
    question_value = str(question or "").strip()
    if not question_value:
        return "❌ deep_research_run 需要非空研究问题。"
    question_profile = classify_question(question_value)

    provider_chain = _provider_chain_for_request(provider)

    # 稳定 job_id：首次落盘由 save_research_activity_job 生成，后续复用
    state: dict[str, Any] = {"job_id": ""}

    def persist(
        *,
        stage: str,
        plan: dict[str, Any],
        gathered: list[dict[str, Any]],
        evidence: list[dict[str, Any]],
        outline: list[str],
        status: str,
        report: str = "",
        gather_done: int = 0,
        gather_total: int = 0,
    ) -> dict[str, Any]:
        activity = _build_activity_payload(
            question_value,
            str(plan.get("brief") or question_value),
            plan,
            gathered,
            evidence,
            outline,
            provider_chain,
            stage=stage,
            job_id=state["job_id"],
            gather_done=gather_done,
            gather_total=gather_total,
            question_profile=question_profile,
        )
        evidence_for_job = [
            {
                "title": item.get("title") or "",
                "url": item.get("url") or "",
                "text": _plain_excerpt(str(item.get("text") or ""), 2000),
                "snippet": item.get("snippet") or "",
                "status": item.get("status") or "opened",
                "chars": item.get("chars") or 0,
                "quality": item.get("source_quality") or "normal",
                "quality_reason": item.get("source_reason") or "",
            }
            for item in evidence
        ]
        try:
            job = save_research_activity_job(activity, report=report, status=status, evidence=evidence_for_job)
            if not state["job_id"]:
                state["job_id"] = str(job.get("id") or "")
            return job
        except Exception as exc:
            activity["job_error"] = f"{type(exc).__name__}: {str(exc)[:160]}"
            return {}

    # 阶段 1：PLAN（复用已确认计划或即时生成）
    plan: dict[str, Any] | None = None
    if plan_json:
        parsed = _extract_json(plan_json)
        if isinstance(parsed, dict) and parsed.get("subquestions"):
            subs = [str(item).strip() for item in parsed.get("subquestions") or [] if str(item or "").strip()]
            if subs:
                plan = {"brief": str(parsed.get("brief") or question_value).strip(), "subquestions": subs[:_MAX_SUBQUESTIONS]}
    if plan is None:
        try:
            plan = _generate_plan(question_value)
        except Exception:
            plan = _deterministic_plan_for_question(question_value, question_profile)
        if plan is None:
            # LLM 不可用 → 降级
            return _fallback_to_simple(
                question_value,
                max_results=max_results,
                max_pages=max_pages,
                max_chars_per_page=max_chars_per_page,
                provider=provider,
            )

    brief = plan["brief"]
    subquestions = plan["subquestions"]

    # PLAN 完成 → 首次落盘 running（建立稳定 job_id，前端轮询即可捞到）
    persist(stage="query", plan=plan, gathered=[], evidence=[], outline=[], status="running")

    # 阶段 2+3：QUERY + GATHER（并行，每个子问题完成即增量落盘）
    gather_total = len(subquestions)

    def on_gather_progress(evidence_so_far: list[dict[str, Any]], done: int, total: int) -> None:
        persist(
            stage="gather",
            plan=plan,
            gathered=[],
            evidence=evidence_so_far,
            outline=[],
            status="running",
            gather_done=done,
            gather_total=total,
        )

    gathered = _gather_parallel(
        subquestions,
        max_results=max_results,
        max_pages=max_pages,
        max_chars_per_page=max_chars_per_page,
        provider=provider,
        question=question_value,
        question_type=question_profile,
        on_progress=on_gather_progress,
    )

    # 阶段 4：GRADE
    evidence = _collect_evidence(gathered, question=question_value, question_type=question_profile)
    if not evidence:
        if question_profile.get("type") == "official_release_check":
            outline = ["结论", "已优先检查的官方渠道", "传闻和误称来源", "判断方法"]
            report = build_official_absence_report(question_value, [], _official_domains_for_question(question_value))
            job = persist(
                stage="synthesize",
                plan=plan,
                gathered=gathered,
                evidence=[],
                outline=outline,
                status="complete",
                report=report,
            )
            return _format_deep_research_completion(
                question=question_value,
                brief=brief,
                subquestions=subquestions,
                evidence=[],
                outline=outline,
                job_id=str(job.get("id") or state["job_id"] or ""),
                report_path=str(job.get("report_path") or ""),
            )
        # 没抓到任何证据 → 降级
        return _fallback_to_simple(
            question_value,
            max_results=max_results,
            max_pages=max_pages,
            max_chars_per_page=max_chars_per_page,
            provider=provider,
        )
    persist(stage="grade", plan=plan, gathered=gathered, evidence=evidence, outline=[], status="running")
    evidence = _grade_evidence(question_value, evidence)
    evidence = _prefer_primary_evidence(question_value, evidence, question_profile)

    # 阶段 5：OUTLINE
    persist(stage="outline", plan=plan, gathered=gathered, evidence=evidence, outline=[], status="running")
    outline = _build_outline(question_value, brief, subquestions)

    # 阶段 6：SYNTHESIZE
    persist(stage="synthesize", plan=plan, gathered=gathered, evidence=evidence, outline=outline, status="running")
    try:
        report = _synthesize_report(question_value, brief, outline, evidence)
    except Exception:
        report = ""
    report = _sanitize_report_text(report)
    if not report:
        return _fallback_to_simple(
            question_value,
            max_results=max_results,
            max_pages=max_pages,
            max_chars_per_page=max_chars_per_page,
            provider=provider,
        )
    report_check = review_report(report, question_value, evidence, question_profile)
    if not report_check.get("ok") and question_profile.get("type") == "official_release_check":
        has_official = any(str(item.get("source_quality") or "") == "official" for item in evidence)
        if not has_official:
            report = build_official_absence_report(question_value, evidence, _official_domains_for_question(question_value))

    # 完成 → 落盘 complete（带最终报告）
    job = persist(
        stage="synthesize",
        plan=plan,
        gathered=gathered,
        evidence=evidence,
        outline=outline,
        status="complete",
        report=report,
    )
    job_id = str(job.get("id") or state["job_id"] or "")
    report_path = str(job.get("report_path") or "")

    return _format_deep_research_completion(
        question=question_value,
        brief=brief,
        subquestions=subquestions,
        evidence=evidence,
        outline=outline,
        job_id=job_id,
        report_path=report_path,
    )

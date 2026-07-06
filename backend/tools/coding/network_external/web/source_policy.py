"""Source classification and source policy for Metis Research."""
from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

OFFICIAL_SOURCE_HINTS: list[tuple[str, tuple[str, ...]]] = [
    (
        r"\b(openai|chatgpt|gpt|codex|sora)\b",
        (
            "openai.com",
            "platform.openai.com",
            "help.openai.com",
            "cookbook.openai.com",
            "github.com/openai",
            "community.openai.com",
        ),
    ),
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
            "support.google.com",
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

AUTHORITATIVE_DOMAIN_SUFFIXES = {
    "arxiv.org",
    "nature.com",
    "science.org",
    "acm.org",
    "ieee.org",
    "reuters.com",
    "apnews.com",
    "bloomberg.com",
    "ft.com",
    "theverge.com",
    "techcrunch.com",
    "wired.com",
    "github.com",
    "bbc.com",
}

SUSPICIOUS_BRAND_TLDS = (".cc", ".top", ".xyz", ".vip", ".icu", ".shop", ".site", ".online", ".ru")
BRAND_TERMS = r"(gemini|google|claude|anthropic|openai|chatgpt|gpt|codex|sora|copilot|llama|grok)"
SPAM_PATTERNS = [
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
]

CRYPTO_RUMOR_DOMAINS = {
    "marsbit.co",
    "www.marsbit.co",
    "cointelegraph.com",
    "decrypt.co",
}


def official_domains_for_question(question: str) -> list[str]:
    text = str(question or "")
    domains: list[str] = []
    seen: set[str] = set()
    for pattern, candidates in OFFICIAL_SOURCE_HINTS:
        if not re.search(pattern, text, flags=re.IGNORECASE):
            continue
        for domain in candidates:
            if domain not in seen:
                seen.add(domain)
                domains.append(domain)
    return domains


def classify_source(item: dict[str, Any], question: str, question_type: dict[str, Any] | None = None) -> dict[str, Any]:
    url = source_url(item.get("url"))
    host = host_for_url(url)
    title = str(item.get("title") or "")
    snippet = str(item.get("snippet") or "")
    haystack = "\n".join([title, snippet, url, host])
    official_domains = official_domains_for_question(question)
    qtype = str((question_type or {}).get("type") or "")

    if any(url_matches_domain_hint(url, domain) for domain in official_domains):
        return {"label": "official", "score": 120, "reason": "matched known official domain", "core_allowed": True}

    if is_spam_text(haystack) or is_login_or_auth_url(haystack):
        return {"label": "rejected", "score": -250, "reason": "spam/login/navigation source", "core_allowed": False}

    if is_impersonating_or_low_quality(haystack=haystack, host=host, official_domains=official_domains):
        return {"label": "rejected", "score": -220, "reason": "low quality or impersonating source", "core_allowed": False}

    if qtype == "official_release_check" and host in CRYPTO_RUMOR_DOMAINS:
        return {"label": "normal", "score": 16, "reason": "rumor source only; not valid release evidence", "core_allowed": False, "role": "rumor"}

    if any(url_matches_domain_hint(url, domain) for domain in AUTHORITATIVE_DOMAIN_SUFFIXES):
        return {"label": "authoritative", "score": 82, "reason": "known authoritative domain", "core_allowed": qtype != "official_release_check"}

    score = 35
    if re.search(r"\b(official|announcement|release|changelog|docs?|paper|research|blog)\b|官方|发布|公告|文档|论文|研究", haystack, flags=re.IGNORECASE):
        score += 14
    if re.search(r"\b(news|press|report|analysis)\b|新闻|报道|报告|分析", haystack, flags=re.IGNORECASE):
        score += 6
    if re.search(r"\b(blog|medium|substack|reddit|zhihu|csdn|juejin|segmentfault)\b|知乎|掘金|论坛|贴吧", host, flags=re.IGNORECASE):
        score -= 14
    return {"label": "normal", "score": score, "reason": "general web source", "core_allowed": qtype != "official_release_check"}


def is_spam_text(value: str) -> bool:
    text = str(value or "")
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in SPAM_PATTERNS)


def spam_hit_count(value: str) -> int:
    text = str(value or "")
    return sum(1 for pattern in SPAM_PATTERNS if re.search(pattern, text, flags=re.IGNORECASE))


def is_login_or_auth_url(value: str) -> bool:
    return bool(re.search(r"\baccounts\.google\.com\b|/signin|/login|ServiceLogin|WebLiteSignIn|/auth/", str(value or ""), flags=re.IGNORECASE))


def is_impersonating_or_low_quality(*, haystack: str, host: str, official_domains: list[str]) -> bool:
    official_like_query = bool(official_domains)
    brand_hit = bool(re.search(BRAND_TERMS, haystack, flags=re.IGNORECASE))
    if not official_like_query and not brand_hit:
        return False
    if re.search(r"教程网|中文网|下载站|资源站|镜像站|非官方|官网入口|最新网址|国内入口|网页版入口", haystack, flags=re.IGNORECASE):
        return True
    if brand_hit and host.endswith(SUSPICIOUS_BRAND_TLDS):
        return True
    if brand_hit and re.search(rf"{BRAND_TERMS}[-_.]", host, flags=re.IGNORECASE):
        return True
    if official_like_query and re.search(r"官网|官方", haystack) and not any(domain_hint_matches_host(host, domain) for domain in official_domains):
        return True
    return False


def host_for_url(url: str) -> str:
    try:
        return re.sub(r"^www\.", "", urlparse(source_url(url)).netloc.lower(), flags=re.IGNORECASE)
    except Exception:
        return ""


def source_url(value: Any) -> str:
    return str(value or "").strip()


def url_matches_domain_hint(url: str, domain_hint: str) -> bool:
    parsed = urlparse(source_url(url))
    host = re.sub(r"^www\.", "", parsed.netloc.lower(), flags=re.IGNORECASE)
    hint = str(domain_hint or "").strip().lower().strip("/")
    if not host or not hint:
        return False
    if "/" not in hint:
        return domain_hint_matches_host(host, hint)
    hint_host, hint_path = hint.split("/", 1)
    if not domain_hint_matches_host(host, hint_host):
        return False
    return parsed.path.lower().lstrip("/").startswith(hint_path)


def domain_hint_matches_host(host: str, domain_hint: str) -> bool:
    value = re.sub(r"^www\.", "", str(host or "").lower(), flags=re.IGNORECASE)
    hint = re.sub(r"^www\.", "", str(domain_hint or "").lower().split("/", 1)[0], flags=re.IGNORECASE)
    return bool(value and hint and (value == hint or value.endswith(f".{hint}")))

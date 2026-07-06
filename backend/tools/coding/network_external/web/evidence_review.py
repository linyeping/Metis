"""Evidence review for Metis Research.

The goal is to reject irrelevant crawler output before synthesis.  This is a
deterministic gate, not a model preference.
"""
from __future__ import annotations

import re
from typing import Any

from backend.tools.coding.network_external.web.question_type import extract_key_entities
from backend.tools.coding.network_external.web.source_policy import classify_source, spam_hit_count

_BOILERPLATE_PATTERNS = [
    r"下载APP|扫描下载APP|账号密码登录|注册新账号|忘记密码|退出登录",
    r"跳过此内容|主页|观看|分类|繁|简",
    r"Main menu|Navigation|Personal tools|Appearance|Jump to content",
    r"Use your Google Account|Email or phone|Forgot email",
]


def review_evidence_item(item: dict[str, Any], question: str, question_type: dict[str, Any] | None = None) -> dict[str, Any]:
    title = str(item.get("title") or "")
    url = str(item.get("url") or "")
    domain = str(item.get("domain") or "")
    snippet = str(item.get("snippet") or "")
    text = str(item.get("text") or "")
    haystack = "\n".join([title, url, domain, snippet, text[:2500]])
    policy = classify_source({"title": title, "url": url, "snippet": snippet}, question, question_type)
    qtype = str((question_type or {}).get("type") or "")
    required_entities = extract_key_entities(question)
    relevance = relevance_score(haystack, required_entities, qtype)
    boilerplate = boilerplate_score(text)
    spam_hits = spam_hit_count(haystack)

    rejected_reasons: list[str] = []
    if policy["label"] == "rejected":
        rejected_reasons.append(str(policy.get("reason") or "rejected source"))
    if relevance < 0.25:
        rejected_reasons.append("missing required entity/topic match")
    if boilerplate > 0.45:
        rejected_reasons.append("crawler boilerplate dominates content")
    if spam_hits >= 2:
        rejected_reasons.append("spam/ad source")

    return {
        "accepted": not rejected_reasons,
        "reasons": rejected_reasons,
        "relevance": relevance,
        "authority": policy["label"],
        "authority_score": policy["score"],
        "authority_reason": policy["reason"],
        "core_allowed": bool(policy.get("core_allowed", True)) and not rejected_reasons,
        "source_role": policy.get("role") or ("core" if policy.get("core_allowed", True) else "context"),
        "boilerplate_score": boilerplate,
        "spam_hits": spam_hits,
    }


def relevance_score(text: str, entities: list[str], qtype: str) -> float:
    value = str(text or "").lower()
    if not entities:
        return 0.65
    hits = 0
    for entity in entities:
        variants = entity_variants(entity)
        if any(variant in value for variant in variants):
            hits += 1
    score = hits / max(1, len(entities))
    # Official release checks are especially sensitive to version mentions.
    if qtype == "official_release_check":
        model_entities = [entity for entity in entities if re.search(r"\d", entity)]
        if model_entities and not any(any(variant in value for variant in entity_variants(entity)) for entity in model_entities):
            score = min(score, 0.2)
    return max(0.0, min(1.0, score))


def entity_variants(entity: str) -> list[str]:
    lowered = str(entity or "").lower().strip()
    if not lowered:
        return []
    variants = {lowered}
    variants.add(lowered.replace(" ", "-"))
    variants.add(lowered.replace("-", " "))
    variants.add(lowered.replace(" ", ""))
    variants.add(lowered.replace("-", ""))
    if lowered.startswith("gpt"):
        variants.add(lowered.replace("gpt", "gpt-", 1).replace("--", "-"))
    return [item for item in variants if item]


def boilerplate_score(text: str) -> float:
    value = str(text or "")
    if not value.strip():
        return 0.0
    lines = [line.strip() for line in value.splitlines() if line.strip()]
    if not lines:
        return 0.0
    bad = 0
    for line in lines[:120]:
        if any(re.search(pattern, line, flags=re.IGNORECASE) for pattern in _BOILERPLATE_PATTERNS):
            bad += 1
        elif len(line) <= 3:
            bad += 1
    return bad / max(1, min(len(lines), 120))


def summarize_rejections(reviews: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for review in reviews:
        if review.get("accepted"):
            continue
        rows.append({"title": review.get("title") or "", "url": review.get("url") or "", "reasons": review.get("reasons") or []})
    return rows

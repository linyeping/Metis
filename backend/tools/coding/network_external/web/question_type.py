"""Question classification for Metis web research.

This module is intentionally heuristic-first.  It gives the research pipeline a
stable routing signal before any LLM planning happens, so high-risk questions
such as "has product X been officially released?" can use stricter source gates.
"""
from __future__ import annotations

import re
from typing import Any

QuestionType = dict[str, Any]

_OFFICIAL_RELEASE_PATTERNS = [
    r"是否.*官方.*(?:发布|推出|上线|公布)",
    r"是否.*(?:已经|已).*发布",
    r"(?:官方|正式).*(?:发布|推出|上线|公布)",
    r"has .* officially (?:released|launched|announced)",
    r"official(?:ly)? (?:released|launched|announced)",
]

_LATEST_PATTERNS = [
    r"最新|最近|近期|today|latest|recent",
    r"反响|评价|reaction|feedback|response",
]

_COMPARISON_PATTERNS = [
    r"对比|比较|区别|差异|versus|\bvs\.?\b|compare|comparison",
]

_REPO_PATTERNS = [
    r"github\.com/[\w.-]+/[\w.-]+",
    r"\brepo\b|repository|代码库|仓库",
]


def classify_question(question: str) -> QuestionType:
    text = str(question or "").strip()
    lowered = text.lower()
    qtype = "general_explainer"
    confidence = 0.45
    reasons: list[str] = []

    if _matches_any(text, _OFFICIAL_RELEASE_PATTERNS):
        qtype = "official_release_check"
        confidence = 0.92
        reasons.append("official release wording")
    elif _matches_any(text, _REPO_PATTERNS):
        qtype = "technical_repo_research"
        confidence = 0.82
        reasons.append("repository wording")
    elif _matches_any(text, _COMPARISON_PATTERNS):
        qtype = "comparison"
        confidence = 0.72
        reasons.append("comparison wording")
    elif _matches_any(text, _LATEST_PATTERNS):
        qtype = "latest_news"
        confidence = 0.66
        reasons.append("latest/news wording")

    entities = extract_key_entities(text)
    return {
        "type": qtype,
        "confidence": confidence,
        "requires_official_evidence": qtype == "official_release_check",
        "entities": entities,
        "reasons": reasons,
        "question": text,
        "lowered": lowered,
    }


def extract_key_entities(question: str) -> list[str]:
    text = str(question or "")
    entities: list[str] = []
    patterns = [
        r"\bGPT[-\s]?\d+(?:\.\d+)?\b",
        r"\bGemini\s+\d+(?:\.\d+)?\b",
        r"\bClaude\s+\d+(?:\.\d+)?\b",
        r"\bOpenAI\b",
        r"\bChatGPT\b",
        r"\bGoogle\b",
        r"\bAnthropic\b",
        r"\bGemini\b",
        r"\bClaude\b",
    ]
    for pattern in patterns:
        for match in re.finditer(pattern, text, flags=re.IGNORECASE):
            value = re.sub(r"\s+", " ", match.group(0)).strip()
            if value and value.lower() not in {item.lower() for item in entities}:
                entities.append(value)
    return entities


def _matches_any(text: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

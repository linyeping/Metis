from __future__ import annotations

import json
from collections.abc import Mapping as MappingABC
from typing import Any, Dict, List, Mapping, Optional

from .llm_backends import Usage


_DEFAULT_CONTEXT_LIMIT = 128_000
IMAGE_BLOCK_TOKEN_ESTIMATE = 1600
_SYSTEM_BREAKDOWN_KEYS = ("system_prompt", "skills", "memory")
_SCHEMA_BREAKDOWN_KEYS = ("mcp", "builtin")
_SYSTEM_MARKER_CATEGORIES = {
    "[可用技能 / Available Skills]": "skills",
    "[Desktop Automation Skill Reference]": "skills",
    "[User METIS.md]": "memory",
}
_MODEL_CONTEXT_LIMITS: Dict[str, int] = {
    "deepseek-v4-flash": 1_000_000,
    "deepseek-v4-pro": 1_000_000,
    "deepseek-chat": 128_000,
    "deepseek-coder": 128_000,
    "deepseek-reasoner": 64_000,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4-turbo": 128_000,
    "gpt-4.1": 1_047_576,
    "o3": 200_000,
    "o3-mini": 200_000,
    "o4-mini": 200_000,
    "claude": 200_000,
    "gpt-5.5": 1_000_000,
    "gpt-5.4": 1_000_000,
    "codex-auto-review": 1_000_000,
    "qwen3-coder-plus": 1_000_000,
    "gemini-2.0-flash": 1_000_000,
}


def context_limit_for_model(model: str = "") -> int:
    name = str(model or "").strip().lower()
    if not name:
        return _DEFAULT_CONTEXT_LIMIT
    for key, limit in _MODEL_CONTEXT_LIMITS.items():
        if key in name:
            return limit
    return _DEFAULT_CONTEXT_LIMIT


def estimate_tokens(value: Any) -> int:
    """Return a deterministic rough token estimate for context budgeting."""
    if value is None:
        return 0
    if isinstance(value, str):
        return _estimate_text_tokens(value)
    if isinstance(value, MappingABC):
        if not value:
            return 0
        if _is_image_content_block(value):
            return IMAGE_BLOCK_TOKEN_ESTIMATE
        total = 2
        for key, item in value.items():
            total += estimate_tokens(str(key))
            total += estimate_tokens(item)
        return max(1, total)
    if isinstance(value, (list, tuple, set)) and not value:
        return 0
    if isinstance(value, (list, tuple, set)):
        return sum(estimate_tokens(item) for item in value)
    try:
        text = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except TypeError:
        text = str(value)
    return _estimate_text_tokens(text)


def _estimate_text_tokens(text: str) -> int:
    if not text:
        return 0
    cjk = 0
    other = 0
    for char in text:
        codepoint = ord(char)
        if (
            0x3400 <= codepoint <= 0x4DBF
            or 0x4E00 <= codepoint <= 0x9FFF
            or 0xF900 <= codepoint <= 0xFAFF
            or 0x3040 <= codepoint <= 0x30FF
            or 0xAC00 <= codepoint <= 0xD7AF
        ):
            cjk += 1
        else:
            other += 1
    return max(1, cjk + ((other + 3) // 4))


def _is_image_content_block(value: MappingABC[str, Any]) -> bool:
    block_type = str(value.get("type") or "").lower()
    if "image" in block_type:
        return True
    image_url = value.get("image_url")
    return isinstance(image_url, MappingABC) and bool(image_url.get("url"))


def _empty_breakdown(keys: tuple[str, ...]) -> Dict[str, int]:
    return {key: 0 for key in keys}


def _system_content_breakdown(content: Any) -> Dict[str, int]:
    breakdown = _empty_breakdown(_SYSTEM_BREAKDOWN_KEYS)
    if not isinstance(content, str):
        breakdown["system_prompt"] = estimate_tokens(content)
        return breakdown
    if not content:
        return breakdown

    markers: List[tuple[int, str]] = []
    for marker, category in _SYSTEM_MARKER_CATEGORIES.items():
        start = 0
        while True:
            index = content.find(marker, start)
            if index < 0:
                break
            markers.append((index, category))
            start = index + len(marker)
    if not markers:
        breakdown["system_prompt"] = estimate_tokens(content)
        return breakdown

    markers.sort(key=lambda item: item[0])
    deduped: List[tuple[int, str]] = []
    seen_positions: set[int] = set()
    for position, category in markers:
        if position in seen_positions:
            continue
        deduped.append((position, category))
        seen_positions.add(position)

    cursor = 0
    current_category = "system_prompt"
    for position, next_category in deduped:
        if position > cursor:
            breakdown[current_category] += estimate_tokens(content[cursor:position])
        current_category = next_category
        cursor = position
    if cursor < len(content):
        breakdown[current_category] += estimate_tokens(content[cursor:])
    return breakdown


def _system_breakdown(messages: List[Mapping[str, Any]]) -> Dict[str, int]:
    breakdown = _empty_breakdown(_SYSTEM_BREAKDOWN_KEYS)
    for message in messages:
        if str(message.get("role") or "") != "system":
            continue
        for key, value in _system_content_breakdown(message.get("content")).items():
            breakdown[key] += value
        if message.get("tool_calls"):
            breakdown["system_prompt"] += estimate_tokens(message.get("tool_calls"))
        if message.get("name"):
            breakdown["system_prompt"] += estimate_tokens(message.get("name"))
    return breakdown


def _schema_breakdown(tools: Optional[List[Mapping[str, Any]]]) -> Dict[str, int]:
    breakdown = _empty_breakdown(_SCHEMA_BREAKDOWN_KEYS)
    for tool in tools or []:
        category = "mcp" if _is_mcp_tool_schema(tool) else "builtin"
        breakdown[category] += estimate_tokens(tool)
    return breakdown


def _is_mcp_tool_schema(tool: Mapping[str, Any]) -> bool:
    source = str(tool.get("source") or tool.get("_metis_source") or "").strip().lower()
    function = tool.get("function") if isinstance(tool.get("function"), MappingABC) else {}
    name = str(tool.get("name") or function.get("name") or "").strip()
    description = str(tool.get("description") or function.get("description") or "").lstrip()
    return source.startswith("mcp:") or name.startswith("mcp_") or description.startswith("[MCP:")


def context_ledger(
    messages: List[Mapping[str, Any]],
    tools: Optional[List[Mapping[str, Any]]] = None,
    *,
    usage: Optional[Usage] = None,
    model: str = "",
) -> Dict[str, Any]:
    system_tokens = 0
    history_tokens = 0
    for message in messages:
        role = str(message.get("role") or "")
        tokens = estimate_tokens(message.get("content"))
        if message.get("tool_calls"):
            tokens += estimate_tokens(message.get("tool_calls"))
        if message.get("name"):
            tokens += estimate_tokens(message.get("name"))
        if role == "system":
            system_tokens += tokens
        else:
            history_tokens += tokens

    schema_tokens = estimate_tokens(tools or [])
    system_parts = _system_breakdown(messages)
    schema_parts = _schema_breakdown(tools)
    estimated_total = system_tokens + schema_tokens + history_tokens
    limit = context_limit_for_model(model)
    cache_hit = int(getattr(usage, "prompt_cache_hit_tokens", 0) or 0)
    cache_miss = int(getattr(usage, "prompt_cache_miss_tokens", 0) or 0)

    return {
        "system_tokens": system_tokens,
        "schema_tokens": schema_tokens,
        "history_tokens": history_tokens,
        "estimated_total_tokens": estimated_total,
        "context_limit": limit,
        "context_ratio": round(estimated_total / limit, 4) if limit > 0 else 0.0,
        "cache_hit_tokens": cache_hit,
        "cache_miss_tokens": cache_miss,
        # FABLEADV-25: 命中率突降 = prompt 前缀有变（缓存被打破）。是诊断负优化的核心指标。
        "cache_hit_rate": round(cache_hit / (cache_hit + cache_miss), 4) if (cache_hit + cache_miss) > 0 else 0.0,
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
        "message_count": len(messages),
        "tool_count": len(tools or []),
        "system_breakdown": system_parts,
        "schema_breakdown": schema_parts,
    }

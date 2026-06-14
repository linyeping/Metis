from __future__ import annotations

import json
from collections.abc import Mapping as MappingABC
from typing import Any, Dict, List, Mapping, Optional

from .llm_backends import Usage


_DEFAULT_CONTEXT_LIMIT = 128_000
IMAGE_BLOCK_TOKEN_ESTIMATE = 1600
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
    }

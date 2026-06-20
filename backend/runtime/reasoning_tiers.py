"""Per-model reasoning-effort tiers.

Different vendors expose different reasoning-effort ladders (verified 2026-06):
  - GPT-5.5 / GPT-5.4 : low, medium, high, xhigh
  - GPT-5.2           : minimal, low, medium, high, xhigh
  - GPT-5 / o-series  : minimal, low, medium, high
  - Claude (opus/sonnet, thinking budgets): low, medium, high, xhigh, max
  - Gemini            : low, medium, high
  - DeepSeek reasoner : low, high, max

The composer shows ONLY the tiers a model supports (plus an "off" switch the UI
adds), and the backend passes the real chosen level through instead of
collapsing everything to "high".
"""
from __future__ import annotations

import re
from typing import List

# Canonical low→high ordering used for clamping.
CANONICAL_ORDER: List[str] = ["minimal", "low", "medium", "high", "xhigh", "max"]

# (compiled model-id pattern, ordered reasoning levels). First match wins.
_TIERS = [
    (re.compile(r"gpt-?5\.(5|4)", re.I), ["low", "medium", "high", "xhigh"]),
    (re.compile(r"gpt-?5\.2", re.I), ["minimal", "low", "medium", "high", "xhigh"]),
    (re.compile(r"gpt-?5|(\b|[-_])o[1345](\b|[-_])", re.I), ["minimal", "low", "medium", "high"]),
    (re.compile(r"claude|opus|sonnet|haiku|fable", re.I), ["low", "medium", "high", "xhigh", "max"]),
    (re.compile(r"gemini", re.I), ["low", "medium", "high"]),
    # DeepSeek: only the reasoning models (v4+, r1/reasoner) — NOT deepseek-chat.
    (re.compile(r"deepseek[-_]?(v[4-9]|r\d|reason)", re.I), ["low", "high", "max"]),
    (re.compile(r"\b(qwq|glm-?z|grok.*reason)", re.I), ["low", "medium", "high"]),
]

# Unknown / non-reasoning models inject nothing — sending reasoning_effort to a
# model that doesn't support it (gpt-4o, deepseek-chat, ...) errors. Only models
# matched above are treated as reasoning-capable.
_DEFAULT: List[str] = []


def effort_levels_for(model: str) -> List[str]:
    """Return the ordered reasoning levels a model supports (excluding 'off').

    Empty list = the model is not reasoning-capable (do not inject effort).
    """
    name = str(model or "").strip()
    if not name:
        return list(_DEFAULT)
    for pattern, levels in _TIERS:
        if pattern.search(name):
            return list(levels)
    return list(_DEFAULT)


def clamp_effort(effort: str, levels: List[str]) -> str:
    """Map a chosen level onto the nearest level the model actually supports.

    e.g. 'max' on a model that tops out at 'xhigh' -> 'xhigh';
    'minimal' on a model that starts at 'low' -> 'low'.
    """
    effort = str(effort or "").strip().lower()
    if effort in levels:
        return effort
    if not levels:
        return effort
    try:
        want = CANONICAL_ORDER.index(effort)
    except ValueError:
        return levels[-1]
    # pick the supported level whose canonical rank is closest to the request.
    best = min(levels, key=lambda lv: abs(CANONICAL_ORDER.index(lv) - want) if lv in CANONICAL_ORDER else 99)
    return best

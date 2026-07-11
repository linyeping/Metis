from __future__ import annotations

import re
import time
from collections.abc import Mapping
from typing import Any, Dict, List, Optional

from .context_budget import estimate_tokens


COMPACT_STATE_VERSION = 2
COMPACT_MARKER = "[Metis Context Compact v2]"
USER_INTENT_TOKEN_BUDGET = 20_000
USER_INTENT_LEDGER_MARKER = "[Preserved User Intent Ledger]"
COMPACT_SECTION_KEYS = (
    "user_intent",
    "current_work",
    "changed_files",
    "decisions",
    "errors_fixes",
    "tool_evidence",
    "user_preferences",
    "next_step",
)

_SECTION_LABELS = {
    "user_intent": "User Intent",
    "current_work": "Current Work",
    "changed_files": "Changed Files",
    "decisions": "Decisions",
    "errors_fixes": "Errors And Fixes",
    "tool_evidence": "Tool Evidence",
    "user_preferences": "User Preferences",
    "next_step": "Next Step",
}

_SECTION_LIMITS = {
    "user_intent": 3,
    "current_work": 4,
    "changed_files": 18,
    "decisions": 8,
    "errors_fixes": 8,
    "tool_evidence": 14,
    "user_preferences": 8,
    "next_step": 4,
}

_EDIT_TOOL_HINTS = (
    "write",
    "append",
    "replace",
    "edit",
    "patch",
    "rename",
    "delete",
    "create",
    "move",
)

_ERROR_HINTS = (
    "error",
    "failed",
    "failure",
    "traceback",
    "exception",
    "denied",
    "cancelled",
    "错误",
    "失败",
    "异常",
    "拒绝",
)

_DECISION_HINTS = (
    "decided",
    "choose",
    "chosen",
    "approach",
    "strategy",
    "plan",
    "will",
    "implemented",
    "决定",
    "采用",
    "方案",
    "计划",
    "实现",
)

_PREFERENCE_HINTS = (
    "prefer",
    "preference",
    "always",
    "never",
    "do not",
    "don't",
    "中文",
    "不要",
    "先不要",
    "只",
    "必须",
    "devlog",
    "release",
    "commit",
)


def normalize_compact_state(value: Any) -> Dict[str, Any]:
    """Return a stable compact-state dict.

    v1 states only had summary and boundary fields. v2 keeps those fields
    intact while adding structured sections and rehydration hints.
    """

    if not isinstance(value, Mapping):
        return {}
    raw_sections = value.get("sections")
    sections = _normalize_sections(raw_sections if isinstance(raw_sections, Mapping) else {})
    summary = str(value.get("summary") or "").strip()
    if not summary and any(sections.values()):
        summary = render_summary_from_sections(sections)
    if not summary:
        return {}

    requested_version = _safe_int(value.get("version", value.get("schema_version", 1)), 1)
    version = COMPACT_STATE_VERSION if requested_version >= COMPACT_STATE_VERSION or any(sections.values()) else 1
    state: Dict[str, Any] = {
        "version": version,
        "mode": _normalize_compact_mode(
            str(value.get("mode") or value.get("compact_mode") or value.get("compactMode") or "partial_older")
        ),
        "summary": summary,
        "boundary_message_id": str(value.get("boundary_message_id") or value.get("boundaryMessageId") or ""),
        "boundary_index": max(0, _safe_int(value.get("boundary_index", value.get("boundaryIndex")), 0)),
        "compacted_at": _safe_float(value.get("compacted_at", value.get("compactedAt")), 0.0),
        "compact_count": max(0, _safe_int(value.get("compact_count", value.get("compactCount")), 0)),
    }
    if version >= COMPACT_STATE_VERSION:
        state.update(
            {
                "sections": sections,
                "rehydration_hints": _coerce_text_list(
                    value.get("rehydration_hints", value.get("rehydrationHints")),
                    limit=8,
                    item_limit=260,
                ),
                "preserved_message_ids": _coerce_text_list(
                    value.get("preserved_message_ids", value.get("preservedMessageIds")),
                    limit=256,
                    item_limit=120,
                ),
                "preserved_user_message_ids": _coerce_text_list(
                    value.get("preserved_user_message_ids", value.get("preservedUserMessageIds")),
                    limit=256,
                    item_limit=120,
                ),
                "user_intent_token_budget": max(
                    1,
                    _safe_int(
                        value.get("user_intent_token_budget", value.get("userIntentTokenBudget")),
                        USER_INTENT_TOKEN_BUDGET,
                    ),
                ),
                "source_message_count": max(
                    0,
                    _safe_int(value.get("source_message_count", value.get("sourceMessageCount")), 0),
                ),
            }
        )
    return state


def compact_boundary_index(history: List[Dict[str, Any]], compact_state: Any) -> int:
    state = normalize_compact_state(compact_state)
    if not state:
        return 0
    boundary_id = str(state.get("boundary_message_id") or "")
    if boundary_id:
        for index, message in enumerate(history):
            if isinstance(message, Mapping) and str(message.get("id") or "") == boundary_id:
                return index
    return min(max(0, _safe_int(state.get("boundary_index"), 0)), len(history))


def model_context_for_history(history: List[Dict[str, Any]], compact_state: Any) -> List[Dict[str, Any]]:
    state = normalize_compact_state(compact_state)
    if not state:
        return model_visible_history(history)
    boundary_index = compact_boundary_index(history, state)
    summary_message = {"role": "system", "content": render_compact_context(state)}
    mode = str(state.get("mode") or "partial_older").strip().lower()
    if _safe_int(state.get("version"), 1) < COMPACT_STATE_VERSION:
        if mode == "full":
            return [summary_message]
        if mode == "partial_recent":
            return model_visible_history(history[:boundary_index]) + [summary_message]
        return [summary_message] + model_visible_history(history[boundary_index:])
    user_budget = max(1, _safe_int(state.get("user_intent_token_budget"), USER_INTENT_TOKEN_BUDGET))
    if mode == "full":
        return [summary_message] + preserved_user_intent_messages(history, token_budget=user_budget)
    if mode == "partial_recent":
        summarized = history[boundary_index:]
        return (
            model_visible_history(history[:boundary_index])
            + [summary_message]
            + preserved_user_intent_messages(summarized, token_budget=user_budget)
        )
    summarized = history[:boundary_index]
    return (
        [summary_message]
        + preserved_user_intent_messages(summarized, token_budget=user_budget)
        + model_visible_history(history[boundary_index:])
    )


def model_visible_history(history: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Return messages that belong in a provider request.

    Transcript-only tool cards are persisted for the desktop UI, but they are
    not valid model messages and must not affect compaction thresholds.
    """

    visible: List[Dict[str, Any]] = []
    for message in history:
        if not isinstance(message, Mapping):
            continue
        if _is_transcript_only_tool(message):
            evidence = _transcript_tool_context_message(message)
            if evidence:
                visible.append(evidence)
            continue
        visible.append(dict(message))
    return visible


def recent_turn_boundary(history: List[Dict[str, Any]], keep_recent_turns: int = 6) -> int:
    """Return the raw-history index that starts the recent user-turn tail."""

    keep = max(0, int(keep_recent_turns or 0))
    if keep <= 0:
        return len(history)
    seen_users = 0
    for index in range(len(history) - 1, -1, -1):
        message = history[index]
        if not isinstance(message, Mapping) or _is_transcript_only_tool(message):
            continue
        if _role(message) != "user":
            continue
        seen_users += 1
        if seen_users == keep:
            return index
    return 0


def preserved_user_intent_messages(
    messages: List[Dict[str, Any]],
    *,
    token_budget: int = USER_INTENT_TOKEN_BUDGET,
) -> List[Dict[str, Any]]:
    """Select old user-authored intent verbatim within a bounded hot-context budget."""

    budget = max(1, int(token_budget or USER_INTENT_TOKEN_BUDGET))
    selected: List[Dict[str, Any]] = []
    used = 0
    for raw_message in reversed(messages):
        if not isinstance(raw_message, Mapping) or _role(raw_message) != "user":
            continue
        message = _user_intent_message(raw_message)
        tokens = max(1, estimate_tokens(message.get("content")))
        if selected and used + tokens > budget:
            remaining = budget - used
            if remaining > 64:
                message["content"] = _truncate_content_to_token_budget(message.get("content"), remaining)
                selected.append(message)
            break
        if not selected and tokens > budget:
            message["content"] = _truncate_content_to_token_budget(message.get("content"), budget)
            tokens = max(1, estimate_tokens(message.get("content")))
        selected.append(message)
        used += tokens
        if used >= budget:
            break
    selected.reverse()
    if not selected:
        return []
    return [
        {
            "role": "system",
            "content": (
                f"{USER_INTENT_LEDGER_MARKER}\n"
                "The following messages are preserved user-authored instructions from compacted history. "
                "Keep their original order. When instructions conflict, the later user message wins."
            ),
        },
        *selected,
    ]


def compact_state_after_truncate(
    original_history: List[Dict[str, Any]],
    new_history: List[Dict[str, Any]],
    compact_state: Any,
) -> Dict[str, Any]:
    state = normalize_compact_state(compact_state)
    if not state:
        return {}
    boundary_index = compact_boundary_index(original_history, state)
    if len(new_history) <= boundary_index:
        return {}
    boundary_id = str(state.get("boundary_message_id") or "")
    if boundary_id and not any(str(message.get("id") or "") == boundary_id for message in new_history if isinstance(message, Mapping)):
        return {}
    next_state = dict(state)
    next_boundary = compact_boundary_index(new_history, state)
    next_state["boundary_index"] = next_boundary
    if _safe_int(next_state.get("version"), 1) >= COMPACT_STATE_VERSION:
        if str(next_state.get("mode") or "") == "partial_recent":
            preserved_segment = new_history[:next_boundary]
            summarized_segment = new_history[next_boundary:]
        else:
            preserved_segment = new_history[next_boundary:]
            summarized_segment = new_history[:next_boundary]
        next_state["preserved_message_ids"] = _message_ids(model_visible_history(preserved_segment), limit=256)
        intent_messages = preserved_user_intent_messages(
            summarized_segment,
            token_budget=max(
                1,
                _safe_int(next_state.get("user_intent_token_budget"), USER_INTENT_TOKEN_BUDGET),
            ),
        )
        next_state["preserved_user_message_ids"] = _message_ids(intent_messages, limit=256)
        next_state["source_message_count"] = min(
            max(0, _safe_int(next_state.get("source_message_count"), 0)),
            max(0, len(new_history) - next_boundary)
            if str(next_state.get("mode") or "") == "partial_recent"
            else next_boundary,
        )
    return next_state


def build_compact_state_v2(
    history: List[Dict[str, Any]],
    *,
    summary: str,
    keep_recent: int = 6,
    previous_state: Any = None,
    compacted_at: Optional[float] = None,
    mode: str = "partial_older",
    boundary_index: Optional[int] = None,
) -> Dict[str, Any]:
    previous = normalize_compact_state(previous_state)
    compact_mode = _normalize_compact_mode(mode)
    if boundary_index is None:
        boundary_index = (
            len(history)
            if compact_mode == "full"
            else recent_turn_boundary(history, keep_recent_turns=max(0, keep_recent))
        )
    else:
        boundary_index = max(0, min(_safe_int(boundary_index, 0), len(history)))
    boundary_message_id = ""
    if boundary_index < len(history) and isinstance(history[boundary_index], Mapping):
        boundary_message_id = str(history[boundary_index].get("id") or "")

    section_history = history[boundary_index:] if compact_mode == "partial_recent" else history
    section_boundary = len(section_history) if compact_mode in {"full", "partial_recent"} else boundary_index
    extracted = extract_compact_sections(section_history, boundary_index=section_boundary)
    previous_sections = previous.get("sections") if isinstance(previous.get("sections"), Mapping) else {}
    sections = merge_sections(extracted, previous_sections)
    clean_summary = str(summary or "").strip()
    if not clean_summary:
        clean_summary = render_summary_from_sections(sections)
    preserved_messages = history[:boundary_index] if compact_mode == "partial_recent" else history[boundary_index:]
    source_message_count = len(history[boundary_index:]) if compact_mode == "partial_recent" else boundary_index
    summarized_messages = history[boundary_index:] if compact_mode == "partial_recent" else history[:boundary_index]
    preserved_user_messages = preserved_user_intent_messages(
        summarized_messages,
        token_budget=USER_INTENT_TOKEN_BUDGET,
    )
    preserved_user_ids = _message_ids(preserved_user_messages, limit=256)

    return {
        "version": COMPACT_STATE_VERSION,
        "mode": compact_mode,
        "summary": clean_summary,
        "boundary_message_id": boundary_message_id,
        "boundary_index": boundary_index,
        "compacted_at": float(compacted_at if compacted_at is not None else time.time()),
        "compact_count": max(0, _safe_int(previous.get("compact_count"), 0)) + 1,
        "source_message_count": source_message_count,
        "sections": sections,
        "preserved_message_ids": _message_ids(model_visible_history(preserved_messages), limit=256),
        "preserved_user_message_ids": preserved_user_ids,
        "user_intent_token_budget": USER_INTENT_TOKEN_BUDGET,
        "rehydration_hints": [
            "The full persisted transcript remains the source of truth; this message is only a compact continuity layer.",
            _rehydration_hint_for_mode(compact_mode),
            (
                f"User-authored instructions from compacted history are replayed verbatim under "
                f"{USER_INTENT_LEDGER_MARKER}, bounded to about {USER_INTENT_TOKEN_BUDGET} tokens."
                if preserved_user_messages
                else "No user-authored instructions were found in the compacted segment."
            ),
            "Re-run read/search/observe tools when exact omitted file, page, or desktop details matter.",
        ],
    }


def extract_compact_sections(
    history: List[Dict[str, Any]],
    *,
    boundary_index: Optional[int] = None,
) -> Dict[str, List[str]]:
    boundary = len(history) if boundary_index is None else max(0, min(boundary_index, len(history)))
    compacted = history[:boundary]
    sections = _empty_sections()
    user_messages = [message for message in history if _role(message) == "user"]
    compacted_users = [message for message in compacted if _role(message) == "user"]

    if compacted_users:
        _append_unique(sections["user_intent"], _truncate(_message_text(compacted_users[0]), 260))
    elif user_messages:
        _append_unique(sections["user_intent"], _truncate(_message_text(user_messages[0]), 260))

    if user_messages:
        _append_unique(sections["current_work"], _truncate(_message_text(user_messages[-1]), 280))
        _append_unique(sections["next_step"], _truncate(_message_text(user_messages[-1]), 240))

    for message in compacted:
        role = _role(message)
        text = _message_text(message)
        lowered = text.lower()
        if role == "user" and _matches_any(lowered, _PREFERENCE_HINTS):
            _append_unique(sections["user_preferences"], _truncate(text, 220))
        if role == "assistant":
            if _matches_any(lowered, _DECISION_HINTS):
                _append_unique(sections["decisions"], _truncate(text, 260))
            if _matches_any(lowered, _ERROR_HINTS):
                _append_unique(sections["errors_fixes"], _truncate(text, 260))

        tool = _tool_record(message)
        if not tool:
            continue
        name = str(tool.get("name") or "tool")
        status = str(tool.get("status") or "").strip().lower()
        result = str(tool.get("result") or tool.get("content") or "")
        args = tool.get("arguments") if isinstance(tool.get("arguments"), Mapping) else {}
        hint = _file_hint(name, args, result)
        evidence = f"{name}: {status or 'observed'}"
        if hint:
            evidence += f" ({hint})"
        _append_unique(sections["tool_evidence"], _truncate(evidence, 220))
        if _is_edit_tool(name) and hint:
            _append_unique(sections["changed_files"], hint)
        if status == "error" or _matches_any(result.lower(), _ERROR_HINTS):
            detail = f"{name}: {_first_line(result) or status or 'error'}"
            _append_unique(sections["errors_fixes"], _truncate(detail, 240))

    return _trim_sections(sections)


def merge_sections(*section_maps: Any) -> Dict[str, List[str]]:
    merged = _empty_sections()
    for section_map in section_maps:
        sections = _normalize_sections(section_map if isinstance(section_map, Mapping) else {})
        for key in COMPACT_SECTION_KEYS:
            for item in sections.get(key, []):
                _append_unique(merged[key], item)
    return _trim_sections(merged)


def render_compact_context(compact_state: Any) -> str:
    state = normalize_compact_state(compact_state)
    if not state:
        return ""
    if _safe_int(state.get("version"), 1) < COMPACT_STATE_VERSION:
        return str(state.get("summary") or "")

    sections = _normalize_sections(state.get("sections") if isinstance(state.get("sections"), Mapping) else {})
    lines = [
        COMPACT_MARKER,
        "This is a compact continuity layer. The full transcript is still persisted outside model context.",
        f"Compaction count: {max(0, _safe_int(state.get('compact_count'), 0))}.",
        f"Compaction mode: {str(state.get('mode') or 'partial_older')}.",
    ]
    boundary_id = str(state.get("boundary_message_id") or "")
    if boundary_id:
        lines.append(f"Boundary message id: {boundary_id}.")
    lines.extend(["", "## Summary", _strip_compact_marker(str(state.get("summary") or "").strip()) or "(no summary)"])

    for key in COMPACT_SECTION_KEYS:
        items = sections.get(key, [])
        if not items:
            continue
        lines.extend(["", f"## {_SECTION_LABELS[key]}"])
        lines.extend(f"- {item}" for item in items[: _SECTION_LIMITS[key]])

    hints = _coerce_text_list(state.get("rehydration_hints"), limit=8, item_limit=260)
    if hints:
        lines.extend(["", "## Rehydration Hints"])
        lines.extend(f"- {hint}" for hint in hints)
    return "\n".join(lines).strip()


def render_summary_from_sections(sections: Any) -> str:
    normalized = _normalize_sections(sections if isinstance(sections, Mapping) else {})
    lines = ["[Context Summary]"]
    for key in COMPACT_SECTION_KEYS:
        items = normalized.get(key, [])
        if not items:
            continue
        lines.append("")
        lines.append(f"## {_SECTION_LABELS[key]}")
        lines.extend(f"- {item}" for item in items[: _SECTION_LIMITS[key]])
    return "\n".join(lines).strip()


def _normalize_compact_mode(value: str) -> str:
    mode = str(value or "partial_older").strip().lower().replace("-", "_")
    if mode in {"full", "full_context"}:
        return "full"
    if mode in {"partial_recent", "recent", "recent_context"}:
        return "partial_recent"
    return "partial_older"


def _rehydration_hint_for_mode(mode: str) -> str:
    if mode == "full":
        return "The full model context is represented by this compact summary; reload transcript details from persisted history when needed."
    if mode == "partial_recent":
        return "Messages before the boundary are still included before this compact summary; the recent tail is summarized."
    return "Messages at and after the boundary are still included after this compact summary."


def _normalize_sections(value: Mapping[str, Any]) -> Dict[str, List[str]]:
    sections = _empty_sections()
    for key in COMPACT_SECTION_KEYS:
        camel_key = _to_camel(key)
        raw = value.get(key, value.get(camel_key))
        sections[key] = _coerce_text_list(
            raw,
            limit=_SECTION_LIMITS[key],
            item_limit=280,
        )
    return sections


def _empty_sections() -> Dict[str, List[str]]:
    return {key: [] for key in COMPACT_SECTION_KEYS}


def _trim_sections(sections: Dict[str, List[str]]) -> Dict[str, List[str]]:
    return {
        key: list(sections.get(key, []))[: _SECTION_LIMITS[key]]
        for key in COMPACT_SECTION_KEYS
    }


def _coerce_text_list(value: Any, *, limit: int, item_limit: int) -> List[str]:
    if value is None:
        return []
    raw_items: List[Any]
    if isinstance(value, str):
        raw_items = [line for line in value.splitlines() if line.strip()]
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = [value]

    items: List[str] = []
    for raw in raw_items:
        text = _strip_bullet(str(raw or "").strip())
        if not text:
            continue
        _append_unique(items, _truncate(text, item_limit))
        if len(items) >= limit:
            break
    return items


def _message_ids(messages: List[Dict[str, Any]], *, limit: int = 256) -> List[str]:
    ids: List[str] = []
    for message in messages:
        if isinstance(message, Mapping):
            message_id = str(message.get("id") or "").strip()
            if message_id:
                ids.append(message_id)
    return ids[: max(1, limit)]


def _role(message: Any) -> str:
    return str(message.get("role") or "") if isinstance(message, Mapping) else ""


def _message_text(message: Any) -> str:
    if not isinstance(message, Mapping):
        return str(message or "")
    return _content_text(message.get("content"))


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping):
                if block.get("type") == "image_url":
                    parts.append("[Image attachment]")
                else:
                    parts.append(str(block.get("text") or block.get("content") or ""))
        return "\n".join(part for part in parts if part)
    return str(content or "")


def _is_transcript_only_tool(message: Mapping[str, Any]) -> bool:
    return str(message.get("metis_kind") or "") == "tool"


def _transcript_tool_context_message(message: Mapping[str, Any]) -> Dict[str, Any]:
    tool = message.get("metis_tool") if isinstance(message.get("metis_tool"), Mapping) else {}
    if not tool:
        return {}
    name = str(tool.get("name") or "tool").strip() or "tool"
    status = str(tool.get("status") or "observed").strip() or "observed"
    summary = _truncate(str(tool.get("summary") or "").strip(), 260)
    result = str(tool.get("result") or "")
    archive_line = ""
    for line in result.splitlines():
        if line.startswith("[Full tool result archived:"):
            archive_line = line[:700]
            break
    details = f"[Persisted tool evidence] {name}: {status}."
    if summary:
        details += f" {summary}"
    if archive_line:
        details += f"\n{archive_line}"
    return {"role": "assistant", "content": details}


def _user_intent_message(message: Mapping[str, Any]) -> Dict[str, Any]:
    content = message.get("content")
    if isinstance(content, list):
        blocks: List[Dict[str, Any]] = []
        inserted_image_placeholder = False
        for block in content:
            if isinstance(block, str):
                blocks.append({"type": "text", "text": block})
                continue
            if not isinstance(block, Mapping):
                continue
            block_type = str(block.get("type") or "").lower()
            if "image" in block_type or isinstance(block.get("image_url"), Mapping):
                if not inserted_image_placeholder:
                    blocks.append(
                        {
                            "type": "text",
                            "text": "[Historical image omitted from hot context; re-open the persisted transcript if needed.]",
                        }
                    )
                    inserted_image_placeholder = True
                continue
            text = str(block.get("text") or block.get("content") or "")
            if text:
                blocks.append({"type": "text", "text": text})
        clean_content: Any = blocks
    else:
        clean_content = str(content or "")
    clean: Dict[str, Any] = {"role": "user", "content": clean_content}
    message_id = str(message.get("id") or "").strip()
    if message_id:
        clean["id"] = message_id
    return clean


def _truncate_content_to_token_budget(content: Any, token_budget: int) -> Any:
    text = _content_text(content)
    if estimate_tokens(text) <= token_budget:
        return content
    if not text:
        return ""
    low = 0
    high = len(text)
    while low < high:
        middle = (low + high + 1) // 2
        if estimate_tokens(text[:middle]) <= token_budget:
            low = middle
        else:
            high = middle - 1
    return text[:low].rstrip() + "\n[User message truncated at the preserved-intent token budget.]"


def _tool_record(message: Mapping[str, Any]) -> Dict[str, Any]:
    if message.get("metis_kind") == "tool" and isinstance(message.get("metis_tool"), Mapping):
        tool = dict(message.get("metis_tool") or {})
        tool.setdefault("content", tool.get("result", ""))
        return tool
    if str(message.get("role") or "") == "tool":
        return {
            "name": str(message.get("name") or "tool"),
            "content": _content_text(message.get("content")),
            "result": _content_text(message.get("content")),
            "status": "observed",
            "arguments": {},
        }
    return {}


def _file_hint(tool_name: str, args: Mapping[str, Any], result: str) -> str:
    for key in ("path", "file", "file_path", "target", "target_path", "relative_path"):
        value = str(args.get(key) or "").strip()
        if value:
            return value
    text = str(result or "")
    for line in text.splitlines()[:16]:
        stripped = line.strip()
        if stripped.startswith("=== ") and stripped.endswith(" ==="):
            return stripped[4:-4].strip()
        for prefix in ("File: ", "Path: ", "Saved: ", "Modified: ", "Created: ", "Deleted: ", "Updated: "):
            if stripped.startswith(prefix):
                return stripped[len(prefix) :].strip()
    match = re.search(r"([A-Za-z]:\\[^:*?\"<>|\r\n]+|\b[\w./-]+\.(?:py|ts|tsx|js|jsx|md|json|css|html|yml|yaml))", text)
    if match and _is_edit_tool(tool_name):
        return match.group(1).strip()
    return ""


def _first_line(text: str) -> str:
    for line in str(text or "").splitlines():
        stripped = line.strip()
        if stripped:
            return stripped[:220]
    return ""


def _is_edit_tool(tool_name: str) -> bool:
    lowered = str(tool_name or "").lower()
    return any(hint in lowered for hint in _EDIT_TOOL_HINTS)


def _matches_any(text: str, hints: tuple[str, ...]) -> bool:
    lowered = str(text or "").lower()
    return any(hint in lowered for hint in hints)


def _append_unique(items: List[str], value: str) -> None:
    text = str(value or "").strip()
    if not text:
        return
    if text not in items:
        items.append(text)


def _strip_bullet(text: str) -> str:
    return re.sub(r"^\s*(?:[-*]|\d+[.)])\s+", "", text).strip()


def _strip_compact_marker(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith(COMPACT_MARKER):
        cleaned = cleaned[len(COMPACT_MARKER) :].strip()
    return cleaned


def _truncate(text: str, limit: int) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def _safe_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return fallback


def _to_camel(value: str) -> str:
    pieces = str(value or "").split("_")
    if not pieces:
        return value
    return pieces[0] + "".join(piece[:1].upper() + piece[1:] for piece in pieces[1:])

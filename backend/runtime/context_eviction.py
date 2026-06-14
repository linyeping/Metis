from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from .context_budget import context_ledger


_ARCHIVE_PREFIX = "[Archived tool result]"
_IMAGE_ARCHIVE_TEXT = "[历史截图已移除，如需重新观察屏幕请再次截图]"
_READ_TOOLS = {"read_file", "read_multiple_files"}
_PROTECTED_TOOLS = {"todo_write", "load_skill"}


@dataclass(frozen=True)
class AutoCompactResult:
    messages: List[Dict[str, Any]]
    compacted: bool = False
    before_count: int = 0
    after_count: int = 0
    summary_preview: str = ""
    context_ratio: float = 0.0


def evict_tool_results(
    messages: List[Mapping[str, Any]],
    *,
    context_ratio: float = 0.0,
    force: bool = False,
    min_chars: int = 1000,
    protect_recent: int = 2,
) -> Tuple[List[Dict[str, Any]], int]:
    image_indexes = [
        index
        for index, message in enumerate(messages)
        if _message_has_image_blocks(message)
    ]
    protected_images = set(image_indexes[-protect_recent:]) if protect_recent > 0 else set()
    tool_indexes = [
        index
        for index, message in enumerate(messages)
        if str(message.get("role") or "") == "tool"
    ]
    protected_recent = set(tool_indexes[-protect_recent:]) if protect_recent > 0 else set()
    read_counts: Dict[str, int] = {}
    for message in messages:
        if str(message.get("role") or "") != "tool":
            continue
        tool_name = str(message.get("name") or "")
        if tool_name not in _READ_TOOLS:
            continue
        file_hint = _file_hint(message)
        if file_hint:
            read_counts[file_hint] = read_counts.get(file_hint, 0) + 1

    seen_reads: Dict[str, int] = {}
    evicted = 0
    next_messages: List[Dict[str, Any]] = []
    for index, raw_message in enumerate(messages):
        message = dict(raw_message)
        if index not in protected_images and (force or context_ratio >= 0.35):
            next_content, image_count = _evict_image_blocks(message.get("content"))
            if image_count:
                message["content"] = next_content
                evicted += image_count
        if str(message.get("role") or "") != "tool":
            next_messages.append(message)
            continue
        content = str(message.get("content") or "")
        tool_name = str(message.get("name") or "")
        file_hint = _file_hint(message)
        if tool_name in _READ_TOOLS and file_hint:
            seen_reads[file_hint] = seen_reads.get(file_hint, 0) + 1
        later_same_file = bool(
            tool_name in _READ_TOOLS
            and file_hint
            and seen_reads.get(file_hint, 0) < read_counts.get(file_hint, 0)
        )
        if (
            index not in protected_recent
            and _can_evict_tool_result(tool_name, content, min_chars=min_chars)
            and (force or context_ratio >= 0.5 or later_same_file)
        ):
            message["content"] = _archive_placeholder(tool_name, file_hint, len(content))
            evicted += 1
        next_messages.append(message)
    return next_messages, evicted


def _message_has_image_blocks(message: Mapping[str, Any]) -> bool:
    content = message.get("content")
    if not isinstance(content, list):
        return False
    return any(isinstance(block, Mapping) and _is_image_block(block) for block in content)


def _evict_image_blocks(content: Any) -> Tuple[Any, int]:
    if not isinstance(content, list):
        return content, 0
    next_blocks: List[Any] = []
    count = 0
    inserted_placeholder = False
    for block in content:
        if isinstance(block, Mapping) and _is_image_block(block):
            count += 1
            if not inserted_placeholder:
                next_blocks.append({"type": "text", "text": _IMAGE_ARCHIVE_TEXT})
                inserted_placeholder = True
            continue
        next_blocks.append(block)
    return next_blocks, count


def _is_image_block(block: Mapping[str, Any]) -> bool:
    block_type = str(block.get("type") or "").lower()
    return "image" in block_type or isinstance(block.get("image_url"), Mapping)


def maybe_auto_compact_messages(
    messages: List[Mapping[str, Any]],
    *,
    tools: Optional[List[Mapping[str, Any]]] = None,
    model: str = "",
    ratio_threshold: float = 0.7,
    keep_recent: int = 6,
) -> AutoCompactResult:
    if ratio_threshold <= 0:
        return AutoCompactResult(messages=[dict(message) for message in messages])

    ledger = context_ledger(messages, tools, model=model)
    ratio = float(ledger.get("context_ratio") or 0.0)
    if ratio < ratio_threshold or len(messages) <= keep_recent + 2:
        return AutoCompactResult(messages=[dict(message) for message in messages], context_ratio=ratio)

    compacted = mechanical_compact_messages(messages, keep_recent=keep_recent)
    return AutoCompactResult(
        messages=compacted,
        compacted=True,
        before_count=len(messages),
        after_count=len(compacted),
        summary_preview="Auto-compacted runtime context while preserving the original task and recent turns.",
        context_ratio=ratio,
    )


def mechanical_compact_messages(
    messages: List[Mapping[str, Any]],
    *,
    keep_recent: int = 6,
) -> List[Dict[str, Any]]:
    copied = [dict(message) for message in messages]
    leading_system_count = 0
    for message in copied:
        if str(message.get("role") or "") != "system":
            break
        leading_system_count += 1

    leading_system = copied[:leading_system_count]
    body = copied[leading_system_count:]
    if len(body) <= keep_recent:
        return copied

    recent = body[-keep_recent:]
    recent_ids = {id(message) for message in recent}
    protected_skill_messages = [
        dict(message)
        for message in body[:-keep_recent]
        if str(message.get("role") or "") == "tool"
        and str(message.get("name") or "") == "load_skill"
        and id(message) not in recent_ids
    ]
    for message in protected_skill_messages:
        content = str(message.get("content") or "")
        if len(content) > 8000:
            message["content"] = content[:8000].rstrip() + "\n\n[Skill content truncated to 2K tokens during compaction.]"
    summary = {
        "role": "system",
        "content": _mechanical_summary(body[:-keep_recent]),
    }
    evicted_recent, _evicted = evict_tool_results(recent, force=True, protect_recent=2)
    return leading_system + [summary] + protected_skill_messages[-8:] + evicted_recent


def _can_evict_tool_result(tool_name: str, content: str, *, min_chars: int) -> bool:
    lowered = content[:500].lower()
    if len(content) <= min_chars:
        return False
    if tool_name in _PROTECTED_TOOLS:
        return False
    if content.startswith(_ARCHIVE_PREFIX):
        return False
    if "error" in lowered:
        return False
    return True


def _archive_placeholder(tool_name: str, file_hint: str, original_chars: int) -> str:
    subject = f" File: {file_hint}." if file_hint else ""
    recovery = (
        f" Re-run {tool_name} if you need the full content."
        if tool_name
        else " Re-run the original tool if you need the full content."
    )
    return (
        f"{_ARCHIVE_PREFIX} Tool: {tool_name or 'tool'}.{subject} "
        f"Original result had {original_chars} chars.{recovery}"
    )


def _mechanical_summary(messages: List[Mapping[str, Any]]) -> str:
    original_task = _first_user_text(messages)
    changed_files = _changed_file_hints(messages)
    lines = [
        "[Auto compacted context summary]",
        "Original task:",
        _truncate(original_task, 1600) or "(not available)",
        "",
        "Changed files:",
    ]
    if changed_files:
        lines.extend(f"- {path}" for path in changed_files[:20])
    else:
        lines.append("- No edited file paths were detected in compacted history.")
    lines.extend(
        [
            "",
            "Progress:",
            "- Older conversation turns were mechanically compacted to keep the run within the model context window.",
            "- Recent messages remain below this summary and are the source of truth for the current step.",
            "",
            "Next:",
            "- Continue from the preserved recent messages.",
            "- Re-run read/search tools when a missing detail is needed.",
        ]
    )
    return "\n".join(lines)


def _first_user_text(messages: List[Mapping[str, Any]]) -> str:
    for message in messages:
        if str(message.get("role") or "") == "user":
            return _message_text(message)
    return ""


def _changed_file_hints(messages: List[Mapping[str, Any]]) -> List[str]:
    hints: List[str] = []
    seen: set[str] = set()
    edit_names = (
        "write",
        "append",
        "replace",
        "edit",
        "patch",
        "rename",
        "delete",
        "create",
    )
    for message in messages:
        tool_name = str(message.get("name") or "").lower()
        if str(message.get("role") or "") != "tool" or not any(name in tool_name for name in edit_names):
            continue
        hint = _file_hint(message)
        if hint and hint not in seen:
            hints.append(hint)
            seen.add(hint)
    return hints


def _file_hint(message: Mapping[str, Any]) -> str:
    content = str(message.get("content") or "")
    for line in content.splitlines()[:12]:
        stripped = line.strip()
        if stripped.startswith("=== ") and stripped.endswith(" ==="):
            return stripped[4:-4].strip()
        for prefix in ("File: ", "Path: ", "Saved: ", "Modified: ", "Created: ", "Deleted: "):
            if stripped.startswith(prefix):
                return stripped[len(prefix):].strip()
    return ""


def _message_text(message: Mapping[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        pieces: List[str] = []
        for block in content:
            if isinstance(block, Mapping):
                text = block.get("text")
                if isinstance(text, str):
                    pieces.append(text)
            elif isinstance(block, str):
                pieces.append(block)
        return "\n".join(pieces)
    return str(content or "")


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."

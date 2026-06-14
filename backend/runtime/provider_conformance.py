from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

from backend.core.paths import metis_dir
from backend.runtime.llm_backends.base import LLMResponse, ToolCall
from backend.runtime.llm_backends.openai_compat import OpenAICompatBackend


CONFORMANCE_VERSION = 1


def conformance_dir() -> Path:
    return metis_dir("provider-conformance")


def conformance_file(provider_id: str, model: str) -> Path:
    provider_slug = _slug(provider_id or "provider")
    model_slug = _slug(model or "default")
    return conformance_dir() / f"{provider_slug}-{model_slug}.json"


def load_provider_conformance(provider_id: str, model: str) -> Optional[Dict[str, Any]]:
    path = conformance_file(provider_id, model)
    try:
        with path.open("r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def save_provider_conformance(result: Dict[str, Any]) -> Path:
    provider_id = str(result.get("provider_id") or "provider")
    model = str(result.get("model") or "default")
    path = conformance_file(provider_id, model)
    result["path"] = str(path)
    payload = dict(result)
    payload.setdefault("version", CONFORMANCE_VERSION)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    return path


def run_provider_conformance_probe(
    *,
    provider_id: str,
    base_url: str,
    api_key: str,
    model: str,
    timeout: float = 45.0,
) -> Dict[str, Any]:
    """Probe an OpenAI-compatible provider and persist observed behavior.

    This is intentionally best-effort. A failed probe returns a structured result
    and never prevents normal model use.
    """

    base_result: Dict[str, Any] = {
        "version": CONFORMANCE_VERSION,
        "provider_id": str(provider_id or "").strip() or "provider",
        "base_url": str(base_url or "").strip().rstrip("/"),
        "model": str(model or "").strip(),
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ok": False,
        "requires_reasoning_passback": None,
        "parallel_tool_calls": None,
        "reasoning_mode": "unknown",
        "cache_fields": "unknown",
        "tool_schema_strictness": "unknown",
        "multi_round_continuation": "unknown",
        "notes": [],
        "raw": {},
    }
    if not base_result["base_url"] or not base_result["model"]:
        return {**base_result, "error": "base_url and model are required for deep provider probing"}
    if not str(api_key or "").strip():
        return {**base_result, "error": "api_key is required for deep provider probing"}

    backend = OpenAICompatBackend(
        base_url=base_result["base_url"],
        api_key=str(api_key).strip(),
        model=base_result["model"],
        max_retries=0,
    )

    try:
        first = backend.chat(
            [
                {
                    "role": "user",
                    "content": (
                        "Probe tool behavior. Call both tools exactly once: "
                        "read_probe_file with path alpha.txt and inspect_probe_config with mode summary."
                    ),
                }
            ],
            tools=_probe_tools(),
            temperature=0.0,
            max_tokens=256,
            timeout=timeout,
        )
    except Exception as exc:
        result = {**base_result, "error": _safe_exception_text(exc)}
        save_provider_conformance(result)
        return result

    result = dict(base_result)
    result["reasoning_mode"] = _reasoning_mode(first)
    result["cache_fields"] = _cache_field_shape(first.raw)
    result["parallel_tool_calls"] = len(first.tool_calls) >= 2
    result["tool_schema_strictness"] = _tool_schema_strictness(first.tool_calls)
    result["raw"] = {
        "first_stop_reason": first.stop_reason,
        "first_tool_call_count": len(first.tool_calls),
    }

    if not first.tool_calls:
        result["ok"] = True
        result["multi_round_continuation"] = "no_tool_calls_observed"
        result["notes"].append("The probe response did not produce tool calls, so multi-round continuation was not observed.")
        save_provider_conformance(result)
        return result

    history_without_reasoning = [
        {"role": "user", "content": "Probe tool behavior. Call both tools exactly once."},
        _assistant_message(first, include_reasoning=False),
        *[_tool_result_message(call) for call in first.tool_calls],
    ]
    history_with_reasoning = [
        {"role": "user", "content": "Probe tool behavior. Call both tools exactly once."},
        _assistant_message(first, include_reasoning=True),
        *[_tool_result_message(call) for call in first.tool_calls],
    ]

    try:
        backend.chat(
            history_without_reasoning,
            tools=_probe_tools(),
            temperature=0.0,
            max_tokens=64,
            timeout=timeout,
        )
        result["requires_reasoning_passback"] = False
        result["multi_round_continuation"] = "ok_without_reasoning_content"
        result["ok"] = True
    except Exception as exc:
        error_text = _safe_exception_text(exc)
        if "reasoning_content" in error_text:
            result["requires_reasoning_passback"] = True
            result["multi_round_continuation"] = "reasoning_content_required"
            try:
                backend.chat(
                    history_with_reasoning,
                    tools=_probe_tools(),
                    temperature=0.0,
                    max_tokens=64,
                    timeout=timeout,
                )
                result["ok"] = True
            except Exception as replay_exc:
                result["ok"] = False
                result["error"] = _safe_exception_text(replay_exc)
                result["notes"].append("Provider required reasoning_content but rejected the replay-with-reasoning probe.")
        else:
            result["ok"] = False
            result["multi_round_continuation"] = "failed"
            result["error"] = error_text

    save_provider_conformance(result)
    return result


def _probe_tools() -> list[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "read_probe_file",
                "description": "Read a tiny probe file.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "inspect_probe_config",
                "description": "Inspect a tiny probe config.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "mode": {"type": "string", "enum": ["summary", "full"]},
                        "optional_note": {"type": "string"},
                    },
                    "required": ["mode"],
                },
            },
        },
    ]


def _assistant_message(response: LLMResponse, *, include_reasoning: bool) -> Dict[str, Any]:
    message: Dict[str, Any] = {"role": "assistant", "content": response.content or None}
    if include_reasoning and response.reasoning_content:
        message["reasoning_content"] = response.reasoning_content
    if response.tool_calls:
        message["tool_calls"] = [
            {
                "id": call.id,
                "type": "function",
                "function": {
                    "name": call.name,
                    "arguments": json.dumps(call.arguments, ensure_ascii=False),
                },
            }
            for call in response.tool_calls
        ]
    return message


def _tool_result_message(call: ToolCall) -> Dict[str, Any]:
    return {
        "role": "tool",
        "tool_call_id": call.id,
        "content": json.dumps({"ok": True, "tool": call.name}, ensure_ascii=False),
    }


def _reasoning_mode(response: LLMResponse) -> str:
    if response.reasoning_content:
        return "reasoning_content"
    usage = response.raw.get("usage") if isinstance(response.raw, dict) else {}
    details = usage.get("completion_tokens_details") if isinstance(usage, dict) else {}
    if isinstance(details, dict) and int(details.get("reasoning_tokens") or 0) > 0:
        return "usage_reasoning_tokens"
    return "none"


def _cache_field_shape(raw: Dict[str, Any]) -> str:
    usage = raw.get("usage") if isinstance(raw, dict) else {}
    if not isinstance(usage, dict):
        return "none"
    has_deepseek_fields = "prompt_cache_hit_tokens" in usage or "prompt_cache_miss_tokens" in usage
    details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    has_openai_fields = "cached_tokens" in details
    if has_deepseek_fields and has_openai_fields:
        return "deepseek_and_openai"
    if has_deepseek_fields:
        return "deepseek"
    if has_openai_fields:
        return "openai"
    return "none"


def _tool_schema_strictness(tool_calls: list[ToolCall]) -> str:
    if not tool_calls:
        return "not_observed"
    names = {call.name for call in tool_calls}
    if "inspect_probe_config" not in names:
        return "missing_enum_tool"
    for call in tool_calls:
        if call.name == "inspect_probe_config" and call.arguments.get("mode") not in {"summary", "full"}:
            return "invalid_enum"
    return "ok"


def _safe_exception_text(exc: BaseException) -> str:
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            text = str(response.text or "")
        except Exception:
            text = ""
        if text:
            return text[:2000]
    return str(exc)[:2000]


def _slug(value: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(value or "").strip())
    return text.strip(".-")[:120] or "default"

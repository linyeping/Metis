from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
from typing import Any, Dict, Generator, List, Optional

logger = logging.getLogger(__name__)

try:
    from backend.bridges.provider_registry import normalize_chat_completions_url, parallel_tool_calls_enabled
except ImportError:  # pragma: no cover - supports running from inside miro/
    from backend.bridges.provider_registry import normalize_chat_completions_url, parallel_tool_calls_enabled

from ._common import (
    iter_utf8_lines,
    openai_stop_reason,
    parse_openai_tool_calls,
    post_with_retries,
    usage_from_openai,
)
from .base import LLMBackend, LLMResponse, Usage
from .deepseek_schema import sanitize_deepseek_strict_tools

_VISION_MODELS = {
    "gpt-4o",
    "gpt-4o-mini",
    "gpt-4-turbo",
    "gpt-4-vision",
    "gpt-4.1",
    "gpt-4.1-mini",
    "gpt-4.1-nano",
    "gpt-5",
    "gpt-5.4",
    "gpt-5.5",
    "gpt-5.5-mini",
    "o3",
    "o3-mini",
    "o4-mini",
    "chatgpt-4o",
}
_OLLAMA_VISION_MODELS = {
    "llava",
    "bakllava",
    "moondream",
    "llama3.2-vision",
    "minicpm-v",
    "cogvlm",
    "yi-vl",
}
_NO_VISION_PREFIXES = ("deepseek",)
_MODEL_HEADERS = ("x-model", "x-actual-model", "x-served-model", "openai-model")
_detected_models: dict[str, str] = {}


class OpenAICompatBackend(LLMBackend):
    """OpenAI-compatible Chat Completions backend."""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        model: str,
        *,
        max_retries: int = 3,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.model = model
        self.detected_model = model
        self.max_retries = max_retries
        # opt-in 推理强度（由工厂在构造后注入）。空=关。
        self.reasoning_effort = ""
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

    @property
    def chat_completions_url(self) -> str:
        # DeepSeek /beta 端点：仅 chat completions 走 /beta，开启 strict tool mode + chat prefix
        # completion（提升工具调用可靠性，配合稳定前缀更利于上下文缓存命中）。用户显式带 /v1 或
        # /beta 视为自主选择不再追加；/models 等其它路径不受影响（走独立函数）。
        # strict 模式拒绝自由形态对象参数(如 map/无属性对象)；METIS_DEEPSEEK_STRICT=0 退回普通模式。
        base = self.base_url.rstrip("/")
        if base.lower().endswith("api.deepseek.com") and _deepseek_strict_enabled():
            base = base + "/beta"
        return normalize_chat_completions_url(base)

    @property
    def supports_vision(self) -> bool:
        url = self.base_url.lower()
        model = self.model.lower()

        if any(model.startswith(prefix) for prefix in _NO_VISION_PREFIXES):
            return False
        if "deepseek" in url:
            return False
        if any(model.startswith(vision_model) for vision_model in _VISION_MODELS):
            return True
        if any(host in url for host in ("localhost", "127.0.0.1", "0.0.0.0")):
            return any(vision_model in model for vision_model in _OLLAMA_VISION_MODELS)
        if "openai.com" in url:
            return True
        return False

    def _reasoning_params(self) -> Optional[Dict[str, Any]]:
        """opt-in 推理强度。按模型支持的档位透传真实档位（不再统一塌成 high）。

        off/空 -> 不注入（保持非推理行为）。选中的档位若超出该模型支持范围，clamp
        到最接近的有效档位（例如 'max' 落到只支持到 'xhigh' 的模型 -> 'xhigh'）。
        DeepSeek 额外带 thinking enabled 标志。
        """
        from backend.runtime.reasoning_tiers import clamp_effort, effort_levels_for

        effort = (self.reasoning_effort or "").strip().lower()
        if not effort or effort in ("off", "none"):
            return None
        model = self.detected_model or self.model or ""
        levels = effort_levels_for(model)
        if not levels:
            return None
        level = clamp_effort(effort, levels)
        params: Dict[str, Any] = {"reasoning_effort": level}
        if "deepseek" in self.base_url.lower():
            params["thinking"] = {"type": "enabled"}
        return params

    @property
    def supports_parallel_tool_calls(self) -> bool:
        return parallel_tool_calls_enabled(
            "openai",
            base_url=self.base_url,
            model=self.detected_model or self.model,
        )

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        cancel_event: Optional[threading.Event] = None,
    ) -> LLMResponse:
        request_tools = _provider_tools(self.base_url, self.model, tools)
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": _effective_tool_temperature(self.base_url, self.model, temperature, request_tools),
            "max_tokens": max_tokens,
        }
        if request_tools:
            payload["tools"] = request_tools
            # Explicitly allow parallel tool calls so the model can batch
            # independent actions in one turn (fewer round-trips). Some relays
            # default this off when the field is omitted.
            if self.supports_parallel_tool_calls:
                payload["parallel_tool_calls"] = True
        reasoning = self._reasoning_params()
        if reasoning:
            payload.update(reasoning)

        response = post_with_retries(
            self.chat_completions_url,
            headers=self.headers,
            payload=payload,
            timeout=timeout,
            max_retries=self.max_retries,
            cancel_event=cancel_event,
        )
        detected = detect_and_cache_model(self.base_url, self.api_key, response, allow_body=True)
        if detected:
            self.detected_model = detected
        data = response.json()
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        thinking = message.get("reasoning_content") or ""
        tool_calls = parse_openai_tool_calls(
            message.get("tool_calls"),
            tools=request_tools,
            parallel=self.supports_parallel_tool_calls,
        )
        return LLMResponse(
            content=content,
            thinking=thinking,
            reasoning_content=thinking,
            tool_calls=tool_calls,
            stop_reason=openai_stop_reason(choice.get("finish_reason") or "", bool(tool_calls)),
            usage=usage_from_openai(data),
            raw=data,
        )

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        cancel_event: Optional[threading.Event] = None,
    ) -> Generator[str, None, LLMResponse]:
        request_tools = _provider_tools(self.base_url, self.model, tools)
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": _effective_tool_temperature(self.base_url, self.model, temperature, request_tools),
            "max_tokens": max_tokens,
            "stream": True,
        }
        if request_tools:
            payload["tools"] = request_tools
            # Explicitly allow parallel tool calls so the model can batch
            # independent actions in one turn (fewer round-trips). Some relays
            # default this off when the field is omitted.
            if self.supports_parallel_tool_calls:
                payload["parallel_tool_calls"] = True
        reasoning = self._reasoning_params()
        if reasoning:
            payload.update(reasoning)
        if not any(host in self.base_url.lower() for host in ("localhost", "127.0.0.1", "0.0.0.0")):
            payload["stream_options"] = {"include_usage": True}

        response = post_with_retries(
            self.chat_completions_url,
            headers=self.headers,
            payload=payload,
            timeout=timeout,
            stream=True,
            max_retries=self.max_retries,
            cancel_event=cancel_event,
        )
        detected = detect_and_cache_model(self.base_url, self.api_key, response, allow_body=False)
        if detected:
            self.detected_model = detected

        content_parts: List[str] = []
        thinking_parts: List[str] = []
        tool_call_chunks: Dict[int, Dict[str, Any]] = {}
        finish_reason = ""
        usage = Usage()
        raw_chunks: List[Dict[str, Any]] = []

        for raw_line in iter_utf8_lines(response, cancel_event=cancel_event):
            if not raw_line:
                continue
            line = raw_line.strip()
            if line.startswith("data:"):
                line = line[5:].strip()
            if line == "[DONE]":
                break
            try:
                chunk = json.loads(line)
            except json.JSONDecodeError:
                continue
            raw_chunks.append(chunk)
            if chunk.get("usage"):
                usage = usage_from_openai(chunk)
            choice = (chunk.get("choices") or [{}])[0]
            finish_reason = choice.get("finish_reason") or finish_reason
            delta = choice.get("delta") or {}

            text = delta.get("content")
            if text:
                content_parts.append(text)
                yield text

            reasoning = delta.get("reasoning_content")
            if reasoning:
                thinking_parts.append(reasoning)

            for call_delta in delta.get("tool_calls") or []:
                index = int(call_delta.get("index") or 0)
                current = tool_call_chunks.setdefault(
                    index, {"id": "", "name": "", "arguments": ""}
                )
                if call_delta.get("id"):
                    current["id"] = call_delta["id"]
                function = call_delta.get("function") or {}
                if function.get("name"):
                    current["name"] += function["name"]
                if function.get("arguments"):
                    current["arguments"] += function["arguments"]

        raw_tool_calls: List[Dict[str, Any]] = []
        for index in sorted(tool_call_chunks):
            chunk = tool_call_chunks[index]
            if not chunk["name"]:
                continue
            # FABLEADV-18: half-streamed protection. If the stream broke mid tool
            # call, the accumulated arguments JSON is truncated; executing a tool
            # with malformed args fails. Drop tool calls whose arguments are not
            # parseable JSON (empty args are valid for no-param tools).
            args_str = (chunk["arguments"] or "").strip()
            if args_str:
                try:
                    json.loads(args_str)
                except json.JSONDecodeError:
                    logger.warning(
                        "dropping tool call %r with incomplete streamed arguments (%d chars)",
                        chunk["name"],
                        len(args_str),
                    )
                    continue
            raw_tool_calls.append(
                {
                    "id": chunk["id"] or f"call_{index}",
                    "type": "function",
                    "function": {
                        "name": chunk["name"],
                        "arguments": chunk["arguments"],
                    },
                }
            )
        tool_calls = parse_openai_tool_calls(
            raw_tool_calls,
            tools=request_tools,
            parallel=self.supports_parallel_tool_calls,
        )

        return LLMResponse(
            content="".join(content_parts),
            thinking="".join(thinking_parts),
            reasoning_content="".join(thinking_parts),
            tool_calls=tool_calls,
            stop_reason=openai_stop_reason(finish_reason, bool(tool_calls)),
            usage=usage,
            raw={"chunks": raw_chunks},
        )


def _extract_model_from_response(
    response: Any,
    body: Optional[Dict[str, Any]] = None,
    *,
    allow_body: bool = True,
) -> Optional[str]:
    headers = getattr(response, "headers", {}) or {}
    for header in _MODEL_HEADERS:
        value = str(headers.get(header, "") or "").strip()
        if value:
            return value

    if not allow_body:
        return None

    payload = body
    if payload is None:
        try:
            payload = response.json()
        except Exception:
            payload = {}
    response_model = str((payload or {}).get("model") or "").strip()
    return response_model or None


def detect_and_cache_model(base_url: str, api_key: str, response: Any, *, allow_body: bool) -> Optional[str]:
    key_fingerprint = hashlib.sha256(api_key.encode("utf-8")).hexdigest()[:12] if api_key else "no-key"
    cache_key = f"{base_url}:{key_fingerprint}"
    if cache_key in _detected_models:
        return _detected_models[cache_key]

    detected = _extract_model_from_response(response, allow_body=allow_body)
    if detected:
        _detected_models[cache_key] = detected
    return detected


def _effective_tool_temperature(
    base_url: str,
    model: str,
    temperature: float,
    tools: Optional[List[Dict[str, Any]]],
) -> float:
    if not tools:
        return temperature
    override = os.environ.get("METIS_TOOL_TEMPERATURE") or os.environ.get("MIRO_TOOL_TEMPERATURE")
    if override is not None and str(override).strip():
        try:
            return float(override)
        except ValueError:
            return temperature
    if _is_deepseek_target(base_url, model):
        return 0.0
    return temperature


def _provider_tools(
    base_url: str,
    model: str,
    tools: Optional[List[Dict[str, Any]]],
) -> Optional[List[Dict[str, Any]]]:
    if not tools:
        return tools
    if _is_deepseek_target(base_url, model) and _deepseek_strict_enabled():
        return sanitize_deepseek_strict_tools(tools)
    return tools


def _deepseek_strict_enabled() -> bool:
    """DeepSeek /beta strict tool mode. Off => normal /chat/completions, which
    accepts loose schemas (free-form object params strict mode would reject)."""
    # Default OFF: /beta strict mode 400s on common tool schemas (free-form
    # object params like todo items / env maps), which breaks every tool call.
    # Opt back in with METIS_DEEPSEEK_STRICT=1 if you know your tools are strict-safe.
    return os.environ.get("METIS_DEEPSEEK_STRICT", "0").strip().lower() in {"1", "true", "yes", "on"}


def _is_deepseek_target(base_url: str, model: str) -> bool:
    value = f"{base_url} {model}".lower()
    return "deepseek" in value

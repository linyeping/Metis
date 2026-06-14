from __future__ import annotations

import threading
from typing import Any, Dict, Generator, List, Optional, Tuple

from ._common import parse_json_object, post_with_retries, to_text
from .base import LLMBackend, LLMResponse, ToolCall, Usage


class AnthropicBackend(LLMBackend):
    """Anthropic Messages API backend."""

    def __init__(
        self,
        api_key: str,
        model: str = "claude-sonnet-4-20250514",
        *,
        base_url: str = "https://api.anthropic.com/v1/messages",
        max_retries: int = 3,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url = base_url
        self.max_retries = max_retries
        self.headers = {
            "Content-Type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        }

    @property
    def supports_vision(self) -> bool:
        return True

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
        converted_messages, system = self._convert_messages(messages)
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": converted_messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        if system:
            payload["system"] = system
        if tools:
            payload["tools"] = self._convert_tools(tools)

        response = post_with_retries(
            self.base_url,
            headers=self.headers,
            payload=payload,
            timeout=timeout,
            max_retries=self.max_retries,
            cancel_event=cancel_event,
        )
        data = response.json()
        return self._parse_response(data)

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
        response = self.chat(
            messages,
            tools,
            temperature=temperature,
            max_tokens=max_tokens,
            timeout=timeout,
            cancel_event=cancel_event,
        )
        if response.content:
            yield response.content
        return response

    def _convert_tools(self, tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        converted: List[Dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") != "function":
                continue
            function = tool.get("function") or {}
            converted.append(
                {
                    "name": function.get("name", ""),
                    "description": function.get("description", ""),
                    "input_schema": function.get("parameters") or {"type": "object"},
                }
            )
        return [tool for tool in converted if tool["name"]]

    def _convert_messages(
        self, messages: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], str]:
        converted: List[Dict[str, Any]] = []
        system_parts: List[str] = []

        for message in messages:
            role = message.get("role")
            if role == "system":
                text = to_text(message.get("content"))
                if text:
                    system_parts.append(text)
                continue

            if role == "assistant":
                content_blocks: List[Dict[str, Any]] = []
                text = to_text(message.get("content"))
                if text:
                    content_blocks.append({"type": "text", "text": text})
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    name = function.get("name") or call.get("name")
                    if not name:
                        continue
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": call.get("id", ""),
                            "name": name,
                            "input": parse_json_object(
                                function.get("arguments", call.get("arguments"))
                            ),
                        }
                    )
                if content_blocks:
                    converted.append({"role": "assistant", "content": content_blocks})
                continue

            if role == "tool":
                converted.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": message.get("tool_call_id", ""),
                                "content": to_text(message.get("content")),
                            }
                        ],
                    }
                )
                continue

            if role == "user":
                raw_content = message.get("content")
                if isinstance(raw_content, list):
                    blocks = _convert_content_blocks_anthropic(raw_content)
                    if blocks:
                        converted.append({"role": "user", "content": blocks})
                else:
                    converted.append({"role": "user", "content": to_text(raw_content)})

        return converted, "\n\n".join(system_parts)

    def _parse_response(self, data: Dict[str, Any]) -> LLMResponse:
        text_parts: List[str] = []
        thinking_parts: List[str] = []
        tool_calls: List[ToolCall] = []
        for block in data.get("content") or []:
            block_type = block.get("type")
            if block_type == "text":
                text_parts.append(block.get("text") or "")
            elif block_type == "thinking":
                thinking_parts.append(block.get("thinking") or "")
            elif block_type == "tool_use":
                tool_calls.append(
                    ToolCall(
                        id=str(block.get("id") or ""),
                        name=str(block.get("name") or ""),
                        arguments=parse_json_object(block.get("input")),
                    )
                )
        usage_data = data.get("usage") or {}
        usage = Usage(
            prompt_tokens=int(usage_data.get("input_tokens") or 0),
            completion_tokens=int(usage_data.get("output_tokens") or 0),
            total_tokens=int(usage_data.get("input_tokens") or 0)
            + int(usage_data.get("output_tokens") or 0),
        )
        stop_reason = data.get("stop_reason") or ""
        if tool_calls:
            stop_reason = "tool_use"
        return LLMResponse(
            content="".join(text_parts),
            thinking="\n".join(part for part in thinking_parts if part),
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
            raw=data,
        )


def _convert_content_blocks_anthropic(blocks: List[Any]) -> List[Dict[str, Any]]:
    """Convert OpenAI-format content blocks to Anthropic message blocks."""
    result: List[Dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text", "")
            if text:
                result.append({"type": "text", "text": str(text)})
            continue
        if block_type == "image_url":
            image_url = block.get("image_url") or {}
            url = image_url.get("url", "")
            if not isinstance(url, str) or not url.startswith("data:"):
                continue
            header, _, b64data = url.partition(",")
            mime_type = header.replace("data:", "").replace(";base64", "")
            if b64data:
                result.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime_type or "image/png",
                            "data": b64data,
                        },
                    }
                )
    return result

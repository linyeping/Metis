from __future__ import annotations

import threading
from typing import Any, Dict, Generator, List, Optional, Tuple

from ._common import json_response, parse_json_object, post_with_retries, to_text
from .base import LLMBackend, LLMResponse, ToolCall, Usage


class GeminiBackend(LLMBackend):
    """Google Gemini generateContent backend."""

    def __init__(
        self,
        api_key: str,
        model: str = "gemini-2.0-flash",
        *,
        base_url_template: str = (
            "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
        ),
        max_retries: int = 3,
    ) -> None:
        self.api_key = api_key
        self.model = model
        self.base_url_template = base_url_template
        self.max_retries = max_retries
        self.headers = {"Content-Type": "application/json"}

    @property
    def supports_vision(self) -> bool:
        return True

    @property
    def generate_url(self) -> str:
        return f"{self.base_url_template.format(model=self.model)}?key={self.api_key}"

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
        contents, system_instruction = self._convert_messages(messages)
        payload: Dict[str, Any] = {
            "contents": contents,
            "generationConfig": {
                "temperature": temperature,
                "maxOutputTokens": max_tokens,
            },
        }
        if system_instruction:
            payload["systemInstruction"] = {"parts": [{"text": system_instruction}]}
        if tools:
            payload["tools"] = [self._convert_tools(tools)]

        response = post_with_retries(
            self.generate_url,
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

    def _convert_tools(self, tools: List[Dict[str, Any]]) -> Dict[str, Any]:
        declarations: List[Dict[str, Any]] = []
        for tool in tools:
            if tool.get("type") != "function":
                continue
            function = tool.get("function") or {}
            name = function.get("name")
            if not name:
                continue
            declarations.append(
                {
                    "name": name,
                    "description": function.get("description", ""),
                    "parameters": function.get("parameters") or {"type": "object"},
                }
            )
        return {"function_declarations": declarations}

    def _convert_messages(
        self, messages: List[Dict[str, Any]]
    ) -> Tuple[List[Dict[str, Any]], str]:
        contents: List[Dict[str, Any]] = []
        system_parts: List[str] = []
        tool_call_names: Dict[str, str] = {}

        for message in messages:
            role = message.get("role")
            if role == "system":
                text = to_text(message.get("content"))
                if text:
                    system_parts.append(text)
                continue

            if role == "assistant":
                parts: List[Dict[str, Any]] = []
                text = to_text(message.get("content"))
                if text:
                    parts.append({"text": text})
                for call in message.get("tool_calls") or []:
                    function = call.get("function") or {}
                    name = function.get("name") or call.get("name")
                    call_id = str(call.get("id") or "")
                    if not name:
                        continue
                    if call_id:
                        tool_call_names[call_id] = name
                    parts.append(
                        {
                            "functionCall": {
                                "name": name,
                                "args": parse_json_object(
                                    function.get("arguments", call.get("arguments"))
                                ),
                            }
                        }
                    )
                if parts:
                    contents.append({"role": "model", "parts": parts})
                continue

            if role == "tool":
                tool_call_id = str(message.get("tool_call_id") or "")
                name = message.get("name") or tool_call_names.get(tool_call_id) or "tool_result"
                contents.append(
                    {
                        "role": "user",
                        "parts": [
                            {
                                "functionResponse": {
                                    "name": name,
                                    "response": json_response(message.get("content")),
                                }
                            }
                        ],
                    }
                )
                continue

            if role == "user":
                raw_content = message.get("content")
                if isinstance(raw_content, list):
                    parts = _convert_content_parts_gemini(raw_content)
                    if parts:
                        contents.append({"role": "user", "parts": parts})
                else:
                    contents.append(
                        {"role": "user", "parts": [{"text": to_text(raw_content)}]}
                    )

        return contents, "\n\n".join(system_parts)

    def _parse_response(self, data: Dict[str, Any]) -> LLMResponse:
        candidate = (data.get("candidates") or [{}])[0]
        content = candidate.get("content") or {}
        text_parts: List[str] = []
        tool_calls: List[ToolCall] = []

        for index, part in enumerate(content.get("parts") or []):
            if "text" in part:
                text_parts.append(part.get("text") or "")
            function_call = part.get("functionCall")
            if function_call:
                tool_calls.append(
                    ToolCall(
                        id=f"gemini_call_{index}",
                        name=str(function_call.get("name") or ""),
                        arguments=parse_json_object(function_call.get("args")),
                    )
                )

        usage_data = data.get("usageMetadata") or {}
        usage = Usage(
            prompt_tokens=int(usage_data.get("promptTokenCount") or 0),
            completion_tokens=int(usage_data.get("candidatesTokenCount") or 0),
            total_tokens=int(usage_data.get("totalTokenCount") or 0),
        )
        finish_reason = str(candidate.get("finishReason") or "")
        stop_reason = "tool_use" if tool_calls else self._map_finish_reason(finish_reason)
        return LLMResponse(
            content="".join(text_parts),
            tool_calls=tool_calls,
            stop_reason=stop_reason,
            usage=usage,
            raw=data,
        )

    def _map_finish_reason(self, finish_reason: str) -> str:
        if finish_reason == "STOP":
            return "end_turn"
        if finish_reason == "MAX_TOKENS":
            return "max_tokens"
        return finish_reason.lower()


def _convert_content_parts_gemini(blocks: List[Any]) -> List[Dict[str, Any]]:
    """Convert OpenAI-format content blocks to Gemini parts."""
    parts: List[Dict[str, Any]] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text":
            text = block.get("text", "")
            if text:
                parts.append({"text": str(text)})
            continue
        if block_type == "image_url":
            image_url = block.get("image_url") or {}
            url = image_url.get("url", "")
            if not isinstance(url, str) or not url.startswith("data:"):
                continue
            header, _, b64data = url.partition(",")
            mime_type = header.replace("data:", "").replace(";base64", "")
            if b64data:
                parts.append(
                    {
                        "inlineData": {
                            "mimeType": mime_type or "image/png",
                            "data": b64data,
                        }
                    }
                )
    return parts

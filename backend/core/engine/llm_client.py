"""上游 Chat Completions 封装（蓝图 `llm_client.py`；当前默认 DeepSeek）。"""
from typing import Any, Dict, List, Optional

import requests

from .constants import DEEPSEEK_CHAT_MODEL, REQUEST_TIMEOUT

DEFAULT_MODEL = DEEPSEEK_CHAT_MODEL


class LLMClient:
    """统一 `requests` + JSON 解析；重试策略留在各调用方（流式循环需配合 SSE）。"""

    def __init__(self, api_url: str, api_key: str, model: str = DEFAULT_MODEL) -> None:
        self.api_url = api_url
        self.api_key = api_key
        self.model = model
        self.headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }

    def chat(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        tools: Optional[List[Any]] = None,
        timeout: float = REQUEST_TIMEOUT,
    ) -> Dict[str, Any]:
        """返回 assistant message 字典（含 content / tool_calls 等）。"""
        payload: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if tools is not None:
            payload["tools"] = tools
        r = requests.post(self.api_url, headers=self.headers, json=payload, timeout=timeout)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]

    def chat_text(
        self,
        messages: List[Dict[str, Any]],
        *,
        temperature: float,
        max_tokens: int,
        timeout: float = 60.0,
    ) -> str:
        """仅需文本 content 的便捷封装（执行计划、意图 JSON、修复提示等）。"""
        msg = self.chat(
            messages, temperature=temperature, max_tokens=max_tokens, timeout=timeout
        )
        return (msg.get("content") or "").strip()

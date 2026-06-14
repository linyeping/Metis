from __future__ import annotations

import threading
from typing import Any, Dict, Generator, List, Optional

try:
    from backend.bridges.provider_contract import ProviderRegistryError
    from backend.bridges.provider_registry import available_provider_ids, build_backend_kwargs
except ImportError:  # pragma: no cover - supports running from inside miro/
    from backend.bridges.provider_contract import ProviderRegistryError
    from backend.bridges.provider_registry import available_provider_ids, build_backend_kwargs

from .anthropic import AnthropicBackend
from .base import LLMBackend, LLMResponse, ToolCall as ToolCall, Usage as Usage
from .gemini import GeminiBackend
from .openai_compat import OpenAICompatBackend


class FakeLLMBackend(LLMBackend):
    """Offline backend used only by provider registry tests."""

    def __init__(self, response_text: str = "fake backend response", model: str = "fake-model", **_: Any) -> None:
        self.response_text = response_text
        self.model = model

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
        return LLMResponse(content=self.response_text, raw={"message_count": len(messages)})

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


def get_backend(backend_type: str, **kwargs) -> LLMBackend:
    """Create a backend by type string."""
    backends = {
        "openai": OpenAICompatBackend,
        "anthropic": AnthropicBackend,
        "gemini": GeminiBackend,
        "fake": FakeLLMBackend,
    }
    try:
        backend_key, normalized_kwargs = build_backend_kwargs(backend_type, **kwargs)
    except ProviderRegistryError as exc:
        choices = ", ".join(available_provider_ids())
        raise ValueError(f"{exc}. Choose from: {choices}") from exc

    cls = backends.get(backend_key)
    if not cls:
        choices = ", ".join(sorted(backends))
        raise ValueError(f"Unknown backend: {backend_key}. Choose from: {choices}")
    return cls(**normalized_kwargs)

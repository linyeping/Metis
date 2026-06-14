"""Provider boundary for future Hermes-style model runtime migration."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Mapping, NewType, Optional, Protocol, Sequence, TypedDict


ProviderId = NewType("ProviderId", str)
ProviderCapability = Literal["stream", "tools", "vision", "parallel_tool_calls", "requires_reasoning_passback"]


class ChatMessage(TypedDict):
    role: str
    content: str


@dataclass(frozen=True)
class ProviderConfig:
    provider_id: ProviderId
    model: str
    base_url: Optional[str] = None
    api_key_name: Optional[str] = None
    options: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ProviderProfile:
    provider_id: ProviderId
    display_name: str
    backend_type: str
    aliases: tuple[str, ...] = ()
    base_url: str = ""
    chat_completions_path: str = "/chat/completions"
    default_model: str = ""
    fallback_models: tuple[str, ...] = ()
    api_key_required: bool = True
    supports_stream: bool = True
    supports_tools: bool = True
    supports_vision: bool = False
    parallel_tool_calls: bool = False
    requires_reasoning_passback: bool = False
    openai_compatible: bool = False
    model_context_windows: Mapping[str, int] = field(default_factory=dict)
    model_notes: Mapping[str, str] = field(default_factory=dict)
    # FABLEADV-15: config-driven providers. source="builtin" cannot be deleted;
    # "user" comes from providers.json. api_key_env names the env var holding the
    # key (key itself never stored in providers.json).
    source: str = "builtin"
    api_key_env: str = ""


class ProviderRegistryError(ValueError):
    """Raised when a provider id, alias, or config cannot be resolved."""


@dataclass(frozen=True)
class ProviderError:
    code: str
    message: str
    retryable: bool = False
    suggestion: Optional[str] = None
    raw_type: Optional[str] = None


@dataclass(frozen=True)
class ProviderResult:
    text: str
    model: str
    provider_id: ProviderId
    usage: Mapping[str, int] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


class ProviderProtocol(Protocol):
    provider_id: ProviderId

    def complete(
        self,
        messages: Sequence[ChatMessage],
        config: ProviderConfig,
    ) -> ProviderResult:
        """Return a non-streaming response.

        Later phases can add streaming on top of the event contract. NEW-16
        keeps this small so smoke tests remain offline and deterministic.
        """


class FakeProvider:
    """Offline provider for bridge smoke tests."""

    provider_id = ProviderId("fake")

    def __init__(self, response_text: str = "fake provider response") -> None:
        self.response_text = response_text

    def complete(
        self,
        messages: Sequence[ChatMessage],
        config: ProviderConfig,
    ) -> ProviderResult:
        return ProviderResult(
            text=self.response_text,
            model=config.model,
            provider_id=self.provider_id,
            metadata={"message_count": len(messages)},
        )

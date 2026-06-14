"""Bridge contracts for gradually adapting Hermes runtime ideas into Metis.

NEW-16 intentionally keeps this package dependency-light. Production Metis
code should depend on these contracts, not on the third-party Hermes snapshot
directly.
"""

from .event_contract import (
    AgentEventEnvelope,
    AgentEventKind,
    ContentDeltaEvent,
    DoneEvent,
    ErrorEvent,
    EVENT_SCHEMA,
    EventKind,
    MetisEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from .event_serializer import agent_event_payload, normalize_agent_event_payload, sse_data
from .provider_contract import (
    ChatMessage,
    FakeProvider,
    ProviderCapability,
    ProviderConfig,
    ProviderError,
    ProviderId,
    ProviderProtocol,
    ProviderProfile,
    ProviderRegistryError,
    ProviderResult,
)
from .provider_errors import classify_provider_error
from .provider_registry import (
    available_provider_ids,
    build_backend_kwargs,
    get_provider_profile,
    list_provider_profiles,
    normalize_chat_completions_url,
    resolve_provider_id,
)
from .session_contract import (
    SessionId,
    SessionRecord,
    SessionStoreProtocol,
    WorkspaceId,
)
from .tool_contract import (
    ToolApproval,
    ToolAvailabilityError,
    ToolCallRequest,
    ToolCallResult,
    ToolName,
    ToolProfile,
    ToolRegistryProtocol,
)
from .tool_profiles import (
    infer_tool_profile,
    infer_toolset,
    is_destructive_tool,
    is_safe_tool,
)
from .tool_registry_adapter import profiles_from_runtime_registry

__all__ = [
    "ChatMessage",
    "AgentEventEnvelope",
    "AgentEventKind",
    "ContentDeltaEvent",
    "DoneEvent",
    "ErrorEvent",
    "EVENT_SCHEMA",
    "EventKind",
    "FakeProvider",
    "MetisEvent",
    "ProviderCapability",
    "ProviderConfig",
    "ProviderError",
    "ProviderId",
    "ProviderProtocol",
    "ProviderProfile",
    "ProviderRegistryError",
    "ProviderResult",
    "SessionId",
    "SessionRecord",
    "SessionStoreProtocol",
    "TextDeltaEvent",
    "ToolCallEvent",
    "ToolCallRequest",
    "ToolCallResult",
    "ToolName",
    "ToolApproval",
    "ToolAvailabilityError",
    "ToolProfile",
    "ToolRegistryProtocol",
    "ToolResultEvent",
    "WorkspaceId",
    "agent_event_payload",
    "available_provider_ids",
    "build_backend_kwargs",
    "classify_provider_error",
    "get_provider_profile",
    "list_provider_profiles",
    "normalize_chat_completions_url",
    "resolve_provider_id",
    "infer_tool_profile",
    "infer_toolset",
    "is_destructive_tool",
    "is_safe_tool",
    "normalize_agent_event_payload",
    "profiles_from_runtime_registry",
    "sse_data",
]

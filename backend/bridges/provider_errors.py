"""Provider-facing error mapping helpers."""

from __future__ import annotations

from typing import Optional

try:
    from backend.runtime.error_catalog import classify_llm_error
except ImportError:  # pragma: no cover - supports running from inside miro/
    from backend.runtime.error_catalog import classify_llm_error

from .provider_contract import ProviderError


def classify_provider_error(
    exc: Optional[BaseException] = None,
    *,
    message: str = "",
    recoverable: bool = False,
) -> ProviderError:
    info = classify_llm_error(exc, message=message, recoverable=recoverable)
    return ProviderError(
        code=info.code,
        message=f"{info.title}: {info.message}",
        retryable=info.recoverable,
        suggestion=info.hint,
        raw_type=type(exc).__name__ if exc else None,
    )

from __future__ import annotations

import pytest

from backend.bridges.provider_errors import classify_provider_error
from backend.runtime.error_catalog import classify_llm_error


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class FakeHTTPError(Exception):
    def __init__(self, status_code: int, message: str = "") -> None:
        super().__init__(message or f"{status_code} error")
        self.response = FakeResponse(status_code)


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("401 unauthorized invalid api key", "LLM_AUTH_FAILED"),
        ("missing api key", "LLM_API_KEY_MISSING"),
        ("403 forbidden", "LLM_FORBIDDEN"),
        ("404 not found", "LLM_ENDPOINT_NOT_FOUND"),
        ("429 rate limit", "LLM_RATE_LIMITED"),
        ("500 server error", "LLM_PROVIDER_UNAVAILABLE"),
        ("502 bad gateway", "LLM_PROVIDER_UNAVAILABLE"),
        ("503 service unavailable", "LLM_PROVIDER_UNAVAILABLE"),
        ("SSLZeroReturnError TLS/SSL connection has been closed EOF", "LLM_TLS_ERROR"),
        ("request timeout", "LLM_TIMEOUT"),
        ("connection refused max retries exceeded", "LLM_NETWORK_ERROR"),
    ],
)
def test_classify_llm_error_codes_from_messages(message: str, expected: str) -> None:
    assert classify_llm_error(message=message).code == expected


@pytest.mark.parametrize(
    ("status_code", "expected"),
    [
        (401, "LLM_AUTH_FAILED"),
        (403, "LLM_FORBIDDEN"),
        (404, "LLM_ENDPOINT_NOT_FOUND"),
        (429, "LLM_RATE_LIMITED"),
        (500, "LLM_PROVIDER_UNAVAILABLE"),
        (502, "LLM_PROVIDER_UNAVAILABLE"),
        (503, "LLM_PROVIDER_UNAVAILABLE"),
    ],
)
def test_classify_llm_error_codes_from_exception_response(status_code: int, expected: str) -> None:
    assert classify_llm_error(FakeHTTPError(status_code)).code == expected


def test_classify_provider_error_returns_bridge_error() -> None:
    error = classify_provider_error(message="SSLZeroReturnError TLS/SSL connection has been closed EOF")
    assert error.code == "LLM_TLS_ERROR"
    assert error.retryable is True
    assert error.suggestion
    assert "TLS" in error.message

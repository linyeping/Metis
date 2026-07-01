from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ErrorInfo:
    code: str
    title: str
    message: str
    hint: str
    recoverable: bool
    status: int = 0
    details: str = ""


_HTTP_STATUS_RE = re.compile(r"\b(4\d\d|5\d\d)\b")
_SENSITIVE_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9_\-.]{8,}", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r'"api[_-]?key"\s*:\s*"[^"]{4,}"', re.IGNORECASE),
    re.compile(r"[Aa]uthorization:\s*\S+"),
    re.compile(r"X-API-Key:\s*\S+", re.IGNORECASE),
    re.compile(r"[?&](?:api_?key|token|secret)=[^&\s]{4,}", re.IGNORECASE),
]


def classify_llm_error(
    exc: Optional[BaseException] = None,
    *,
    message: str = "",
    recoverable: bool = False,
) -> ErrorInfo:
    """Map provider/runtime failures to stable product-facing errors."""
    raw = message or (f"{type(exc).__name__}: {exc}" if exc else "")
    details = _sanitize_details(raw)
    text = raw.lower()
    status = _http_status_from_error(exc) or _http_status_from_text(raw)

    if status == 401 or _has_any(text, "authorization required", "unauthorized", "invalid api key", "incorrect api key"):
        return ErrorInfo(
            code="LLM_AUTH_FAILED",
            title="API Key 验证失败",
            message="模型服务拒绝了当前 API Key 或授权信息。",
            hint="打开设置，确认供应商、Base URL、模型名和 API Key 匹配。DeepSeek 通常使用 https://api.deepseek.com，并需要有效的 sk- 开头密钥。",
            recoverable=False,
            status=status or 401,
            details=details,
        )
    if status == 403:
        return ErrorInfo(
            code="LLM_FORBIDDEN",
            title="模型服务拒绝访问",
            message="当前账号或 API Key 没有访问这个模型/接口的权限。",
            hint="检查账号额度、模型权限、组织权限，或切换到该 Key 已开通的模型。",
            recoverable=False,
            status=status,
            details=details,
        )
    if status == 404:
        return ErrorInfo(
            code="LLM_ENDPOINT_NOT_FOUND",
            title="模型接口地址不正确",
            message="Metis 没有找到当前配置对应的模型接口。",
            hint="检查 Base URL 是否填错。OpenAI 兼容接口通常只填根地址，例如 https://api.deepseek.com，不要重复拼 /chat/completions。",
            recoverable=False,
            status=status,
            details=details,
        )
    if status == 429 or "rate limit" in text or "too many requests" in text:
        return ErrorInfo(
            code="LLM_RATE_LIMITED",
            title="请求太频繁或额度不足",
            message="模型服务暂时限制了请求。",
            hint="稍后重试，或检查账号余额、速率限制和并发限制。",
            recoverable=True,
            status=status or 429,
            details=details,
        )
    if status >= 500:
        return ErrorInfo(
            code="LLM_PROVIDER_UNAVAILABLE",
            title="模型服务暂时不可用",
            message="模型供应商返回了服务端错误。",
            hint="稍后重试；如果持续出现，切换模型或检查供应商状态页。",
            recoverable=True,
            status=status,
            details=details,
        )
    if _has_any(text, "ssl", "tls", "certificate", "sslzeroreturnerror"):
        return ErrorInfo(
            code="LLM_TLS_ERROR",
            title="TLS/证书连接失败",
            message="与模型服务建立安全连接时失败。",
            hint="检查系统时间、公司网络证书拦截和 Base URL；如果开启了代理/VPN，再检查代理链路。",
            recoverable=True,
            status=status,
            details=details,
        )
    if "timeout" in text or "timed out" in text:
        return ErrorInfo(
            code="LLM_TIMEOUT",
            title="模型请求超时",
            message="模型服务在规定时间内没有返回。",
            hint="稍后重试，或在设置中切换更快的模型。",
            recoverable=True,
            status=status,
            details=details,
        )
    if _has_any(text, "connectionerror", "connection aborted", "connection refused", "failed to establish", "max retries exceeded"):
        return ErrorInfo(
            code="LLM_NETWORK_ERROR",
            title="网络连接失败",
            message="Metis 无法连接到模型服务。",
            hint="检查网络、代理/VPN、Base URL 和防火墙设置。",
            recoverable=True,
            status=status,
            details=details,
        )
    if _has_any(text, "api key required", "missing api key"):
        return ErrorInfo(
            code="LLM_API_KEY_MISSING",
            title="未配置 API Key",
            message="当前模型供应商需要 API Key。",
            hint="打开设置或首次配置向导，填入供应商提供的 API Key。",
            recoverable=False,
            status=status,
            details=details,
        )

    return ErrorInfo(
        code="LLM_ERROR",
        title="模型调用失败",
        message="模型调用没有成功完成。",
        hint="检查模型配置后重试；如果问题持续出现，请查看技术详情。",
        recoverable=recoverable,
        status=status,
        details=details,
    )


def is_non_retryable_llm_error(exc: BaseException) -> bool:
    info = classify_llm_error(exc, recoverable=True)
    if info.status and 400 <= info.status < 500 and info.status not in {408, 409, 425, 429}:
        return True
    return info.code in {
        "LLM_AUTH_FAILED",
        "LLM_FORBIDDEN",
        "LLM_ENDPOINT_NOT_FOUND",
        "LLM_API_KEY_MISSING",
    }


def _http_status_from_error(exc: Optional[BaseException]) -> int:
    if exc is None:
        return 0
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return status
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        return code
    return 0


def _http_status_from_text(text: str) -> int:
    match = _HTTP_STATUS_RE.search(text or "")
    return int(match.group(1)) if match else 0


def _has_any(text: str, *needles: str) -> bool:
    return any(needle in text for needle in needles)


def _sanitize_details(text: str) -> str:
    value = str(text or "")
    for pattern in _SENSITIVE_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return value[:2000]

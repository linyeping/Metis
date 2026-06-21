from __future__ import annotations

import json
import os
import re
import threading
from typing import Any, Dict, Iterable, Iterator, List, Optional
from urllib.parse import urlparse

import requests

from ..cancellation import OperationCancelled, raise_if_cancelled, wait_or_cancel
from .base import ToolCall, Usage
from .toolcall_repair import repair_tool_calls


DEFAULT_BACKOFF_SECONDS = (2.0, 4.0, 8.0)
_PROXY_ENV_NAMES = (
    "METIS_LLM_PROXY",
    "MIRO_LLM_PROXY",
    "METIS_PROXY",
    "MIRO_PROXY",
    "HTTPS_PROXY",
    "https_proxy",
    "HTTP_PROXY",
    "http_proxy",
    "ALL_PROXY",
    "all_proxy",
)
_SENSITIVE_PATTERNS = [
    re.compile(r"Bearer\s+[A-Za-z0-9_\-.]{8,}", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    re.compile(r'"api[_-]?key"\s*:\s*"[^"]{4,}"', re.IGNORECASE),
    re.compile(r"[Aa]uthorization:\s*\S+"),
    re.compile(r"X-API-Key:\s*\S+", re.IGNORECASE),
    re.compile(r"[?&](?:api_?key|token|secret)=[^&\s]{4,}", re.IGNORECASE),
]


def sanitize_for_log(text: Any) -> str:
    value = str(text or "")
    for pattern in _SENSITIVE_PATTERNS:
        value = pattern.sub("[REDACTED]", value)
    return value[:2000]


def parse_json_object(value: Any) -> Dict[str, Any]:
    """Return a dict for provider argument payloads."""
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except json.JSONDecodeError:
            return {"_raw": value}
        if isinstance(parsed, dict):
            return parsed
        return {"value": parsed}
    return {"value": value}


def to_text(content: Any) -> str:
    """Normalize message content into text where a provider requires strings."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text is None and item.get("type") == "image_url":
                    parts.append("[image]")
                    continue
                if text is not None:
                    parts.append(str(text))
        if parts:
            return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)


def json_response(content: Any) -> Dict[str, Any]:
    """Convert tool result content into a JSON object for function responses."""
    if isinstance(content, dict):
        return content
    if isinstance(content, str):
        text = content.strip()
        if text:
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                return {"content": content}
            if isinstance(parsed, dict):
                return parsed
            return {"value": parsed}
    return {"content": to_text(content)}


def iter_utf8_lines(
    response: requests.Response,
    *,
    cancel_event: Optional[threading.Event] = None,
) -> Iterator[str]:
    """Yield response lines decoded as UTF-8 regardless of missing charset headers."""
    watcher_stop = threading.Event()

    def close_on_cancel() -> None:
        if cancel_event is None:
            return
        while not watcher_stop.is_set():
            if cancel_event.wait(0.1):
                response.close()
                return

    watcher: Optional[threading.Thread] = None
    if cancel_event is not None:
        watcher = threading.Thread(target=close_on_cancel, daemon=True, name="metis-llm-stream-cancel")
        watcher.start()
    try:
        for raw_line in response.iter_lines(decode_unicode=False):
            raise_if_cancelled(cancel_event)
            if raw_line is None:
                continue
            if isinstance(raw_line, bytes):
                yield raw_line.decode("utf-8-sig", errors="replace")
            elif isinstance(raw_line, bytearray):
                yield bytes(raw_line).decode("utf-8-sig", errors="replace")
            else:
                yield str(raw_line)
    finally:
        watcher_stop.set()
        if cancel_event is not None and cancel_event.is_set():
            response.close()
        if watcher is not None and not watcher.is_alive():
            watcher.join(timeout=0)


def post_with_retries(
    url: str,
    *,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout: float,
    stream: bool = False,
    max_retries: int = 3,
    backoff_seconds: Iterable[float] = DEFAULT_BACKOFF_SECONDS,
    cancel_event: Optional[threading.Event] = None,
) -> requests.Response:
    """POST JSON with retries on timeouts, connection failures, and 5xx errors."""
    delays = list(backoff_seconds)
    last_error: Optional[BaseException] = None
    # When the user disabled the proxy (mode=off) or bypassed this host, ignore
    # the OS env proxy from the first request — otherwise requests' trust_env
    # would still route through it (e.g. a clash tunnel that breaks DeepSeek TLS).
    bypass_env_proxy = _force_direct_connection(url)
    for attempt in range(max_retries + 1):
        raise_if_cancelled(cancel_event)
        try:
            response = _post_json(
                url,
                headers=headers,
                payload=payload,
                timeout=timeout,
                stream=stream,
                trust_env=not bypass_env_proxy,
                cancel_event=cancel_event,
            )
            if response.status_code < 500:
                response.raise_for_status()
                return response
            last_error = requests.exceptions.HTTPError(
                f"{response.status_code} server error for url: {url}",
                response=response,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            last_error = exc
            if not bypass_env_proxy and _should_retry_without_env_proxy(url, exc):
                bypass_env_proxy = True
                try:
                    response = _post_json(
                        url,
                        headers=headers,
                        payload=payload,
                        timeout=timeout,
                        stream=stream,
                        trust_env=False,
                        cancel_event=cancel_event,
                    )
                    if response.status_code < 500:
                        response.raise_for_status()
                        return response
                    last_error = requests.exceptions.HTTPError(
                        f"{response.status_code} server error for url: {url}",
                        response=response,
                    )
                except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as direct_exc:
                    last_error = direct_exc
                except requests.exceptions.HTTPError:
                    raise
        except requests.exceptions.HTTPError:
            raise
        except OperationCancelled:
            raise

        if attempt >= max_retries:
            break
        delay = delays[min(attempt, len(delays) - 1)]
        wait_or_cancel(cancel_event, delay)

    if isinstance(last_error, BaseException):
        raise last_error
    raise RuntimeError(f"Request failed without a response: {url}")


def _post_json(
    url: str,
    *,
    headers: Dict[str, str],
    payload: Dict[str, Any],
    timeout: float,
    stream: bool,
    trust_env: bool,
    cancel_event: Optional[threading.Event] = None,
) -> requests.Response:
    session = requests.Session()
    session.trust_env = trust_env
    proxies = _proxies_for_url(url) if trust_env else None
    watcher_stop = threading.Event()

    def close_on_cancel() -> None:
        if cancel_event is None:
            return
        while not watcher_stop.is_set():
            if cancel_event.wait(0.1):
                session.close()
                return

    watcher: Optional[threading.Thread] = None
    if cancel_event is not None:
        watcher = threading.Thread(target=close_on_cancel, daemon=True, name="metis-llm-cancel")
        watcher.start()
    try:
        raise_if_cancelled(cancel_event)
        return session.post(
            url,
            headers=headers,
            json=payload,
            timeout=timeout,
            stream=stream,
            proxies=proxies,
        )
    finally:
        watcher_stop.set()
        if not stream:
            session.close()
        if watcher is not None and not watcher.is_alive():
            watcher.join(timeout=0)


def _proxies_for_url(url: str) -> Optional[Dict[str, str]]:
    if _is_local_url(url) or _is_proxy_bypassed(url):
        return None
    mode = os.environ.get("METIS_PROXY_MODE", os.environ.get("MIRO_PROXY_MODE", "")).strip().lower()
    if mode == "off":
        return None
    if mode == "custom":
        proxy = _proxy_from_custom_settings()
    else:
        proxy = _proxy_from_env() or _proxy_from_windows_settings(url)
    if not proxy:
        return None
    return {"http": proxy, "https": proxy}


def _is_local_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def _force_direct_connection(url: str) -> bool:
    """True when the request must ignore the OS env proxy entirely."""
    if _is_local_url(url) or _is_proxy_bypassed(url):
        return True
    mode = os.environ.get("METIS_PROXY_MODE", os.environ.get("MIRO_PROXY_MODE", "")).strip().lower()
    return mode == "off"


def _is_proxy_bypassed(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return False
    raw = os.environ.get("METIS_PROXY_BYPASS") or os.environ.get("MIRO_PROXY_BYPASS") or ""
    for item in raw.split(","):
        pattern = item.strip().lower()
        if not pattern:
            continue
        if pattern.startswith("*.") and host.endswith(pattern[1:]):
            return True
        if host == pattern or host.endswith(f".{pattern}"):
            return True
    return False


def _proxy_from_env() -> str:
    for name in _PROXY_ENV_NAMES:
        value = os.environ.get(name)
        if value:
            return _normalize_proxy_url(value)
    return ""


def _proxy_from_custom_settings() -> str:
    explicit = os.environ.get("METIS_LLM_PROXY") or os.environ.get("MIRO_LLM_PROXY") or os.environ.get("METIS_PROXY") or os.environ.get("MIRO_PROXY")
    if explicit:
        return _normalize_proxy_url(explicit)
    scheme = (os.environ.get("METIS_PROXY_SCHEME") or os.environ.get("MIRO_PROXY_SCHEME") or "http").strip() or "http"
    host = (os.environ.get("METIS_PROXY_HOST") or os.environ.get("MIRO_PROXY_HOST") or "").strip()
    port = (os.environ.get("METIS_PROXY_PORT") or os.environ.get("MIRO_PROXY_PORT") or "").strip()
    if not host or not port:
        return ""
    return _normalize_proxy_url(f"{scheme}://{host}:{port}")


def _proxy_from_windows_settings(url: str) -> str:
    if os.name != "nt":
        return ""
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings",
        ) as key:
            proxy_enabled = int(winreg.QueryValueEx(key, "ProxyEnable")[0])
            if not proxy_enabled:
                return ""
            proxy_server = str(winreg.QueryValueEx(key, "ProxyServer")[0] or "")
    except Exception:
        return ""

    if not proxy_server:
        return ""
    scheme = (urlparse(url).scheme or "https").lower()
    entries = {}
    for part in proxy_server.split(";"):
        if "=" in part:
            name, value = part.split("=", 1)
            entries[name.strip().lower()] = value.strip()
    if entries:
        return _normalize_proxy_url(entries.get(scheme) or entries.get("http") or next(iter(entries.values())))
    return _normalize_proxy_url(proxy_server.strip())


def _normalize_proxy_url(proxy: str) -> str:
    proxy = str(proxy or "").strip()
    if not proxy:
        return ""
    if "://" not in proxy:
        proxy = f"http://{proxy}"
    return proxy


def _should_retry_without_env_proxy(url: str, exc: BaseException) -> bool:
    """Bypass broken local proxy tunnels for DeepSeek TLS EOF failures."""
    if os.environ.get("METIS_PROXY_MODE", os.environ.get("MIRO_PROXY_MODE", "")).strip().lower() == "off":
        return False
    if os.environ.get("METIS_DISABLE_DIRECT_PROXY_FALLBACK", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    if not any(os.environ.get(name) for name in _PROXY_ENV_NAMES):
        return False
    if os.environ.get("METIS_DISABLE_PROXY_FALLBACK", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    host = (urlparse(url).hostname or "").lower()
    if host != "api.deepseek.com" and not host.endswith(".deepseek.com"):
        return False
    text = repr(exc)
    return (
        isinstance(exc, requests.exceptions.SSLError)
        or "SSLZeroReturnError" in text
        or "TLS/SSL connection has been closed" in text
        or "failed to receive handshake" in text
    )


def parse_openai_tool_calls(
    raw_tool_calls: Any,
    tools: Optional[List[Dict[str, Any]]] = None,
    parallel: Optional[bool] = None,
) -> List[ToolCall]:
    return repair_tool_calls(raw_tool_calls, tools=tools, parallel=parallel)


def openai_stop_reason(finish_reason: str, has_tool_calls: bool = False) -> str:
    if has_tool_calls or finish_reason == "tool_calls":
        return "tool_use"
    if finish_reason == "length":
        return "max_tokens"
    if finish_reason == "stop":
        return "end_turn"
    return finish_reason or ""


def usage_from_openai(data: Dict[str, Any]) -> Usage:
    usage = data.get("usage") or {}
    prompt_details = usage.get("prompt_tokens_details") if isinstance(usage.get("prompt_tokens_details"), dict) else {}
    cache_hit = (
        usage.get("prompt_cache_hit_tokens")
        or usage.get("prompt_cache_hit")
        or prompt_details.get("cached_tokens")
        or 0
    )
    cache_miss = (
        usage.get("prompt_cache_miss_tokens")
        or usage.get("prompt_cache_miss")
        or max(0, int(usage.get("prompt_tokens") or 0) - int(cache_hit or 0))
    )
    return Usage(
        prompt_tokens=int(usage.get("prompt_tokens") or 0),
        completion_tokens=int(usage.get("completion_tokens") or 0),
        total_tokens=int(usage.get("total_tokens") or 0),
        prompt_cache_hit_tokens=int(cache_hit or 0),
        prompt_cache_miss_tokens=int(cache_miss or 0),
    )

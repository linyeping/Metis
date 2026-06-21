from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any


_SECRET_RE = re.compile(
    r"(?i)(sk-[a-z0-9_-]{16,}|api[_-]?key\s*[:=]\s*['\"]?[^'\"\s,}]+|token\s*[:=]\s*['\"]?[^'\"\s,}]+)"
)
_WIN_PATH_RE = re.compile(r"\b[A-Za-z]:\\(?:[^\\/:*?\"<>|\r\n]+\\)*[^\\/:*?\"<>|\r\n]*")
_SENSITIVE_TOOL_PREFIXES = (
    "metis_runtime",
    "metis_vm",
    "metis_wsl",
    "metis_sandbox",
    "metis_rootfs",
    "desktop_",
    "preview_browser",
    "browse_",
    "browser_",
)
_PUBLIC_KEYS = {
    "backend",
    "debug_category",
    "debug_next_action",
    "debug_summary",
    "error",
    "fallback_reason",
    "message",
    "ok",
    "ready",
    "status",
    "summary",
}


@dataclass(frozen=True)
class ToolVisibility:
    public_result: str
    diagnostic_result: str
    changed: bool


def sanitize_tool_result(tool_name: str, result: Any) -> ToolVisibility:
    raw = "" if result is None else str(result)
    diagnostic = redact_secrets(raw)
    if not _is_sensitive_tool(tool_name):
        return ToolVisibility(diagnostic, diagnostic, diagnostic != raw)

    public = _public_sensitive_result(tool_name, diagnostic)
    return ToolVisibility(public, diagnostic, public != raw)


def redact_secrets(value: Any) -> Any:
    if isinstance(value, dict):
        return {key: redact_secrets(item) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_secrets(item) for item in value]
    if not isinstance(value, str):
        return value
    return _SECRET_RE.sub("<redacted>", value)


def _is_sensitive_tool(tool_name: str) -> bool:
    return str(tool_name or "").startswith(_SENSITIVE_TOOL_PREFIXES)


def _public_sensitive_result(tool_name: str, text: str) -> str:
    data = _loads_json(text)
    if isinstance(data, dict):
        safe = _safe_public_dict(data)
        summary = _summary_line(tool_name, safe)
        if safe:
            return summary + "\n" + json.dumps(safe, ensure_ascii=False, indent=2)
        return summary
    if "traceback (most recent call last)" in text.lower():
        return f"{tool_name}: 执行失败，详细堆栈已写入本地诊断日志。\n{_last_error_line(text)}"
    return _redact_paths(text)


def _loads_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def _safe_public_dict(data: dict[str, Any]) -> dict[str, Any]:
    return {
        key: _redact_public_value(data[key])
        for key in sorted(_PUBLIC_KEYS)
        if key in data
    }


def _redact_public_value(value: Any) -> Any:
    if isinstance(value, str):
        return _redact_paths(redact_secrets(value))
    if isinstance(value, dict):
        return {
            key: _redact_public_value(item)
            for key, item in value.items()
            if key in _PUBLIC_KEYS
        }
    if isinstance(value, list):
        return [_redact_public_value(item) for item in value[:5]]
    return value


def _redact_paths(text: str) -> str:
    return _WIN_PATH_RE.sub("<local path>", text)


def _summary_line(tool_name: str, safe: dict[str, Any]) -> str:
    summary = str(safe.get("debug_summary") or safe.get("summary") or safe.get("message") or "").strip()
    if summary:
        return f"{tool_name}: {summary}"
    status = str(safe.get("status") or ("ok" if safe.get("ok") is True else "")).strip()
    if status:
        return f"{tool_name}: status={status}"
    return f"{tool_name}: 结果已记录，详细诊断保存在本地日志。"


def _last_error_line(text: str) -> str:
    for line in reversed(text.splitlines()):
        stripped = _redact_paths(line.strip())
        if stripped:
            return stripped[:500]
    return "错误详情已记录。"

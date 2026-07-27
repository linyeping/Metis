from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Generator, Mapping, TextIO

from .sessions import tool_transcript_record

EXIT_SUCCESS = 0
EXIT_TASK_FAILED = 1
EXIT_PERMISSION = 2
EXIT_ENVIRONMENT = 3
EXIT_BUDGET = 4
EXIT_CANCELLED = 5
EXIT_USAGE = 64


@dataclass
class HeadlessResult:
    exit_code: int = EXIT_TASK_FAILED
    session_id: str = ""
    final_text: str = ""
    event_count: int = 0
    turns: int = 0
    tool_calls: int = 0
    usage: Dict[str, int] = field(default_factory=dict)
    error: Dict[str, Any] = field(default_factory=dict)

    def payload(self) -> Dict[str, Any]:
        return {
            "schema": "metis.cli_result.v1",
            "session_id": self.session_id,
            "exit": self.exit_code,
            "text": self.final_text,
            "event_count": self.event_count,
            "turns": self.turns,
            "tool_calls": self.tool_calls,
            "usage": dict(self.usage),
            "error": dict(self.error) if self.error else None,
        }


class HeadlessRenderer:
    def __init__(
        self,
        *,
        output_format: str,
        stdout: TextIO,
        stderr: TextIO,
        serializer: Callable[[Any], Mapping[str, Any]],
    ) -> None:
        self.output_format = output_format
        self.stdout = stdout
        self.stderr = stderr
        self.serializer = serializer
        self._delta_text = ""
        self._delta_open = False
        self.transcript_records: list[Dict[str, Any]] = []

    def event(self, event: Any, result: HeadlessResult) -> None:
        result.event_count += 1
        kind = str(getattr(event, "type", "") or getattr(event, "kind", "") or "event")
        if self.output_format == "stream-json":
            self._json_line(self.stdout, self.serializer(event))
        elif self.output_format == "text":
            self._text_event(kind, event)

        transcript_record = tool_transcript_record(event)
        if transcript_record is not None:
            self.transcript_records.append(transcript_record)

        if kind in {"content", "content_delta", "text_delta"}:
            text = str(getattr(event, "text", "") or "")
            if kind == "content" or text:
                result.final_text = text if kind == "content" else result.final_text + text
        elif kind == "done":
            result.turns = int(getattr(event, "total_turns", 0) or 0)
            result.tool_calls = int(getattr(event, "total_tool_calls", 0) or 0)
            result.usage = {
                "prompt_tokens": int(getattr(event, "prompt_tokens", 0) or 0),
                "completion_tokens": int(getattr(event, "completion_tokens", 0) or 0),
                "total_tokens": int(getattr(event, "total_tokens", 0) or 0),
                "prompt_cache_hit_tokens": int(getattr(event, "prompt_cache_hit_tokens", 0) or 0),
                "prompt_cache_miss_tokens": int(getattr(event, "prompt_cache_miss_tokens", 0) or 0),
            }
        elif kind == "error":
            result.error = error_payload(event)

    def permission_error(self, event: Any) -> Dict[str, Any]:
        error = {
            "error": "permission_required",
            "tool": str(getattr(event, "tool_name", "") or ""),
            "arguments": _sanitize_permission_arguments(getattr(event, "arguments", {}) or {}),
            "request_id": str(getattr(event, "request_id", "") or ""),
            "how_to_allow": "Use --permission-mode, --policy, or --allowed-tools to define a non-interactive rule.",
        }
        self._json_line(self.stderr, error)
        return error

    def finish(self, result: HeadlessResult) -> None:
        if self._delta_open:
            self.stdout.write("\n")
            self.stdout.flush()
            self._delta_open = False
        if self.output_format == "json":
            self._json_line(self.stdout, result.payload())

    def runtime_exception(self, exc: BaseException) -> None:
        self._json_line(
            self.stderr,
            {"error": "runtime_exception", "type": type(exc).__name__, "message": str(exc)},
        )

    def _text_event(self, kind: str, event: Any) -> None:
        if kind in {"content_delta", "text_delta"}:
            text = str(getattr(event, "text", "") or "")
            self.stdout.write(text)
            self.stdout.flush()
            self._delta_text += text
            self._delta_open = True
            return
        if kind == "content":
            text = str(getattr(event, "text", "") or "")
            if not self._delta_open:
                self.stdout.write(text + ("" if text.endswith("\n") else "\n"))
                self.stdout.flush()
            elif text != self._delta_text:
                self.stdout.write("\n" + text + ("" if text.endswith("\n") else "\n"))
                self.stdout.flush()
            self._delta_open = False
            return
        if kind == "tool_call":
            self._ensure_line_break()
            self.stdout.write(f"[tool] {getattr(event, 'tool_name', '')}\n")
            self.stdout.flush()
            return
        if kind == "tool_result":
            summary = str(getattr(event, "result", "") or "").replace("\r", " ").replace("\n", " ").strip()
            if len(summary) > 180:
                summary = summary[:177] + "..."
            self.stdout.write(f"[result] {summary}\n")
            self.stdout.flush()
            return
        if kind == "todo_update":
            summary = str(getattr(event, "summary", "") or "").strip()
            if summary:
                self._ensure_line_break()
                self.stdout.write(f"> {summary}\n")
                self.stdout.flush()
            return
        if kind == "error":
            self._ensure_line_break()
            code = str(getattr(event, "code", "") or "RUNTIME_ERROR")
            message = str(getattr(event, "message", "") or "")
            self.stderr.write(f"[error {code}] {message}\n")
            self.stderr.flush()

    def _ensure_line_break(self) -> None:
        if self._delta_open:
            self.stdout.write("\n")
            self._delta_open = False

    @staticmethod
    def _json_line(stream: TextIO, payload: Mapping[str, Any]) -> None:
        stream.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        stream.flush()


def drive_headless(
    events: Generator[Any, Any, None],
    *,
    renderer: HeadlessRenderer,
    session_id: str,
) -> HeadlessResult:
    result = HeadlessResult(session_id=session_id)
    saw_done = False
    fatal_exit = 0
    try:
        while True:
            try:
                event = next(events)
            except StopIteration:
                break
            renderer.event(event, result)
            kind = str(getattr(event, "type", "") or getattr(event, "kind", "") or "")
            if kind == "permission_request":
                result.exit_code = EXIT_PERMISSION
                result.error = renderer.permission_error(event)
                events.close()
                renderer.finish(result)
                return result
            if kind == "error" and not bool(getattr(event, "recoverable", False)):
                fatal_exit = classify_error_exit(str(getattr(event, "code", "") or ""))
            if kind == "done":
                saw_done = True
    finally:
        try:
            events.close()
        except Exception:
            pass

    if fatal_exit:
        result.exit_code = fatal_exit
    elif saw_done:
        result.exit_code = EXIT_SUCCESS
    else:
        result.exit_code = EXIT_TASK_FAILED
        if not result.error:
            result.error = {"error": "run_incomplete", "message": "The agent stopped without a done event."}
    renderer.finish(result)
    return result


def classify_error_exit(code: str) -> int:
    normalized = str(code or "").strip().upper()
    if normalized == "RUNTIME_MAX_TURNS":
        return EXIT_BUDGET
    if normalized in {"RUNTIME_CANCELLED", "USER_CANCELLED", "CANCELLED"}:
        return EXIT_CANCELLED
    if normalized.startswith(("LLM_", "PROVIDER_", "SANDBOX_", "VM_", "ENV_", "CONFIG_")):
        return EXIT_ENVIRONMENT
    if normalized.startswith("RUNTIME_") and normalized not in {"RUNTIME_TASK_FAILED"}:
        return EXIT_ENVIRONMENT
    return EXIT_TASK_FAILED


def error_payload(event: Any) -> Dict[str, Any]:
    return {
        "error": "agent_error",
        "code": str(getattr(event, "code", "") or "RUNTIME_ERROR"),
        "title": str(getattr(event, "title", "") or ""),
        "message": str(getattr(event, "message", "") or ""),
        "hint": str(getattr(event, "hint", "") or ""),
        "recoverable": bool(getattr(event, "recoverable", False)),
    }


def _sanitize_permission_arguments(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "<truncated>"
    if isinstance(value, Mapping):
        out: Dict[str, Any] = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 20:
                out["..."] = "<truncated>"
                break
            key_text = str(key)
            if any(marker in key_text.lower() for marker in ("api_key", "apikey", "token", "secret", "password", "authorization")):
                out[key_text] = "***"
            else:
                out[key_text] = _sanitize_permission_arguments(item, depth=depth + 1)
        return out
    if isinstance(value, (list, tuple)):
        return [_sanitize_permission_arguments(item, depth=depth + 1) for item in list(value)[:20]]
    if isinstance(value, str):
        return value if len(value) <= 240 else value[:237] + "..."
    return value

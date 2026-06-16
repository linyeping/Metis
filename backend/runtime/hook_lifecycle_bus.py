"""Lifecycle event bus for Metis runtime hooks."""
from __future__ import annotations

import fnmatch
import json
import os
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from backend.core.paths import legacy_miro_home, metis_home, metis_path


HOOK_LIFECYCLE_SCHEMA = "metis.hook.lifecycle.v2"
_MAX_TEXT = 4000
_MAX_COMMAND_TEXT = 200
_SECRET_PARTS = ("key", "token", "secret", "password", "authorization", "cookie")


@dataclass(frozen=True)
class HookLifecycleEvent:
    kind: str
    workspace_root: str = ""
    tool_name: str = ""
    call_id: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    result_preview: str = ""
    ok: Optional[bool] = None
    status: str = ""
    error: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    timestamp: float = field(default_factory=time.time)
    schema: str = HOOK_LIFECYCLE_SCHEMA

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "event_id": self.event_id,
            "kind": self.kind,
            "timestamp": self.timestamp,
            "workspace_root": self.workspace_root,
            "tool_name": self.tool_name,
            "call_id": self.call_id,
            "arguments": _sanitize(self.arguments),
            "result_preview": _truncate(self.result_preview),
            "ok": self.ok,
            "status": self.status,
            "error": _truncate(self.error),
            "metadata": _sanitize(self.metadata),
        }


@dataclass(frozen=True)
class HookDispatchResult:
    event: HookLifecycleEvent
    handler_errors: List[str] = field(default_factory=list)
    command_outputs: List[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.handler_errors

    @property
    def display_output(self) -> str:
        return "\n".join(text for text in self.command_outputs if text)


HookHandler = Callable[[HookLifecycleEvent], Any]


class HookLifecycleBus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._subscribers: Dict[str, tuple[Optional[set[str]], HookHandler]] = {}
        self._recent: List[HookLifecycleEvent] = []
        self._recent_limit = 200

    def subscribe(self, handler: HookHandler, kinds: Optional[Iterable[str]] = None) -> str:
        sid = uuid.uuid4().hex
        kind_set = {str(kind) for kind in kinds} if kinds is not None else None
        with self._lock:
            self._subscribers[sid] = (kind_set, handler)
        return sid

    def unsubscribe(self, subscription_id: str) -> None:
        with self._lock:
            self._subscribers.pop(subscription_id, None)

    def recent(self, limit: int = 50) -> List[HookLifecycleEvent]:
        with self._lock:
            return list(self._recent[-max(0, int(limit or 0)) :])

    def reset_for_tests(self) -> None:
        with self._lock:
            self._subscribers.clear()
            self._recent.clear()

    def emit(
        self,
        kind: str,
        *,
        workspace_root: str = "",
        tool_name: str = "",
        call_id: str = "",
        arguments: Optional[Dict[str, Any]] = None,
        result: Any = "",
        ok: Optional[bool] = None,
        status: str = "",
        error: str = "",
        metadata: Optional[Dict[str, Any]] = None,
        run_configured_hooks: bool = True,
    ) -> HookDispatchResult:
        event = HookLifecycleEvent(
            kind=str(kind or "").strip() or "unknown",
            workspace_root=str(workspace_root or ""),
            tool_name=str(tool_name or ""),
            call_id=str(call_id or ""),
            arguments=dict(arguments or {}),
            result_preview=str(result or "")[:_MAX_TEXT],
            ok=ok,
            status=str(status or ""),
            error=str(error or ""),
            metadata=dict(metadata or {}),
        )
        with self._lock:
            self._recent.append(event)
            if len(self._recent) > self._recent_limit:
                self._recent = self._recent[-self._recent_limit :]
            subscribers = list(self._subscribers.items())

        _append_audit_event(event)
        command_outputs = _run_configured_command_hooks(event) if run_configured_hooks else []

        errors: List[str] = []
        for _sid, (kinds, handler) in subscribers:
            if kinds is not None and event.kind not in kinds:
                continue
            try:
                handler(event)
            except Exception as exc:
                errors.append(f"{type(exc).__name__}: {exc}")
        return HookDispatchResult(event=event, handler_errors=errors, command_outputs=command_outputs)


_BUS = HookLifecycleBus()


def get_hook_lifecycle_bus() -> HookLifecycleBus:
    return _BUS


def subscribe_hook_lifecycle(handler: HookHandler, kinds: Optional[Iterable[str]] = None) -> str:
    return _BUS.subscribe(handler, kinds=kinds)


def unsubscribe_hook_lifecycle(subscription_id: str) -> None:
    _BUS.unsubscribe(subscription_id)


def emit_hook_lifecycle(kind: str, **kwargs: Any) -> HookDispatchResult:
    return _BUS.emit(kind, **kwargs)


def recent_hook_lifecycle_events(limit: int = 50) -> List[Dict[str, Any]]:
    return [event.to_dict() for event in _BUS.recent(limit)]


def reset_hook_lifecycle_bus_for_tests() -> None:
    _BUS.reset_for_tests()


def _append_audit_event(event: HookLifecycleEvent) -> None:
    if os.environ.get("METIS_HOOK_LIFECYCLE_AUDIT", "1").strip().lower() in {"0", "false", "no", "off"}:
        return
    try:
        path = metis_path("audit", "hook-lifecycle.jsonl")
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")
    except Exception:
        return


def _run_configured_command_hooks(event: HookLifecycleEvent) -> List[str]:
    if os.environ.get("METIS_HOOK_COMMANDS", "1").strip().lower() in {"0", "false", "no", "off"}:
        return []
    outputs: List[str] = []
    for hook in _load_configured_hooks(event.workspace_root):
        if not isinstance(hook, dict) or not _hook_matches(hook, event):
            continue
        command = str(hook.get("command") or "").strip()
        if not command:
            continue
        description = str(hook.get("description") or hook.get("name") or command)
        rendered = _substitute_event(command, event)
        cwd = event.workspace_root if event.workspace_root and os.path.isdir(event.workspace_root) else os.getcwd()
        timeout = _hook_timeout(hook)
        try:
            proc = subprocess.run(
                rendered,
                shell=True,
                capture_output=True,
                text=True,
                timeout=timeout,
                cwd=cwd,
            )
            text = (proc.stdout if proc.returncode == 0 else (proc.stderr or proc.stdout) or "").strip()
            if proc.returncode == 0:
                suffix = f": {text[:_MAX_COMMAND_TEXT]}" if text else ""
                outputs.append(f"Hook [{description}]: ok{suffix}")
            else:
                outputs.append(f"Hook [{description}]: failed: {text[:_MAX_COMMAND_TEXT]}")
        except subprocess.TimeoutExpired:
            outputs.append(f"Hook [{description}]: timeout")
        except Exception as exc:
            outputs.append(f"Hook error [{description}]: {type(exc).__name__}: {exc}")
    return outputs


def _load_configured_hooks(workspace_root: str) -> List[Dict[str, Any]]:
    hooks: List[Dict[str, Any]] = []
    seen: set[str] = set()
    candidates: List[Path] = []
    if workspace_root:
        root = Path(workspace_root)
        candidates.extend([root / ".metis" / "hooks.json", root / ".miro" / "hooks.json"])
    else:
        cwd = Path(os.getcwd())
        candidates.extend([cwd / ".metis" / "hooks.json", cwd / ".miro" / "hooks.json"])
    candidates.extend([metis_home() / "hooks.json", legacy_miro_home() / "hooks.json"])
    for path in candidates:
        key = str(path.resolve(strict=False))
        if key in seen:
            continue
        seen.add(key)
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        rows = data.get("hooks", []) if isinstance(data, dict) else []
        if isinstance(rows, list):
            hooks.extend(item for item in rows if isinstance(item, dict))
    return hooks


def _hook_matches(hook: Dict[str, Any], event: HookLifecycleEvent) -> bool:
    trigger = str(hook.get("event") or hook.get("trigger") or "").strip()
    if not trigger:
        return False
    if not _trigger_matches(trigger, event):
        return False
    hook_tool = str(hook.get("tool") or hook.get("tool_name") or "").strip()
    if hook_tool and event.tool_name and not fnmatch.fnmatch(event.tool_name, hook_tool):
        return False
    condition = hook.get("condition", {})
    return _condition_matches(condition, event)


def _trigger_matches(trigger: str, event: HookLifecycleEvent) -> bool:
    if trigger.startswith("post:"):
        tool = trigger[5:] or "*"
        return event.kind == "tool.finish" and fnmatch.fnmatch(event.tool_name, tool)
    if trigger.startswith("pre:"):
        tool = trigger[4:] or "*"
        return event.kind == "tool.start" and fnmatch.fnmatch(event.tool_name, tool)
    aliases = {
        "preToolUse": "tool.start",
        "postToolUse": "tool.finish",
        "postToolUseFailure": "tool.error",
        "fileEdited": "file.changed",
        "fileCreated": "file.changed",
        "fileDeleted": "file.changed",
        "promptSubmit": "agent.start",
        "agentStop": "agent.stop",
        "preTaskExecution": "task.start",
        "postTaskExecution": "task.finish",
    }
    target = aliases.get(trigger, trigger)
    return fnmatch.fnmatch(event.kind, target)


def _condition_matches(condition: Any, event: HookLifecycleEvent) -> bool:
    if not isinstance(condition, dict) or not condition:
        return True
    payload = event.to_dict()
    for key, pattern in condition.items():
        raw_key = str(key)
        value = _lookup_condition_value(payload, raw_key)
        if value is None and "." not in raw_key:
            value = event.arguments.get(raw_key)
        if not fnmatch.fnmatch(str(value or ""), str(pattern)):
            return False
    return True


def _lookup_condition_value(payload: Dict[str, Any], dotted: str) -> Any:
    value: Any = payload
    for part in dotted.split("."):
        if isinstance(value, dict) and part in value:
            value = value[part]
        else:
            return None
    return value


def _substitute_event(command: str, event: HookLifecycleEvent) -> str:
    rendered = command
    replacements = {
        "event_kind": event.kind,
        "tool_name": event.tool_name,
        "call_id": event.call_id,
        "workspace_root": event.workspace_root,
        "result": event.result_preview[:1000],
        "result_preview": event.result_preview[:1000],
        "status": event.status,
        "error": event.error[:1000],
    }
    for key, value in replacements.items():
        rendered = rendered.replace(f"{{{key}}}", str(value))
    for key, value in event.arguments.items():
        rendered = rendered.replace(f"{{{key}}}", str(value))
        rendered = rendered.replace(f"{{arguments.{key}}}", str(value))
    return rendered


def _hook_timeout(hook: Dict[str, Any]) -> int:
    try:
        return max(1, min(int(hook.get("timeout", 30)), 120))
    except (TypeError, ValueError):
        return 30


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        out: Dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if any(part in key_text.lower() for part in _SECRET_PARTS):
                out[key_text] = "[redacted]"
            else:
                out[key_text] = _sanitize(item)
        return out
    if isinstance(value, list):
        return [_sanitize(item) for item in value[:100]]
    if isinstance(value, tuple):
        return [_sanitize(item) for item in value[:100]]
    if isinstance(value, str):
        return _truncate(value)
    return value


def _truncate(text: str) -> str:
    raw = str(text or "")
    if len(raw) <= _MAX_TEXT:
        return raw
    return raw[: _MAX_TEXT - 40] + "\n[truncated by hook lifecycle bus]"


__all__ = [
    "HOOK_LIFECYCLE_SCHEMA",
    "HookDispatchResult",
    "HookLifecycleBus",
    "HookLifecycleEvent",
    "emit_hook_lifecycle",
    "get_hook_lifecycle_bus",
    "recent_hook_lifecycle_events",
    "reset_hook_lifecycle_bus_for_tests",
    "subscribe_hook_lifecycle",
    "unsubscribe_hook_lifecycle",
]

from __future__ import annotations

import contextlib
import io
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Generator, Iterable, Iterator, Mapping, TextIO, cast

from backend.bridges.event_serializer import agent_event_payload
from backend.cli.args import ParsedCliArgs
from backend.cli.config import build_cli_runtime
from backend.cli.headless import EXIT_ENVIRONMENT, EXIT_SUCCESS, EXIT_TASK_FAILED, classify_error_exit
from backend.cli.policy import build_permission_checker
from backend.cli.sessions import CliSessionStore, tool_transcript_record

from .events import AgentEvent

PermissionHandler = Callable[[AgentEvent], bool]
EventHandler = Callable[[AgentEvent], None]

_RUN_LOCK = threading.RLock()
_RUNTIME_ENV_KEYS = (
    "METIS_LLM_BACKEND",
    "METIS_LLM_BASE_URL",
    "METIS_LLM_API_KEY",
    "METIS_LLM_MODEL",
    "METIS_TEMPERATURE",
    "METIS_REASONING_EFFORT",
    "METIS_MAX_TOKENS",
    "METIS_MAX_TURNS",
    "METIS_LLM_TIMEOUT",
    "METIS_PROXY_MODE",
    "METIS_PROXY_SCHEME",
    "METIS_PROXY_HOST",
    "METIS_PROXY_PORT",
    "METIS_PROXY_BYPASS",
    "METIS_DISABLE_DESKTOP_TOOLS",
    "METIS_DISABLE_MCP",
)


@dataclass(frozen=True)
class AgentResult:
    session_id: str
    exit_code: int
    final_text: str = ""
    event_count: int = 0
    turns: int = 0
    tool_calls: int = 0
    usage: Mapping[str, int] = field(default_factory=dict)
    error: Mapping[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.exit_code == int(EXIT_SUCCESS)


class AgentRunError(RuntimeError):
    def __init__(self, result: AgentResult) -> None:
        message = str(result.error.get("message") or result.error.get("code") or "agent run failed")
        super().__init__(message)
        self.result = result


class Agent:
    """In-process Metis agent using the same runtime and event contract as CLI."""

    def __init__(
        self,
        *,
        backend: str = "",
        base_url: str = "",
        model: str = "",
        api_key: str = "",
        permission_mode: str = "ask",
        allowed_tools: Iterable[str] | None = None,
        policy: str | Path = "",
        max_turns: int | None = None,
        include_desktop: bool = False,
        include_mcp: bool = False,
        diagnostics: TextIO | None = None,
    ) -> None:
        if max_turns is not None and max_turns <= 0:
            raise ValueError("max_turns must be greater than zero")
        self.backend = str(backend or "")
        self.base_url = str(base_url or "")
        self.model = str(model or "")
        self.api_key = str(api_key or "")
        self.permission_mode = str(permission_mode or "ask")
        self.allowed_tools = tuple(str(item).strip() for item in (allowed_tools or ()) if str(item).strip())
        self.policy = str(policy or "")
        self.max_turns = max_turns
        self.include_desktop = bool(include_desktop)
        self.include_mcp = bool(include_mcp)
        self.diagnostics = diagnostics

    def run(
        self,
        prompt: str,
        *,
        workspace: str | Path = ".",
        session_id: str = "",
        continue_session: bool = False,
        permission_handler: PermissionHandler | None = None,
    ) -> Generator[AgentEvent, bool | None, AgentResult]:
        """Yield typed events; send a bool at permission events to decide once.

        When the consumer sends ``None``, ``permission_handler`` is consulted.
        If neither supplies a decision, the request is denied.
        """

        prompt_text = str(prompt or "").strip()
        if not prompt_text:
            raise ValueError("prompt is required")
        if session_id and continue_session:
            raise ValueError("session_id and continue_session are mutually exclusive")
        workspace_path = _workspace_path(workspace)
        diagnostics = self.diagnostics or io.StringIO()
        args = self._cli_args(prompt_text)
        explicit_env = {
            "METIS_LLM_BACKEND": self.backend,
            "METIS_LLM_BASE_URL": self.base_url,
            "METIS_LLM_API_KEY": self.api_key,
            "METIS_LLM_MODEL": self.model,
        }

        with _RUN_LOCK, _temporary_environment(explicit_env), _working_directory(workspace_path):
            store = CliSessionStore()
            resume = store.resolve_resume(session_id, latest=continue_session) if (session_id or continue_session) else None
            if resume is not None and str(workspace or ".") == ".":
                workspace_path = _workspace_path(resume.workspace)
            run_session_id, messages = store.begin_run(prompt=prompt_text, workspace=workspace_path, resume=resume)
            permission_checker = build_permission_checker(workspace_path, self.policy)
            renderer_records: list[dict[str, Any]] = []
            result = _MutableResult(session_id=run_session_id)

            events = None
            try:
                with contextlib.redirect_stdout(diagnostics), _working_directory(workspace_path):
                    config, registry = build_cli_runtime(args, workspace=workspace_path, session_id=run_session_id)
                    config.permission_checker = permission_checker
                    from backend.runtime.agent_loop import run as runtime_run

                    events = runtime_run(messages, config, registry=registry)
                    send_pending = False
                    send_value = False
                    while True:
                        try:
                            if send_pending:
                                runtime_event = events.send(send_value)
                                send_pending = False
                            else:
                                runtime_event = next(events)
                        except StopIteration:
                            break

                        event = AgentEvent(agent_event_payload(runtime_event))
                        result.observe(event)
                        transcript = tool_transcript_record(runtime_event)
                        if transcript is not None:
                            renderer_records.append(transcript)
                        consumer_decision = yield event
                        if event.kind == "permission_request":
                            if consumer_decision is None and permission_handler is not None:
                                consumer_decision = bool(permission_handler(event))
                            send_value = bool(consumer_decision) if consumer_decision is not None else False
                            send_pending = True
            except Exception as exc:
                error_event = AgentEvent(
                    agent_event_payload(
                        {
                            "kind": "error",
                            "code": "SDK_RUNTIME_EXCEPTION",
                            "message": f"{type(exc).__name__}: {exc}",
                            "recoverable": False,
                        }
                    )
                )
                result.observe(error_event)
                result.fatal_exit = EXIT_ENVIRONMENT
                yield error_event
            finally:
                if events is not None:
                    try:
                        events.close()
                    except Exception:
                        pass
                final = result.freeze()
                store.finish_run(run_session_id, transcript_records=renderer_records, final_text=final.final_text)
            return final

    def run_to_completion(
        self,
        prompt: str,
        *,
        workspace: str | Path = ".",
        session_id: str = "",
        continue_session: bool = False,
        permission_handler: PermissionHandler | None = None,
        on_event: EventHandler | None = None,
        raise_on_error: bool = True,
    ) -> AgentResult:
        stream = self.run(
            prompt,
            workspace=workspace,
            session_id=session_id,
            continue_session=continue_session,
            permission_handler=permission_handler,
        )
        completed = False
        try:
            while True:
                try:
                    event = next(stream)
                except StopIteration as stop:
                    result = cast(AgentResult, stop.value)
                    completed = True
                    break
                if on_event is not None:
                    on_event(event)
        finally:
            if not completed:
                stream.close()
        if raise_on_error and not result.ok:
            raise AgentRunError(result)
        return result

    def _cli_args(self, prompt: str) -> ParsedCliArgs:
        return ParsedCliArgs(
            prompt=prompt,
            print_mode=True,
            attach=False,
            output_format="stream-json",
            workspace="",
            permission_mode=self.permission_mode,
            allowed_tools=",".join(self.allowed_tools),
            policy=self.policy,
            backend=self.backend,
            base_url=self.base_url,
            model=self.model,
            max_turns=self.max_turns,
            no_desktop=not self.include_desktop,
            no_mcp=not self.include_mcp,
            debug=False,
            resume_id="",
            continue_session=False,
        )


@dataclass
class _MutableResult:
    session_id: str
    final_text: str = ""
    event_count: int = 0
    turns: int = 0
    tool_calls: int = 0
    usage: dict[str, int] = field(default_factory=dict)
    error: dict[str, Any] = field(default_factory=dict)
    saw_done: bool = False
    fatal_exit: int = 0

    def observe(self, event: AgentEvent) -> None:
        self.event_count += 1
        if event.kind in {"content_delta", "text_delta"}:
            self.final_text += event.text
        elif event.kind == "content":
            self.final_text = event.text or self.final_text
        elif event.kind == "error":
            self.error = {
                "code": str(event.get("code") or event.payload.get("code") or "RUNTIME_ERROR"),
                "message": str(event.get("message") or event.payload.get("message") or ""),
                "recoverable": bool(event.get("recoverable", event.payload.get("recoverable", False))),
            }
            if not self.error["recoverable"]:
                self.fatal_exit = classify_error_exit(self.error["code"])
        elif event.kind == "done":
            self.saw_done = True
            self.turns = int(event.get("turns") or event.payload.get("turns") or 0)
            self.tool_calls = int(event.get("tool_calls") or event.payload.get("tool_calls") or 0)
            usage = event.get("usage") or event.payload.get("usage") or {}
            if isinstance(usage, Mapping):
                self.usage = {str(key): int(value or 0) for key, value in usage.items()}

    def freeze(self) -> AgentResult:
        exit_code = self.fatal_exit or (EXIT_SUCCESS if self.saw_done else EXIT_TASK_FAILED)
        error = dict(self.error)
        if exit_code == EXIT_TASK_FAILED and not error:
            error = {"code": "RUN_INCOMPLETE", "message": "The agent stopped without a done event."}
        return AgentResult(
            session_id=self.session_id,
            exit_code=exit_code,
            final_text=self.final_text,
            event_count=self.event_count,
            turns=self.turns,
            tool_calls=self.tool_calls,
            usage=dict(self.usage),
            error=error,
        )


@contextlib.contextmanager
def _temporary_environment(explicit: Mapping[str, str]) -> Iterator[None]:
    snapshot = {key: os.environ.get(key) for key in _RUNTIME_ENV_KEYS}
    try:
        for key, value in explicit.items():
            if value:
                os.environ[key] = value
        yield
    finally:
        for snapshot_key, snapshot_value in snapshot.items():
            if snapshot_value is None:
                os.environ.pop(snapshot_key, None)
            else:
                os.environ[snapshot_key] = snapshot_value


def _workspace_path(value: str | Path) -> Path:
    path = Path(value or ".").expanduser().resolve(strict=False)
    if not path.exists():
        raise ValueError(f"workspace does not exist: {path}")
    if not path.is_dir():
        raise ValueError(f"workspace is not a directory: {path}")
    return path


@contextlib.contextmanager
def _working_directory(workspace: Path) -> Iterator[None]:
    previous = Path.cwd()
    os.chdir(workspace)
    try:
        yield
    finally:
        os.chdir(previous)


__all__ = ["Agent", "AgentResult", "AgentRunError", "EventHandler", "PermissionHandler"]

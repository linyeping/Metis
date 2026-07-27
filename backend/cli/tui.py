from __future__ import annotations

import contextlib
import json
import os
import sys
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Generator, Mapping, Protocol, TextIO

from .args import CliUsageError, ParsedCliArgs
from .config import build_cli_runtime
from .headless import (
    EXIT_SUCCESS,
    EXIT_TASK_FAILED,
    HeadlessRenderer,
    HeadlessResult,
    classify_error_exit,
)
from .policy import build_permission_checker
from .sessions import CliSessionError, CliSessionStore


class TuiFrontend(Protocol):
    def read_prompt(self, *, toolbar: str) -> str | None: ...

    def write(self, text: str, *, style: str = "") -> None: ...

    def write_chunk(self, text: str) -> None: ...

    def confirm_permission(self, *, tool: str, arguments: Mapping[str, Any]) -> bool: ...

    def clear(self) -> None: ...


class PromptToolkitFrontend:
    _ANSI = {
        "accent": "\x1b[38;5;141m",
        "dim": "\x1b[2m",
        "error": "\x1b[31m",
        "success": "\x1b[32m",
        "tool": "\x1b[36m",
        "warning": "\x1b[33m",
    }

    def __init__(self, *, stdin: TextIO, stdout: TextIO) -> None:
        try:
            from prompt_toolkit import PromptSession
            from prompt_toolkit.formatted_text import HTML
            from prompt_toolkit.history import InMemoryHistory
        except ImportError as exc:  # pragma: no cover - packaging gate covers this path
            raise CliUsageError("interactive TUI requires prompt_toolkit") from exc

        self.stdin = stdin
        self.stdout = stdout
        self.color = not bool(os.environ.get("NO_COLOR")) and _isatty(stdout)
        self._html = HTML
        self._session = PromptSession(history=InMemoryHistory(), input=None, output=None)
        self._permission_session = PromptSession(input=None, output=None)

    def read_prompt(self, *, toolbar: str) -> str | None:
        prompt = self._html("<ansimagenta>you</ansimagenta> <ansigreen>❯</ansigreen> ") if self.color else "you ❯ "
        try:
            return self._session.prompt(prompt, bottom_toolbar=toolbar or None).strip()
        except KeyboardInterrupt:
            self.write("^C", style="dim")
            return ""
        except EOFError:
            return None

    def write(self, text: str, *, style: str = "") -> None:
        rendered = text
        if self.color and style in self._ANSI:
            rendered = f"{self._ANSI[style]}{text}\x1b[0m"
        self.stdout.write(rendered + ("" if rendered.endswith("\n") else "\n"))
        self.stdout.flush()

    def write_chunk(self, text: str) -> None:
        self.stdout.write(text)
        self.stdout.flush()

    def confirm_permission(self, *, tool: str, arguments: Mapping[str, Any]) -> bool:
        self.write(f"Permission requested: {tool}", style="warning")
        self.write(_compact_json(arguments, limit=600), style="dim")
        try:
            answer = self._permission_session.prompt("Allow once? [y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return False
        return answer in {"y", "yes"}

    def clear(self) -> None:
        self.stdout.write("\x1b[2J\x1b[H")
        self.stdout.flush()


@dataclass
class TuiState:
    workspace: Path
    session_id: str = ""
    pending_resume: str = ""
    continue_next: bool = False
    workspace_explicit: bool = False


class TuiRenderer(HeadlessRenderer):
    def __init__(self, frontend: TuiFrontend, *, serializer: Callable[[Any], Mapping[str, Any]]) -> None:
        super().__init__(
            output_format="tui",
            stdout=sys.stdout,
            stderr=sys.stderr,
            serializer=serializer,
        )
        self.frontend = frontend
        self._answer_started = False
        self._answer_text = ""

    def event(self, event: Any, result: HeadlessResult) -> None:
        super().event(event, result)
        kind = str(getattr(event, "type", "") or getattr(event, "kind", "") or "event")
        if kind in {"content_delta", "text_delta"}:
            text = str(getattr(event, "text", "") or "")
            if text:
                self._start_answer()
                self.frontend.write_chunk(text)
                self._answer_text += text
            return
        if kind == "content":
            text = str(getattr(event, "text", "") or "")
            if not self._answer_started:
                self._start_answer()
                self.frontend.write(text)
            elif text and text != self._answer_text:
                self.frontend.write("")
                self.frontend.write(text)
            else:
                self.frontend.write("")
            self._answer_started = False
            self._answer_text = ""
            return
        if kind == "thinking":
            text = str(getattr(event, "text", "") or "").strip()
            if text:
                self._end_answer()
                self.frontend.write(f"◇ {text}", style="dim")
            return
        if kind == "tool_call":
            self._end_answer()
            tool = str(getattr(event, "tool_name", "") or getattr(event, "tool", "") or "tool")
            arguments = getattr(event, "arguments", None) or getattr(event, "args", None) or {}
            suffix = f"  {_compact_json(_redact(arguments), limit=220)}" if arguments else ""
            self.frontend.write(f"● {tool}{suffix}", style="tool")
            return
        if kind == "tool_result":
            self._end_answer()
            summary = " ".join(str(getattr(event, "result", "") or "").split())
            if len(summary) > 260:
                summary = summary[:257] + "..."
            self.frontend.write(f"  ↳ {summary or 'completed'}", style="dim")
            return
        if kind == "todo_update":
            self._end_answer()
            summary = str(getattr(event, "summary", "") or "").strip()
            if summary:
                self.frontend.write(f"▣ {summary}", style="accent")
            return
        if kind == "permission_request":
            self._end_answer()
            tool = str(getattr(event, "tool_name", "") or getattr(event, "tool", "") or "tool")
            self.frontend.write(f"◆ {tool} needs approval", style="warning")
            return
        if kind == "error":
            self._end_answer()
            code = str(getattr(event, "code", "") or "RUNTIME_ERROR")
            message = str(getattr(event, "message", "") or "")
            self.frontend.write(f"Error [{code}] {message}", style="error")
            return
        if kind == "done":
            self._end_answer()
            turns = int(getattr(event, "total_turns", 0) or getattr(event, "turns", 0) or 0)
            tools = int(getattr(event, "total_tool_calls", 0) or getattr(event, "tool_calls", 0) or 0)
            tokens = int(getattr(event, "total_tokens", 0) or 0)
            self.frontend.write(f"✓ {turns} turn(s) · {tools} tool(s) · {tokens} token(s)", style="success")

    def permission_waiting(self, event: Any) -> None:
        tool = str(getattr(event, "tool_name", "") or getattr(event, "tool", "") or "tool")
        self.frontend.write(f"Approve or deny {tool} in Metis desktop; this terminal will keep waiting.", style="warning")

    def finish(self, result: HeadlessResult) -> None:
        self._end_answer()

    def runtime_exception(self, exc: BaseException) -> None:
        self._end_answer()
        self.frontend.write(f"Runtime error: {type(exc).__name__}: {exc}", style="error")

    def _start_answer(self) -> None:
        if not self._answer_started:
            self.frontend.write("Metis", style="accent")
            self._answer_started = True

    def _end_answer(self) -> None:
        if self._answer_started:
            self.frontend.write("")
            self._answer_started = False
            self._answer_text = ""


def drive_tui(
    events: Generator[Any, Any, None],
    *,
    renderer: TuiRenderer,
    session_id: str,
    permission_handler: Callable[[Any], bool] | None,
) -> HeadlessResult:
    result = HeadlessResult(session_id=session_id)
    saw_done = False
    fatal_exit = 0
    send_pending = False
    send_value = False
    try:
        while True:
            try:
                if send_pending:
                    event = events.send(send_value)
                    send_pending = False
                else:
                    event = next(events)
            except StopIteration:
                break
            renderer.event(event, result)
            kind = str(getattr(event, "type", "") or getattr(event, "kind", "") or "")
            if kind == "permission_request":
                if permission_handler is None:
                    renderer.permission_waiting(event)
                else:
                    send_value = bool(permission_handler(event))
                    send_pending = True
            elif kind == "error" and not bool(getattr(event, "recoverable", False)):
                fatal_exit = classify_error_exit(str(getattr(event, "code", "") or ""))
            elif kind == "done":
                saw_done = True
    finally:
        try:
            events.close()
        except Exception:
            pass

    result.exit_code = fatal_exit or (EXIT_SUCCESS if saw_done else EXIT_TASK_FAILED)
    if result.exit_code == EXIT_TASK_FAILED and not result.error:
        result.error = {"error": "run_incomplete", "message": "The agent stopped without a done event."}
    renderer.finish(result)
    return result


def run_tui(
    args: ParsedCliArgs,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stderr: TextIO,
    frontend: TuiFrontend | None = None,
) -> int:
    ui = frontend or PromptToolkitFrontend(stdin=stdin, stdout=stdout)
    store = CliSessionStore()
    state = _initial_state(args, store)
    ui.write("Metis CLI", style="accent")
    ui.write("Interactive session · /help for commands · Ctrl-D or /exit to quit", style="dim")
    ui.write(f"Workspace: {state.workspace}", style="dim")

    pending = str(args.prompt or "").strip()
    while True:
        if pending:
            prompt = pending
            pending = ""
        else:
            prompt = ui.read_prompt(toolbar=_toolbar(state, attach=args.attach))
            if prompt is None:
                ui.write("bye", style="dim")
                return EXIT_SUCCESS
            prompt = prompt.strip()
            if not prompt:
                continue
        if prompt.startswith("/"):
            try:
                if not _handle_command(prompt, state=state, store=store, ui=ui, attach=args.attach):
                    return EXIT_SUCCESS
            except (CliUsageError, CliSessionError) as exc:
                ui.write(f"{exc}", style="error")
            continue

        try:
            if args.attach:
                result = _run_attached_turn(args, prompt=prompt, state=state, ui=ui)
            else:
                result = _run_local_turn(args, prompt=prompt, state=state, store=store, ui=ui, stderr=stderr)
            state.session_id = result.session_id or state.session_id
            state.pending_resume = ""
            state.continue_next = False
            state.workspace_explicit = False
        except KeyboardInterrupt:
            ui.write("Run cancelled. The interactive session is still open.", style="warning")
        except (CliUsageError, CliSessionError) as exc:
            ui.write(f"{exc}", style="error")
        except Exception as exc:
            ui.write(f"{type(exc).__name__}: {exc}", style="error")
            if args.debug:
                traceback.print_exc(file=stderr)


def _run_local_turn(
    args: ParsedCliArgs,
    *,
    prompt: str,
    state: TuiState,
    store: CliSessionStore,
    ui: TuiFrontend,
    stderr: TextIO,
) -> HeadlessResult:
    resume = None
    if state.session_id:
        resume = store.resolve_resume(state.session_id)
    elif state.pending_resume or state.continue_next:
        resume = store.resolve_resume(state.pending_resume, latest=state.continue_next)
    workspace = _resolve_workspace(state.workspace if resume is None or state.workspace_explicit else resume.workspace)
    state.workspace = workspace
    session_id, messages = store.begin_run(prompt=prompt, workspace=workspace, resume=resume)
    permission_checker = build_permission_checker(workspace, args.policy)

    with contextlib.redirect_stdout(stderr), _working_directory(workspace):
        config, registry = build_cli_runtime(args, workspace=workspace, session_id=session_id)
        config.permission_checker = permission_checker
        from backend.bridges.event_serializer import agent_event_payload
        from backend.runtime.agent_loop import run

        renderer = TuiRenderer(ui, serializer=agent_event_payload)
        result = drive_tui(
            run(messages, config, registry=registry),
            renderer=renderer,
            session_id=session_id,
            permission_handler=lambda event: ui.confirm_permission(
                tool=str(getattr(event, "tool_name", "") or "tool"),
                arguments=_redact(getattr(event, "arguments", {}) or {}),
            ),
        )
    store.finish_run(session_id, transcript_records=renderer.transcript_records, final_text=result.final_text)
    return result


def _run_attached_turn(
    args: ParsedCliArgs,
    *,
    prompt: str,
    state: TuiState,
    ui: TuiFrontend,
) -> HeadlessResult:
    from .attach import AttachClient, load_attach_endpoint, validate_attach_args

    validate_attach_args(args)
    client = AttachClient(load_attach_endpoint())
    client.handshake()
    resume_id = state.session_id or state.pending_resume
    workspace = state.workspace if state.workspace_explicit or (not resume_id and not state.continue_next) else None
    session = client.prepare_session(workspace=workspace, resume_id=resume_id, continue_session=state.continue_next)
    created = client.create_run(prompt=prompt, session=session)
    run_id = str(created["run_id"])
    renderer = TuiRenderer(ui, serializer=lambda event: event._raw)
    try:
        return drive_tui(
            client.events(run_id),
            renderer=renderer,
            session_id=str(session["session_id"]),
            permission_handler=None,
        )
    except KeyboardInterrupt:
        client.cancel(run_id)
        raise


def _initial_state(args: ParsedCliArgs, store: CliSessionStore) -> TuiState:
    workspace = _resolve_workspace(args.workspace or ".")
    if args.attach:
        return TuiState(
            workspace=workspace,
            pending_resume=args.resume_id,
            continue_next=args.continue_session,
            workspace_explicit=bool(args.workspace),
        )
    if args.resume_id or args.continue_session:
        resume = store.resolve_resume(args.resume_id, latest=args.continue_session)
        return TuiState(workspace=_resolve_workspace(args.workspace or resume.workspace), session_id=resume.session_id)
    return TuiState(workspace=workspace, workspace_explicit=bool(args.workspace))


def _handle_command(
    command: str,
    *,
    state: TuiState,
    store: CliSessionStore,
    ui: TuiFrontend,
    attach: bool,
) -> bool:
    name, _, raw_arg = command.partition(" ")
    arg = raw_arg.strip()
    name = name.lower()
    if name in {"/exit", "/quit"}:
        return False
    if name == "/help":
        ui.write("/sessions [N]   list recent sessions")
        ui.write("/resume ID      continue a session by ID or unique prefix")
        ui.write("/continue       continue the most recent session")
        ui.write("/new            start a new session")
        ui.write("/workspace PATH start the next session in another workspace")
        ui.write("/status         show current runtime, session, and workspace")
        ui.write("/clear          clear the terminal")
        ui.write("/exit           leave the TUI")
        return True
    if name == "/clear":
        ui.clear()
        return True
    if name == "/new":
        state.session_id = ""
        state.pending_resume = ""
        state.continue_next = False
        ui.write("New session ready.", style="success")
        return True
    if name == "/sessions":
        try:
            limit = max(1, min(50, int(arg or "10")))
        except ValueError:
            ui.write("Usage: /sessions [1-50]", style="error")
            return True
        sessions = store.list_payload(limit=limit, archived=False)["sessions"]
        if not sessions:
            ui.write("No sessions.", style="dim")
        for item in sessions:
            marker = "*" if item["id"] == state.session_id else " "
            ui.write(f"{marker} {item['id'][:12]}  {item['title']}  [{item['workspace']}]", style="dim")
        return True
    if name == "/resume":
        if not arg:
            ui.write("Usage: /resume ID", style="error")
            return True
        if attach:
            state.session_id = ""
            state.pending_resume = arg
        else:
            resume = store.resolve_resume(arg)
            state.session_id = resume.session_id
            state.workspace = _resolve_workspace(resume.workspace)
        state.continue_next = False
        ui.write(f"Next message continues {arg}.", style="success")
        return True
    if name == "/continue":
        if attach:
            state.session_id = ""
            state.pending_resume = ""
            state.continue_next = True
        else:
            resume = store.resolve_resume(latest=True)
            state.session_id = resume.session_id
            state.workspace = _resolve_workspace(resume.workspace)
        ui.write("Next message continues the most recent session.", style="success")
        return True
    if name == "/workspace":
        if not arg:
            ui.write("Usage: /workspace PATH", style="error")
            return True
        state.workspace = _resolve_workspace(arg)
        state.session_id = ""
        state.pending_resume = ""
        state.continue_next = False
        state.workspace_explicit = True
        ui.write(f"New session workspace: {state.workspace}", style="success")
        return True
    if name == "/status":
        ui.write(f"Runtime: {'desktop attach' if attach else 'embedded'}")
        ui.write(f"Session: {state.session_id or state.pending_resume or ('latest' if state.continue_next else 'new')}")
        ui.write(f"Workspace: {state.workspace}")
        return True
    ui.write(f"Unknown command: {name}. Use /help.", style="error")
    return True


def _toolbar(state: TuiState, *, attach: bool) -> str:
    runtime = "desktop" if attach else "embedded"
    session = state.session_id[:10] if state.session_id else "new"
    return f" {runtime} · {session} · {state.workspace} "


def _resolve_workspace(value: str | Path) -> Path:
    path = Path(value or ".").expanduser().resolve(strict=False)
    if not path.exists():
        raise CliUsageError(f"workspace does not exist: {path}")
    if not path.is_dir():
        raise CliUsageError(f"workspace is not a directory: {path}")
    return path


@contextlib.contextmanager
def _working_directory(workspace: Path):
    previous = Path.cwd()
    os.chdir(workspace)
    try:
        yield
    finally:
        os.chdir(previous)


def _redact(value: Any, *, depth: int = 0) -> Any:
    if depth > 4:
        return "<truncated>"
    if isinstance(value, Mapping):
        output = {}
        for index, (key, item) in enumerate(value.items()):
            if index >= 20:
                output["..."] = "<truncated>"
                break
            text = str(key)
            if any(marker in text.lower() for marker in ("api_key", "apikey", "token", "secret", "password", "authorization")):
                output[text] = "***"
            else:
                output[text] = _redact(item, depth=depth + 1)
        return output
    if isinstance(value, (list, tuple)):
        return [_redact(item, depth=depth + 1) for item in list(value)[:20]]
    if isinstance(value, str):
        return value if len(value) <= 240 else value[:237] + "..."
    return value


def _compact_json(value: Any, *, limit: int) -> str:
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    return text if len(text) <= limit else text[: max(0, limit - 3)] + "..."


def _isatty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError):
        return False

from __future__ import annotations

import contextlib
import os
import sys
import traceback
from pathlib import Path
from typing import Sequence, TextIO

from .args import (
    CliUsageError,
    DoctorCommandArgs,
    ParsedCliArgs,
    SandboxCommandArgs,
    SessionCommandArgs,
    parse_args,
)
from .config import CliConfigError, build_cli_runtime
from .headless import (
    EXIT_CANCELLED,
    EXIT_ENVIRONMENT,
    EXIT_USAGE,
    HeadlessRenderer,
    drive_headless,
)
from .policy import CliPolicyError, build_permission_checker
from .sessions import CliSessionError, CliSessionStore, handle_session_command


def main(
    argv: Sequence[str] | None = None,
    *,
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    stderr: TextIO | None = None,
) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout
    stderr = stderr or sys.stderr
    _configure_stream(stdout)
    _configure_stream(stderr)

    try:
        args = parse_args(argv)
        if isinstance(args, SessionCommandArgs):
            return handle_session_command(args, stdout=stdout)
        if isinstance(args, (DoctorCommandArgs, SandboxCommandArgs)):
            from .doctor import handle_diagnostic_command

            return handle_diagnostic_command(args, stdout=stdout)
        if _should_run_tui(args, stdin=stdin, stdout=stdout):
            from .tui import run_tui

            return run_tui(args, stdin=stdin, stdout=stdout, stderr=stderr)
        prompt = _prompt_text(args, stdin)
        if args.attach:
            from .attach import CliAttachError, run_attached

            if args.workspace:
                workspace = _workspace_path(args.workspace)
            elif args.resume_id or args.continue_session:
                workspace = None
            else:
                workspace = _workspace_path(".")
            try:
                result = run_attached(
                    args,
                    prompt=prompt,
                    workspace=workspace,
                    stdout=stdout,
                    stderr=stderr,
                )
            except KeyboardInterrupt:
                stderr.write('{"error":"cancelled","message":"Run cancelled by user."}\n')
                stderr.flush()
                return EXIT_CANCELLED
            except CliAttachError as exc:
                stderr.write(f"metis: attach failed: {exc}\n")
                stderr.flush()
                if args.debug:
                    traceback.print_exc(file=stderr)
                return EXIT_ENVIRONMENT
            return result.exit_code
        session_store = CliSessionStore()
        resume = session_store.resolve_resume(args.resume_id, latest=args.continue_session) if (args.resume_id or args.continue_session) else None
        workspace = _workspace_path(args.workspace or (resume.workspace if resume is not None else "."))
        session_id, messages = session_store.begin_run(prompt=prompt, workspace=workspace, resume=resume)
        permission_checker = build_permission_checker(workspace, args.policy)
    except SystemExit as exc:
        return 0 if int(exc.code or 0) == 0 else EXIT_USAGE
    except (CliUsageError, CliConfigError, CliPolicyError, CliSessionError) as exc:
        stderr.write(f"metis: {exc}\n")
        stderr.flush()
        return EXIT_USAGE
    except Exception as exc:
        stderr.write(f"metis: startup failed: {type(exc).__name__}: {exc}\n")
        stderr.flush()
        return EXIT_ENVIRONMENT

    # Keep stdout machine-readable even when imported libraries or tools emit
    # incidental diagnostics. Renderers retain the original stdout handle.
    try:
        with contextlib.redirect_stdout(stderr), _working_directory(workspace):
            config, registry = build_cli_runtime(args, workspace=workspace, session_id=session_id)
            config.permission_checker = permission_checker
            from backend.bridges.event_serializer import agent_event_payload
            from backend.runtime.agent_loop import run

            renderer = HeadlessRenderer(
                output_format=args.output_format,
                stdout=stdout,
                stderr=stderr,
                serializer=agent_event_payload,
            )
            events = run(messages, config, registry=registry)
            result = drive_headless(events, renderer=renderer, session_id=session_id)
    except KeyboardInterrupt:
        stderr.write('{"error":"cancelled","message":"Run cancelled by user."}\n')
        stderr.flush()
        return EXIT_CANCELLED
    except Exception as exc:
        try:
            renderer
        except UnboundLocalError:
            renderer = None
        if renderer is not None:
            renderer.runtime_exception(exc)
        else:
            stderr.write(f"metis: {type(exc).__name__}: {exc}\n")
            stderr.flush()
        if args.debug:
            traceback.print_exc(file=stderr)
        return EXIT_ENVIRONMENT
    session_store.finish_run(
        session_id,
        transcript_records=renderer.transcript_records,
        final_text=result.final_text,
    )
    return result.exit_code


def _workspace_path(value: str) -> Path:
    path = Path(value or ".").expanduser().resolve(strict=False)
    if not path.exists():
        raise CliUsageError(f"workspace does not exist: {path}")
    if not path.is_dir():
        raise CliUsageError(f"workspace is not a directory: {path}")
    return path


def _prompt_text(args: ParsedCliArgs, stdin: TextIO) -> str:
    prompt = str(args.prompt or "").strip()
    if not prompt and (args.print_mode or not _isatty(stdin)):
        prompt = stdin.read().strip()
    if not prompt:
        raise CliUsageError("a prompt is required; pass a prompt argument or pipe stdin with -p")
    return prompt


def _isatty(stream: TextIO) -> bool:
    try:
        return bool(stream.isatty())
    except (AttributeError, OSError):
        return False


def _should_run_tui(args: ParsedCliArgs, *, stdin: TextIO, stdout: TextIO) -> bool:
    return (
        not args.print_mode
        and args.output_format == "text"
        and _isatty(stdin)
        and _isatty(stdout)
    )


def _configure_stream(stream: TextIO) -> None:
    reconfigure = getattr(stream, "reconfigure", None)
    if callable(reconfigure):
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (OSError, ValueError):
            pass


@contextlib.contextmanager
def _working_directory(workspace: Path):
    previous = Path.cwd()
    os.chdir(workspace)
    try:
        yield
    finally:
        os.chdir(previous)

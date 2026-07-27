from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from typing import Sequence, Union

from backend.version import __version__

OUTPUT_FORMATS = ("text", "json", "stream-json")
PERMISSION_MODES = ("ask", "edit", "plan", "auto_guard", "bypass", "auto", "read_only")


class CliUsageError(ValueError):
    """Raised instead of argparse's default exit code 2."""


class MetisArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise CliUsageError(message)


@dataclass(frozen=True)
class ParsedCliArgs:
    prompt: str
    print_mode: bool
    output_format: str
    workspace: str
    permission_mode: str
    allowed_tools: str
    policy: str
    backend: str
    base_url: str
    model: str
    max_turns: int | None
    no_desktop: bool
    no_mcp: bool
    debug: bool
    resume_id: str
    continue_session: bool


@dataclass(frozen=True)
class SessionCommandArgs:
    command: str
    action: str
    session_id: str
    output_format: str
    export_format: str
    output: str
    limit: int
    archived: bool


def build_parser() -> MetisArgumentParser:
    parser = MetisArgumentParser(
        prog="metis",
        description="Metis headless agent CLI",
        epilog=(
            "Session commands:\n"
            "  metis resume ID [PROMPT]       Continue a session (alias for --resume).\n"
            "  metis sessions list            List durable desktop and CLI sessions.\n"
            "  metis sessions show ID         Show a transcript.\n"
            "  metis sessions export ID       Export JSON or Markdown."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("prompt", nargs="?", default="", help="Task prompt. With -p it may also come from stdin.")
    parser.add_argument("-p", "--print", dest="print_mode", action="store_true", help="Run non-interactively and exit.")
    parser.add_argument(
        "--output-format",
        choices=OUTPUT_FORMATS,
        default="text",
        help="Output text, one final JSON object, or one event JSON object per line.",
    )
    parser.add_argument("--workspace", default="", help="Workspace root (default: current directory, or the stored workspace when resuming).")
    parser.add_argument(
        "--permission-mode",
        choices=PERMISSION_MODES,
        default="",
        help="Permission preset. CI defaults to ask; local runs default to auto_guard.",
    )
    parser.add_argument("--allowed-tools", default="", help="Comma-separated tool allowlist that can only reduce access.")
    parser.add_argument("--policy", default="", help="Additional permission policy JSON file.")
    parser.add_argument("--backend", default="", help="Override the configured model provider.")
    parser.add_argument("--base-url", default="", help="Override the configured model API base URL.")
    parser.add_argument("--model", default="", help="Override the configured model name.")
    parser.add_argument("--max-turns", type=_positive_int, default=None, help="Stop after N agent turns (exit 4).")
    parser.add_argument("--no-desktop", action="store_true", help="Do not register desktop-control tools.")
    parser.add_argument("--no-mcp", action="store_true", help="Do not load MCP servers.")
    resume_group = parser.add_mutually_exclusive_group()
    resume_group.add_argument("--resume", dest="resume_id", default="", metavar="ID", help="Continue an existing session by ID or unique prefix.")
    resume_group.add_argument("--continue", dest="continue_session", action="store_true", help="Continue the most recently updated session.")
    parser.add_argument("--debug", action="store_true", help="Print exception tracebacks to stderr.")
    parser.add_argument("--version", action="version", version=f"Metis {__version__}")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> Union[ParsedCliArgs, SessionCommandArgs]:
    raw = list(argv) if argv is not None else sys.argv[1:]
    if raw and raw[0] == "sessions":
        return _parse_session_command(raw[1:])
    if raw and raw[0] == "resume":
        if len(raw) >= 2 and raw[1] in {"-h", "--help"}:
            raw = ["--help"]
            return build_parser().parse_args(raw)
        if len(raw) < 2 or raw[1].startswith("-"):
            raise CliUsageError("resume requires a session ID; use --continue for the latest session")
        raw = ["--resume", raw[1], *raw[2:]]
    values = build_parser().parse_args(raw)
    return ParsedCliArgs(
        prompt=str(values.prompt or ""),
        print_mode=bool(values.print_mode),
        output_format=str(values.output_format),
        workspace=str(values.workspace or ""),
        permission_mode=str(values.permission_mode or ""),
        allowed_tools=str(values.allowed_tools or ""),
        policy=str(values.policy or ""),
        backend=str(values.backend or ""),
        base_url=str(values.base_url or ""),
        model=str(values.model or ""),
        max_turns=values.max_turns,
        no_desktop=bool(values.no_desktop),
        no_mcp=bool(values.no_mcp),
        debug=bool(values.debug),
        resume_id=str(values.resume_id or ""),
        continue_session=bool(values.continue_session),
    )


def _parse_session_command(argv: Sequence[str]) -> SessionCommandArgs:
    parser = MetisArgumentParser(prog="metis sessions", description="Inspect and export durable Metis sessions")
    subparsers = parser.add_subparsers(dest="action", required=True)

    list_parser = subparsers.add_parser("list", help="List sessions, newest first")
    list_parser.add_argument("--limit", type=_positive_int, default=20)
    list_parser.add_argument("--archived", action="store_true")
    list_parser.add_argument("--output-format", choices=("text", "json"), default="text")

    show_parser = subparsers.add_parser("show", help="Show one session and its transcript")
    show_parser.add_argument("session_id")
    show_parser.add_argument("--output-format", choices=("text", "json"), default="text")

    export_parser = subparsers.add_parser("export", help="Export one portable session transcript")
    export_parser.add_argument("session_id")
    export_parser.add_argument("--format", dest="export_format", choices=("json", "markdown"), default="json")
    export_parser.add_argument("--output", default="", help="Destination file; stdout when omitted.")

    values = parser.parse_args(list(argv))
    return SessionCommandArgs(
        command="sessions",
        action=str(values.action),
        session_id=str(getattr(values, "session_id", "") or ""),
        output_format=str(getattr(values, "output_format", "text") or "text"),
        export_format=str(getattr(values, "export_format", "json") or "json"),
        output=str(getattr(values, "output", "") or ""),
        limit=int(getattr(values, "limit", 20) or 20),
        archived=bool(getattr(values, "archived", False)),
    )


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed

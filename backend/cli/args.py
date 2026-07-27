from __future__ import annotations

import argparse
from dataclasses import dataclass
from typing import Sequence

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


def build_parser() -> MetisArgumentParser:
    parser = MetisArgumentParser(
        prog="metis",
        description="Metis headless agent CLI",
    )
    parser.add_argument("prompt", nargs="?", default="", help="Task prompt. With -p it may also come from stdin.")
    parser.add_argument("-p", "--print", dest="print_mode", action="store_true", help="Run non-interactively and exit.")
    parser.add_argument(
        "--output-format",
        choices=OUTPUT_FORMATS,
        default="text",
        help="Output text, one final JSON object, or one event JSON object per line.",
    )
    parser.add_argument("--workspace", default=".", help="Workspace root (default: current directory).")
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
    parser.add_argument("--debug", action="store_true", help="Print exception tracebacks to stderr.")
    parser.add_argument("--version", action="version", version=f"Metis {__version__}")
    return parser


def parse_args(argv: Sequence[str] | None = None) -> ParsedCliArgs:
    values = build_parser().parse_args(argv)
    return ParsedCliArgs(
        prompt=str(values.prompt or ""),
        print_mode=bool(values.print_mode),
        output_format=str(values.output_format),
        workspace=str(values.workspace or "."),
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
    )


def _positive_int(value: str) -> int:
    try:
        parsed = int(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if parsed <= 0:
        raise argparse.ArgumentTypeError("must be greater than zero")
    return parsed

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Any, List

from .agent_loop import (
    AgentConfig,
    ContentEvent,
    DoneEvent,
    ErrorEvent,
    ToolCallEvent,
    ToolResultEvent,
    run,
    run_sync,
)
from .tool_registry import get_registry


def _env(new_key: str, old_key: str, default: str = "") -> str:
    return os.environ.get(new_key) or os.environ.get(old_key) or default


def main(argv: List[str] | None = None) -> int:
    _load_dotenv()
    parser = argparse.ArgumentParser(description="Metis AI Agent")
    parser.add_argument(
        "--mode",
        choices=["desktop", "web", "cli", "once"],
        default="desktop",
        help="Run mode: desktop, web, cli, or once.",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Port for web or desktop mode.",
    )
    parser.add_argument("--prompt", default="", help="Prompt for once mode.")
    parser.add_argument(
        "--backend",
        default=_env("METIS_LLM_BACKEND", "MIRO_LLM_BACKEND", "openai"),
        help="LLM backend: openai, anthropic, or gemini.",
    )
    parser.add_argument(
        "--model",
        default=_env("METIS_LLM_MODEL", "MIRO_LLM_MODEL", ""),
        help="Model name.",
    )
    parser.add_argument("--mcp-config", default="", help="Optional MCP config path.")
    parser.add_argument("--no-desktop", action="store_true", help="Disable desktop tools.")
    parser.add_argument("--no-mcp", action="store_true", help="Disable MCP loading.")
    args = parser.parse_args(argv)

    if args.no_desktop:
        os.environ["METIS_DISABLE_DESKTOP_TOOLS"] = "1"
    if args.no_mcp:
        os.environ["METIS_DISABLE_MCP"] = "1"

    if args.mode == "desktop":
        _run_desktop(args)
    elif args.mode == "web":
        _run_web(args)
    elif args.mode == "once":
        _run_once(args)
    else:
        _run_cli(args)
    return 0


def _load_dotenv() -> None:
    paths = [
        Path.cwd() / ".env",
        Path(__file__).resolve().parents[1] / ".env",
    ]
    seen: set[Path] = set()
    for path in paths:
        path = path.resolve()
        if path in seen or not path.exists():
            continue
        seen.add(path)
        for raw_line in path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("export "):
                line = line[7:].strip()
            if "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or key.startswith("#"):
                continue
            if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
                value = value[1:-1]
            os.environ.setdefault(key, value)


def _run_desktop(args: argparse.Namespace) -> None:
    from backend.runtime.desktop import launch

    launch(port=args.port, debug=False)


def _run_web(args: argparse.Namespace) -> None:
    from backend.web.app import app

    port = args.port or int(_env("METIS_PORT", "MIRO_PORT", "5000"))
    os.environ["METIS_PORT"] = str(port)
    print(f"Metis Agent web mode: http://127.0.0.1:{port}")
    app.run(host="127.0.0.1", port=port, threaded=True, debug=False)


def _run_cli(args: argparse.Namespace) -> None:
    config = _build_config(args)
    registry = get_registry(
        mcp_config_path=args.mcp_config,
        include_desktop=not args.no_desktop,
        include_mcp=not args.no_mcp,
    )

    print("Metis Agent v2.0 - CLI mode")
    print(f"  Backend: {config.llm_backend} / {config.llm_model}")
    print(f"  Tools: {registry.tool_count} available")
    print("  Type 'exit' to quit, 'reset' to clear history")
    print()

    history: List[dict[str, Any]] = []
    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nBye.")
            return

        if not user_input:
            continue
        if user_input.lower() in {"exit", "quit", "q"}:
            print("Bye.")
            return
        if user_input.lower() == "reset":
            history.clear()
            print("History cleared.\n")
            continue

        history.append({"role": "user", "content": user_input})
        final_text = _run_turn(history, config)
        if final_text:
            history.append({"role": "assistant", "content": final_text})
        print()


def _run_turn(history: List[dict[str, Any]], config: AgentConfig) -> str:
    print("Metis: ", end="", flush=True)
    final_text = ""
    for event in run(list(history), config):
        if isinstance(event, ToolCallEvent):
            print(f"\n  [calling {event.tool_name}]", flush=True)
        elif isinstance(event, ToolResultEvent):
            result = event.result[:200]
            suffix = "..." if len(event.result) > 200 else ""
            print(f"  [result: {result}{suffix}]", flush=True)
        elif isinstance(event, ContentEvent):
            final_text = event.text
            print(event.text, flush=True)
        elif isinstance(event, ErrorEvent):
            label = event.code or "ERROR"
            title = f"{event.title}: " if event.title else ""
            print(f"\n  [error {label}: {title}{event.message}]", flush=True)
            if event.hint:
                print(f"  [fix: {event.hint}]", flush=True)
        elif isinstance(event, DoneEvent) and not final_text:
            print("(no response)", flush=True)
    return final_text


def _run_once(args: argparse.Namespace) -> None:
    if not args.prompt:
        print("Error: --prompt is required for --mode once", file=sys.stderr)
        raise SystemExit(1)

    config = _build_config(args)
    get_registry(
        mcp_config_path=args.mcp_config,
        include_desktop=not args.no_desktop,
        include_mcp=not args.no_mcp,
    )
    print(run_sync(args.prompt, config))


def _build_config(args: argparse.Namespace) -> AgentConfig:
    return AgentConfig(
        llm_backend=args.backend,
        llm_base_url=_env("METIS_LLM_BASE_URL", "MIRO_LLM_BASE_URL", "https://api.deepseek.com"),
        llm_api_key=_env("METIS_LLM_API_KEY", "MIRO_LLM_API_KEY", ""),
        llm_model=args.model or _env("METIS_LLM_MODEL", "MIRO_LLM_MODEL", "deepseek-v4-flash"),
        temperature=float(_env("METIS_TEMPERATURE", "MIRO_TEMPERATURE", "0.3")),
        max_tokens=int(_env("METIS_MAX_TOKENS", "MIRO_MAX_TOKENS", "4096")),
        max_turns=int(_env("METIS_MAX_TURNS", "MIRO_MAX_TURNS", "64")),
        timeout=float(_env("METIS_LLM_TIMEOUT", "MIRO_LLM_TIMEOUT", "120")),
        system_prompt=_load_system_prompt(),
    )


def _load_system_prompt() -> str:
    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    prompt_path = os.path.join(backend_root, "core", "prompts", "MAIN_PROMPT.txt")
    if os.path.exists(prompt_path):
        with open(prompt_path, "r", encoding="utf-8") as handle:
            return handle.read()
    return "You are Metis, an AI coding and desktop automation assistant."


if __name__ == "__main__":
    raise SystemExit(main())

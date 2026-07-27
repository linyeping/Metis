from __future__ import annotations

import argparse
from pathlib import Path
from typing import cast

from metis import SDK_API_VERSION, Agent, AgentEvent, AgentResult


def permission_policy(event: AgentEvent) -> bool:
    """Example policy: permit read-only tool requests and deny mutations."""
    return event.tool in {"read_file", "search_files", "list_directory", "glob_search"}


def run(prompt: str, workspace: Path) -> AgentResult:
    agent = Agent(permission_mode="ask")
    stream = agent.run(prompt, workspace=workspace, permission_handler=permission_policy)
    while True:
        try:
            event = next(stream)
        except StopIteration as stopped:
            return cast(AgentResult, stopped.value)
        if event.kind in {"content_delta", "content"} and event.text:
            print(event.text, end="", flush=True)
        elif event.kind == "tool_call":
            print(f"\n[tool] {event.tool}")


def main() -> None:
    parser = argparse.ArgumentParser(description=f"Metis Agent SDK API {SDK_API_VERSION} streaming example")
    parser.add_argument("prompt")
    parser.add_argument("--workspace", type=Path, default=Path.cwd())
    args = parser.parse_args()
    result = run(args.prompt, args.workspace)
    print(f"\n[session] {result.session_id}")
    if not result.ok:
        raise SystemExit(result.exit_code)


if __name__ == "__main__":
    main()

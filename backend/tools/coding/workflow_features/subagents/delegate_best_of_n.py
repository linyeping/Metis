"""Best-of-N sub-agent using independent lightweight attempts."""
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


ATTEMPT_SYSTEM_PROMPT = """You are a coding sub-agent.
Complete the task independently. Read existing code before modifying it.
Prefer small correct changes and report what you changed or found."""

ATTEMPT_TOOLS = [
    "read_file",
    "write_file",
    "robust_replace_in_file",
    "glob_search",
    "grep_search",
    "execute_bash_command",
    "list_directory",
]


@trace_execution
def delegate_best_of_n(task: str, n: int = 3) -> str:
    from backend.runtime.agent_loop import _create_backend
    from backend.runtime.mini_agent import MiniAgentConfig, run_mini_agent
    from backend.runtime.tool_registry import get_registry
    from backend.web.app import _load_config_for_workspace

    attempts = max(1, min(int(n or 1), 3))
    config = _load_config_for_workspace(".")
    registry = get_registry()
    results = []
    for index in range(attempts):
        backend = _create_backend(config)
        results.append(
            run_mini_agent(
                task=f"Attempt {index + 1}/{attempts}:\n{task}",
                config=MiniAgentConfig(
                    system_prompt=ATTEMPT_SYSTEM_PROMPT,
                    max_turns=10,
                    tool_names=ATTEMPT_TOOLS,
                    temperature=0.3 + (index * 0.15),
                    workspace_root=".",
                ),
                registry=registry,
                backend=backend,
            )
        )

    parts = []
    for index, result in enumerate(results):
        status = "OK" if result.ok else "FAILED"
        body = result.output if result.ok else result.error
        parts.append(
            f"### Attempt {index + 1} {status}\n"
            f"Turns: {result.turns_used}, Tool calls: {result.tool_calls_made}\n\n"
            f"{body[:2000]}"
        )
    return "\n\n---\n\n".join(parts)

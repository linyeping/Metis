"""Best-of-N sub-agent using isolated git worktree attempts."""
import json

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
def delegate_best_of_n(task: str, n: int = 3, workspace_root: str = ".") -> str:
    from backend.runtime.subagent_worktree import create_subagent_worktree, worktree_plans_payload

    attempts = max(1, min(int(n or 1), 3))
    plans = [
        create_subagent_worktree(
            workspace_root=workspace_root,
            label="best-of-n",
            attempt_index=index + 1,
        )
        for index in range(attempts)
    ]
    if not all(plan.ok for plan in plans):
        payload = worktree_plans_payload(plans)
        return (
            "Error: Subagent Worktree Isolation v2 refused to run write-capable attempts.\n\n"
            "delegate_best_of_n requires a real isolated git worktree for every attempt. "
            "No attempt was started in the main working tree.\n\n"
            "Isolation payload:\n"
            f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```"
        )

    from backend.runtime.agent_loop import _create_backend
    from backend.runtime.mini_agent import MiniAgentConfig, run_mini_agent
    from backend.runtime.tool_registry import get_registry
    from backend.web.app import _load_config_for_workspace
    from backend.tools.coding.foundation.core_mechanisms.execution_boundary_context import (
        workspace_root_override,
    )

    registry = get_registry()
    results = []
    for index, plan in enumerate(plans):
        attempt_workspace = plan.worktree_path
        config = _load_config_for_workspace(attempt_workspace)
        backend = _create_backend(config)
        with workspace_root_override(attempt_workspace):
            results.append(
                run_mini_agent(
                    task=(
                        f"Attempt {index + 1}/{attempts}\n"
                        f"Isolated workspace: {attempt_workspace}\n"
                        f"Branch: {plan.branch}\n\n"
                        f"{task}"
                    ),
                    config=MiniAgentConfig(
                        system_prompt=ATTEMPT_SYSTEM_PROMPT,
                        max_turns=10,
                        tool_names=ATTEMPT_TOOLS,
                        temperature=0.3 + (index * 0.15),
                        workspace_root=attempt_workspace,
                    ),
                    registry=registry,
                    backend=backend,
                )
            )

    payload = worktree_plans_payload(plans)
    dirty_note = (
        "\n\nNote: the source workspace had uncommitted changes when the worktrees were created. "
        "Attempts are based on HEAD; uncommitted main-tree changes are not mirrored automatically."
        if any(plan.dirty for plan in plans)
        else ""
    )
    parts = [
        "### Subagent Worktree Isolation v2",
        f"```json\n{json.dumps(payload, ensure_ascii=False, indent=2)}\n```{dirty_note}",
    ]
    for index, (plan, result) in enumerate(zip(plans, results)):
        status = "OK" if result.ok else "FAILED"
        body = result.output if result.ok else result.error
        parts.append(
            f"### Attempt {index + 1} {status}\n"
            f"Worktree: `{plan.worktree_path}`\n"
            f"Branch: `{plan.branch}`\n"
            f"Turns: {result.turns_used}, Tool calls: {result.tool_calls_made}\n\n"
            f"{body[:2000]}"
        )
    return "\n\n---\n\n".join(parts)

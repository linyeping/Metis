"""Real explore sub-agent with an independent lightweight loop."""
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def delegate_explore(goal: str, root: str = ".", max_depth: int = 3) -> str:
    from backend.runtime.agent_loop import _create_backend
    from backend.runtime.mini_agent import MiniAgentConfig, run_mini_agent
    from backend.runtime.subagent_prompts import (
        EXPLORE_SYSTEM_PROMPT,
        EXPLORE_TOOLS,
        compress_subagent_result,
    )
    from backend.runtime.tool_registry import get_registry
    from backend.web.app import _load_config_for_workspace

    config = _load_config_for_workspace(root)
    backend = _create_backend(config)
    registry = get_registry()
    result = run_mini_agent(
        task=f"Explore '{root}' up to depth {max_depth} for:\n{goal}",
        config=MiniAgentConfig(
            system_prompt=EXPLORE_SYSTEM_PROMPT,
            max_turns=8,
            tool_names=EXPLORE_TOOLS,
            workspace_root=root,
        ),
        registry=registry,
        backend=backend,
    )
    return compress_subagent_result(str(result))

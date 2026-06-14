"""Real shell sub-agent with an independent lightweight loop."""
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def delegate_shell(script_description: str, cwd: str = ".") -> str:
    from backend.runtime.agent_loop import _create_backend
    from backend.runtime.mini_agent import MiniAgentConfig, run_mini_agent
    from backend.runtime.subagent_prompts import (
        SHELL_SYSTEM_PROMPT,
        SHELL_TOOLS,
        compress_subagent_result,
    )
    from backend.runtime.tool_registry import get_registry
    from backend.web.app import _load_config_for_workspace

    config = _load_config_for_workspace(cwd)
    backend = _create_backend(config)
    registry = get_registry()
    result = run_mini_agent(
        task=f"Working directory: {cwd}\n\nTask:\n{script_description}",
        config=MiniAgentConfig(
            system_prompt=SHELL_SYSTEM_PROMPT,
            max_turns=6,
            tool_names=SHELL_TOOLS,
            workspace_root=cwd,
        ),
        registry=registry,
        backend=backend,
    )
    return compress_subagent_result(str(result))

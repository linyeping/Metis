"""Custom agent creator placeholder with honest availability messaging."""
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def custom_agent_creator(name: str, system_prompt: str, tools_allow: str = "*") -> str:
    del system_prompt, tools_allow
    return (
        "Custom agent creation is not yet available.\n\n"
        "The explore, shell, and best_of_n sub-agents are available now.\n"
        f"Original request: create agent '{name}'."
    )

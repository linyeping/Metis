"""兼容入口：拼装发往 LLM 的 messages。"""
from typing import Any, Dict, List, Optional

from .prompt_runtime import build_runtime_messages


def build_llm_message_list(
    system_prompt: str,
    history_turns: List[Dict[str, Any]],
    *,
    workspace_root: Optional[str] = None,
    include_workspace_hint: Optional[bool] = None,
    include_agent_state_hint: Optional[bool] = None,
    include_open_files_hint: Optional[bool] = None,
    include_terminal_hint: Optional[bool] = None,
    include_mode_router_hint: Optional[bool] = None,
    include_workflow_hint: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """兼容旧接口，内部委托给显式 prompt runtime compiler。"""
    return build_runtime_messages(
        system_prompt,
        history_turns,
        workspace_root=workspace_root,
        include_workspace_hint=include_workspace_hint,
        include_agent_state_hint=include_agent_state_hint,
        include_open_files_hint=include_open_files_hint,
        include_terminal_hint=include_terminal_hint,
        include_mode_router_hint=include_mode_router_hint,
        include_workflow_hint=include_workflow_hint,
    )

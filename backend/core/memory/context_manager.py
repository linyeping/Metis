"""多轮对话上下文与会话级 system prompt（海马体 — 组合 chat_history + context_builder）。"""
from typing import Any, Dict, List

from backend.core.engine.constants import CURRENT_WORKSPACE, MAX_CONTEXT_TOKENS
from backend.core.engine.context_builder import build_llm_message_list
from backend.core.engine.prompt_runtime import compile_prompt_runtime

from .chat_history import ChatHistory
from .prompt_loader import load_kiro_soul


class ContextManager:
    """智能上下文管理：主 system prompt + ChatHistory + 工作区提示拼装。"""

    def __init__(self, max_tokens: int = MAX_CONTEXT_TOKENS) -> None:
        self.chat_history = ChatHistory(max_tokens=max_tokens)
        self.base_system_prompt = load_kiro_soul()

    @property
    def conversation_history(self) -> List[Dict[str, Any]]:
        """供 /status 等读取的纯会话轮次（不含拼装后的双 system 块）。"""
        return self.chat_history.snapshot()

    def add_message(self, message: Dict[str, Any]) -> None:
        self.chat_history.add_message(message)

    def get_prompt_runtime_snapshot(self):
        """返回当前会话的 prompt runtime 编译结果，供调试与验收使用。"""
        return compile_prompt_runtime(
            self.base_system_prompt,
            workspace_root=CURRENT_WORKSPACE,
        )

    def get_messages(self) -> List[Dict[str, Any]]:
        return build_llm_message_list(
            self.base_system_prompt,
            self.chat_history.snapshot(),
            workspace_root=CURRENT_WORKSPACE,
        )

    def reset(self) -> None:
        self.chat_history.reset()


context_manager = ContextManager()

"""多轮对话条目的存储与压缩（蓝图 chat_history；与 ContextManager 组合使用）。"""
import json
import threading
from typing import Any, Dict, List

from backend.tools.coding.foundation.core_mechanisms.colored_logger import colored_log

from backend.core.engine.constants import MAX_CONTEXT_TOKENS


class ChatHistory:
    """会话内 user/assistant/tool 等消息（不含主 system prompt）。"""

    def __init__(self, max_tokens: int = MAX_CONTEXT_TOKENS) -> None:
        self.max_tokens = max_tokens
        self._turns: List[Dict[str, Any]] = []
        self._lock = threading.Lock()

    def estimate_tokens(self, text: str) -> int:
        return len(text) // 4

    def add_message(self, message: Dict[str, Any]) -> None:
        with self._lock:
            self._turns.append(message)
            if self.estimate_tokens(json.dumps(self._turns)) > self.max_tokens * 0.8:
                self._compress_unlocked()

    def _compress_unlocked(self) -> None:
        if len(self._turns) <= 10:
            return
        colored_log.info(f"🗜️ 压缩历史 ({len(self._turns)} 条)")
        recent = self._turns[-10:]
        summary = {
            "role": "system",
            "content": f"[历史摘要: 省略 {len(self._turns) - 10} 条消息]",
        }
        self._turns = [summary] + recent

    def snapshot(self) -> List[Dict[str, Any]]:
        with self._lock:
            return list(self._turns)

    def reset(self) -> None:
        with self._lock:
            self._turns.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._turns)

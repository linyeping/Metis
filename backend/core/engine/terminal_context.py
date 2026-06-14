"""C 风格终端上下文：聚合 .cursor/**/terminals/*.txt，注入 system（复用 read_terminal_state）。"""
import os
from typing import Optional


def _terminal_hint_enabled() -> bool:
    v = os.environ.get("MIRO_CONTEXT_TERMINAL_HINT", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def terminal_context_block(workspace_root: Optional[str]) -> str:
    """
    返回可追加到 system 的终端快照块；无可用快照或关闭开关时返回空串。
    上限由环境变量约束，避免撑爆上下文。
    """
    if not _terminal_hint_enabled():
        return ""

    max_files = max(1, int(os.environ.get("MIRO_CONTEXT_TERMINAL_MAX_FILES", "4")))
    tail = max(500, int(os.environ.get("MIRO_CONTEXT_TERMINAL_TAIL", "2500")))
    total_cap = max(1000, int(os.environ.get("MIRO_CONTEXT_TERMINAL_MAX_CHARS", "8000")))

    from backend.tools.coding.read_search.read_analyze.read_terminal_state import read_terminal_state

    terminals_base = None
    if workspace_root:
        candidate = os.path.join(workspace_root, ".cursor", "terminals")
        if os.path.isdir(candidate):
            terminals_base = candidate

    # 走 __wrapped__：拼 system 上下文 ≠ Agent 主动调工具，避免每条请求刷 TOOL_CALL 日志
    _impl = getattr(read_terminal_state, "__wrapped__", read_terminal_state)
    raw = _impl(
        terminals_base=terminals_base,
        max_terminal_files=max_files,
        tail_chars=tail,
    )

    if "未发现终端快照" in raw or raw.startswith("📟"):
        return ""

    if len(raw) > total_cap:
        raw = raw[:total_cap] + "\n... [terminal context truncated]\n"

    return "\n---\n[Miro terminal snapshots]\n" + raw + "\n"

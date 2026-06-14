"""读取 Cursor / IDE 终端快照目录下的 .txt 状态（c 资料：terminals/$id.txt）。"""
import os
from pathlib import Path
from typing import List, Optional, Set

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


def _discover_terminal_files(terminals_base: Optional[str], max_files: int) -> List[Path]:
    candidates: List[Path] = []
    seen: Set[Path] = set()
    roots: List[Path] = []

    if terminals_base:
        roots.append(Path(terminals_base).expanduser().resolve())
    else:
        roots.append(Path(".cursor") / "terminals")
        roots.append(Path(".cursor") / "projects")
        env_t = os.environ.get("CURSOR_TERMINALS_DIR")
        if env_t:
            roots.append(Path(env_t).expanduser().resolve())

    for root in roots:
        if not root.exists():
            continue
        if root.is_file():
            if root.suffix.lower() == ".txt" and root not in seen:
                seen.add(root)
                candidates.append(root)
            if len(candidates) >= max_files:
                return candidates
            continue

        if root.is_dir() and root.name == "terminals":
            for p in sorted(root.glob("*.txt")):
                if p.is_file() and p not in seen:
                    seen.add(p)
                    candidates.append(p)
                    if len(candidates) >= max_files:
                        return candidates
            continue

        if root.is_dir() and root.name == "projects":
            for term_dir in root.glob("*/terminals"):
                if not term_dir.is_dir():
                    continue
                for p in sorted(term_dir.glob("*.txt")):
                    if p.is_file() and p not in seen:
                        seen.add(p)
                        candidates.append(p)
                        if len(candidates) >= max_files:
                            return candidates
            continue

        if root.is_dir():
            for p in sorted(root.glob("*.txt")):
                if p.is_file() and p not in seen:
                    seen.add(p)
                    candidates.append(p)
                    if len(candidates) >= max_files:
                        return candidates

    return candidates[:max_files]


@trace_execution
def read_terminal_state(
    terminals_base: Optional[str] = None,
    max_terminal_files: int = 15,
    tail_chars: int = 12000,
) -> str:
    """
    聚合读取终端状态快照文本（通常来自 .cursor/**/terminals/*.txt）。

    Args:
        terminals_base: 显式指定 terminals 目录；为 None 时自动探测 .cursor/terminals 与 .cursor/projects/*/terminals。
        max_terminal_files: 最多读取多少个终端文件。
        tail_chars: 每个文件只保留末尾若干字符，防止撑爆上下文。
    """
    try:
        files = _discover_terminal_files(terminals_base, max_terminal_files)
        if not files:
            return (
                "📟 未发现终端快照文件。可设置环境变量 CURSOR_TERMINALS_DIR，"
                "或传入 terminals_base 指向 .../terminals 目录。"
            )

        parts: List[str] = [f"=== 终端快照 ({len(files)} 个文件) ===\n"]
        for p in files:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
                if len(text) > tail_chars:
                    text = f"[仅显示末尾 {tail_chars} 字符]\n" + text[-tail_chars:]
                parts.append(f"--- {p} ---\n{text}\n")
            except Exception as ex:
                parts.append(f"--- {p} ---\n❌ 读取失败: {ex}\n")

        return "\n".join(parts).strip()
    except Exception as e:
        return f"❌ 读取终端状态失败: {str(e)}"

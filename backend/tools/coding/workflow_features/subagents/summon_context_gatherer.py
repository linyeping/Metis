"""召唤上下文：仓库地图 + 可选文件列表。"""
from typing import List, Optional

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def summon_context_gatherer(
    workspace: str = ".",
    extra_paths: Optional[List[str]] = None,
    max_depth: int = 2,
) -> str:
    from backend.tools.coding.read_search.read_analyze.generate_repo_map import generate_repo_map
    from backend.tools.coding.read_search.read_single.read_file import read_file

    parts = [generate_repo_map(workspace, max_depth=max_depth)]
    for p in extra_paths or []:
        parts.append(read_file(p, skipPruning=True))
    return "\n\n".join(parts)

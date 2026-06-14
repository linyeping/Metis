"""并发读取多个文件（c 资料：多工具并行 / 效率）。"""
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Optional

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution
from backend.tools.coding.read_search.read_single.read_file import read_file


@trace_execution
def read_multiple_files(
    file_paths: List[str],
    skipPruning: bool = False,
    explanation: str = "",
    max_workers: int = 8,
) -> str:
    """
    线程池并发调用 read_file，保持 file_paths 顺序输出摘要。

    Args:
        file_paths: 路径列表。
        skipPruning: 传给每个 read_file。
        explanation: 剪枝上下文。
        max_workers: 最大并发数（上限与文件数取 min）。
    """
    if not file_paths:
        return "⚠️ file_paths 为空"

    workers = min(max(1, max_workers), len(file_paths))
    order = {p: i for i, p in enumerate(file_paths)}
    results: List[tuple] = []

    with ThreadPoolExecutor(max_workers=workers) as pool:
        future_map = {
            pool.submit(read_file, p, None, None, skipPruning, explanation): p
            for p in file_paths
        }
        for fut in as_completed(future_map):
            path = future_map[fut]
            try:
                results.append((path, fut.result()))
            except Exception as e:
                results.append((path, f"❌ 读取异常: {str(e)}"))

    results.sort(key=lambda x: order[x[0]])
    blocks = [f"########## {p}\n{text}" for p, text in results]
    return "\n\n".join(blocks)

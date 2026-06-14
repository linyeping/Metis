# -*- coding: utf-8 -*-
"""P6：按 DAG 拓扑执行多 Task（run_task_graph 工具）。"""
from __future__ import annotations

from typing import Any, Dict, List

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution

from .task_graph_runner import run_task_graph_execution


@trace_execution
def run_task_graph(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, str]],
    workspace_root: str = ".",
    timeout_sec: int = 180,
    max_workers: int = 0,
    resume: str = "",
) -> str:
    """
    按有向无环图执行子任务。edges 中 from -> to 表示 from 先于 to。

    resume 非空：必须存在 `<workspace_root>/.delegate_sessions/<resume>.json`（图级 checkpoint）。
    """
    return run_task_graph_execution(
        nodes=nodes,
        edges=edges,
        workspace_root=workspace_root,
        timeout_sec=timeout_sec,
        max_workers=max_workers,
        resume=resume or "",
    )

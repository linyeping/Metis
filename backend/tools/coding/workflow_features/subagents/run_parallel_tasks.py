# -*- coding: utf-8 -*-
"""
并行任务执行工具（块5 增强）：一次性执行多个独立的 Task。

与 task_dispatch 的区别：
- task_dispatch: 单个任务，串行执行
- run_parallel_tasks: 多个任务，并行执行

适用场景：
- 多个独立的探索任务
- 多个不同目录的分析
- 多个独立的测试/验证
"""
from __future__ import annotations

import os
from typing import Any, Dict, List

from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution

from .delegate_workspace import resolve_delegate_workspace_for_task
from .task_parallel_runner import run_tasks_parallel, format_parallel_results


@trace_execution
def run_parallel_tasks(
    tasks: List[Dict[str, Any]],
    workspace_root: str = ".",
    timeout_sec: int = 180,
    max_workers: int = 0,
) -> str:
    """
    并行执行多个 Task。
    
    Args:
        tasks: 任务列表，每个任务包含：
            - prompt (required): 任务描述
            - subagent_type (optional): 子代理类型，默认 "explore"
            - resume (optional): 恢复会话 ID
        workspace_root: 工作区根目录
        timeout_sec: 每个任务的超时时间（秒）
        max_workers: 最大并行数，0 表示自动（默认 min(任务数, CPU核心数)）
    
    Returns:
        格式化的结果文本，包含所有任务的执行结果
    
    Example:
        run_parallel_tasks(
            tasks=[
                {"prompt": "分析 src/auth 目录", "subagent_type": "explore"},
                {"prompt": "分析 src/api 目录", "subagent_type": "explore"},
                {"prompt": "运行测试", "subagent_type": "shell"},
            ],
            workspace_root=".",
            timeout_sec=120
        )
    """
    if not tasks:
        return "❌ 任务列表为空"
    
    if not isinstance(tasks, list):
        return f"❌ tasks 参数必须是列表，当前类型: {type(tasks).__name__}"
    
    # 检查是否启用子进程模式
    use_subprocess = os.environ.get("MIRO_TASK_SUBPROCESS", "1").strip().lower()
    if use_subprocess in ("0", "false", "no", "off"):
        return (
            "⚠️ 并行任务需要子进程模式（MIRO_TASK_SUBPROCESS=1）。\n"
            "当前为同进程模式，将串行执行任务。\n"
            "建议：export MIRO_TASK_SUBPROCESS=1"
        )

    try:
        workspace_root = str(resolve_delegate_workspace_for_task(workspace_root))
    except PathSecurityError as e:
        return str(e)
    
    # 执行并行任务
    max_w = max_workers if max_workers > 0 else None
    results = run_tasks_parallel(
        tasks=tasks,
        workspace_root=workspace_root,
        timeout_sec=timeout_sec,
        max_workers=max_w,
    )
    
    # 格式化输出
    return format_parallel_results(results)

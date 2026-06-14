# -*- coding: utf-8 -*-
"""
Task 并行执行器（块5 增强）：支持多个 Task 同时运行。

使用方法：
    from backend.tools.coding.workflow_features.subagents.task_parallel_runner import run_tasks_parallel
    
    tasks = [
        {"prompt": "任务1", "subagent_type": "explore"},
        {"prompt": "任务2", "subagent_type": "shell"},
    ]
    results = run_tasks_parallel(tasks, workspace_root=".", timeout_sec=180)
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from .task_session_persistence import write_task_session_state
from .task_subprocess_runner import run_task_subprocess


def run_tasks_parallel(
    tasks: List[Dict[str, Any]],
    workspace_root: str = ".",
    timeout_sec: int = 180,
    max_workers: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """
    并行执行多个 Task。
    
    Args:
        tasks: 任务列表，每个任务是一个字典，包含：
            - prompt (required): 任务描述
            - subagent_type (optional): 子代理类型，默认 "explore"
            - resume (optional): 恢复会话 ID
        workspace_root: 工作区根目录
        timeout_sec: 每个任务的超时时间（秒）
        max_workers: 最大并行数，默认为 min(len(tasks), os.cpu_count() or 4)
    
    Returns:
        结果列表，每个结果包含：
            - task_index: 任务索引
            - ok: 是否成功
            - result: 结果文本
            - exit_code: 退出码
            - session_id: 会话 ID
    """
    if not tasks:
        return []
    
    # 确定并行数
    if max_workers is None:
        max_workers = min(len(tasks), os.cpu_count() or 4)
    
    # 为每个任务生成 session_id（如果没有 resume）
    task_configs = []
    for i, task in enumerate(tasks):
        prompt = task.get("prompt", "")
        if not prompt:
            task_configs.append({
                "index": i,
                "error": "任务 prompt 为空",
            })
            continue
        
        subagent_type = task.get("subagent_type", "explore")
        resume = task.get("resume", "")
        forced_session_id = task.get("forced_session_id", "") or ""
        node_id = task.get("node_id") or task.get("id") or str(i)

        task_configs.append({
            "index": i,
            "node_id": str(node_id),
            "prompt": prompt,
            "subagent_type": subagent_type,
            "resume": resume,
            "forced_session_id": forced_session_id,
            "workspace_root": workspace_root,
            "timeout_sec": timeout_sec,
        })
    
    # 并行执行
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_config = {
            executor.submit(_run_single_task, cfg): cfg
            for cfg in task_configs
            if "error" not in cfg
        }
        
        # 处理有错误的任务
        for cfg in task_configs:
            if "error" in cfg:
                results.append({
                    "task_index": cfg["index"],
                    "ok": False,
                    "result": f"❌ {cfg['error']}",
                    "exit_code": -1,
                    "session_id": "",
                })
        
        # 收集并行执行结果
        for future in concurrent.futures.as_completed(future_to_config):
            cfg = future_to_config[future]
            try:
                ok, result, exit_code, session_id = future.result()
                results.append({
                    "task_index": cfg["index"],
                    "ok": ok,
                    "result": result,
                    "exit_code": exit_code,
                    "session_id": session_id,
                })
            except Exception as e:
                results.append({
                    "task_index": cfg["index"],
                    "ok": False,
                    "result": f"❌ 并行执行异常: {e}",
                    "exit_code": -2,
                    "session_id": "",
                })
    
    # 按原始顺序排序
    results.sort(key=lambda x: x["task_index"])
    return results


def _run_single_task(cfg: Dict[str, Any]) -> Tuple[bool, str, int, str]:
    """执行单个任务，返回 (ok, result, exit_code, session_id)"""
    ok, result, exit_code, sid = run_task_subprocess(
        prompt=cfg["prompt"],
        subagent_type=cfg["subagent_type"],
        workspace_root=cfg["workspace_root"],
        timeout_sec=cfg["timeout_sec"],
        resume=cfg.get("resume") or "",
        forced_session_id=cfg.get("forced_session_id") or "",
    )
    ws = str(Path(cfg["workspace_root"]).resolve())
    write_task_session_state(
        ws,
        sid,
        cfg.get("node_id", str(cfg["index"])),
        result,
        ok,
    )
    return ok, result, exit_code, sid


def format_parallel_results(results: List[Dict[str, Any]]) -> str:
    """格式化并行任务结果为人类可读文本"""
    lines = [
        f"并行执行 {len(results)} 个任务：",
        "",
    ]
    
    success_count = sum(1 for r in results if r["ok"])
    lines.append(f"成功: {success_count}/{len(results)}")
    lines.append("")
    
    for r in results:
        idx = r["task_index"]
        ok = r["ok"]
        status = "✅" if ok else "❌"
        lines.append(f"{status} 任务 {idx + 1}:")
        
        result = r["result"]
        if len(result) > 500:
            result = result[:500] + "\n... (截断)"
        
        lines.append(f"   {result}")
        lines.append("")
    
    return "\n".join(lines)

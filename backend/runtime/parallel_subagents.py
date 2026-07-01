# -*- coding: utf-8 -*-
"""FABLEADV-27: 只读/分析的并行子智能体扇出（orchestrator-worker，Scope A）。

调研结论（Anthropic 多智能体研究系统 + Claude Code 社区）：
- 多智能体只在任务能拆成**独立并行**线程时才赢（"两个人不用先商量"法则）。
- 最贵的坑是过度 spawn + 15x token；并发**写**必然冲突。
- 所以 Metis 只做**只读/分析并行扇出**：调研 / 代码分析 / 多模块理解。
  每个子任务是自包含、隔离 context 的只读子智能体；主智能体负责拆分与综合。

安全性：子智能体只给**只读工具白名单**（检索/读取/分析），杜绝并发写冲突。
守卫：并发上限、任务数上限、每个子智能体轮次上限、总开关。
"""
from __future__ import annotations

import contextvars
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

# 只读/分析工具白名单——并发安全（无写冲突）。
READONLY_SUBAGENT_TOOLS = [
    "read_file",
    "read_multiple_files",
    "read_file_chunk",
    "list_directory",
    "find_files",
    "glob_search",
    "grep_search",
    "search_in_files",
    "search_in_codebase",
    "semantic_search",
    "generate_repo_map",
    "ast_search_code",
    "fetch_content",
    "web_fetch",
    "web_search",
    "web_research",
]

_SUBAGENT_SYSTEM_PROMPT = (
    "You are a focused read-only research subagent. You were given ONE self-contained "
    "subtask as part of a larger parallel investigation. Use only retrieval/read/analysis "
    "tools (you cannot edit files or run commands). Investigate efficiently, then return a "
    "concise, self-contained finding: what you found, where (file:line or source), and the "
    "direct answer to your subtask. Do not ask questions; do your best with what you can read."
)


def parallel_enabled() -> bool:
    value = os.environ.get("METIS_PARALLEL_SUBAGENTS", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _max_concurrency() -> int:
    try:
        return max(1, min(int(os.environ.get("METIS_PARALLEL_MAX", "4")), 8))
    except ValueError:
        return 4


def _max_tasks() -> int:
    try:
        return max(1, min(int(os.environ.get("METIS_PARALLEL_MAX_TASKS", "6")), 10))
    except ValueError:
        return 6


def _subagent_max_turns() -> int:
    try:
        return max(1, min(int(os.environ.get("METIS_PARALLEL_SUBAGENT_TURNS", "12")), 30))
    except ValueError:
        return 12


def _run_one(task: str, workspace_root: str = "") -> Dict[str, str]:
    """跑一个只读子智能体，返回 {task, result}。隔离 context、只读工具。"""
    from .agent_loop import AgentConfig, ContentEvent, DoneEvent, ErrorEvent, run
    from .expert_tools import (
        _get_current_api_key,
        _get_current_backend,
        _get_current_base_url,
        _get_current_model,
    )
    from .tool_registry import get_registry

    config = AgentConfig(
        system_prompt=_SUBAGENT_SYSTEM_PROMPT,
        max_turns=_subagent_max_turns(),
        execution_mode="execute",
        llm_backend=_get_current_backend(),
        llm_base_url=_get_current_base_url(),
        llm_api_key=_get_current_api_key(),
        llm_model=_get_current_model(),
        enabled_tools=list(READONLY_SUBAGENT_TOOLS),
        workspace_root=workspace_root,
    )
    registry = get_registry(include_mcp=False)
    messages = [{"role": "user", "content": task}]
    parts: List[str] = []
    try:
        for event in run(messages, config, registry=registry):
            if isinstance(event, ContentEvent) and event.text:
                parts.append(event.text)
            elif isinstance(event, ErrorEvent):
                parts.append(f"[subagent error: {event.message}]")
            elif isinstance(event, DoneEvent):
                break
    except Exception as exc:  # noqa: BLE001
        return {"task": task, "result": f"[subagent failed: {type(exc).__name__}: {exc}]"}
    result = "\n".join(parts).strip() or "[no output]"
    if len(result) > 6000:
        result = result[:5800] + f"\n[... truncated {len(result) - 5800} chars ...]"
    return {"task": task, "result": result}


def _normalize_tasks(tasks: Any) -> List[str]:
    out: List[str] = []
    if isinstance(tasks, str):
        tasks = [tasks]
    if not isinstance(tasks, (list, tuple)):
        return out
    for item in tasks:
        if isinstance(item, str) and item.strip():
            out.append(item.strip())
        elif isinstance(item, dict):
            text = str(item.get("task") or item.get("goal") or item.get("description") or "").strip()
            if text:
                out.append(text)
    return out


def delegate_parallel(tasks: Any = None, workspace_root: str = "") -> str:
    """并发跑多个**只读/分析**子任务，返回合并后的发现，供主智能体综合。"""
    task_list = _normalize_tasks(tasks)
    if not task_list:
        return "Error: delegate_parallel 需要一个非空的 tasks 列表（每项是自包含的只读子任务描述）。"
    if not parallel_enabled():
        # 关闭并行：退化为顺序执行（仍可用，只是不并发）
        results = [_run_one(t, workspace_root) for t in task_list[: _max_tasks()]]
        return _format_results(results, parallel=False)

    capped = task_list[: _max_tasks()]
    # ContextVar 不会自动传到线程：在主线程为每个任务快照当前上下文（含工作区边界），
    # 让子智能体的文件工具解析到正确的工作区。
    contexts = [contextvars.copy_context() for _ in capped]
    results: List[Dict[str, str]] = [{} for _ in capped]
    with ThreadPoolExecutor(max_workers=_max_concurrency(), thread_name_prefix="metis-subagent") as pool:
        future_to_idx = {
            pool.submit(contexts[i].run, _run_one, task, workspace_root): i
            for i, task in enumerate(capped)
        }
        for future in as_completed(future_to_idx):
            idx = future_to_idx[future]
            try:
                results[idx] = future.result()
            except Exception as exc:  # noqa: BLE001
                results[idx] = {"task": capped[idx], "result": f"[subagent crashed: {exc}]"}
    return _format_results(results, parallel=True)


def _format_results(results: List[Dict[str, str]], *, parallel: bool) -> str:
    mode = "并行" if parallel else "顺序"
    lines = [f"[{len(results)} 个只读子智能体{mode}完成 — 请综合以下发现]"]
    for i, item in enumerate(results, 1):
        task = item.get("task", "")
        result = item.get("result", "")
        lines.append(f"\n### 子任务 {i}: {task}\n{result}")
    return "\n".join(lines)

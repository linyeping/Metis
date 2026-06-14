# -*- coding: utf-8 -*-
"""P6：DAG 拓扑执行 + 图级 resume（与 task_parallel_runner 同线程池策略）。"""
from __future__ import annotations

import concurrent.futures
import os
import re
import uuid
from collections import defaultdict
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError

from .delegate_workspace import resolve_delegate_workspace_for_task
from .task_parallel_runner import _run_single_task
from .task_session_persistence import read_task_session_json, resume_state_file_path, write_task_session_state


def sanitize_node_id_for_sid(node_id: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_-]", "_", (node_id or "").strip())
    return (s[:64] if s else "node") or "node"


def forced_session_id_for_graph_node(graph_sid: str, node_id: str) -> str:
    return f"{graph_sid}_{sanitize_node_id_for_sid(node_id)}"


def compute_dag_layers_or_error(
    node_ids: List[str],
    edges: List[Dict[str, str]],
) -> Tuple[Optional[List[List[str]]], Optional[str]]:
    """
    edges：from -> to 表示 from 必须先于 to 完成。
    返回按拓扑分层的节点 id 列表（同层可并行），或 (None, ❌ 错误)。
    """
    sset = set(node_ids)
    if len(sset) != len(node_ids):
        return None, "❌ nodes 中存在重复的 id"

    indeg = {n: 0 for n in sset}
    outgoing: Dict[str, List[str]] = defaultdict(list)
    for e in edges:
        fr = str(e.get("from", "")).strip()
        to = str(e.get("to", "")).strip()
        if not fr or not to:
            return None, "❌ edges 项缺少有效的 from 或 to"
        if fr not in sset or to not in sset:
            return None, f"❌ 边引用未知节点: {fr!r} -> {to!r}"
        outgoing[fr].append(to)
        indeg[to] += 1

    indeg_work = dict(indeg)
    remaining = set(sset)
    layers: List[List[str]] = []
    while remaining:
        layer = sorted(n for n in remaining if indeg_work[n] == 0)
        if not layer:
            return None, "❌ 任务依赖图含有向环，已中止执行"
        layers.append(layer)
        for n in layer:
            remaining.remove(n)
            for v in outgoing[n]:
                indeg_work[v] -= 1
    return layers, None


def load_graph_resume_state_or_error(
    workspace_root: str,
    resume: str,
) -> Tuple[Optional[Set[str]], Optional[str]]:
    """
    resume 非空：必须存在且可读 JSON，且含 P6 MUST 字段；返回 completed_node_ids。
    resume 空：返回空 set。
    """
    rid = (resume or "").strip()
    if not rid:
        return set(), None
    p = resume_state_file_path(workspace_root, rid)
    if not p.is_file():
        return None, f"❌ resume 状态文件不存在: {p}（禁止静默新开会话）"
    data = read_task_session_json(workspace_root, rid)
    if not data:
        return None, f"❌ resume 状态文件不可解析: {p}"
    for k in ("session_id", "node_id", "last_messages_digest", "finished", "utc_timestamp"):
        if k not in data:
            return None, f"❌ 状态文件缺少必填字段 {k!r}"
    raw = data.get("completed_node_ids")
    if raw is None:
        completed = set()
    elif isinstance(raw, list):
        completed = {str(x) for x in raw}
    else:
        return None, "❌ 状态文件 completed_node_ids 必须为数组"
    if str(data.get("session_id", "")).strip() != rid:
        return None, "❌ 状态文件 session_id 与 resume 不一致"
    return completed, None


def _node_by_id(nodes: List[Dict[str, Any]]) -> Dict[str, Dict[str, Any]]:
    out: Dict[str, Dict[str, Any]] = {}
    for n in nodes:
        nid = str(n.get("id", "")).strip()
        if not nid:
            raise ValueError("节点 id 不能为空")
        if nid in out:
            raise ValueError("nodes 中存在重复的 id")
        out[nid] = n
    return out


def run_task_graph_core(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, str]],
    workspace_root: str,
    timeout_sec: int = 180,
    max_workers: Optional[int] = None,
    resume: str = "",
) -> str:
    """返回人类可读汇总（含各节点结果）。"""
    if not nodes:
        return "❌ nodes 为空"
    if not isinstance(nodes, list):
        return f"❌ nodes 必须为列表，当前: {type(nodes).__name__}"
    if not isinstance(edges, list):
        return f"❌ edges 必须为列表，当前: {type(edges).__name__}"

    try:
        by_id = _node_by_id(nodes)
    except ValueError as e:
        return f"❌ {e}"

    node_ids = list(by_id.keys())
    layers, err = compute_dag_layers_or_error(node_ids, edges)
    if err:
        return err

    completed, rerr = load_graph_resume_state_or_error(workspace_root, resume)
    if rerr:
        return rerr

    graph_sid = (resume or "").strip() or uuid.uuid4().hex[:16]

    if max_workers is None:
        max_workers = min(len(node_ids), os.cpu_count() or 4)
    max_workers = max(1, max_workers)

    all_results: List[Dict[str, Any]] = []
    ord_i = 0

    for layer in layers:
        batch: List[Dict[str, Any]] = []
        for nid in layer:
            if nid in completed:
                all_results.append(
                    {
                        "task_index": ord_i,
                        "node_id": nid,
                        "ok": True,
                        "result": "（已从 checkpoint 跳过，completed_node_ids）",
                        "exit_code": 0,
                        "session_id": forced_session_id_for_graph_node(graph_sid, nid),
                        "skipped": True,
                    }
                )
                ord_i += 1
                continue
            spec = by_id[nid]
            prompt = str(spec.get("prompt", "")).strip()
            if not prompt:
                all_results.append(
                    {
                        "task_index": ord_i,
                        "node_id": nid,
                        "ok": False,
                        "result": f"❌ 节点 {nid!r} 的 prompt 为空",
                        "exit_code": -1,
                        "session_id": "",
                        "skipped": False,
                    }
                )
                ord_i += 1
                continue
            st = spec.get("subagent_type", "explore")
            nresume = str(spec.get("resume", "") or "")
            fid = forced_session_id_for_graph_node(graph_sid, nid)
            # 节点显式 resume 时走子进程 resume 语义（须已有 .json）；否则用图内稳定 sid
            if nresume:
                batch.append(
                    {
                        "index": ord_i,
                        "node_id": nid,
                        "prompt": prompt,
                        "subagent_type": st,
                        "resume": nresume,
                        "forced_session_id": "",
                        "workspace_root": workspace_root,
                        "timeout_sec": timeout_sec,
                    }
                )
            else:
                batch.append(
                    {
                        "index": ord_i,
                        "node_id": nid,
                        "prompt": prompt,
                        "subagent_type": st,
                        "resume": "",
                        "forced_session_id": fid,
                        "workspace_root": workspace_root,
                        "timeout_sec": timeout_sec,
                    }
                )
            ord_i += 1

        if not batch:
            continue

        layer_results: List[Dict[str, Any]] = []
        with concurrent.futures.ThreadPoolExecutor(max_workers=min(max_workers, len(batch))) as ex:
            futs = {ex.submit(_run_single_task, cfg): cfg for cfg in batch}
            for fut in concurrent.futures.as_completed(futs):
                cfg = futs[fut]
                nid = cfg["node_id"]
                try:
                    ok, result, exit_code, sid = fut.result()
                    layer_results.append(
                        {
                            "task_index": cfg["index"],
                            "node_id": nid,
                            "ok": ok,
                            "result": result,
                            "exit_code": exit_code,
                            "session_id": sid,
                            "skipped": False,
                        }
                    )
                except Exception as e:
                    layer_results.append(
                        {
                            "task_index": cfg["index"],
                            "node_id": nid,
                            "ok": False,
                            "result": f"❌ 并行执行异常: {e}",
                            "exit_code": -2,
                            "session_id": "",
                            "skipped": False,
                        }
                    )
        layer_results.sort(key=lambda x: x["task_index"])
        all_results.extend(layer_results)

        for r in layer_results:
            if r.get("ok") and not r.get("skipped"):
                completed.add(str(r["node_id"]))
        if layer_results:
            last = layer_results[-1]
            write_task_session_state(
                workspace_root,
                graph_sid,
                str(last["node_id"]),
                str(last.get("result", "")),
                bool(last.get("ok")),
                extra={"completed_node_ids": sorted(completed), "kind": "task_graph"},
            )

    lines = [
        f"DAG 执行（graph_session_id={graph_sid}），层数={len(layers)}",
        "",
    ]
    ok_count = sum(1 for r in all_results if r.get("ok"))
    lines.append(f"节点结果: 成功 {ok_count}/{len(all_results)}")
    lines.append("")
    for r in sorted(all_results, key=lambda x: x["task_index"]):
        st = "✅" if r.get("ok") else "❌"
        nid = r.get("node_id", "?")
        lines.append(f"{st} [{nid}] session={r.get('session_id', '')!r}")
        body = str(r.get("result", ""))
        if len(body) > 400:
            body = body[:400] + "\n... (截断)"
        lines.append(f"   {body}")
        lines.append("")
    return "\n".join(lines)


def run_task_graph_execution(
    nodes: List[Dict[str, Any]],
    edges: List[Dict[str, str]],
    workspace_root: str = ".",
    timeout_sec: int = 180,
    max_workers: int = 0,
    resume: str = "",
) -> str:
    """供工具层调用：解析 workspace、子进程开关、max_workers。"""
    use_subprocess = os.environ.get("MIRO_TASK_SUBPROCESS", "1").strip().lower()
    if use_subprocess in ("0", "false", "no", "off"):
        return (
            "⚠️ DAG 任务需要子进程模式（MIRO_TASK_SUBPROCESS=1）。\n"
            "当前为同进程模式，已中止。\n"
            "建议：export MIRO_TASK_SUBPROCESS=1"
        )
    try:
        ws = str(resolve_delegate_workspace_for_task(workspace_root))
    except PathSecurityError as e:
        return str(e)
    mw = max_workers if max_workers > 0 else None
    return run_task_graph_core(
        nodes=nodes,
        edges=edges,
        workspace_root=ws,
        timeout_sec=timeout_sec,
        max_workers=mw,
        resume=resume or "",
    )

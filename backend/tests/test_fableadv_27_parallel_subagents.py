# -*- coding: utf-8 -*-
"""FABLEADV-27: 只读/分析并行子智能体扇出（Scope A）。"""
from __future__ import annotations

import threading

import pytest

from backend.runtime import parallel_subagents as ps


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setenv("METIS_PARALLEL_SUBAGENTS", "1")
    monkeypatch.delenv("METIS_PARALLEL_MAX", raising=False)
    monkeypatch.delenv("METIS_PARALLEL_MAX_TASKS", raising=False)


def _fake_run_one(monkeypatch):
    def fake(task, workspace_root=""):
        return {"task": task, "result": f"RESULT:{task}"}

    monkeypatch.setattr(ps, "_run_one", fake)


def test_runs_all_tasks_and_merges(monkeypatch):
    _fake_run_one(monkeypatch)
    out = ps.delegate_parallel(["analyze auth", "analyze db", "research caching"])
    assert "子任务 1" in out and "子任务 2" in out and "子任务 3" in out
    assert "RESULT:analyze auth" in out
    assert "RESULT:research caching" in out
    assert "并行" in out


def test_caps_task_count(monkeypatch):
    _fake_run_one(monkeypatch)
    monkeypatch.setenv("METIS_PARALLEL_MAX_TASKS", "3")
    out = ps.delegate_parallel([f"task {i}" for i in range(20)])
    # 只跑前 3 个（防过度 spawn）
    assert out.count("### 子任务") == 3


def test_normalizes_strings_and_dicts(monkeypatch):
    _fake_run_one(monkeypatch)
    out = ps.delegate_parallel(["plain string", {"task": "from task key"}, {"goal": "from goal key"}, "", {"x": 1}])
    assert "RESULT:plain string" in out
    assert "RESULT:from task key" in out
    assert "RESULT:from goal key" in out
    # 空串和无识别字段的 dict 被过滤
    assert out.count("### 子任务") == 3


def test_empty_tasks_returns_error(monkeypatch):
    _fake_run_one(monkeypatch)
    assert "Error" in ps.delegate_parallel([])
    assert "Error" in ps.delegate_parallel(None)


def test_disabled_runs_sequential(monkeypatch):
    _fake_run_one(monkeypatch)
    monkeypatch.setenv("METIS_PARALLEL_SUBAGENTS", "0")
    out = ps.delegate_parallel(["a", "b"])
    assert "顺序" in out  # 退化为顺序执行
    assert "RESULT:a" in out and "RESULT:b" in out


def test_actually_runs_in_parallel(monkeypatch):
    # 用 Barrier 证明并发：3 个任务必须同时到达 barrier 才能通过；若顺序执行会超时。
    barrier = threading.Barrier(3, timeout=4)

    def fake(task, workspace_root=""):
        barrier.wait()  # 顺序执行时第一个会卡死直到超时
        return {"task": task, "result": "ok"}

    monkeypatch.setattr(ps, "_run_one", fake)
    out = ps.delegate_parallel(["t1", "t2", "t3"])
    assert out.count("### 子任务") == 3  # 都通过了 barrier = 真并发


def test_delegate_parallel_in_full_profile_not_lean():
    # full profile 含 delegate_parallel（否则 builtin 不进 schema = 死工具）；
    # lean（弱模型最小集）不含——多智能体是高级/高成本能力，弱模型不该拿到。
    from backend.runtime.tool_registry import ToolDefinition, ToolRegistry

    reg = ToolRegistry()
    reg.register(
        ToolDefinition(
            name="delegate_parallel",
            description="x",
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=lambda **_: "",
            source="builtin",
        )
    )
    full = {s["function"]["name"] for s in reg.get_schemas_for_profile("full")}
    lean = {s["function"]["name"] for s in reg.get_schemas_for_profile("lean")}
    assert "delegate_parallel" in full
    assert "delegate_parallel" not in lean


def test_readonly_whitelist_has_no_write_tools():
    forbidden = {
        "write_file",
        "append_to_file",
        "robust_replace_in_file",
        "edit_code_ast",
        "apply_patch",
        "delete_file",
        "delete_directory",
        "execute_bash_command",
        "run_tests",
    }
    assert forbidden.isdisjoint(set(ps.READONLY_SUBAGENT_TOOLS))

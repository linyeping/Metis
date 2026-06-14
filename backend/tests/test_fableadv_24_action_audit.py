# -*- coding: utf-8 -*-
"""FABLEADV-24: 全量动作审计。"""
from __future__ import annotations

import os


from backend.runtime import action_audit
from backend.runtime.llm_backends import ToolCall


def _results():
    return [
        (ToolCall("c1", "read_file", {"path": "a.py"}), "file contents here"),
        (ToolCall("c2", "desktop_action", {"action": "click", "x": 1, "y": 2}), "Done: click"),
        (ToolCall("c3", "run_tests", {}), "Error: tests failed"),
    ]


def test_records_and_reads_back(monkeypatch, tmp_path):
    monkeypatch.setenv("METIS_ACTION_AUDIT", "1")
    n = action_audit.record_actions(_results(), workspace_root=str(tmp_path), turn=3)
    assert n == 3
    rows = action_audit.read_recent(str(tmp_path))
    assert [r["tool"] for r in rows] == ["read_file", "desktop_action", "run_tests"]
    assert all(r["turn"] == 3 for r in rows)
    assert rows[0]["status"] == "success"
    assert rows[2]["status"] == "error"  # "Error: ..." 识别为失败
    assert rows[1]["args"]["action"] == "click"
    # 写到工作区 .metis/audit 下
    assert os.path.isfile(os.path.join(str(tmp_path), ".metis", "audit", "agent-actions.jsonl"))


def test_disabled_writes_nothing(monkeypatch, tmp_path):
    monkeypatch.setenv("METIS_ACTION_AUDIT", "0")
    assert action_audit.record_actions(_results(), workspace_root=str(tmp_path)) == 0
    assert action_audit.read_recent(str(tmp_path)) == []


def test_truncates_long_fields(monkeypatch, tmp_path):
    monkeypatch.setenv("METIS_ACTION_AUDIT", "1")
    big = "x" * 50_000
    action_audit.record_actions([(ToolCall("c", "write_file", {"content": big}), big)], workspace_root=str(tmp_path))
    rows = action_audit.read_recent(str(tmp_path))
    assert len(rows) == 1
    assert "chars)" in rows[0]["result"]  # 结果被截断并标注
    assert rows[0]["result_chars"] == 50_000  # 原始长度仍记录


def test_read_recent_limit(monkeypatch, tmp_path):
    monkeypatch.setenv("METIS_ACTION_AUDIT", "1")
    for i in range(10):
        action_audit.record_actions([(ToolCall(f"c{i}", "echo", {"i": i}), "ok")], workspace_root=str(tmp_path), turn=i)
    rows = action_audit.read_recent(str(tmp_path), limit=3)
    assert len(rows) == 3
    assert [r["turn"] for r in rows] == [7, 8, 9]  # 最近 3 条

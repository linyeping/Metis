# -*- coding: utf-8 -*-
"""FABLEADV-45: 编辑后反馈回路（auto-diagnostics）。

改完 Python 文件就地跑 ruff，有真实报错则回灌进工具结果;干净/非 .py/ruff 缺失/被关 → 静默。
"""
from __future__ import annotations

import shutil

import backend.tools.coding.workflow_features.hooks.edit_diagnostics as ed
from backend.tools.coding.workflow_features.hooks.edit_diagnostics import (
    edit_diagnostics_feedback,
    quick_python_diagnostics,
)
from backend.tools.coding.workflow_features.hooks.post_tool_hook import (
    _FILE_MODIFY_TOOLS,
    post_tool_hook,
)


def test_no_feedback_for_non_modify_tool():
    assert edit_diagnostics_feedback("read_file", {"file_path": "x.py"}, _FILE_MODIFY_TOOLS) == ""


def test_disabled_env_returns_empty(monkeypatch):
    monkeypatch.setenv("METIS_EDIT_DIAGNOSTICS", "0")
    assert edit_diagnostics_feedback("write_file", {"file_path": "x.py"}, _FILE_MODIFY_TOOLS) == ""


def test_silent_when_no_diagnostics(monkeypatch):
    monkeypatch.setattr(ed, "quick_python_diagnostics", lambda p: "")
    assert edit_diagnostics_feedback("write_file", {"file_path": "x.py"}, _FILE_MODIFY_TOOLS) == ""


def test_feedback_appended_when_issues(monkeypatch):
    monkeypatch.setattr(ed, "quick_python_diagnostics", lambda p: "x.py:1:1 F401 unused import")
    out = edit_diagnostics_feedback("write_file", {"file_path": "x.py"}, _FILE_MODIFY_TOOLS)
    assert "auto-diagnostics" in out and "F401" in out


def test_post_tool_hook_integrates(monkeypatch):
    monkeypatch.setattr(ed, "quick_python_diagnostics", lambda p: "bad.py:2:1 E999 SyntaxError")
    result = post_tool_hook("robust_replace_in_file", {"file_path": "bad.py"}, "edit ok")
    assert "edit ok" in result and "E999" in result


def test_real_ruff_detects_error(tmp_path):
    if not shutil.which("ruff"):
        return  # 环境无 ruff，跳过真机断言（优雅降级已由其它测试覆盖）
    bad = tmp_path / "bad.py"
    bad.write_text("import os\nx =\n", encoding="utf-8")  # 语法错误
    diag = quick_python_diagnostics(str(bad))
    assert diag  # 应检出问题

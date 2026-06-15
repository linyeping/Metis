# -*- coding: utf-8 -*-
"""FABLEADV-44: read_workspace_memory 工具。

workspace 记忆(.metis/memory.json)被移出 system 前缀以保缓存命中后，本工具让模型按需取回，
不留"只写不读"的孤儿，长期项目可恢复连续性。
"""
from __future__ import annotations

from backend.core.memory.workspace_memory import WorkspaceMemory
from backend.tools.coding.workflow_features.agent_state.update_project_memory import (
    read_workspace_memory,
)


def test_registered_as_tool():
    from backend.tools.registry import AVAILABLE_TOOLS

    assert AVAILABLE_TOOLS.get("read_workspace_memory") is read_workspace_memory


def test_has_schema_and_is_safe():
    from backend.tools.schema_definitions import build_tools_schema
    from backend.bridges.tool_profiles import is_safe_tool

    names = {(s.get("function") or {}).get("name") for s in build_tools_schema()}
    assert "read_workspace_memory" in names
    assert is_safe_tool("read_workspace_memory")


def test_empty_memory_message(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    out = read_workspace_memory()
    assert "empty" in out.lower() or "memory" in out.lower()


def test_reads_saved_memory(tmp_path, monkeypatch):
    mem = WorkspaceMemory(workspace_root=str(tmp_path), project_type="Python")
    mem.key_files = ["app.py", "config.py"]
    mem.architecture_notes = "Flask + SSE backend, Electron front end."
    mem.save()
    monkeypatch.chdir(tmp_path)
    out = read_workspace_memory()
    assert "Python" in out
    assert "app.py" in out
    assert "Flask" in out

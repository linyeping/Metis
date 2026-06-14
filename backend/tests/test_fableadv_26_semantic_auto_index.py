# -*- coding: utf-8 -*-
"""FABLEADV-26: 语义索引自动构建 / 增量刷新。

现状：semantic_search 无索引/过期时只甩"请手动 build"的降级信息。
目标：无索引→自动构建；有索引但文件有变更→自动增量刷新；再检索。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from backend.tools.coding.foundation.core_mechanisms.execution_boundary_context import (
    workspace_root_override,
)
from backend.tools.coding.read_search.search.semantic_local import (
    DEFAULT_INDEX_NAME,
    build_semantic_index,
    index_is_stale,
    load_index,
)
from backend.tools.coding.read_search.search.semantic_search import semantic_search


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    monkeypatch.setenv("METIS_SEMANTIC_AUTO", "1")
    monkeypatch.delenv("METIS_SEMANTIC_MAX_FILES", raising=False)
    (tmp_path / "auth.py").write_text(
        "def login(user, password):\n    return verify_credentials(user, password)\n",
        encoding="utf-8",
    )
    (tmp_path / "db.py").write_text(
        "def run_query(sql):\n    return database.execute(sql)\n",
        encoding="utf-8",
    )
    # 把工作区根绑定到 tmp_path（context 优先级最高），过路径安全校验
    with workspace_root_override(str(tmp_path)):
        yield tmp_path


def _idx(root: Path) -> Path:
    return root / DEFAULT_INDEX_NAME


def test_auto_build_on_missing_index(workspace):
    assert not _idx(workspace).is_file()  # 起始无索引
    out = semantic_search("login password credentials", workspace_root=str(workspace))
    assert "自动构建" in out  # 提示已自动构建
    assert "auth.py" in out  # 检索命中
    assert _idx(workspace).is_file()  # 索引已落盘


def test_auto_incremental_refresh_on_new_file(workspace):
    # 先建索引
    semantic_search("login", workspace_root=str(workspace))
    # 新增一个文件，带唯一 token
    (workspace / "payments.py").write_text(
        "def refund_transaction(charge_id):\n    return stripe_gateway.refund(charge_id)\n",
        encoding="utf-8",
    )
    out = semantic_search("refund transaction stripe", workspace_root=str(workspace))
    assert "刷新" in out  # 提示已增量刷新
    assert "payments.py" in out  # 新文件可被检索到


def test_disabled_returns_fallback(workspace, monkeypatch):
    monkeypatch.setenv("METIS_SEMANTIC_AUTO", "0")
    out = semantic_search("login", workspace_root=str(workspace))
    assert "未找到本地语义索引" in out  # 关闭自动 → 降级提示
    assert not _idx(workspace).is_file()  # 未自动构建


def test_max_files_guard_skips_autobuild(workspace, monkeypatch):
    monkeypatch.setenv("METIS_SEMANTIC_MAX_FILES", "0")  # 任何文件数都超限
    out = semantic_search("login", workspace_root=str(workspace))
    assert "未找到本地语义索引" in out  # 超限 → 不自动构建，降级
    assert not _idx(workspace).is_file()


def test_index_is_stale_detection(workspace):
    build_semantic_index(str(workspace), incremental=False)
    doc = load_index(_idx(workspace))
    assert doc is not None
    assert index_is_stale(doc, workspace) is False  # 刚建，未过期

    # 新增文件 → 过期
    (workspace / "new_module.py").write_text("x = 1\n", encoding="utf-8")
    assert index_is_stale(doc, workspace) is True


def test_index_is_stale_on_modify_and_delete(workspace):
    build_semantic_index(str(workspace), incremental=False)
    doc = load_index(_idx(workspace))

    # 修改 mtime（设为未来）→ 过期
    future = os.path.getmtime(workspace / "auth.py") + 100
    os.utime(workspace / "auth.py", (future, future))
    assert index_is_stale(doc, workspace) is True

    # 删除文件 → 过期
    build_semantic_index(str(workspace), incremental=False)
    doc2 = load_index(_idx(workspace))
    (workspace / "db.py").unlink()
    assert index_is_stale(doc2, workspace) is True

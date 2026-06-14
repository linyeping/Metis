# -*- coding: utf-8 -*-
"""
P3：IDE 通过 HTTP 推送的「打开文件」快照（进程内、最后写入 wins）。

与磁盘 `.miro_open_files.json` 使用同一 JSON 语义；解析须与
`engine.open_files_context.parse_open_files_payload` 一致。
"""
from __future__ import annotations

import os
import threading
from typing import Any, List, Optional, Tuple

_lock = threading.Lock()
_active: bool = False
_bound_root: str = ""
_paths: List[str] = []
_focus: Optional[str] = None


def clear_http_open_files() -> None:
    """清除 HTTP 覆盖（测试或管理用）；清除后 `open_files_context_block` 回退读磁盘。"""
    global _active, _bound_root, _paths, _focus
    with _lock:
        _active = False
        _bound_root = ""
        _paths = []
        _focus = None


def set_http_open_files(workspace_root: str, payload: Any) -> None:
    """
    用一次 POST 的 JSON 体更新缓存；仅对 `abspath(workspace_root)` 与绑定根一致时生效。
    并发 POST 在锁内串行，最后一条覆盖前一条。
    """
    from .open_files_context import parse_open_files_payload

    root = os.path.abspath(workspace_root)
    paths, focus = parse_open_files_payload(payload)
    with _lock:
        global _active, _bound_root, _paths, _focus
        _active = True
        _bound_root = root
        _paths = list(paths)
        _focus = focus


def get_http_open_files(workspace_root: str) -> Optional[Tuple[List[str], Optional[str]]]:
    """
    若存在针对该工作区根的 HTTP 快照则返回 (paths, focus)，否则 None（调用方应读磁盘）。
    """
    if not workspace_root:
        return None
    root = os.path.abspath(workspace_root)
    with _lock:
        if not _active or _bound_root != root:
            return None
        return (list(_paths), _focus)

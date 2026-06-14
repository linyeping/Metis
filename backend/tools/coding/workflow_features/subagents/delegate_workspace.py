# -*- coding: utf-8 -*-
"""子代理 / Task 子进程的 workspace_root：受 allow_delegate_subagent_outside_workspace 与总闸约束。"""
from __future__ import annotations

from pathlib import Path


def delegate_workspace_outside_allowed() -> bool:
    from backend.tools.coding.foundation.core_mechanisms.execution_boundary_context import (
        get_effective_sub_allow,
    )

    return get_effective_sub_allow("allow_delegate_subagent_outside_workspace")


def resolve_delegate_workspace_for_task(workspace_root: str) -> Path:
    """
    解析 Task / 并行任务使用的目录（允许不存在，由调用方 mkdir）。
    默认须在配置工作区内；开启分项或总闸后可指向工作区外。
    """
    from backend.tools.coding.foundation.core_mechanisms.path_security import validate_path

    allow = delegate_workspace_outside_allowed()
    raw = (workspace_root or ".").strip() or "."
    p, _ = validate_path(
        raw,
        must_exist=False,
        allow_create=True,
        allow_paths_outside_workspace=allow,
    )
    return p

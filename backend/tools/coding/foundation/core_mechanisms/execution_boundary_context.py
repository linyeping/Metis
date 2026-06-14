# -*- coding: utf-8 -*-
"""
执行边界：配置真源 + 请求级覆盖（ContextVar）。

总闸 full_unrestricted 为 True 时，所有分项「允许工作区外」视为 True。
分项仅在 full_unrestricted 为 False 时各自生效。

优先级（单次查询某分项的有效值）：
1. ContextVar 中对该键的覆盖（若本次上下文设置了该键）
2. MiroConfig（环境变量 > miro_config.json > 默认）
3. 对分项：若 effective(full_unrestricted) 为 True，则分项强制为 True
"""
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Dict, Iterator, Optional

# 与 MiroConfig.DEFAULTS 中的布尔边界项保持一致（实现阶段 B）
BOUNDARY_BOOL_KEYS = (
    "full_unrestricted",
    "allow_paths_outside_workspace",
    "allow_shell_cwd_outside_workspace",
    "allow_search_outside_workspace",
    "allow_semantic_outside_workspace",
    "allow_notebook_paths_outside_workspace",
    "allow_delegate_subagent_outside_workspace",
)

SUB_ALLOW_KEYS = BOUNDARY_BOOL_KEYS[1:]

_ctx_overrides: ContextVar[Optional[Dict[str, bool]]] = ContextVar(
    "miro_boundary_overrides", default=None
)
_ctx_workspace_root: ContextVar[Optional[str]] = ContextVar(
    "miro_workspace_root", default=None
)


def _config_get_bool(key: str) -> bool:
    from backend.tools.coding.foundation.core_mechanisms.config import get_config

    v = get_config().get(key, False)
    return bool(v)


def _merged_raw() -> Dict[str, bool]:
    """合并默认/文件/环境与当前 ContextVar 覆盖（不应用总闸推导）。"""
    out = {k: _config_get_bool(k) for k in BOUNDARY_BOOL_KEYS}
    o = _ctx_overrides.get()
    if o:
        out.update(o)
    return out


def get_effective_full_unrestricted() -> bool:
    return _merged_raw()["full_unrestricted"]


def get_effective_sub_allow(key: str) -> bool:
    """
    分项「允许工作区外」类开关的有效值。
    key 须为 SUB_ALLOW_KEYS 之一。
    """
    if key not in SUB_ALLOW_KEYS:
        raise ValueError(f"unknown sub-allow key: {key!r}")
    m = _merged_raw()
    if m["full_unrestricted"]:
        return True
    return bool(m[key])


def set_boundary_overrides(updates: Dict[str, bool]) -> None:
    """直接写入当前上下文覆盖（测试或高级用法）；一般用 boundary_override 上下文管理器。"""
    bad = set(updates) - set(BOUNDARY_BOOL_KEYS)
    if bad:
        raise ValueError(f"invalid boundary keys: {bad}")
    _ctx_overrides.set(dict(updates))


def clear_boundary_overrides() -> None:
    _ctx_overrides.set(None)


def get_context_workspace_root() -> Optional[str]:
    value = _ctx_workspace_root.get()
    return value if value else None


@contextmanager
def boundary_override(**kwargs: bool) -> Iterator[None]:
    """
    临时覆盖边界项（通常单次 HTTP 请求内）。未传入的键沿用配置 + 已有覆盖合并。

    例：with boundary_override(full_unrestricted=True): ...
    """
    bad = set(kwargs) - set(BOUNDARY_BOOL_KEYS)
    if bad:
        raise ValueError(f"invalid boundary keys: {bad}")
    prev = _ctx_overrides.get()
    merged: Dict[str, bool] = dict(prev) if prev else {}
    merged.update({k: bool(v) for k, v in kwargs.items()})
    token = _ctx_overrides.set(merged)
    try:
        yield
    finally:
        _ctx_overrides.reset(token)


@contextmanager
def workspace_root_override(workspace_root: str) -> Iterator[None]:
    """Temporarily bind file tools to one workspace root without process-wide chdir."""
    token = _ctx_workspace_root.set(str(workspace_root or "").strip() or None)
    try:
        yield
    finally:
        _ctx_workspace_root.reset(token)


__all__ = [
    "BOUNDARY_BOOL_KEYS",
    "SUB_ALLOW_KEYS",
    "boundary_override",
    "clear_boundary_overrides",
    "get_context_workspace_root",
    "get_effective_full_unrestricted",
    "get_effective_sub_allow",
    "set_boundary_overrides",
    "workspace_root_override",
]

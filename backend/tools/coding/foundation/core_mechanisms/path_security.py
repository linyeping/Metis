# -*- coding: utf-8 -*-
"""
路径安全模块：防止路径穿越攻击，限制文件操作在工作区内。

对齐 C 与 K 的安全要求：
- 所有文件路径必须规范化为工作区内的绝对路径
- 拒绝访问工作区外的文件（防止 ../ 穿越）
- 拒绝访问符号链接指向工作区外的文件

阶段 C：可通过配置 / ContextVar（execution_boundary_context）放开工作区外路径；
path_profile=notebook 时，allow_paths 与 allow_notebook_paths 任一为真即可放开。

阶段 2.5：validate_search_scope 供 Grep/Glob/基础搜索，受 allow_search_outside_workspace 与总闸约束。
"""
from pathlib import Path
from typing import Literal, Optional, Tuple

PathProfile = Literal["file", "notebook"]


class PathSecurityError(Exception):
    """路径安全违规异常"""
    pass


def _allow_outside_workspace(
    explicit: Optional[bool],
    path_profile: PathProfile,
) -> bool:
    if explicit is not None:
        return bool(explicit)
    from backend.tools.coding.foundation.core_mechanisms.execution_boundary_context import (
        get_effective_sub_allow,
    )

    if path_profile == "notebook":
        return get_effective_sub_allow("allow_paths_outside_workspace") or get_effective_sub_allow(
            "allow_notebook_paths_outside_workspace"
        )
    return get_effective_sub_allow("allow_paths_outside_workspace")


def get_workspace_root() -> Path:
    """
    获取工作区根目录。
    
    优先级：
    1. 环境变量 MIRO_WORKSPACE_ROOT
    2. miro_config.json 中的 workspace_root
    3. 当前工作目录
    """
    from backend.tools.coding.foundation.core_mechanisms.execution_boundary_context import (
        get_context_workspace_root,
    )

    context_root = get_context_workspace_root()
    if context_root:
        return Path(context_root).resolve()

    from backend.tools.coding.foundation.core_mechanisms.config import config
    return Path(config.workspace_root).resolve()


def validate_path(
    path: str,
    *,
    workspace_root: Optional[str] = None,
    allow_create: bool = True,
    must_exist: bool = False,
    allow_paths_outside_workspace: Optional[bool] = None,
    path_profile: PathProfile = "file",
) -> Tuple[Path, str]:
    """
    验证并规范化路径，默认限制在工作区内。
    
    Args:
        path: 用户提供的路径（相对或绝对）
        workspace_root: 工作区根（None 则自动获取）
        allow_create: 是否允许路径不存在（用于写操作）
        must_exist: 是否必须存在（用于读操作）
        allow_paths_outside_workspace: 显式覆盖；None 时读 execution_boundary 有效值
        path_profile: file 或 notebook（后者额外尊重 allow_notebook_paths_outside_workspace）
    
    Returns:
        (规范化的绝对路径, 相对于工作区的路径字符串)
    
    Raises:
        PathSecurityError: 路径安全违规
    """
    if not path or not isinstance(path, str):
        raise PathSecurityError(f"无效路径: {path!r}")
    
    # 获取工作区根
    if workspace_root:
        root = Path(workspace_root).resolve()
    else:
        root = get_workspace_root()
    
    # 规范化用户路径
    try:
        user_path = Path(path)
        
        # 如果是相对路径，相对于工作区根
        if not user_path.is_absolute():
            abs_path = (root / user_path).resolve()
        else:
            abs_path = user_path.resolve()
    except Exception as e:
        raise PathSecurityError(f"路径解析失败: {path} - {e}")

    allow_outside = _allow_outside_workspace(allow_paths_outside_workspace, path_profile)

    # 检查是否在工作区内（可配置 / 请求级放开）
    if not allow_outside:
        try:
            abs_path.relative_to(root)
        except ValueError:
            raise PathSecurityError(
                f"❌ 路径安全：拒绝访问工作区外的文件\n"
                f"  请求路径: {path}\n"
                f"  解析为: {abs_path}\n"
                f"  工作区根: {root}\n"
                f"  提示: 所有文件操作必须在工作区内"
            )

        # 检查符号链接
        if abs_path.is_symlink():
            real_path = abs_path.resolve()
            try:
                real_path.relative_to(root)
            except ValueError:
                raise PathSecurityError(
                    f"❌ 路径安全：符号链接指向工作区外\n"
                    f"  链接: {abs_path}\n"
                    f"  目标: {real_path}\n"
                    f"  工作区根: {root}"
                )
    
    # 检查存在性
    if must_exist and not abs_path.exists():
        raise PathSecurityError(f"路径不存在: {abs_path}")
    
    if not allow_create and not abs_path.exists():
        raise PathSecurityError(f"路径不存在且不允许创建: {abs_path}")
    
    # 返回绝对路径和相对路径字符串
    try:
        rel_str = str(abs_path.relative_to(root))
    except ValueError:
        rel_str = str(abs_path)
    
    return abs_path, rel_str


def validate_search_scope(
    path: str,
    *,
    must_exist: bool = True,
    allow_create: bool = False,
) -> Path:
    """
    Grep / Glob / search_in_files 的搜索根路径。
    受 allow_search_outside_workspace 与总闸（full_unrestricted）约束。
    """
    from backend.tools.coding.foundation.core_mechanisms.execution_boundary_context import (
        get_effective_sub_allow,
    )

    allow = get_effective_sub_allow("allow_search_outside_workspace")
    p, _ = validate_path(
        path,
        must_exist=must_exist,
        allow_create=allow_create,
        allow_paths_outside_workspace=allow,
    )
    return p


def safe_path_for_read(
    path: str,
    workspace_root: Optional[str] = None,
    *,
    allow_paths_outside_workspace: Optional[bool] = None,
    path_profile: PathProfile = "file",
) -> Path:
    """
    读操作的安全路径验证（必须存在）。
    
    Returns:
        规范化的绝对路径
    
    Raises:
        PathSecurityError: 路径安全违规或不存在
    """
    abs_path, _ = validate_path(
        path,
        workspace_root=workspace_root,
        allow_create=False,
        must_exist=True,
        allow_paths_outside_workspace=allow_paths_outside_workspace,
        path_profile=path_profile,
    )
    return abs_path


def safe_path_for_write(
    path: str,
    workspace_root: Optional[str] = None,
    *,
    allow_paths_outside_workspace: Optional[bool] = None,
    path_profile: PathProfile = "file",
) -> Path:
    """
    写操作的安全路径验证（允许不存在）。
    
    Returns:
        规范化的绝对路径
    
    Raises:
        PathSecurityError: 路径安全违规
    """
    root = Path(workspace_root).resolve() if workspace_root else get_workspace_root()
    raw_path = Path(path)
    if not raw_path.is_absolute():
        raw_path = root / raw_path
    if raw_path.is_symlink():
        raise PathSecurityError(f"❌ 路径安全：拒绝写入符号链接\n  链接: {raw_path}")

    abs_path, _ = validate_path(
        path,
        workspace_root=str(root),
        allow_create=True,
        must_exist=False,
        allow_paths_outside_workspace=allow_paths_outside_workspace,
        path_profile=path_profile,
    )
    if abs_path.is_symlink():
        raise PathSecurityError(f"❌ 路径安全：拒绝写入符号链接\n  链接: {abs_path}")
    return abs_path


def safe_path_for_delete(
    path: str,
    workspace_root: Optional[str] = None,
    *,
    allow_paths_outside_workspace: Optional[bool] = None,
    path_profile: PathProfile = "file",
) -> Path:
    """
    删除操作的安全路径验证（必须存在）。
    
    Returns:
        规范化的绝对路径
    
    Raises:
        PathSecurityError: 路径安全违规或不存在
    """
    abs_path, _ = validate_path(
        path,
        workspace_root=workspace_root,
        allow_create=False,
        must_exist=True,
        allow_paths_outside_workspace=allow_paths_outside_workspace,
        path_profile=path_profile,
    )
    return abs_path


__all__ = [
    "PathProfile",
    "PathSecurityError",
    "get_workspace_root",
    "validate_path",
    "validate_search_scope",
    "safe_path_for_read",
    "safe_path_for_write",
    "safe_path_for_delete",
]

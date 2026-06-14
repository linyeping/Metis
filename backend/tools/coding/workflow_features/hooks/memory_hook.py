"""Post-tool hook：根据工具结果更新项目记忆。

注册到 ``post_tool_hook`` 系统，在工具执行完成后自动
提取可持久化的信息到 ``WorkspaceMemory``。

不会对每次调用都触发 I/O，而是仅在特定条件下保存：
- ``generate_repo_map`` 首次执行 → 推断 project_type
- ``execute_bash_command`` 成功执行 → 记录常用命令
- ``read_file`` / ``grep_search`` → 提取 key_files
"""
from __future__ import annotations

import os
import logging
from typing import Any, Dict

logger = logging.getLogger(__name__)

# 需要记录的 shell 命令前缀（排除 echo、cat 等临时查看命令）
_USEFUL_CMD_PREFIXES = (
    "pytest", "python", "npm", "npx", "yarn", "pnpm",
    "pip", "git", "make", "cargo", "go ", "node ",
    "tsc", "eslint", "flake8", "mypy", "black",
    "docker", "kubectl",
)


def update_memory_from_tool_result(
    tool_name: str,
    kwargs: Dict[str, Any],
    result: str,
) -> None:
    """Post-tool hook 入口，根据工具结果更新项目记忆。"""
    workspace_root = _get_workspace_root()
    if not workspace_root:
        return

    try:
        _do_update(tool_name, kwargs, result, workspace_root)
    except Exception:
        # 非关键功能，静默失败
        logger.debug("memory_hook failed", exc_info=True)


def _do_update(
    tool_name: str,
    kwargs: Dict[str, Any],
    result: str,
    workspace_root: str,
) -> None:
    from backend.core.memory.workspace_memory import WorkspaceMemory

    # --- repo_map → 推断 project_type ---
    if tool_name == "generate_repo_map":
        memory = WorkspaceMemory.load(workspace_root)
        if not memory.project_type:
            ptype = _infer_project_type(result)
            if ptype:
                memory.set_project_type(ptype)
                memory.save()
        return

    # --- shell 命令 → 记录常用命令 ---
    if tool_name == "execute_bash_command":
        cmd = kwargs.get("command", "").strip()
        if not cmd:
            return
        # 只记录有意义的命令
        cmd_lower = cmd.lower()
        if any(cmd_lower.startswith(p) for p in _USEFUL_CMD_PREFIXES):
            memory = WorkspaceMemory.load(workspace_root)
            memory.add_common_command(cmd)
            memory.save()
        return

    # --- 文件读取 → 提取 key_files ---
    if tool_name == "read_file":
        path = kwargs.get("path", "") or kwargs.get("file_path", "")
        if path:
            # 只记录项目内文件（相对路径或在 workspace_root 下的绝对路径）
            if _is_project_file(path, workspace_root):
                memory = WorkspaceMemory.load(workspace_root)
                # 转为相对路径存储
                rel = _to_relative(path, workspace_root)
                if rel:
                    memory.add_key_file(rel)
                    memory.save()
        return


def _infer_project_type(repo_map_text: str) -> str:
    """从 repo_map 结果推断项目类型。"""
    has_py = ".py" in repo_map_text
    has_ts = ".ts" in repo_map_text or ".tsx" in repo_map_text
    has_js = ".js" in repo_map_text or ".jsx" in repo_map_text

    if has_py and (has_ts or has_js):
        return "mixed"
    if has_py:
        if "setup.py" in repo_map_text or "pyproject.toml" in repo_map_text:
            return "python-package"
        return "python"
    if has_ts:
        return "typescript"
    if has_js:
        return "javascript"
    return ""


def _get_workspace_root() -> str:
    """从环境获取当前 workspace root。"""
    return os.environ.get("METIS_WORKSPACE_ROOT", "")


def _is_project_file(path: str, workspace_root: str) -> bool:
    """判断路径是否属于当前项目。"""
    if not os.path.isabs(path):
        return True  # 相对路径默认视为项目内
    try:
        abs_path = os.path.abspath(path)
        abs_root = os.path.abspath(workspace_root)
        return abs_path.startswith(abs_root)
    except Exception:
        return False


def _to_relative(path: str, workspace_root: str) -> str:
    """将路径转为相对于 workspace_root 的形式。"""
    try:
        if os.path.isabs(path):
            return os.path.relpath(path, workspace_root).replace("\\", "/")
        return path.replace("\\", "/")
    except Exception:
        return path

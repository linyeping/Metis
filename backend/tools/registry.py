# -*- coding: utf-8 -*-
"""
Miro 工具注册表：全量 TOOLS_SCHEMA、AVAILABLE_TOOLS、execute_tool（C 与 Miro 参数别名 + C 风格工具名）。
"""
from __future__ import annotations

import inspect
import json
import os
from typing import Any, Callable, Dict, List, Optional, Set

from backend.tools.coding.foundation.core_mechanisms.log_config import logger
from backend.tools.schema_definitions import build_tools_schema

# --- 业务实现导入 ---
from backend.tools.coding.diagnosis.code_quality.analyze_complexity import analyze_complexity
from backend.tools.coding.diagnosis.errors.read_lints import read_lints
from backend.tools.coding.diagnosis.validation.undo_last_edit import undo_last_edit
from backend.tools.coding.diagnosis.validation.verify_compilation import verify_compilation
from backend.tools.coding.execution.package_manager.auto_install_package import auto_install_package
from backend.tools.coding.execution.runtime_manager import (
    check_dev_environment,
    install_dev_runtime,
    setup_workspace,
)
from backend.tools.coding.execution.version_control.check_git_status import check_git_status
from backend.tools.coding.execution.version_control.git_commit_pr import git_commit_pr
from backend.tools.coding.execution.version_control.git_workflow import (
    git_create_branch,
    git_diff,
    git_log,
    git_stage,
)
from backend.tools.coding.execution.test_runner import run_tests
from backend.tools.coding.file_system.append.append_to_file import append_to_file
from backend.tools.coding.file_system.create_overwrite.write_file import write_file
from backend.tools.coding.file_system.delete.delete_directory import delete_directory
from backend.tools.coding.file_system.delete.delete_file import delete_file
from backend.tools.coding.file_system.directory_ops.list_directory import list_directory
from backend.tools.coding.file_system.move_rename.rename_file_update_refs import rename_file_update_refs
from backend.tools.coding.foundation.cli.execute_shell import execute_bash_command
from backend.tools.coding.foundation.cli.manage_long_running import (
    list_long_running_processes,
    register_external_process,
    start_long_running_process,
    stop_long_running_process,
)
try:
    from backend.tools.coding.modify_refactor.modify_ast.edit_code_ast import editCode
except Exception as exc:  # pragma: no cover
    _edit_code_error_message = str(exc)

    def editCode(*_args: Any, **_kwargs: Any) -> str:
        return f"❌ editCode unavailable: {_edit_code_error_message}"
from backend.tools.coding.modify_refactor.modify_special.edit_notebook import edit_notebook
from backend.tools.coding.modify_refactor.modify_text.apply_patch import apply_patch
from backend.tools.coding.modify_refactor.modify_text.diff_preview import diff_preview
from backend.tools.coding.modify_refactor.modify_text.robust_replace import robust_replace_in_file
from backend.tools.coding.modify_refactor.modify_text.undo_edit import undo_edit
from backend.tools.coding.modify_refactor.refactor.extract_method import extract_method
from backend.tools.coding.modify_refactor.refactor.rename_symbol import rename_symbol
from backend.tools.coding.network_external.media.generate_image import generate_image
from backend.tools.coding.network_external.web.web_fetch import web_fetch
from backend.tools.coding.network_external.web.web_search import web_search
from backend.tools.browser_automation.tools import browse_and_extract, browse_web
from backend.tools.coding.read_search.read_analyze.generate_repo_map import generate_repo_map
from backend.tools.coding.read_search.read_analyze.read_terminal_state import read_terminal_state
from backend.tools.coding.read_search.read_multiple.read_multiple_files import read_multiple_files
from backend.tools.coding.read_search.read_single.read_file import read_file, read_file_chunk
from backend.tools.coding.read_search.search.glob_search import glob_search
from backend.tools.coding.read_search.search.grep_search import grep_search
from backend.tools.coding.read_search.search.search_basic import search_in_files
from backend.tools.coding.read_search.search.semantic_search import semantic_search
from backend.tools.coding.user_interaction.context.populate_steering import populate_steering
from backend.tools.coding.user_interaction.dialog.ask_question import ask_question
from backend.tools.coding.workflow_features.agent_state.switch_mode import switch_mode
from backend.tools.coding.workflow_features.agent_state.todo_write import todo_write
from backend.tools.coding.workflow_features.agent_state.update_project_memory import (
    read_project_memory,
    read_workspace_memory,
    update_project_memory,
)
from backend.tools.coding.workflow_features.agent_state.write_open_files_context import write_open_files_context
from backend.tools.coding.workflow_features.special_powers.load_workflow_guidelines import load_workflow_guidelines
from backend.tools.coding.workflow_features.special_powers.manage_mcp_servers import manage_mcp_servers
from backend.tools.coding.workflow_features.subagents.custom_agent_creator import custom_agent_creator
from backend.tools.coding.workflow_features.subagents.delegate_best_of_n import delegate_best_of_n
from backend.tools.coding.workflow_features.subagents.delegate_browser import delegate_browser
from backend.tools.coding.workflow_features.subagents.delegate_explore import delegate_explore
from backend.tools.coding.workflow_features.subagents.delegate_shell import delegate_shell
from backend.tools.coding.workflow_features.subagents.summon_context_gatherer import summon_context_gatherer
from backend.tools.coding.workflow_features.subagents.task_dispatch import task_dispatch
from backend.tools.coding.workflow_features.subagents.run_parallel_tasks import run_parallel_tasks
from backend.tools.coding.workflow_features.subagents.run_task_graph import run_task_graph

try:
    from backend.tools.coding.workflow_features.hooks.post_tool_hook import post_tool_hook
    from backend.tools.coding.workflow_features.hooks.pre_tool_hook import pre_tool_hook
except Exception:  # pragma: no cover

    def pre_tool_hook(*_a, **_k):  # type: ignore
        pass

    def post_tool_hook(*_a, **_k):  # type: ignore
        pass


TOOLS_SCHEMA: List[Dict[str, Any]] = build_tools_schema()

# C 风格 function.name -> 实现函数名（OpenAI 要求唯一名时可只用右侧；此处支持两侧调用）
TOOL_NAME_ALIASES: Dict[str, str] = {
    "Read": "read_file",
    "Shell": "execute_bash_command",
    "Glob": "glob_search",
    "Grep": "grep_search",
    "Write": "write_file",
    "StrReplace": "robust_replace_in_file",
    "Delete": "delete_file",
    "EditNotebook": "edit_notebook",
    "SemanticSearch": "semantic_search",
    "WebSearch": "web_search",
    "WebFetch": "web_fetch",
    "GenerateImage": "generate_image",
    "AskQuestion": "ask_question",
    "TodoWrite": "todo_write",
    "ReadLints": "read_lints",
    "SwitchMode": "switch_mode",
    "Task": "task_dispatch",
    "ApplyPatch": "apply_patch",
}

WRITE_LIKE_TOOLS: Set[str] = {
    "write_file",
    "append_to_file",
    "update_project_memory",
    "robust_replace_in_file",
    "apply_patch",
    "editCode",
    "edit_notebook",
    "rename_file_update_refs",
    "delete_file",
    "delete_directory",
}

AVAILABLE_TOOLS: Dict[str, Callable[..., str]] = {
    "execute_bash_command": execute_bash_command,
    "start_long_running_process": start_long_running_process,
    "stop_long_running_process": stop_long_running_process,
    "list_long_running_processes": list_long_running_processes,
    "register_external_process": register_external_process,
    "write_file": write_file,
    "append_to_file": append_to_file,
    "delete_file": delete_file,
    "delete_directory": delete_directory,
    "rename_file_update_refs": rename_file_update_refs,
    "list_directory": list_directory,
    "read_file": read_file,
    "read_file_chunk": read_file_chunk,
    "read_multiple_files": read_multiple_files,
    "generate_repo_map": generate_repo_map,
    "read_terminal_state": read_terminal_state,
    "search_in_files": search_in_files,
    "grep_search": grep_search,
    "glob_search": glob_search,
    "semantic_search": semantic_search,
    "robust_replace_in_file": robust_replace_in_file,
    "apply_patch": apply_patch,
    "diff_preview": diff_preview,
    "undo_edit": undo_edit,
    "editCode": editCode,
    "edit_notebook": edit_notebook,
    "rename_symbol": rename_symbol,
    "extract_method": extract_method,
    "auto_install_package": auto_install_package,
    "check_dev_environment": check_dev_environment,
    "install_dev_runtime": install_dev_runtime,
    "setup_workspace": setup_workspace,
    "check_git_status": check_git_status,
    "git_commit_pr": git_commit_pr,
    "git_diff": git_diff,
    "git_stage": git_stage,
    "git_create_branch": git_create_branch,
    "git_log": git_log,
    "run_tests": run_tests,
    "read_lints": read_lints,
    "analyze_complexity": analyze_complexity,
    "undo_last_edit": undo_last_edit,
    "verify_compilation": verify_compilation,
    "web_search": web_search,
    "web_fetch": web_fetch,
    "browse_web": browse_web,
    "browse_and_extract": browse_and_extract,
    "generate_image": generate_image,
    "ask_question": ask_question,
    "populate_steering": populate_steering,
    "todo_write": todo_write,
    "read_project_memory": read_project_memory,
    "read_workspace_memory": read_workspace_memory,
    "update_project_memory": update_project_memory,
    "write_open_files_context": write_open_files_context,
    "switch_mode": switch_mode,
    "delegate_explore": delegate_explore,
    "delegate_browser": delegate_browser,
    "delegate_shell": delegate_shell,
    "delegate_best_of_n": delegate_best_of_n,
    "summon_context_gatherer": summon_context_gatherer,
    "custom_agent_creator": custom_agent_creator,
    "task_dispatch": task_dispatch,
    "run_parallel_tasks": run_parallel_tasks,
    "run_task_graph": run_task_graph,
    "manage_mcp_servers": manage_mcp_servers,
    "load_workflow_guidelines": load_workflow_guidelines,
}


def canonical_tool_name(name: str) -> str:
    return TOOL_NAME_ALIASES.get(name, name)


def get_tool_names() -> List[str]:
    return sorted(AVAILABLE_TOOLS.keys())


def _filter_kwargs(fn: Callable[..., Any], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    try:
        sig = inspect.signature(fn)
        params = sig.parameters
    except (TypeError, ValueError):
        return kwargs
    out: Dict[str, Any] = {}
    for k, v in kwargs.items():
        if k in params:
            p = params[k]
            if p.kind in (
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                inspect.Parameter.KEYWORD_ONLY,
            ):
                out[k] = v
    return out


def normalize_tool_kwargs(canonical: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    """将 C / 模型习惯参数名映射为 Python 实现形参。"""
    kw = dict(raw)

    if canonical == "read_file":
        if "path" in kw and "file_path" not in kw:
            kw["file_path"] = kw.pop("path")
        off = kw.pop("offset", None)
        lim = kw.pop("limit", None)
        if off is not None:
            kw.setdefault("start_line", int(off))
            if lim is not None:
                kw["end_line"] = int(off) + int(lim) - 1

    elif canonical == "write_file":
        if "path" in kw and "file_path" not in kw:
            kw["file_path"] = kw.pop("path")
        if "contents" in kw and "content" not in kw:
            kw["content"] = kw.pop("contents")

    elif canonical == "delete_file":
        if "file_path" in kw and "path" not in kw:
            kw["path"] = kw.pop("file_path")

    elif canonical == "robust_replace_in_file":
        if "path" in kw and "file_path" not in kw:
            kw["file_path"] = kw.pop("path")
        if "old_string" in kw and "search_text" not in kw:
            kw["search_text"] = kw.pop("old_string")
        if "new_string" in kw and "replace_text" not in kw:
            kw["replace_text"] = kw.pop("new_string")

    elif canonical == "glob_search":
        if "glob_pattern" in kw and "pattern" not in kw:
            kw["pattern"] = kw.pop("glob_pattern")
        if "target_directory" in kw and "root" not in kw:
            kw["root"] = kw.pop("target_directory")

    elif canonical == "grep_search":
        if "glob" in kw and "glob_pattern" not in kw:
            kw["glob_pattern"] = kw.pop("glob")
        # max_results 与 head_limit：实现里 head_limit 覆盖 max_results
        if "head_limit" in kw and "max_results" not in kw:
            kw["max_results"] = kw["head_limit"]

    elif canonical == "semantic_search":
        if "num_results" in kw and "top_k" not in kw:
            kw["top_k"] = int(kw.pop("num_results"))
        td = kw.get("target_directories")
        if "workspace_root" not in kw and td:
            if isinstance(td, str) and td and td.strip():
                kw["workspace_root"] = td
            elif isinstance(td, list) and len(td) > 0:
                kw["workspace_root"] = str(td[0])

    elif canonical == "web_search":
        if "search_term" in kw and "query" not in kw:
            kw["query"] = kw.pop("search_term")

    elif canonical == "generate_image":
        if "description" in kw and "prompt" not in kw:
            kw["prompt"] = kw.pop("description")

    elif canonical == "execute_bash_command":
        if "working_directory" in kw and "cwd" not in kw:
            kw["cwd"] = kw.pop("working_directory")
        bms = kw.pop("block_until_ms", None)
        if bms is not None:
            sec = max(1, int(int(bms) / 1000))
            kw["timeout"] = sec

    elif canonical == "read_lints":
        p = kw.get("paths", ".")
        if isinstance(p, list):
            kw["paths"] = ",".join(str(x) for x in p)

    elif canonical == "read_multiple_files":
        if "paths" in kw and "file_paths" not in kw:
            kw["file_paths"] = kw.pop("paths")

    elif canonical == "edit_notebook":
        if "target_notebook" in kw and "path" not in kw:
            kw["path"] = kw.pop("target_notebook")

    elif canonical == "todo_write":
        if "todo_storage_path" in kw and "path" not in kw:
            kw["path"] = kw.pop("todo_storage_path")

    elif canonical == "write_open_files_context":
        if "open_files_storage_path" in kw and "path" not in kw:
            kw["path"] = kw.pop("open_files_storage_path")

    elif canonical == "ask_question":
        q = kw.get("questions")
        if isinstance(q, str):
            try:
                kw["questions"] = json.loads(q)
            except json.JSONDecodeError:
                pass

    return kw


def _post_edit_lint_enabled() -> bool:
    from backend.tools.coding.foundation.core_mechanisms.config import config
    return config.post_edit_lint


def _lint_target_path(canonical: str, call_kw: Dict[str, Any]) -> Optional[str]:
    if canonical == "edit_notebook":
        return call_kw.get("path")
    if canonical in ("apply_patch", "extract_method"):
        return None
    if canonical == "rename_file_update_refs":
        return call_kw.get("new_path") or call_kw.get("old_path")
    return call_kw.get("file_path") or call_kw.get("path")


def execute_tool(tool_name: str, **kwargs: Any) -> str:
    """
    统一执行入口：工具别名、参数别名、pre/post hook、异常吞掉为可读字符串。
    """
    canonical = canonical_tool_name(tool_name)

    def _finalize(result: str) -> str:
        try:
            from backend.core.engine.attempt_ledger import finalize_tool_result

            return finalize_tool_result(canonical, result)
        except ImportError:
            return result

    if canonical not in AVAILABLE_TOOLS:
        known = ", ".join(sorted(set(AVAILABLE_TOOLS) | set(TOOL_NAME_ALIASES)))
        return _finalize(f"❌ 未知工具: {tool_name}\n可用: {known}")

    fn = AVAILABLE_TOOLS[canonical]
    try:
        normalized = normalize_tool_kwargs(canonical, kwargs)
        pre_tool_hook(canonical, normalized)
        call_kw = _filter_kwargs(fn, normalized)
        result = fn(**call_kw)
        out = result if isinstance(result, str) else str(result)
        if _post_edit_lint_enabled() and canonical in WRITE_LIKE_TOOLS:
            lp = _lint_target_path(canonical, call_kw)
            if lp and str(lp).endswith(".py") and os.path.isfile(str(lp)):
                try:
                    lr = read_lints(str(lp), max_output=4000)
                    out = f"{out}\n\n--- read_lints (post-edit) ---\n{lr}"
                except Exception:
                    pass
        out = post_tool_hook(canonical, normalized, out)
        return _finalize(out)
    except TypeError as e:
        logger.exception("工具参数错误: %s", canonical)
        return _finalize(f"❌ 参数错误 ({canonical}): {e}\n传入键: {list(kwargs.keys())}")
    except Exception as e:
        logger.exception("工具执行异常: %s", canonical)
        return _finalize(f"❌ 工具执行异常 ({canonical}): {e}")


def is_write_like_tool(name: str) -> bool:
    return canonical_tool_name(name) in WRITE_LIKE_TOOLS


__all__ = [
    "TOOLS_SCHEMA",
    "AVAILABLE_TOOLS",
    "TOOL_NAME_ALIASES",
    "WRITE_LIKE_TOOLS",
    "build_tools_schema",
    "canonical_tool_name",
    "execute_tool",
    "get_tool_names",
    "is_write_like_tool",
    "normalize_tool_kwargs",
]


# --- 实时语义索引更新（自动注册） ---
try:
    from backend.tools.coding.read_search.search.semantic_realtime import register_realtime_index_hook
    register_realtime_index_hook()
except Exception:
    pass  # 静默失败，不影响核心功能

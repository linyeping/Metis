"""Tool profile inference for Metis runtime tools."""

from __future__ import annotations

from .tool_contract import ToolName, ToolProfile


SAFE_TOOLS: frozenset[str] = frozenset(
    {
        "read_file",
        "read_file_chunk",
        "read_multiple_files",
        "list_directory",
        "generate_repo_map",
        "read_terminal_state",
        "grep_search",
        "glob_search",
        "search_in_files",
        "search_in_file",
        "search_in_codebase",
        "semantic_search",
        "find_files",
        "ast_search_code",
        "check_git_status",
        "git_diff",
        "git_log",
        "web_search",
        "web_fetch",
        "browse_web",
        "browse_and_extract",
        "read_project_memory",
        "read_workspace_memory",
        "load_skill",
        "check_dev_environment",
        "desktop_screenshot",
        "desktop_inventory",
    }
)


DESTRUCTIVE_TOOLS: frozenset[str] = frozenset(
    {
        "execute_bash_command",
        "start_long_running_process",
        "stop_long_running_process",
        "write_file",
        "append_to_file",
        "delete_file",
        "delete_directory",
        "rename_file_update_refs",
        "robust_replace_in_file",
        "apply_patch",
        "editCode",
        "edit_code_ast",
        "edit_notebook",
        "git_stage",
        "git_commit_pr",
        "auto_install_package",
        "install_dev_runtime",
        "setup_workspace",
        "manage_mcp_servers",
        "desktop_action",
        "desktop_vision_task",
        "update_project_memory",
    }
)


def is_safe_tool(name: str) -> bool:
    return str(name) in SAFE_TOOLS


def is_destructive_tool(name: str) -> bool:
    return str(name) in DESTRUCTIVE_TOOLS


def infer_toolset(name: str, source: str = "", description: str = "") -> str:
    tool = str(name or "")
    src = str(source or "")
    text = f"{tool} {description or ''}".lower()
    if src == "mcp" or src.startswith("mcp:"):
        return "mcp"
    if src == "plugin":
        return "plugin"
    if src == "desktop" or tool.startswith("desktop_"):
        return "desktop"
    if tool in {
        "read_file",
        "read_file_chunk",
        "read_multiple_files",
        "list_directory",
        "write_file",
        "append_to_file",
        "delete_file",
        "delete_directory",
        "rename_file_update_refs",
        "robust_replace_in_file",
        "apply_patch",
        "editCode",
        "edit_code_ast",
        "edit_notebook",
        "diff_preview",
        "undo_edit",
    }:
        return "filesystem"
    if tool in {
        "grep_search",
        "glob_search",
        "search_in_files",
        "search_in_file",
        "search_in_codebase",
        "semantic_search",
        "find_files",
        "ast_search_code",
        "generate_repo_map",
    }:
        return "search"
    if tool in {
        "execute_bash_command",
        "start_long_running_process",
        "stop_long_running_process",
        "list_long_running_processes",
        "register_external_process",
        "read_terminal_state",
    }:
        return "shell"
    if tool.startswith("git_") or tool in {"check_git_status"}:
        return "git"
    if tool in {"check_dev_environment", "install_dev_runtime", "setup_workspace"}:
        return "environment"
    if tool.startswith("web_") or tool.startswith("browse_") or "web" in text:
        return "web"
    if tool in {
        "read_project_memory",
        "read_workspace_memory",
        "load_skill",
        "update_project_memory",
        "todo_write",
        "write_open_files_context",
        "populate_steering",
    }:
        return "memory"
    if (
        tool.startswith("delegate_")
        or tool in {
            "task_dispatch",
            "run_parallel_tasks",
            "run_task_graph",
            "ask_question",
            "switch_mode",
            "custom_agent_creator",
            "summon_context_gatherer",
            "load_workflow_guidelines",
        }
    ):
        return "workflow"
    return "misc"


def infer_tool_profile(
    name: str,
    *,
    canonical_name: str = "",
    source: str = "builtin",
    description: str = "",
    available: bool = True,
) -> ToolProfile:
    canonical = str(canonical_name or name)
    destructive = is_destructive_tool(canonical)
    approval = "never" if is_safe_tool(canonical) else "always" if destructive else "mode"
    return ToolProfile(
        name=ToolName(str(name)),
        canonical_name=ToolName(canonical),
        description=str(description or ""),
        source=str(source or "builtin"),
        toolset=infer_toolset(canonical, source=source, description=description),
        available=bool(available),
        approval=approval,
        destructive=destructive,
    )

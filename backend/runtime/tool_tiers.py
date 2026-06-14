"""Tool tiers for model capability adaptation."""
from __future__ import annotations

from typing import Optional, Set


TIER_3_TOOLS: Set[str] = {
    "read_file",
    "write_file",
    "robust_replace_in_file",
    "grep_search",
    "glob_search",
    "list_directory",
    "execute_bash_command",
    "ask_question",
    "todo_write",
    "load_skill",
    "switch_mode",
    "delete_file",
    "append_to_file",
    "read_lints",
    "verify_compilation",
    "task_dispatch",
}

TIER_2_TOOLS: Set[str] = TIER_3_TOOLS | {
    "semantic_search",
    "generate_repo_map",
    "rename_symbol",
    "extract_method",
    "edit_code_ast",
    "rename_file_update_refs",
    "read_multiple_files",
    "check_git_status",
    "git_commit_pr",
    "git_workflow",
    "web_search",
    "web_fetch",
    "browse_web",
    "browse_and_extract",
    "auto_install_package",
    "check_dev_environment",
    "install_dev_runtime",
    "setup_workspace",
    "manage_long_running",
    "analyze_complexity",
    "edit_notebook",
    "load_workflow_guidelines",
}


def tools_for_tier(tier: int) -> Optional[Set[str]]:
    """Return allowed tool names for a tier, or None for all tools."""
    if tier <= 1:
        return None
    if tier == 2:
        return set(TIER_2_TOOLS)
    return set(TIER_3_TOOLS)

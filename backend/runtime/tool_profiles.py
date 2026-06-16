from __future__ import annotations

from typing import Optional


LEAN_PROFILE = frozenset(
    {
        "read_file",
        "list_directory",
        "grep_search",
        "glob_search",
        "write_file",
        "robust_replace_in_file",
        "execute_bash_command",
        "run_tests",
        "generate_repo_map",
        "check_git_status",
        "git_diff",
        "todo_write",
        "load_skill",
        "delegate_explore",
        "delegate_shell",
        "web_fetch",
        "browse_web",
        "browse_and_extract",
        "web_search",
        "desktop_screenshot",
        "desktop_action",
        "preview_browser_status",
        "preview_browser_navigate",
        "preview_browser_observe",
        "preview_browser_action",
        "preview_browser_screenshot",
        "preview_browser_verify",
    }
)

PROFILE_NAMES = {"lean", "full"}


def normalize_tool_profile(profile: str) -> str:
    value = str(profile or "").strip().lower()
    if value in PROFILE_NAMES:
        return value
    return "lean"


def tool_names_for_profile(profile: str, *, include_desktop: bool = True) -> Optional[frozenset[str]]:
    normalized = normalize_tool_profile(profile)
    if normalized == "full":
        return None
    names = set(LEAN_PROFILE)
    if not include_desktop:
        names.difference_update({"desktop_screenshot", "desktop_action"})
    return frozenset(names)

from __future__ import annotations


SUBAGENT_RESULT_LIMIT = 1500

EXPLORE_SYSTEM_PROMPT = """You are an exploration sub-agent.
Use only read/search/navigation tools. Do not modify files.
Return only a concise conclusion report: relevant files, key symbols, and the answer.
Do not narrate every search step. Keep the final answer under 1500 characters."""

SHELL_SYSTEM_PROMPT = """You are a shell diagnostic sub-agent.
Use shell plus read/search tools to run the requested check or collect diagnostic evidence.
Avoid destructive commands. Return the useful command result, conclusion, and any next action.
Do not narrate every attempt. Keep the final answer under 1500 characters."""

EXPLORE_TOOLS = [
    "glob_search",
    "grep_search",
    "read_file",
    "generate_repo_map",
    "list_directory",
]

SHELL_TOOLS = [
    "execute_bash_command",
    "read_file",
    "glob_search",
    "grep_search",
    "list_directory",
]


def compress_subagent_result(text: str, *, limit: int = SUBAGENT_RESULT_LIMIT) -> str:
    raw = str(text or "").strip()
    if len(raw) <= limit:
        return raw
    return raw[: max(0, limit - 80)].rstrip() + "\n[Sub-agent result truncated to contract limit.]"

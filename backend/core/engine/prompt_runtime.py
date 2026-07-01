"""Prompt runtime assembly for Metis."""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class PromptRuntimeLayer:
    """单个运行时注入层，按稳定性排序以保护 LLM 前缀缓存。"""

    name: str
    content: str
    source: str
    stability: str = "request"


@dataclass(frozen=True)
class PromptRuntimeSnapshot:
    """一次编译后的 prompt runtime 快照。"""

    base_system_prompt: str
    layers: List[PromptRuntimeLayer]
    final_system_prompt: str
    workspace_root: Optional[str]

    def layer_names(self) -> List[str]:
        return [layer.name for layer in self.layers]


_RECENCY_REINFORCEMENT = (
    "\n\n---\nRemember: (1) Read before you write. "
    "(2) Verify after you change. (3) Ask when uncertain.\n"
)
_VERBOSE_TIER_GUIDANCE = (
    "\n\n---\n[Metis execution workflow]\n"
    "1. Read the relevant files before editing.\n"
    "2. Search when exact context is unclear.\n"
    "3. Make the smallest correct change.\n"
    "4. Verify with tests, lint, or type checks.\n"
    "5. If requirements are unclear, ask instead of inventing.\n"
)
_EFFICIENCY_RULES = (
    "\n\n---\n[Efficiency Rules - strictly follow to reduce steps and context usage]\n"
    "1. Parallel tool calls MUST be issued in a single message, never one by one.\n"
    "   Example: need 3 files -> issue 3 read_file calls in one turn.\n"
    "2. Use diff-mode for file edits (old_string->new_string), never rewrite entire files.\n"
    "3. Do NOT re-read files after editing - the tool reports errors if it fails.\n"
    "4. Check if file content is already in context before reading it again.\n"
    "5. Combine shell commands with && into a single execution.\n"
    "6. Plan first (list all steps), then execute in batch. No trial-and-error.\n"
    "7. When creating new files, provide complete content in one call.\n"
)
_STRONG_MODEL_DIRECTIVE = (
    "\n\n---\n[High-Agency Execution — you are a capable model; minimize round-trips]\n"
    "- Batch reconnaissance: in ONE turn, fire every independent read_file / grep_search / "
    "glob_search you already know you need. Reading files one-at-a-time across turns is the "
    "single biggest way you waste turns — do not do it.\n"
    "- Act in large, complete steps: apply the whole edit at once (a multi-hunk apply_patch, or "
    "several edits in the same turn). Do not dribble one small change per turn.\n"
    "- Decide and do. Skip narrating intentions and re-exploring what is already in context.\n"
    "- Calibrate effort: a one-line fix should take a handful of turns, not a dozen.\n"
)
_WEB_STRATEGY_RULES = (
    "\n\n---\n[Web Tool Strategy]\n"
    "1. Use fetch_content first for known URLs, docs, titles, article text, GitHub blobs/repos/trees/commits, and static news pages; it returns cleaned Markdown plus structured source metadata.\n"
    "2. Use web_search for cheap discovery and web_research for multi-source evidence, disputed facts, or source-backed reports.\n"
    "3. Use browse_web only when fetch_content looks incomplete, the page is a JavaScript app, or the task needs clicks, forms, login state, or visual browser interaction.\n"
    "4. Use legacy web_fetch(raw=true) only when raw HTML itself is the target.\n"
)
_USER_MEMORY_TOKEN_BUDGET = 1000
_STABILITY_ORDER = {"static": 0, "session": 1, "request": 2}
_LAYER_ORDER = {
    "tool_strategy_hint": 0,
    "desk_automation_skill": 1,
    "mode_router_hint": 2,
    "workflow_guidelines_hint": 3,
    "loop_discipline": 4,
    "efficiency_rules": 5,
    "strong_model_directive": 6,
    "web_strategy": 7,
    "recency_reinforcement": 8,
    "workspace_hint": 10,
    "skills_index": 10,
    "repo_map_hint": 11,
    "project_profile": 12,
    "workspace_memory": 12,
    "user_memory": 13,
    "agent_state_hint": 20,
    "open_files_hint": 21,
    "terminal_hint": 22,
}


def _env_flag(name: str, default: str = "1") -> bool:
    value = os.environ.get(name, default).strip().lower()
    return value not in ("0", "false", "no", "off")


def _workspace_hint_enabled() -> bool:
    return _env_flag("MIRO_CONTEXT_WORKSPACE_HINT")


def _agent_state_hint_enabled() -> bool:
    return _env_flag("MIRO_CONTEXT_AGENT_STATE_HINT")


def _terminal_hint_enabled() -> bool:
    return _env_flag("MIRO_CONTEXT_TERMINAL_HINT")


def _open_files_hint_enabled() -> bool:
    return _env_flag("MIRO_CONTEXT_OPEN_FILES_HINT")


def _repo_map_hint_enabled() -> bool:
    return _env_flag("MIRO_CONTEXT_REPO_MAP_HINT", default="1")


def _skills_index_enabled() -> bool:
    return _env_flag("METIS_CONTEXT_SKILLS_INDEX", default="1")


def _sort_layers_for_prefix_cache(layers: List[PromptRuntimeLayer]) -> List[PromptRuntimeLayer]:
    indexed_layers = list(enumerate(layers))
    indexed_layers.sort(
        key=lambda item: (
            _STABILITY_ORDER.get(item[1].stability, _STABILITY_ORDER["request"]),
            _LAYER_ORDER.get(item[1].name, 100),
            item[0],
        )
    )
    return [layer for _index, layer in indexed_layers]


def _mode_router_hint_enabled() -> bool:
    return _env_flag("MIRO_CONTEXT_MODE_ROUTER_HINT")


def _tool_strategy_hint_enabled() -> bool:
    return _env_flag("MIRO_CONTEXT_TOOL_STRATEGY_HINT")


def _workspace_memory_hint_enabled() -> bool:
    return _env_flag("MIRO_CONTEXT_WORKSPACE_MEMORY_HINT")


def _project_profile_enabled() -> bool:
    return _env_flag("METIS_CONTEXT_PROJECT_PROFILE", default="1")


def _desk_skill_hint_enabled() -> bool:
    """SKILL.md 注入：默认开启，但仅在桌面自动化实际可用时生效。"""
    if not _env_flag("MIRO_CONTEXT_DESK_SKILL"):
        return False
    if os.environ.get("METIS_DISABLE_DESKTOP_TOOLS", "").strip().lower() in ("1", "true", "yes"):
        return False
    try:
        from backend.tools.desk_automation.config import is_enabled
        return is_enabled()
    except Exception:
        return False


def _workflow_hint_enabled() -> bool:
    from .workflow_context import workflow_guidelines_enabled

    return workflow_guidelines_enabled()


def resolve_runtime_flags(
    *,
    include_workspace_hint: Optional[bool] = None,
    include_agent_state_hint: Optional[bool] = None,
    include_open_files_hint: Optional[bool] = None,
    include_terminal_hint: Optional[bool] = None,
    include_mode_router_hint: Optional[bool] = None,
    include_workflow_hint: Optional[bool] = None,
    include_repo_map_hint: Optional[bool] = None,
    include_desk_skill: Optional[bool] = None,
    include_skills_index: Optional[bool] = None,
    include_tool_strategy_hint: Optional[bool] = None,
    include_project_profile: Optional[bool] = None,
    include_workspace_memory_hint: Optional[bool] = None,
) -> Dict[str, bool]:
    """解析显式入参与环境变量，得到本次 runtime 的开关。"""
    return {
        "include_workspace_hint": (
            _workspace_hint_enabled()
            if include_workspace_hint is None
            else include_workspace_hint
        ),
        "include_agent_state_hint": (
            _agent_state_hint_enabled()
            if include_agent_state_hint is None
            else include_agent_state_hint
        ),
        "include_open_files_hint": (
            _open_files_hint_enabled()
            if include_open_files_hint is None
            else include_open_files_hint
        ),
        "include_terminal_hint": (
            _terminal_hint_enabled()
            if include_terminal_hint is None
            else include_terminal_hint
        ),
        "include_mode_router_hint": (
            _mode_router_hint_enabled()
            if include_mode_router_hint is None
            else include_mode_router_hint
        ),
        "include_workflow_hint": (
            _workflow_hint_enabled()
            if include_workflow_hint is None
            else include_workflow_hint
        ),
        "include_repo_map_hint": (
            _repo_map_hint_enabled()
            if include_repo_map_hint is None
            else include_repo_map_hint
        ),
        "include_desk_skill": (
            _desk_skill_hint_enabled()
            if include_desk_skill is None
            else include_desk_skill
        ),
        "include_skills_index": (
            _skills_index_enabled()
            if include_skills_index is None
            else include_skills_index
        ),
        "include_tool_strategy_hint": (
            _tool_strategy_hint_enabled()
            if include_tool_strategy_hint is None
            else include_tool_strategy_hint
        ),
        "include_project_profile": (
            _project_profile_enabled()
            if include_project_profile is None
            else include_project_profile
        ),
        "include_workspace_memory_hint": (
            _workspace_memory_hint_enabled()
            if include_workspace_memory_hint is None
            else include_workspace_memory_hint
        ),
    }


def _build_workspace_block(workspace_root: Optional[str]) -> str:
    if not workspace_root:
        return ""
    return (
        "\n\n---\n[Metis workspace]\ncwd (absolute): "
        + os.path.abspath(workspace_root)
        + "\n"
    )


def _approx_tokens(text: str) -> int:
    return len(text) // 4


def _truncate_user_memory(text: str) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    max_chars = _USER_MEMORY_TOKEN_BUDGET * 4
    if len(raw) <= max_chars:
        return raw
    return (
        raw[:max_chars].rstrip()
        + "\n\n[User METIS.md truncated to 1000 tokens. Full content available via read_file.]"
    )


def _lean_prompt(base_system_prompt: str) -> str:
    text = str(base_system_prompt or "").strip()
    if _approx_tokens(text) <= 1800:
        return text
    return text[:7200].rstrip() + "\n\n[Lean prompt mode: reference material trimmed for a strong model.]"


def _verbose_prompt(base_system_prompt: str) -> str:
    return str(base_system_prompt or "").rstrip() + _VERBOSE_TIER_GUIDANCE


def compile_prompt_runtime(
    base_system_prompt: str,
    *,
    user_memory_text: str = "",
    model_tier: int = 2,
    model_context_window: Optional[int] = None,
    workspace_root: Optional[str] = None,
    include_workspace_hint: Optional[bool] = None,
    include_agent_state_hint: Optional[bool] = None,
    include_open_files_hint: Optional[bool] = None,
    include_terminal_hint: Optional[bool] = None,
    include_mode_router_hint: Optional[bool] = None,
    include_workflow_hint: Optional[bool] = None,
    include_repo_map_hint: Optional[bool] = None,
    include_desk_skill: Optional[bool] = None,
    include_skills_index: Optional[bool] = None,
    include_tool_strategy_hint: Optional[bool] = None,
    include_project_profile: Optional[bool] = None,
    include_workspace_memory_hint: Optional[bool] = None,
) -> PromptRuntimeSnapshot:
    """
    将 system prompt 与运行时上下文编译为单一 system message。

    运行时层顺序遵循“越稳定越靠前”：
    1. base_system_prompt（按模型 tier 做轻量裁剪或补强）
    2. static: tool strategy / desktop skill / mode / workflow / fixed rules
    3. session: workspace hint / repo map / project profile / workspace memory / user memory
    4. request: agent state / open files / terminal snapshots
    """
    flags = resolve_runtime_flags(
        include_workspace_hint=include_workspace_hint,
        include_agent_state_hint=include_agent_state_hint,
        include_open_files_hint=include_open_files_hint,
        include_terminal_hint=include_terminal_hint,
        include_mode_router_hint=include_mode_router_hint,
        include_workflow_hint=include_workflow_hint,
        include_repo_map_hint=include_repo_map_hint,
        include_desk_skill=include_desk_skill,
        include_skills_index=include_skills_index,
        include_tool_strategy_hint=include_tool_strategy_hint,
        include_project_profile=include_project_profile,
        include_workspace_memory_hint=include_workspace_memory_hint,
    )
    layers: List[PromptRuntimeLayer] = []

    if flags["include_workspace_hint"] and workspace_root:
        block = _build_workspace_block(workspace_root)
        if block:
            layers.append(
                PromptRuntimeLayer(
                    name="workspace_hint",
                    content=block,
                    source="engine.prompt_runtime",
                    stability="session",
                )
            )

    if flags["include_skills_index"]:
        try:
            from backend.runtime.skill_loader import build_skills_index

            skills_block = build_skills_index(
                workspace_root=workspace_root or "",
                context_window=model_context_window or 128_000,
            )
            if skills_block:
                layers.append(
                    PromptRuntimeLayer(
                        name="skills_index",
                        content=skills_block,
                        source="runtime.skill_loader",
                        stability="session",
                    )
                )
        except Exception:
            pass

    if flags["include_repo_map_hint"] and workspace_root:
        try:
            from backend.tools.coding.foundation.repo_map import generate_repo_map as _gen_map
            map_text = _gen_map(workspace_root=workspace_root, max_tokens=4000)
            if map_text and not map_text.startswith("("):
                block = (
                    "\n\n---\n[Repo Map — project structure with signatures]\n"
                    + map_text
                    + "\n"
                )
                layers.append(
                    PromptRuntimeLayer(
                        name="repo_map_hint",
                        content=block,
                        source="tools.foundation.repo_map",
                        stability="session",
                    )
                )
        except Exception:
            pass  # tree-sitter not available — skip silently

    if flags["include_desk_skill"]:
        try:
            from backend.tools.desk_automation import load_skill_prompt

            skill_text = load_skill_prompt()
            if skill_text:
                block = (
                    "\n\n---\n[Desktop Automation Skill Reference]\n"
                    + skill_text
                    + "\n"
                )
                layers.append(
                    PromptRuntimeLayer(
                        name="desk_automation_skill",
                        content=block,
                        source="tools.desk_automation.SKILL",
                        stability="static",
                    )
                )
        except Exception:
            pass

    if flags["include_tool_strategy_hint"]:
        try:
            from backend.core.engine.tool_strategy import tool_strategy_block

            block = tool_strategy_block()
            if block:
                layers.append(
                    PromptRuntimeLayer(
                        name="tool_strategy_hint",
                        content=block,
                        source="engine.tool_strategy",
                        stability="static",
                    )
                )
        except Exception:
            pass

    if flags["include_workspace_memory_hint"] and workspace_root:
        try:
            from backend.core.memory.workspace_memory import WorkspaceMemory

            memory = WorkspaceMemory.load(workspace_root)
            mem_block = memory.to_prompt_block()
            if mem_block:
                layers.append(
                    PromptRuntimeLayer(
                        name="workspace_memory",
                        content=mem_block,
                        source="core.memory.workspace_memory",
                        stability="session",
                    )
                )
        except Exception:
            pass

    if flags["include_project_profile"] and workspace_root:
        try:
            from backend.core.memory.project_profile import ensure_project_profile

            profile = ensure_project_profile(workspace_root)
            block = profile.to_prompt_block()
            if block:
                layers.append(
                    PromptRuntimeLayer(
                        name="project_profile",
                        content=block,
                        source="core.memory.project_profile",
                        stability="session",
                    )
                )
        except Exception:
            pass

    if flags["include_agent_state_hint"] and workspace_root:
        from backend.core.memory.workspace_state import summarize_for_system_prompt

        block = summarize_for_system_prompt(workspace_root)
        if block:
            layers.append(
                PromptRuntimeLayer(
                    name="agent_state_hint",
                    content=block,
                    source="memory.workspace_state",
                    stability="request",
                )
            )

    try:
        from backend.runtime.loop_discipline import LOOP_DISCIPLINE_PROMPT

        if LOOP_DISCIPLINE_PROMPT:
            layers.append(
                PromptRuntimeLayer(
                    name="loop_discipline",
                    content="\n\n" + LOOP_DISCIPLINE_PROMPT,
                    source="runtime.loop_discipline",
                    stability="static",
                )
            )
    except Exception:
        pass

    if flags["include_mode_router_hint"] and workspace_root:
        try:
            from .mode_router import mode_discipline_block
        except ImportError:
            mblock = ""
        else:
            mblock = mode_discipline_block(workspace_root)
        if mblock:
            layers.append(
                PromptRuntimeLayer(
                    name="mode_router_hint",
                    content=mblock,
                    source="engine.mode_router",
                    stability="static",
                )
            )

    if flags["include_workflow_hint"] and workspace_root:
        from .workflow_context import workflow_guidelines_block

        wblock = workflow_guidelines_block(workspace_root)
        if wblock:
            layers.append(
                PromptRuntimeLayer(
                    name="workflow_guidelines_hint",
                    content=wblock,
                    source="engine.workflow_context",
                    stability="static",
                )
            )

    if flags["include_open_files_hint"] and workspace_root:
        from .open_files_context import open_files_context_block

        block = open_files_context_block(workspace_root)
        if block:
            layers.append(
                PromptRuntimeLayer(
                    name="open_files_hint",
                    content=block,
                    source="engine.open_files_context",
                    stability="request",
                )
            )

    if flags["include_terminal_hint"] and workspace_root:
        from .terminal_context import terminal_context_block

        block = terminal_context_block(workspace_root)
        if block:
            layers.append(
                PromptRuntimeLayer(
                    name="terminal_hint",
                    content=block,
                    source="engine.terminal_context",
                    stability="request",
                )
            )

    adapted_base = str(base_system_prompt or "").strip()
    if model_tier == 1:
        adapted_base = _lean_prompt(adapted_base)
    elif model_tier == 3:
        adapted_base = _verbose_prompt(adapted_base)

    memory_block = _truncate_user_memory(user_memory_text)
    if memory_block:
        layers.append(
            PromptRuntimeLayer(
                name="user_memory",
                content="\n\n---\n[User METIS.md]\n" + memory_block,
                source="user.METIS.md",
                stability="session",
            )
        )
    layers.append(
        PromptRuntimeLayer(
            name="efficiency_rules",
            content=_EFFICIENCY_RULES,
            source="engine.prompt_runtime",
            stability="static",
        )
    )
    # Strong models (tier 1, e.g. GPT/Claude class) tend to step one tool per
    # turn; push them toward batching + large, decisive actions. Weaker tiers
    # already run lean/verbose variants and don't need (or want) this.
    if model_tier == 1:
        layers.append(
            PromptRuntimeLayer(
                name="strong_model_directive",
                content=_STRONG_MODEL_DIRECTIVE,
                source="engine.prompt_runtime",
                stability="static",
            )
        )
    layers.append(
        PromptRuntimeLayer(
            name="web_strategy",
            content=_WEB_STRATEGY_RULES,
            source="engine.prompt_runtime",
            stability="static",
        )
    )
    layers.append(
        PromptRuntimeLayer(
            name="recency_reinforcement",
            content=_RECENCY_REINFORCEMENT,
            source="engine.prompt_runtime",
            stability="static",
        )
    )
    layers = _sort_layers_for_prefix_cache(layers)
    final_system_prompt = adapted_base + "".join(layer.content for layer in layers)
    return PromptRuntimeSnapshot(
        base_system_prompt=adapted_base,
        layers=layers,
        final_system_prompt=final_system_prompt,
        workspace_root=workspace_root,
    )


def build_runtime_messages(
    base_system_prompt: str,
    history_turns: List[Dict[str, Any]],
    *,
    user_memory_text: str = "",
    model_tier: int = 2,
    model_context_window: Optional[int] = None,
    workspace_root: Optional[str] = None,
    include_workspace_hint: Optional[bool] = None,
    include_agent_state_hint: Optional[bool] = None,
    include_open_files_hint: Optional[bool] = None,
    include_terminal_hint: Optional[bool] = None,
    include_mode_router_hint: Optional[bool] = None,
    include_workflow_hint: Optional[bool] = None,
    include_repo_map_hint: Optional[bool] = None,
    include_desk_skill: Optional[bool] = None,
    include_skills_index: Optional[bool] = None,
    include_tool_strategy_hint: Optional[bool] = None,
    include_project_profile: Optional[bool] = None,
    include_workspace_memory_hint: Optional[bool] = None,
) -> List[Dict[str, Any]]:
    """生成发送给 LLM 的 messages，并保留显式 runtime 编译入口。"""
    snapshot = compile_prompt_runtime(
        base_system_prompt,
        user_memory_text=user_memory_text,
        model_tier=model_tier,
        model_context_window=model_context_window,
        workspace_root=workspace_root,
        include_workspace_hint=include_workspace_hint,
        include_agent_state_hint=include_agent_state_hint,
        include_open_files_hint=include_open_files_hint,
        include_terminal_hint=include_terminal_hint,
        include_mode_router_hint=include_mode_router_hint,
        include_workflow_hint=include_workflow_hint,
        include_repo_map_hint=include_repo_map_hint,
        include_desk_skill=include_desk_skill,
        include_skills_index=include_skills_index,
        include_tool_strategy_hint=include_tool_strategy_hint,
        include_project_profile=include_project_profile,
        include_workspace_memory_hint=include_workspace_memory_hint,
    )
    return [{"role": "system", "content": snapshot.final_system_prompt}] + list(history_turns)

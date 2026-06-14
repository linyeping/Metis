from __future__ import annotations

import ast
import importlib
import inspect
import json
import logging
import os
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from .cancellation import OperationCancelled, raise_if_cancelled
from .schema_converter import openai_to_anthropic, openai_to_gemini
from .path_safety import validate_tool_paths
from .tool_errors import looks_like_tool_error, teaching_error_text
from .tool_profiles import normalize_tool_profile, tool_names_for_profile
from backend.core.paths import legacy_miro_path, metis_path

logger = logging.getLogger(__name__)

try:
    from backend.bridges.tool_contract import ToolProfile
    from backend.bridges.tool_profiles import infer_tool_profile
except ImportError:  # pragma: no cover - supports alternate package loaders
    from backend.bridges.tool_contract import ToolProfile
    from backend.bridges.tool_profiles import infer_tool_profile


@dataclass
class ToolDefinition:
    name: str
    description: str
    parameters: Dict[str, Any]
    execute_fn: Callable[..., Any]
    usage_hint: str = ""
    source: str = "builtin"
    toolset: str = ""
    check_fn: Optional[Callable[[], bool]] = None
    requires_approval: Optional[bool] = None
    destructive: Optional[bool] = None
    # FABLEADV-23: deferred 工具默认不进 schema，只在目录里列名，由 search_tools 按需激活。
    deferred: bool = False


def deferred_tools_enabled() -> bool:
    """FABLEADV-23: 工具按需加载总开关。默认关 = 行为与改动前完全一致。"""
    value = os.environ.get("METIS_DEFERRED_TOOLS", "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


class ToolRegistry:
    """Central registry for all tools available to the agent."""

    def __init__(self) -> None:
        self._tools: Dict[str, ToolDefinition] = {}
        self._aliases: Dict[str, str] = {}
        self._disabled_tools: set[str] = set()

    def register(self, tool: ToolDefinition) -> None:
        profile = infer_tool_profile(
            tool.name,
            canonical_name=self.resolve_name(tool.name),
            source=tool.source,
            description=tool.description,
            available=True,
        )
        if not tool.toolset:
            tool.toolset = profile.toolset
        if tool.destructive is None:
            tool.destructive = profile.destructive
        if tool.requires_approval is None:
            tool.requires_approval = profile.approval != "never"
        self._tools[tool.name] = tool

    def register_alias(self, alias: str, canonical: str) -> None:
        self._aliases[alias] = canonical

    def resolve_name(self, name: str) -> str:
        return self._aliases.get(name, name)

    def get(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(self.resolve_name(name))

    def remove_tools_by_source(self, source: str) -> int:
        target = str(source or "").strip()
        if not target:
            return 0
        names = [
            name
            for name, tool in self._tools.items()
            if tool.source == target or tool.source.startswith(f"{target}:")
        ]
        for name in names:
            self._tools.pop(name, None)
            self._disabled_tools.discard(name)
        self._aliases = {
            alias: canonical
            for alias, canonical in self._aliases.items()
            if canonical not in names and alias not in names
        }
        return len(names)

    def disable_tools(self, names: List[str]) -> None:
        for name in names:
            canonical = self.resolve_name(str(name or "").strip())
            if canonical:
                self._disabled_tools.add(canonical)

    def set_disabled_tools(self, names: List[str]) -> None:
        self._disabled_tools.clear()
        self.disable_tools(names)

    def disabled_tool_names(self) -> List[str]:
        return sorted(self._disabled_tools)

    def apply_tool_overrides(self, overrides: Dict[str, Dict[str, Any]]) -> None:
        for raw_name, patch in overrides.items():
            tool = self.get(str(raw_name or ""))
            if not tool or not isinstance(patch, dict):
                continue
            if "requires_approval" in patch:
                tool.requires_approval = bool(patch["requires_approval"])
            if "destructive" in patch:
                tool.destructive = bool(patch["destructive"])
            if "description" in patch and str(patch["description"]).strip():
                tool.description = str(patch["description"]).strip()
            if "usage_hint" in patch:
                tool.usage_hint = str(patch["usage_hint"] or "")

    def get_all_schemas(
        self, format: str = "openai", *, activated: Optional[set[str]] = None
    ) -> List[Dict[str, Any]]:
        return self._schemas_for_tools(None, format=format, activated=activated)

    def openai_schema_for(self, name: str) -> Optional[Dict[str, Any]]:
        """Single tool's OpenAI schema (used to inject search_tools on demand)."""
        tool = self.get(name)
        if tool is None:
            return None
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": (
                    f"[When to use] {tool.usage_hint}\n\n{tool.description}"
                    if tool.usage_hint
                    else tool.description
                ),
                "parameters": tool.parameters,
            },
        }

    def get_schemas_for_profile(
        self,
        profile: str,
        *,
        format: str = "openai",
        include_desktop: bool = True,
        activated: Optional[set[str]] = None,
    ) -> List[Dict[str, Any]]:
        normalized = normalize_tool_profile(profile)
        profile_names = tool_names_for_profile(normalized, include_desktop=include_desktop)
        return self._schemas_for_tools(profile_names, format=format, activated=activated)

    def _is_deferred(self, tool: ToolDefinition) -> bool:
        """FABLEADV-23: 该工具本轮是否应延迟（不进 schema，只在目录里列名）。
        总开关关闭时一律返回 False，保证行为与改动前完全一致。"""
        if not deferred_tools_enabled():
            return False
        if tool.deferred:
            return True
        if str(tool.source or "").startswith("mcp:"):
            return True
        return False

    def deferred_catalog(self, activated: Optional[set[str]] = None) -> List[Tuple[str, str]]:
        """未激活的 deferred 工具的 (name, 一句话描述)，供 search_tools 提示模型。"""
        activated = activated or set()
        out: List[Tuple[str, str]] = []
        for tool in sorted(self._tools.values(), key=lambda item: item.name):
            if not self.is_available(tool.name):
                continue
            if self._is_deferred(tool) and tool.name not in activated:
                desc = str(tool.description or "").strip().splitlines()[0] if tool.description else ""
                out.append((tool.name, desc[:120]))
        return out

    def search_deferred(
        self,
        query: str,
        *,
        limit: int = 8,
        activated: Optional[set[str]] = None,
    ) -> Tuple[List[str], str]:
        """按 query 检索未激活的 deferred 工具，返回 (命中名字列表, 给模型看的文本)。"""
        q = str(query or "").strip().lower()
        terms = [t for t in q.replace(",", " ").split() if t]
        scored: List[Tuple[int, str, str]] = []
        for name, desc in self.deferred_catalog(activated=activated):
            name_l = name.lower()
            desc_l = desc.lower()
            score = 0
            if q and q == name_l:
                score += 100
            if q and q in name_l:
                score += 30
            for term in terms:
                if term in name_l:
                    score += 10
                if term in desc_l:
                    score += 4
            if not terms:
                score += 1  # 空 query：返回全部目录
            if score > 0:
                scored.append((score, name, desc))
        scored.sort(key=lambda item: (-item[0], item[1]))
        hits = scored[:limit]
        names = [name for _, name, _ in hits]
        if not names:
            return [], f"未找到与 {query!r} 匹配的可加载工具。"
        lines = [f"已加载 {len(names)} 个工具（本轮起可直接调用）："]
        for _, name, desc in hits:
            lines.append(f"- {name}: {desc}" if desc else f"- {name}")
        return names, "\n".join(lines)

    def _schemas_for_tools(
        self,
        allowed_names: Optional[set[str] | frozenset[str]],
        *,
        format: str = "openai",
        activated: Optional[set[str]] = None,
    ) -> List[Dict[str, Any]]:
        activated = activated or set()
        openai_schemas = [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": (
                        f"[When to use] {tool.usage_hint}\n\n{tool.description}"
                        if tool.usage_hint
                        else tool.description
                    ),
                    "parameters": tool.parameters,
                },
            }
            for tool in sorted(self._tools.values(), key=lambda item: item.name)
            if self.is_available(tool.name)
            and (not self._is_deferred(tool) or tool.name in activated)
            and (
                allowed_names is None
                or tool.name in allowed_names
                or str(tool.source or "").strip().lower() not in {"", "builtin", "desktop"}
            )
        ]
        if format == "openai":
            return openai_schemas
        if format == "anthropic":
            return openai_to_anthropic(openai_schemas)
        if format == "gemini":
            return openai_to_gemini(openai_schemas)
        raise ValueError(f"Unknown schema format: {format}")

    def execute(
        self,
        name: str,
        arguments: Dict[str, Any],
        *,
        cancel_event: Optional[threading.Event] = None,
        workspace_root: Optional[str] = None,
    ) -> str:
        raise_if_cancelled(cancel_event)
        tool = self.get(name)
        if not tool:
            preview = ", ".join(self.tool_names[:20])
            return f"Error: Unknown tool '{name}'. Available tools include: {preview}"
        if not self.is_available(name):
            return f"Error: Tool '{name}' is not available in the current environment"
        if not isinstance(arguments, dict):
            return f"Error: Tool arguments for '{name}' must be an object"
        arguments = dict(arguments)
        if tool.name == "todo_write" and "path" not in arguments and workspace_root:
            arguments["path"] = os.path.join(str(workspace_root), ".agent_todos.json")
        if tool.name == "load_skill" and "workspace_root" not in arguments and workspace_root:
            arguments["workspace_root"] = str(workspace_root)
        safety = validate_tool_paths(tool.name, arguments, workspace_root=workspace_root)
        if not safety.allowed:
            return teaching_error_text(
                tool.name,
                arguments,
                safety.error_text(),
                workspace_root=workspace_root or "",
            )
        try:
            raise_if_cancelled(cancel_event)
            result = tool.execute_fn(**arguments)
            raise_if_cancelled(cancel_event)
            out = str(result) if result is not None else "Done (no output)"
            try:
                from backend.tools.coding.workflow_features.hooks.post_tool_hook import post_tool_hook

                out = post_tool_hook(tool.name, arguments, out)
            except Exception:
                pass
            if looks_like_tool_error(out):
                out = teaching_error_text(
                    tool.name,
                    arguments,
                    out,
                    workspace_root=workspace_root or "",
                )
            return out
        except OperationCancelled:
            raise
        except Exception as exc:
            logger.exception("tool execution failed name=%s", tool.name)
            return teaching_error_text(
                tool.name,
                arguments,
                f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                workspace_root=workspace_root or "",
            )

    @property
    def tool_count(self) -> int:
        return len(self._tools)

    @property
    def tool_names(self) -> List[str]:
        return sorted(self._tools)

    def is_available(self, name: str) -> bool:
        canonical = self.resolve_name(name)
        if canonical in self._disabled_tools:
            return False
        tool = self._tools.get(canonical)
        if not tool:
            return False
        if tool.check_fn is None:
            return True
        try:
            return bool(tool.check_fn())
        except Exception:
            return False

    def get_tool_profile(self, name: str) -> Optional[ToolProfile]:
        tool = self.get(name)
        if not tool:
            return None
        canonical = self.resolve_name(name)
        profile = infer_tool_profile(
            tool.name,
            canonical_name=canonical,
            source=tool.source,
            description=tool.description,
            available=self.is_available(name),
        )
        if tool.toolset:
            profile = ToolProfile(
                name=profile.name,
                canonical_name=profile.canonical_name,
                description=profile.description,
                source=profile.source,
                toolset=tool.toolset,
                available=profile.available,
                approval=profile.approval,
                destructive=profile.destructive,
            )
        if tool.destructive is not None or tool.requires_approval is not None:
            destructive = bool(tool.destructive) if tool.destructive is not None else profile.destructive
            if tool.requires_approval is False:
                approval = "never"
            elif tool.requires_approval is True and destructive:
                approval = "always"
            elif tool.requires_approval is True:
                approval = "mode"
            else:
                approval = profile.approval
            profile = ToolProfile(
                name=profile.name,
                canonical_name=profile.canonical_name,
                description=profile.description,
                source=profile.source,
                toolset=profile.toolset,
                available=profile.available,
                approval=approval,
                destructive=destructive,
            )
        return profile

    def list_tool_profiles(self) -> List[ToolProfile]:
        profiles = [self.get_tool_profile(name) for name in self.tool_names]
        return [profile for profile in profiles if profile is not None]

    def get_toolsets(self) -> List[str]:
        return sorted({profile.toolset for profile in self.list_tool_profiles()})

    def get_tool_names_for_toolset(self, toolset: str) -> List[str]:
        target = str(toolset or "")
        return sorted(
            str(profile.name)
            for profile in self.list_tool_profiles()
            if profile.toolset == target
        )

    def tool_requires_approval(
        self,
        name: str,
        mode: str,
        arguments: Optional[Dict[str, Any]] = None,
    ) -> bool:
        if mode in ("auto", "bypass", "plan"):
            return False
        if mode == "ask":
            return True
        profile = self.get_tool_profile(name)
        if profile is None:
            return mode == "ask"
        if profile.approval == "never":
            return False
        if profile.approval == "always":
            return mode in {"ask", "edit"}
        if mode == "edit":
            return profile.destructive
        return False


_REGISTRY: Optional[ToolRegistry] = None
_LOADED_MCP_CONFIGS: set[str] = set()
_TOOL_USAGE_HINTS: Dict[str, str] = {
    "read_file": "Use this before modifying an existing file. Prefer it over guessing the current code shape.",
    "read_multiple_files": "Use when you need to inspect several known files together before editing or comparing behavior.",
    "grep_search": "Use for exact text or symbol search across the codebase. Prefer this over shell grep.",
    "glob_search": "Use to find files by path or name pattern. Prefer this over shell find for repository navigation.",
    "semantic_search": "Use when you need concept search rather than exact text, such as finding authentication or retry logic.",
    "robust_replace_in_file": "Use for targeted modifications to existing files. You should read the file first and prefer this over full rewrites.",
    "write_file": "Use for creating new files or complete rewrites. For small edits to existing files, prefer robust_replace_in_file.",
    "execute_bash_command": "Use for tests, builds, git, package managers, and other true shell work. Do not use it for file reading or code search.",
    "check_dev_environment": "Use before running unfamiliar projects or when a command reports that Python, Node.js, Git, or another runtime is missing.",
    "install_dev_runtime": "Use only after a missing runtime is identified and installation is appropriate. Installs via winget on Windows.",
    "setup_workspace": "Use to prepare a project on a fresh machine by detecting runtimes and installing common dependencies.",
    "browse_web": "Use when a task needs a real browser to navigate, click, fill forms, or inspect dynamic pages.",
    "browse_and_extract": "Use when you know the URL and need specific information extracted through browser automation.",
    "ask_question": "Use when requirements are unclear or when a destructive action needs explicit user confirmation.",
    "apply_patch": "Use when you have a precise patch for one or more files and want the smallest possible diff.",
    "run_tests": "Use after code changes to verify behavior or reproduce a failure with a focused command.",
    "list_directory": "Use to inspect directory contents before navigating deeper or choosing specific files to read.",
}


FALLBACK_ALIASES: Dict[str, str] = {
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


def get_registry(
    mcp_config_path: str = "",
    *,
    include_desktop: bool = True,
    include_mcp: bool = True,
    include_experts: bool = True,
) -> ToolRegistry:
    """Get or create the global tool registry."""
    global _REGISTRY
    if _REGISTRY is None:
        _REGISTRY = ToolRegistry()
        register_builtin_tools(_REGISTRY)
        if include_desktop and not _env_disabled("METIS_DISABLE_DESKTOP_TOOLS", "MIRO_DISABLE_DESKTOP_TOOLS"):
            register_desktop_tools(_REGISTRY)
        if include_experts and not _env_disabled("METIS_DISABLE_EXPERT_TOOLS"):
            try:
                from .expert_tools import register_expert_tools
                register_expert_tools(_REGISTRY)
            except Exception:
                pass
        if not _env_disabled("METIS_DISABLE_PLUGINS", "MIRO_DISABLE_PLUGINS"):
            try:
                from .plugin_loader import register_plugins

                plugin_count = register_plugins(_REGISTRY)
                if plugin_count:
                    print(f"Plugins: Loaded {plugin_count} custom tools")
            except Exception as exc:
                print(f"Plugins: Failed to load: {exc}")
        apply_user_tool_config(_REGISTRY)
    if include_mcp and not _env_disabled("METIS_DISABLE_MCP", "MIRO_DISABLE_MCP"):
        _register_mcp_if_needed(_REGISTRY, mcp_config_path)
        apply_user_tool_config(_REGISTRY)
    return _REGISTRY


def _env_disabled(new_name: str, old_name: str = "") -> bool:
    value = os.environ.get(new_name)
    if value is None and old_name:
        value = os.environ.get(old_name)
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _register_mcp_if_needed(registry: ToolRegistry, config_path: str = "") -> None:
    key = str(Path(config_path).expanduser()) if config_path else "<default>"
    if key in _LOADED_MCP_CONFIGS:
        return
    try:
        from .mcp_client import register_mcp_tools

        count = register_mcp_tools(registry, config_path)
        if count:
            print(f"MCP: Registered {count} tools from external servers")
    except Exception as exc:
        print(f"MCP: Failed to register tools: {exc}")
    finally:
        _LOADED_MCP_CONFIGS.add(key)


def reload_mcp_tools(registry: Optional[ToolRegistry] = None, config_path: str = "") -> Dict[str, Any]:
    target = registry or _REGISTRY
    if target is None:
        target = get_registry(include_mcp=False)
    try:
        from .mcp_client import get_mcp_manager, register_mcp_tools

        manager = get_mcp_manager()
        if manager:
            manager.disconnect_all()
        removed = target.remove_tools_by_source("mcp")
        _LOADED_MCP_CONFIGS.clear()
        count = register_mcp_tools(target, config_path)
        apply_user_tool_config(target)
        manager = get_mcp_manager()
        return {
            "ok": True,
            "removed": removed,
            "registered": count,
            "status": manager.get_status() if manager else {},
        }
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def apply_user_tool_config(registry: ToolRegistry, config_path: str = "") -> Dict[str, Any]:
    config = _load_user_tool_config(config_path)
    disabled = [str(item) for item in config.get("disabled_tools", []) if str(item).strip()]
    overrides = config.get("tool_overrides") if isinstance(config.get("tool_overrides"), dict) else {}
    registry.set_disabled_tools(disabled)
    registry.apply_tool_overrides(overrides)  # type: ignore[arg-type]
    return {
        "path": config.get("_path", ""),
        "disabled_tools": registry.disabled_tool_names(),
        "tool_overrides": sorted(str(key) for key in overrides),
    }


def _load_user_tool_config(config_path: str = "") -> Dict[str, Any]:
    paths = [Path(config_path).expanduser()] if config_path else _tool_config_paths()
    for path in paths:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"Tools config: Failed to read {path}: {exc}")
            return {"_path": str(path)}
        if not isinstance(data, dict):
            return {"_path": str(path)}
        data["_path"] = str(path)
        return data
    return {"_path": str(paths[0]) if paths else ""}


def _tool_config_paths() -> List[Path]:
    env_path = os.environ.get("METIS_TOOLS_CONFIG") or os.environ.get("MIRO_TOOLS_CONFIG")
    paths: List[Path] = []
    if env_path:
        paths.append(Path(env_path).expanduser())
    paths.extend([metis_path("tools.json"), legacy_miro_path("tools.json")])
    return paths


def register_builtin_tools(registry: ToolRegistry) -> None:
    """Register coding tools from the existing schema and registry metadata."""
    _ensure_repo_root_on_path()
    from backend.tools.schema_definitions import build_tools_schema

    schemas = build_tools_schema()
    import_map, aliases = _load_builtin_registry_metadata()

    for schema in schemas:
        function = schema.get("function") or {}
        name = function.get("name")
        if not name:
            continue
        registry.register(
            ToolDefinition(
                name=name,
                description=function.get("description", f"Tool: {name}"),
                parameters=function.get(
                    "parameters",
                    {"type": "object", "properties": {}, "required": []},
                ),
                execute_fn=_make_builtin_executor(name, import_map.get(name)),
                usage_hint=_TOOL_USAGE_HINTS.get(name, ""),
                source="builtin",
            )
        )

    for alias, canonical in {**FALLBACK_ALIASES, **aliases}.items():
        registry.register_alias(alias, canonical)

    def load_skill(name: str, arguments: str = "", workspace_root: str = "") -> str:
        from backend.runtime.skill_loader import load_skill_content

        return load_skill_content(name, workspace_root=workspace_root, arguments=arguments)

    registry.register(
        ToolDefinition(
            name="load_skill",
            description=(
                "Load the full SKILL.md content for a Metis skill by name. "
                "Use when the skills index says a skill matches the task; the returned "
                "content becomes part of the current run context."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {
                        "type": "string",
                        "description": "Skill name, for example debug-workflow or frontend-app.",
                    },
                    "arguments": {
                        "type": "string",
                        "description": "Optional user arguments to substitute for $ARGUMENTS in the skill.",
                    },
                },
                "required": ["name"],
            },
            execute_fn=load_skill,
            usage_hint="Call this before applying a listed skill's workflow or rules.",
            source="builtin",
            toolset="workflow",
            requires_approval=False,
            destructive=False,
        )
    )

    # FABLEADV-23: 工具按需检索/加载（仅在 deferred 模式 + 有 deferred 工具时进 schema，
    # 由 agent_loop 控制；这里始终注册，执行器纯函数返回检索文本）。
    def search_tools(query: str = "") -> str:
        names, text = get_registry().search_deferred(query)
        return text

    registry.register(
        ToolDefinition(
            name="search_tools",
            description=(
                "Search for additional tools by capability or keyword and load them so "
                "you can call them. Use when the task may need a tool that is not in your "
                "current tool list (for example an MCP/integration tool). After this returns, "
                "the matched tools become callable."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The capability you need, e.g. 'send slack message' or 'query database'.",
                    },
                },
                "required": ["query"],
            },
            execute_fn=search_tools,
            usage_hint="Call when you need a capability not covered by the listed tools.",
            source="builtin",
            toolset="workflow",
            requires_approval=False,
            destructive=False,
        )
    )

    # FABLEADV-27: 只读/分析的并行子智能体扇出（orchestrator-worker, Scope A）。
    def delegate_parallel(tasks: Any = None) -> str:
        from backend.runtime.parallel_subagents import delegate_parallel as _run

        from backend.tools.coding.foundation.core_mechanisms.path_security import get_workspace_root

        try:
            ws = str(get_workspace_root())
        except Exception:
            ws = ""
        return _run(tasks, workspace_root=ws)

    registry.register(
        ToolDefinition(
            name="delegate_parallel",
            description=(
                "Run several INDEPENDENT read-only/analysis subtasks in parallel, each as an "
                "isolated subagent, and get their combined findings back to synthesize. Use ONLY "
                "when the subtasks are genuinely independent (the 'two people who don't need to "
                "talk first' test) — e.g. analyze several modules at once, research multiple "
                "questions, or gather context from different areas. Subagents are read-only "
                "(they cannot edit files or run commands), so never use this to parallelize edits. "
                "For dependent or write tasks, do them yourself in sequence."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "tasks": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Self-contained subtask descriptions; each runs in its own isolated subagent.",
                    },
                },
                "required": ["tasks"],
            },
            execute_fn=delegate_parallel,
            usage_hint="Use for independent read-only fan-out (analysis/research), never for parallel edits.",
            source="builtin",
            toolset="workflow",
            requires_approval=False,
            destructive=False,
        )
    )


def _png_dimensions(png_bytes: bytes) -> Tuple[int, int]:
    """Read (width, height) from a PNG's IHDR chunk. Returns (0, 0) on failure."""
    if len(png_bytes) >= 24 and png_bytes[:8] == b"\x89PNG\r\n\x1a\n" and png_bytes[12:16] == b"IHDR":
        width = int.from_bytes(png_bytes[16:20], "big")
        height = int.from_bytes(png_bytes[20:24], "big")
        return width, height
    return 0, 0


def register_desktop_tools(registry: ToolRegistry) -> None:
    """Register desktop automation wrappers without importing them eagerly."""

    def desktop_screenshot(monitor: str = "primary", window_title: str = "") -> str:
        from backend.tools.desk_automation import config
        from backend.tools.desk_automation.capture.screenshot import grab_screen_png
        from backend.tools.desk_automation.capture.window_shot import grab_window_png
        from backend.tools.desk_automation.input import actions as _desk_actions

        config.assert_automation_allowed()
        if window_title:
            png_bytes = grab_window_png(window_title)
            if not png_bytes:
                return f"Error: Window not found: {window_title}"
            # 窗口截图用窗口相对坐标，交给 desktop_window_action；
            # 清掉全屏帧，避免裸 desktop_action 误用上一帧的缩放映射。
            _desk_actions.clear_screenshot_frame()
        else:
            png_bytes = grab_screen_png()
        path = os.path.join(tempfile.gettempdir(), "metis_screenshot.png")
        with open(path, "wb") as handle:
            handle.write(png_bytes)
        # FABLEADV-20: 记录"物理尺寸 / 模型所见显示尺寸"，供 desktop_action 坐标映射。
        note = ""
        if not window_title:
            try:
                phys_w, phys_h = _png_dimensions(png_bytes)
                if phys_w and phys_h:
                    from backend.runtime.image_utils import predict_display_dimensions

                    disp_w, disp_h = predict_display_dimensions(phys_w, phys_h)
                    _desk_actions.record_screenshot_frame(phys_w, phys_h, disp_w, disp_h)
                    note = (
                        f" (you see it at {disp_w}x{disp_h}; give coordinates in that"
                        " image's pixel space — they are auto-mapped to the screen)"
                    )
            except Exception:
                _desk_actions.clear_screenshot_frame()
        return f"Screenshot saved: {path}{note}"

    def desktop_action(
        action: str,
        x: int = 0,
        y: int = 0,
        text: str = "",
        key: str = "",
    ) -> str:
        from backend.tools.desk_automation import config
        from backend.tools.desk_automation.input.actions import (
            click_at,
            double_click,
            press_key,
            right_click,
            scroll,
            type_text,
        )

        from backend.tools.desk_automation.input.actions import (
            get_screenshot_frame,
            map_model_point,
        )

        config.assert_automation_allowed()
        # FABLEADV-20: 模型坐标在它所见的缩放截图像素空间，映射回物理像素。
        px, py = map_model_point(x, y)
        coord_actions = {"click", "double_click", "right_click", "scroll_up", "scroll_down"}
        if action == "click":
            click_at(px, py)
        elif action == "double_click":
            double_click(px, py)
        elif action == "right_click":
            right_click(px, py)
        elif action == "type":
            type_text(text)
        elif action == "key":
            press_key(key)
        elif action == "scroll_up":
            scroll(clicks=3, x=px or None, y=py or None)
        elif action == "scroll_down":
            scroll(clicks=-3, x=px or None, y=py or None)
        else:
            return f"Error: Unknown desktop action '{action}'"
        # 坐标轨迹（展开工具卡即可见，便于真机校准坐标映射）。
        if action in coord_actions:
            frame = get_screenshot_frame()
            if frame:
                shot = (
                    f"shot {int(frame['phys_w'])}x{int(frame['phys_h'])}"
                    f"->{int(frame['disp_w'])}x{int(frame['disp_h'])}"
                )
            else:
                shot = "no-frame(identity)"
            return f"Done: {action} | model({x},{y}) -> screen({px},{py}) | {shot}"
        return f"Done: {action}"

    def desktop_vision_task(
        goal: str,
        max_steps: int = 20,
        exec_mode: str = "auto",
    ) -> str:
        from backend.tools.desk_automation.orchestrator.vision_loop import get_state, start

        start_result = start(goal=goal, max_steps=max_steps, exec_mode=exec_mode)
        if not start_result.get("ok"):
            return json.dumps(start_result, ensure_ascii=False)

        deadline = time.time() + max(15, max_steps * 15)
        while time.time() < deadline:
            state = get_state()
            if state.get("status") in ("done", "error", "idle", "paused"):
                break
            time.sleep(2)

        state = get_state()
        history = state.get("action_history") or []
        summary = {
            "goal": goal,
            "status": state.get("status"),
            "steps": len(history),
            "error": state.get("error", ""),
            "last_actions": history[-3:],
        }
        return json.dumps(summary, ensure_ascii=False, indent=2)

    def desktop_inventory(query: str = "all") -> str:
        results: Dict[str, Any] = {}
        if query in ("software", "all"):
            from backend.tools.desk_automation.inventory.scan_software import scan_installed_software

            results["software"] = scan_installed_software()
        if query in ("windows", "all"):
            try:
                from backend.tools.desk_automation.capture.window_manager import list_windows as _wm_list

                results["windows"] = [w.to_dict() for w in _wm_list()]
            except Exception:
                from backend.tools.desk_automation.inventory.scan_windows import list_visible_windows

                results["windows"] = list_visible_windows()
        if query in ("processes", "all"):
            from backend.tools.desk_automation.inventory.scan_windows import list_running_processes

            results["processes"] = list_running_processes(top_n=20)
        if query in ("cli", "all"):
            from backend.tools.desk_automation.inventory.scan_cli import scan_cli_candidates

            results["cli"] = scan_cli_candidates()
        if not results:
            return f"Error: Unknown desktop inventory query '{query}'"
        return json.dumps(results, ensure_ascii=False, indent=2)

    # ---- Window-level tools (window_manager) ----

    def desktop_window_list() -> str:
        """List all visible windows, return JSON array."""
        from backend.tools.desk_automation.capture.window_manager import list_windows

        windows = list_windows()
        return json.dumps(
            [
                {
                    "hwnd": w.hwnd,
                    "title": w.title,
                    "exe": w.exe_name,
                    "rect": w.rect,
                    "is_foreground": w.is_foreground,
                }
                for w in windows
            ],
            ensure_ascii=False,
            indent=2,
        )

    def desktop_window_capture(hwnd: int = 0, title: str = "") -> str:
        """Capture a specific window screenshot (works even when occluded)."""
        from backend.tools.desk_automation import config
        from backend.tools.desk_automation.capture.window_manager import (
            capture_window,
            find_window,
        )

        config.assert_automation_allowed()
        hwnd = int(hwnd) if hwnd else 0
        if not hwnd and title:
            win = find_window(title)
            if not win:
                return f"Error: No window matching '{title}'"
            hwnd = win.hwnd
        if not hwnd:
            return "Error: Provide hwnd or title"
        png_bytes = capture_window(hwnd)
        if not png_bytes:
            return f"Error: Failed to capture window {hwnd}"
        path = os.path.join(tempfile.gettempdir(), f"metis_win_{hwnd}.png")
        with open(path, "wb") as f:
            f.write(png_bytes)
        return f"Window screenshot saved: {path}"

    def desktop_window_action(
        hwnd: int = 0,
        action: str = "",
        x: int = 0,
        y: int = 0,
        text: str = "",
        key: str = "",
        scroll_delta: int = 0,
    ) -> str:
        """Perform an action inside a window using window-relative coordinates."""
        from backend.tools.desk_automation import config
        from backend.tools.desk_automation.capture.window_manager import (
            activate_window,
            click_in_window,
            press_key_in_window,
            scroll_in_window,
            type_in_window,
        )

        config.assert_automation_allowed()
        hwnd = int(hwnd) if hwnd else 0
        if not hwnd:
            return "Error: hwnd is required"
        if action == "activate":
            activate_window(hwnd)
        elif action == "click":
            click_in_window(hwnd, x, y)
        elif action == "type":
            type_in_window(hwnd, text)
        elif action == "key":
            press_key_in_window(hwnd, key)
        elif action == "scroll":
            scroll_in_window(hwnd, x, y, delta=scroll_delta)
        else:
            return f"Error: Unknown window action '{action}'"
        return f"Done: {action} on window {hwnd}"

    registry.register(
        ToolDefinition(
            name="desktop_screenshot",
            description=(
                "Take a desktop screenshot or capture a window by title. "
                "Returns the saved PNG path."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "monitor": {
                        "type": "string",
                        "description": "Monitor selector; currently primary is used.",
                    },
                    "window_title": {
                        "type": "string",
                        "description": "Optional title substring for window capture.",
                    },
                },
                "required": [],
            },
            execute_fn=desktop_screenshot,
            source="desktop",
        )
    )
    registry.register(
        ToolDefinition(
            name="desktop_action",
            description=(
                "Perform a single low-level desktop action: click, double click, "
                "right click, type, press key, or scroll. Coordinates (x, y) are in "
                "the pixel space of the screenshot you were just shown; they are "
                "auto-mapped to physical screen pixels, so read them directly off "
                "that image. For multi-step GUI automation (open an app, navigate, "
                "fill a form), prefer desktop_vision_task instead of chaining many "
                "desktop_action calls."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": [
                            "click",
                            "double_click",
                            "right_click",
                            "type",
                            "key",
                            "scroll_up",
                            "scroll_down",
                        ],
                    },
                    "x": {"type": "integer", "description": "X coordinate."},
                    "y": {"type": "integer", "description": "Y coordinate."},
                    "text": {"type": "string", "description": "Text to type."},
                    "key": {"type": "string", "description": "Key name to press."},
                },
                "required": ["action"],
            },
            execute_fn=desktop_action,
            source="desktop",
        )
    )
    registry.register(
        ToolDefinition(
            name="desktop_vision_task",
            description=(
                "Preferred tool for multi-step GUI automation (open an app, search, "
                "navigate, fill forms). Runs an orchestrated vision loop that detects "
                "on-screen elements and maps coordinates precisely, so it is far more "
                "reliable than manually chaining desktop_screenshot + desktop_action. "
                "Give it a high-level goal and it drives the steps itself."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "Desktop task goal."},
                    "max_steps": {
                        "type": "integer",
                        "description": "Maximum number of action steps.",
                    },
                    "exec_mode": {
                        "type": "string",
                        "enum": ["auto", "human", "skill"],
                        "description": "Execution mode.",
                    },
                },
                "required": ["goal"],
            },
            execute_fn=desktop_vision_task,
            source="desktop",
        )
    )
    registry.register(
        ToolDefinition(
            name="desktop_inventory",
            description=(
                "Query desktop environment information: software, windows, "
                "processes, CLI tools, or all."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "enum": ["software", "windows", "processes", "cli", "all"],
                    }
                },
                "required": [],
            },
            execute_fn=desktop_inventory,
            source="desktop",
        )
    )

    # ---- Window-level tools (window_manager) ----
    registry.register(
        ToolDefinition(
            name="desktop_window_list",
            description=(
                "List all visible desktop windows with handles, titles, and bounds. "
                "Use to discover windows before interacting with them."
            ),
            parameters={
                "type": "object",
                "properties": {},
                "required": [],
            },
            execute_fn=desktop_window_list,
            source="desktop",
            usage_hint=(
                "Returns hwnd values needed by desktop_window_capture and "
                "desktop_window_action. Prefer this over desktop_inventory(query='windows') "
                "for window interaction workflows."
            ),
        )
    )
    registry.register(
        ToolDefinition(
            name="desktop_window_capture",
            description=(
                "Capture a window screenshot by handle or title. Works even if "
                "the window is behind other windows (uses PrintWindow API)."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "hwnd": {
                        "type": "integer",
                        "description": "Window handle from desktop_window_list.",
                    },
                    "title": {
                        "type": "string",
                        "description": "Window title substring (if hwnd not available).",
                    },
                },
                "required": [],
            },
            execute_fn=desktop_window_capture,
            source="desktop",
        )
    )
    registry.register(
        ToolDefinition(
            name="desktop_window_action",
            description=(
                "Perform an action inside a specific window using window-relative "
                "coordinates. Coordinates (0,0) = top-left of the window."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "hwnd": {
                        "type": "integer",
                        "description": "Window handle.",
                    },
                    "action": {
                        "type": "string",
                        "enum": ["activate", "click", "type", "key", "scroll"],
                        "description": "Action to perform.",
                    },
                    "x": {"type": "integer", "description": "Window-relative X."},
                    "y": {"type": "integer", "description": "Window-relative Y."},
                    "text": {"type": "string", "description": "Text to type."},
                    "key": {"type": "string", "description": "Key name to press."},
                    "scroll_delta": {
                        "type": "integer",
                        "description": "Scroll amount. Negative = down.",
                    },
                },
                "required": ["hwnd", "action"],
            },
            execute_fn=desktop_window_action,
            source="desktop",
        )
    )


def _ensure_repo_root_on_path() -> None:
    root = str(Path(__file__).resolve().parents[2])
    if root not in sys.path:
        sys.path.insert(0, root)


def _load_builtin_registry_metadata() -> Tuple[Dict[str, Tuple[str, str]], Dict[str, str]]:
    registry_path = Path(__file__).resolve().parents[1] / "tools" / "registry.py"
    if not registry_path.exists():
        return {}, dict(FALLBACK_ALIASES)

    tree = ast.parse(registry_path.read_text(encoding="utf-8"))
    symbol_to_import: Dict[str, Tuple[str, str]] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("backend.tools."):
            for alias in node.names:
                local_name = alias.asname or alias.name
                symbol_to_import[local_name] = (node.module, alias.name)

    tool_imports: Dict[str, Tuple[str, str]] = {}
    aliases = dict(FALLBACK_ALIASES)
    for node in ast.walk(tree):
        target_names: List[str] = []
        value_node: Optional[ast.AST] = None
        if isinstance(node, ast.Assign):
            target_names = [target.id for target in node.targets if isinstance(target, ast.Name)]
            value_node = node.value
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target_names = [node.target.id]
            value_node = node.value
        if value_node is None:
            continue
        if "TOOL_NAME_ALIASES" in target_names:
            try:
                parsed_aliases = ast.literal_eval(value_node)
            except Exception:
                parsed_aliases = {}
            if isinstance(parsed_aliases, dict):
                aliases.update({str(k): str(v) for k, v in parsed_aliases.items()})
        if "AVAILABLE_TOOLS" in target_names and isinstance(value_node, ast.Dict):
            for key_node, item_value_node in zip(value_node.keys, value_node.values):
                if not isinstance(key_node, ast.Constant) or not isinstance(key_node.value, str):
                    continue
                if isinstance(item_value_node, ast.Name):
                    imported = symbol_to_import.get(item_value_node.id)
                    if imported:
                        tool_imports[str(key_node.value)] = imported

    return tool_imports, aliases


def _make_builtin_executor(
    name: str,
    import_target: Optional[Tuple[str, str]],
) -> Callable[..., Any]:
    def execute(**kwargs: Any) -> Any:
        if not import_target:
            return _execute_via_legacy_registry(name, kwargs)
        module_name, attr_name = import_target
        try:
            module = importlib.import_module(module_name)
            fn = getattr(module, attr_name)
        except Exception as exc:
            return f"Error loading tool '{name}': {type(exc).__name__}: {exc}"

        normalized = _normalize_builtin_kwargs(name, kwargs)
        call_kwargs = _filter_kwargs(fn, normalized)
        return fn(**call_kwargs)

    return execute


def _execute_via_legacy_registry(name: str, kwargs: Dict[str, Any]) -> Any:
    """Fallback for packaged/dev environments where AST import metadata is incomplete."""
    try:
        from backend.tools.registry import execute_tool
    except Exception as exc:
        return f"Error loading tool registry for '{name}': {type(exc).__name__}: {exc}"

    try:
        return execute_tool(name, **kwargs)
    except Exception as exc:
        return f"Error executing tool '{name}' via registry: {type(exc).__name__}: {exc}"


def _filter_kwargs(fn: Callable[..., Any], kwargs: Dict[str, Any]) -> Dict[str, Any]:
    try:
        signature = inspect.signature(fn)
    except (TypeError, ValueError):
        return kwargs
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in signature.parameters.values()):
        return kwargs
    allowed = {
        name
        for name, param in signature.parameters.items()
        if param.kind
        in (inspect.Parameter.POSITIONAL_OR_KEYWORD, inspect.Parameter.KEYWORD_ONLY)
    }
    return {key: value for key, value in kwargs.items() if key in allowed}


def _normalize_builtin_kwargs(canonical: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    kwargs = dict(raw)

    if canonical == "list_directory":
        if "path" in kwargs and "dir_path" not in kwargs:
            kwargs["dir_path"] = kwargs.pop("path")

    elif canonical == "read_file":
        if "path" in kwargs and "file_path" not in kwargs:
            kwargs["file_path"] = kwargs.pop("path")
        offset = kwargs.pop("offset", None)
        limit = kwargs.pop("limit", None)
        if offset is not None:
            kwargs.setdefault("start_line", int(offset))
            if limit is not None:
                kwargs["end_line"] = int(offset) + int(limit) - 1

    elif canonical == "write_file":
        if "path" in kwargs and "file_path" not in kwargs:
            kwargs["file_path"] = kwargs.pop("path")
        if "contents" in kwargs and "content" not in kwargs:
            kwargs["content"] = kwargs.pop("contents")

    elif canonical == "delete_file":
        if "file_path" in kwargs and "path" not in kwargs:
            kwargs["path"] = kwargs.pop("file_path")

    elif canonical == "robust_replace_in_file":
        if "path" in kwargs and "file_path" not in kwargs:
            kwargs["file_path"] = kwargs.pop("path")
        if "old_string" in kwargs and "search_text" not in kwargs:
            kwargs["search_text"] = kwargs.pop("old_string")
        if "new_string" in kwargs and "replace_text" not in kwargs:
            kwargs["replace_text"] = kwargs.pop("new_string")

    elif canonical == "glob_search":
        if "glob_pattern" in kwargs and "pattern" not in kwargs:
            kwargs["pattern"] = kwargs.pop("glob_pattern")
        if "target_directory" in kwargs and "root" not in kwargs:
            kwargs["root"] = kwargs.pop("target_directory")

    elif canonical == "grep_search":
        if "glob" in kwargs and "glob_pattern" not in kwargs:
            kwargs["glob_pattern"] = kwargs.pop("glob")
        if "head_limit" in kwargs and "max_results" not in kwargs:
            kwargs["max_results"] = kwargs["head_limit"]

    elif canonical == "semantic_search":
        if "num_results" in kwargs and "top_k" not in kwargs:
            kwargs["top_k"] = int(kwargs.pop("num_results"))
        target_directories = kwargs.get("target_directories")
        if "workspace_root" not in kwargs and target_directories:
            if isinstance(target_directories, str):
                kwargs["workspace_root"] = target_directories
            elif isinstance(target_directories, list):
                kwargs["workspace_root"] = str(target_directories[0])

    elif canonical == "web_search":
        if "search_term" in kwargs and "query" not in kwargs:
            kwargs["query"] = kwargs.pop("search_term")

    elif canonical == "generate_image":
        if "description" in kwargs and "prompt" not in kwargs:
            kwargs["prompt"] = kwargs.pop("description")

    elif canonical == "execute_bash_command":
        if "working_directory" in kwargs and "cwd" not in kwargs:
            kwargs["cwd"] = kwargs.pop("working_directory")
        block_until_ms = kwargs.pop("block_until_ms", None)
        if block_until_ms is not None:
            kwargs["timeout"] = max(1, int(int(block_until_ms) / 1000))

    elif canonical == "read_lints":
        paths = kwargs.get("paths")
        if isinstance(paths, list):
            kwargs["paths"] = ",".join(str(path) for path in paths)

    elif canonical == "read_multiple_files":
        if "paths" in kwargs and "file_paths" not in kwargs:
            kwargs["file_paths"] = kwargs.pop("paths")

    elif canonical == "edit_notebook":
        if "target_notebook" in kwargs and "path" not in kwargs:
            kwargs["path"] = kwargs.pop("target_notebook")

    elif canonical == "todo_write":
        if "todo_storage_path" in kwargs and "path" not in kwargs:
            kwargs["path"] = kwargs.pop("todo_storage_path")

    elif canonical == "write_open_files_context":
        if "open_files_storage_path" in kwargs and "path" not in kwargs:
            kwargs["path"] = kwargs.pop("open_files_storage_path")

    elif canonical == "ask_question":
        questions = kwargs.get("questions")
        if isinstance(questions, str):
            try:
                kwargs["questions"] = json.loads(questions)
            except json.JSONDecodeError:
                pass

    return kwargs

from __future__ import annotations

import ast
import importlib
import inspect
import json
import logging
import os
import re
import sys
import tempfile
import time
import traceback
from dataclasses import dataclass
from pathlib import Path
import threading
from typing import Any, Callable, Dict, List, Optional, Tuple

from .cancellation import OperationCancelled, raise_if_cancelled
from .evidence_chain import build_verifier_evidence_payload
from .hook_lifecycle_bus import emit_hook_lifecycle
from .schema_converter import openai_to_anthropic, openai_to_gemini
from .path_safety import WRITE_TOOLS, validate_tool_paths
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


def _append_hook_output(result: str, hook_output: str) -> str:
    text = str(hook_output or "").strip()
    if not text:
        return result
    return f"{result}\n\n{text}"


def _emit_file_changed_if_needed(
    tool_name: str,
    arguments: Dict[str, Any],
    result: str,
    *,
    workspace_root: str = "",
) -> None:
    if tool_name not in WRITE_TOOLS and tool_name not in {"apply_patch", "edit_notebook"}:
        return
    path = _first_argument_path(arguments)
    operation = "modified"
    if tool_name in {"write_file", "append_to_file"}:
        operation = "written"
    elif tool_name in {"delete_file", "delete_directory"}:
        operation = "deleted"
    elif tool_name == "rename_file_update_refs":
        operation = "renamed"
    emit_hook_lifecycle(
        "file.changed",
        tool_name=tool_name,
        arguments=arguments,
        workspace_root=workspace_root,
        result=result,
        ok=True,
        status=operation,
        metadata={"path": path, "operation": operation},
    )


def _first_argument_path(arguments: Dict[str, Any]) -> str:
    for key in ("file_path", "path", "target_path", "directory_path", "new_path", "old_path"):
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


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
            emit_hook_lifecycle(
                "tool.error",
                tool_name=str(name or ""),
                arguments=arguments if isinstance(arguments, dict) else {},
                workspace_root=workspace_root or "",
                ok=False,
                status="unknown_tool",
                error=f"Unknown tool '{name}'",
                run_configured_hooks=False,
            )
            return f"Error: Unknown tool '{name}'. Available tools include: {preview}"
        if not self.is_available(name):
            emit_hook_lifecycle(
                "tool.skip",
                tool_name=tool.name,
                arguments=arguments if isinstance(arguments, dict) else {},
                workspace_root=workspace_root or "",
                ok=False,
                status="unavailable",
                error=f"Tool '{name}' is not available",
            )
            return f"Error: Tool '{name}' is not available in the current environment"
        if not isinstance(arguments, dict):
            emit_hook_lifecycle(
                "tool.error",
                tool_name=tool.name,
                arguments={},
                workspace_root=workspace_root or "",
                ok=False,
                status="invalid_arguments",
                error=f"Tool arguments for '{name}' must be an object",
            )
            return f"Error: Tool arguments for '{name}' must be an object"
        arguments = dict(arguments)
        if tool.name == "todo_write" and "path" not in arguments and workspace_root:
            arguments["path"] = os.path.join(str(workspace_root), ".agent_todos.json")
        if tool.name == "load_skill" and "workspace_root" not in arguments and workspace_root:
            arguments["workspace_root"] = str(workspace_root)
        emit_hook_lifecycle(
            "tool.start",
            tool_name=tool.name,
            arguments=arguments,
            workspace_root=workspace_root or "",
            ok=None,
            status="starting",
        )
        safety = validate_tool_paths(tool.name, arguments, workspace_root=workspace_root)
        if not safety.allowed:
            out = teaching_error_text(
                tool.name,
                arguments,
                safety.error_text(),
                workspace_root=workspace_root or "",
            )
            dispatch = emit_hook_lifecycle(
                "tool.skip",
                tool_name=tool.name,
                arguments=arguments,
                workspace_root=workspace_root or "",
                result=out,
                ok=False,
                status=safety.code or "path_safety_denied",
                error=safety.error_text(),
            )
            return _append_hook_output(out, dispatch.display_output)
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
                dispatch = emit_hook_lifecycle(
                    "tool.error",
                    tool_name=tool.name,
                    arguments=arguments,
                    workspace_root=workspace_root or "",
                    result=out,
                    ok=False,
                    status="tool_returned_error",
                    error=out[:1000],
                )
                return _append_hook_output(out, dispatch.display_output)
            dispatch = emit_hook_lifecycle(
                "tool.finish",
                tool_name=tool.name,
                arguments=arguments,
                workspace_root=workspace_root or "",
                result=out,
                ok=True,
                status="finished",
            )
            _emit_file_changed_if_needed(tool.name, arguments, out, workspace_root=workspace_root or "")
            out = _append_hook_output(out, dispatch.display_output)
            return out
        except OperationCancelled:
            emit_hook_lifecycle(
                "tool.error",
                tool_name=tool.name,
                arguments=arguments,
                workspace_root=workspace_root or "",
                ok=False,
                status="cancelled",
                error="Tool execution cancelled",
            )
            raise
        except Exception as exc:
            logger.exception("tool execution failed name=%s", tool.name)
            out = teaching_error_text(
                tool.name,
                arguments,
                f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                workspace_root=workspace_root or "",
            )
            dispatch = emit_hook_lifecycle(
                "tool.error",
                tool_name=tool.name,
                arguments=arguments,
                workspace_root=workspace_root or "",
                result=out,
                ok=False,
                status="exception",
                error=f"{type(exc).__name__}: {exc}",
            )
            return _append_hook_output(out, dispatch.display_output)

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
    "metis_rootfs_asset_status": "Use before WSL import to inspect a Metis rootfs asset and verify SHA256/signature state.",
    "metis_rootfs_asset_register": "Use after building or downloading a Metis-owned rootfs asset to copy/register it in the VM pack manifest with SHA256.",
    "metis_rootfs_source_status": "Use before downloading a Metis rootfs to inspect the manifest/direct asset source without writing files.",
    "metis_rootfs_asset_download": "Use to download or copy a Metis rootfs asset only when SHA256 is available; keep dry_run=true until the user approves the write.",
    "metis_rootfs_builder_status": "Use before building a Metis rootfs to inspect Docker/WSL builder readiness without writing files.",
    "metis_rootfs_build": "Use to generate or execute a Metis rootfs build. Keep dry_run=true until approved; profile=standard requires allow_network=true.",
    "metis_rootfs_image_builder_status": "Use before building rootfs.vhdx to inspect WSL import support, rootfs.tar source, target path, and image-builder scripts without writing files.",
    "metis_rootfs_image_build": "Use to generate a Metis-owned rootfs.vhdx from rootfs.tar through WSL2 import. Keep dry_run=true until approved; successful builds register rootfs.vhdx for direct VM assets.",
    "metis_runtime_bundle_package": "Use after metis_runtime_bundle_prepare to produce a release zip, sha256 file, and release manifest for a Metis-owned runtime bundle.",
    "metis_runtime_bundle_package_v2": "Use to package prebuilt direct-VM assets for release: vmlinuz, initrd, rootfs.vhdx.zst, metis-bin.vhdx, manifests, SHA256SUMS, and verify/install scripts. Users do not need Docker/WSL to build assets.",
    "metis_runtime_bundle_prepare": "Use after building/registering a Metis-owned rootfs to write the runtime bundle manifest, origin markers, install scripts, and latest manifest for WSL-import runtime delivery.",
    "metis_vm_direct_assets_prepare": "Use to prepare Metis-owned direct VM assets (rootfs.vhdx, vmlinuz, initrd, metis-bin.vhdx, sessiondata.vhdx) and HCS/Hyper-V runner scaffolding; it does not claim the VM runner is complete.",
    "metis_vm_direct_runner_prepare": "Use to prepare the Metis direct VM runner contract: host HCS runner script, guest metisd JSONL daemon, artifact sync, diagnostics, lifecycle schema, and manifest. HCS start remains gated.",
    "metis_vm_direct_runner_smoke": "Use after metis_vm_direct_runner_prepare to validate host/guest JSONL protocol, artifact sync, diagnostics export, and lifecycle events through stdio. It does not start HCS/Hyper-V.",
    "metis_vm_hcs_starter_prepare": "Use to prepare the real experimental Windows HCS ComputeSystem starter: compute-system JSON, C# HCS API bridge, PowerShell starter, and manifest. It does not start the VM.",
    "metis_vm_hcs_starter_start": "Use to dry-run or explicitly execute the HCS ComputeSystem start path. dry_run=true by default; dry_run=false also requires enable_experimental_hcs=true.",
    "metis_vm_guest_handshake_prepare": "Use after HCS starter/rootfs verifier work to prepare the guest runtime.hello verifier. It writes the handshake plan and manifest; it does not mark the VM runnable.",
    "metis_vm_guest_handshake_verify": "Use to prove guest metisd answers runtime.hello. jsonl-stdio is a host-only protocol smoke; only future hcs-vsock-jsonl success may promote runner_ready.",
    "metis_vm_rootfs_boot_verifier_prepare": "Use after HCS starter prepare to generate rootfs boot verification scripts and a kernel cmdline matrix for root=/dev/sda, /dev/sda1, /dev/vda, and init candidates.",
    "metis_vm_rootfs_boot_verify": "Use to dry-run or explicitly execute HCS boot attempts across rootfs cmdline candidates. It does not mark runner_ready until a future guest metisd handshake verifier succeeds.",
    "metis_vm_bundle_status": "Use to inspect Claude-style / Metis VM Runtime Pack assets and Windows virtualization capability without starting a VM.",
    "metis_vm_pack_adopt_reference": "Use to create a reference-only Metis VM pack adoption plan from a Claude-style bundle; do not use it to mark third-party assets as Metis-owned.",
    "metis_vm_pack_scaffold": "Use to create a clean-room Metis VM Runtime Pack blueprint directory before real VM boot assets or a host runner exist.",
    "metis_wsl_runtime_status": "Use to inspect whether the Metis managed WSL runtime is installed or ready to import from a Metis rootfs asset.",
    "metis_wsl_runtime_import": "Use to plan or execute wsl --import for a Metis-owned rootfs asset. Keep dry_run=true unless the user has approved import.",
    "metis_sandbox_status": "Use before sandbox work to see whether VM Pack, WSL2, or Docker can run, and which backend auto mode will select.",
    "metis_runtime_create": "Use before background code execution, generated artifacts, reports, or risky experiments that should not directly modify the source project. Set backend=auto when sandbox isolation is useful.",
    "metis_runtime_run": "Use after metis_runtime_create to run scripts/tests/builds inside the isolated workspace. Save deliverables to METIS_RUNTIME_ARTIFACTS_DIR.",
    "metis_runtime_collect_artifacts": "Use after runtime commands to gather generated files from the isolated workspace into .metis/artifacts.",
    "metis_runtime_export_patch": "Use when isolated runtime changes should be handed back as a patch instead of directly editing the source project.",
    "metis_runtime_export_diagnostics": "Use after runtime failures to package logs, stdout/stderr, artifacts, and patch evidence.",
    "metis_runtime_status": "Use to inspect recent isolated runtime sessions and artifacts.",
    "check_dev_environment": "Use before running unfamiliar projects or when a command reports that Python, Node.js, Git, or another runtime is missing.",
    "install_dev_runtime": "Use only after a missing runtime is identified and installation is appropriate. Installs via winget on Windows.",
    "setup_workspace": "Use to prepare a project on a fresh machine by detecting runtimes and installing common dependencies.",
    "browse_web": "Use when a task needs a real browser to navigate, click, fill forms, or inspect dynamic pages.",
    "browse_and_extract": "Use when you know the URL and need specific information extracted through browser automation.",
    "web_research": "Use when the user asks for multi-source web evidence or a claim needs verification beyond one search result.",
    "fetch_content": "Use as the unified first choice for a known URL; it returns readable content plus structured source metadata and fallbacks.",
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
    "FetchContent": "fetch_content",
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

    def install_skill_tool(source: str, name: str = "") -> str:
        from backend.runtime.extension_installer import install_skill

        return install_skill(source, name=name)

    registry.register(
        ToolDefinition(
            name="install_skill",
            description=(
                "Install a Metis skill into the skills home so it loads now and after restart. "
                "Use this (NOT a shell download) when asked to add/install a skill — a bare "
                "download lands in the workspace and never loads. source may be a local directory, "
                "a .zip (local path or URL), or a git URL; it must contain a SKILL.md."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "source": {
                        "type": "string",
                        "description": "Local directory, .zip path/URL, or git URL containing SKILL.md.",
                    },
                    "name": {
                        "type": "string",
                        "description": "Optional skill name; defaults to the source folder name.",
                    },
                },
                "required": ["source"],
            },
            execute_fn=install_skill_tool,
            usage_hint="Use to install skills instead of shelling out into the workspace.",
            source="builtin",
            toolset="workflow",
            requires_approval=True,
            destructive=True,
        )
    )

    def install_mcp_server_tool(
        name: str,
        command: str = "",
        args: Any = None,
        env: Any = None,
        url: str = "",
    ) -> str:
        from backend.runtime.extension_installer import install_mcp_server

        arg_list = args if isinstance(args, list) else ([] if args is None else [str(args)])
        env_map = env if isinstance(env, dict) else None
        return install_mcp_server(name, command=command, args=arg_list, env=env_map, url=url)

    registry.register(
        ToolDefinition(
            name="install_mcp_server",
            description=(
                "Register an MCP server in Metis's own mcp.json and reload tools. Use this "
                "(NOT a shell edit of some other app's config) when asked to add/install an MCP "
                "server. Provide a stdio launcher (command + args) or a remote url. Put secrets "
                "in env values, never hard-coded in args."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Server name (key in mcpServers)."},
                    "command": {"type": "string", "description": "Launcher command, e.g. npx or uvx (stdio servers)."},
                    "args": {"type": "array", "items": {"type": "string"}, "description": "Launcher arguments."},
                    "env": {"type": "object", "description": "Environment variables (use for API keys / secrets)."},
                    "url": {"type": "string", "description": "Remote MCP endpoint URL (alternative to command)."},
                },
                "required": ["name"],
            },
            execute_fn=install_mcp_server_tool,
            usage_hint="Use to register MCP servers instead of editing external config files.",
            source="builtin",
            toolset="workflow",
            requires_approval=True,
            destructive=True,
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


def _compact_preview_diagnostics(value: Any) -> Dict[str, Any]:
    diagnostics = value if isinstance(value, dict) else {}
    return {
        "counts": diagnostics.get("counts", {}),
        "recent_console": list(diagnostics.get("recent_console") or [])[-8:],
        "exceptions": list(diagnostics.get("exceptions") or [])[-8:],
        "network_failed": list(diagnostics.get("network_failed") or [])[-8:],
        "page_failures": list(diagnostics.get("page_failures") or [])[-6:],
    }


def _compact_preview_browser_activity(value: Any) -> Dict[str, Any]:
    activity = value if isinstance(value, dict) else {}
    items: list[dict[str, Any]] = []
    for item in list(activity.get("items") or [])[-12:]:
        if not isinstance(item, dict):
            continue
        items.append(
            {
                "at": item.get("at", ""),
                "event": item.get("event", ""),
                "action": item.get("action", ""),
                "ok": item.get("ok", True),
                "blocked": item.get("blocked", False),
                "summary": item.get("summary", ""),
                "target": item.get("target", ""),
                "error": item.get("error", ""),
                "saved_path": item.get("saved_path", ""),
                "navigation_resolution": item.get("navigation_resolution", None),
            }
        )
    return {
        "url": activity.get("url", ""),
        "title": activity.get("title", ""),
        "counts": activity.get("counts", {}),
        "diagnostics_counts": activity.get("diagnostics_counts", {}),
        "items": items,
    }


def _preview_debug_info(value: Any) -> Dict[str, str]:
    result = value if isinstance(value, dict) else {}
    diagnostics = result.get("diagnostics", {}) if isinstance(result.get("diagnostics"), dict) else {}
    counts = diagnostics.get("counts", {}) if isinstance(diagnostics.get("counts"), dict) else {}
    page_health = result.get("page_health", {}) if isinstance(result.get("page_health"), dict) else {}
    screenshot_health = result.get("screenshot_health", {}) if isinstance(result.get("screenshot_health"), dict) else {}
    url = str(result.get("url") or "").strip()
    title = str(result.get("title") or "").strip()
    label = title or url or "当前 Preview 页面"
    if result.get("ok") is False:
        return {
            "debug_category": "preview_bridge_failed",
            "debug_summary": f"Preview Browser 调用失败:{result.get('error') or result.get('message') or '未返回成功状态'}。",
            "debug_next_action": "检查右栏 Preview 是否打开、目标端口是否监听,然后重新 observe。",
        }
    if bool(page_health.get("blank")):
        reasons = ", ".join(str(item) for item in (page_health.get("reasons") or [])[:3])
        return {
            "debug_category": "blank_page",
            "debug_summary": f"Preview 页面疑似白屏:{reasons or label}。",
            "debug_next_action": "查看 console error、failed network 和当前 URL/端口是否正确。",
        }
    if bool(screenshot_health.get("appears_blank")):
        return {
            "debug_category": "blank_screenshot",
            "debug_summary": "Preview 截图疑似纯白/纯黑,页面可能未渲染或被遮挡。",
            "debug_next_action": "重新截图并检查 page_health、viewport 和 failed network。",
        }
    if int(counts.get("exceptions") or 0) > 0 or int(counts.get("console_errors") or 0) > 0:
        return {
            "debug_category": "console_errors",
            "debug_summary": f"Preview 页面有 JS 异常/console error:{counts}。",
            "debug_next_action": "优先查看 diagnostics.recent_console 和 diagnostics.exceptions。",
        }
    if int(counts.get("network_failed") or 0) > 0:
        return {
            "debug_category": "network_failed",
            "debug_summary": f"Preview 页面有失败网络请求:{counts.get('network_failed')} 个。",
            "debug_next_action": "检查 failed network 列表里的 URL、状态码和本地端口。",
        }
    return {
        "debug_category": "ok",
        "debug_summary": f"Preview Browser 状态正常:{label}。",
        "debug_next_action": "",
    }


def _preview_text(value: Any) -> str:
    return str(value or "").strip()


def _preview_casefold(value: Any) -> str:
    return _preview_text(value).casefold()


def _preview_contains(haystack: Any, needle: Any) -> bool:
    query = _preview_casefold(needle)
    if not query:
        return True
    return query in _preview_casefold(haystack)


def _preview_element_blob(element: Dict[str, Any]) -> str:
    parts = [
        element.get("text", ""),
        element.get("ariaLabel", ""),
        element.get("placeholder", ""),
        element.get("labelText", ""),
        element.get("name", ""),
        element.get("title", ""),
        element.get("href", ""),
        element.get("selector", ""),
    ]
    return " ".join(_preview_text(part) for part in parts if _preview_text(part))


def _preview_element_matches(element: Dict[str, Any], query: str) -> bool:
    return not query or _preview_contains(_preview_element_blob(element), query)


def _preview_element_is_button(element: Dict[str, Any]) -> bool:
    tag = _preview_casefold(element.get("tag", ""))
    role = _preview_casefold(element.get("role", ""))
    input_type = _preview_casefold(element.get("type", ""))
    button_type = _preview_casefold(element.get("buttonType", ""))
    return (
        tag == "button"
        or role == "button"
        or input_type in {"button", "submit", "reset"}
        or button_type in {"button", "submit", "reset"}
    )


def _preview_element_is_input(element: Dict[str, Any]) -> bool:
    tag = _preview_casefold(element.get("tag", ""))
    input_type = _preview_casefold(element.get("type", ""))
    role = _preview_casefold(element.get("role", ""))
    return (
        tag in {"input", "textarea", "select"}
        or role in {"textbox", "combobox", "searchbox"}
        or bool(element.get("isContentEditable"))
        or input_type in {"text", "email", "search", "url", "tel", "number", "password"}
    )


def _preview_element_clickable(element: Dict[str, Any]) -> bool:
    rect = element.get("rect") if isinstance(element.get("rect"), dict) else {}
    width = float(rect.get("width") or 0)
    height = float(rect.get("height") or 0)
    return not bool(element.get("disabled")) and width > 0 and height > 0


def _preview_element_editable(element: Dict[str, Any]) -> bool:
    input_type = _preview_casefold(element.get("type", ""))
    if input_type in {"hidden", "button", "submit", "reset", "checkbox", "radio", "file"}:
        return False
    return _preview_element_is_input(element) and not bool(element.get("disabled")) and not bool(element.get("readOnly"))


def _compact_preview_element(element: Dict[str, Any] | None) -> Dict[str, Any]:
    if not isinstance(element, dict):
        return {}
    return {
        key: element.get(key)
        for key in [
            "element_id",
            "tag",
            "role",
            "type",
            "text",
            "ariaLabel",
            "placeholder",
            "labelText",
            "name",
            "disabled",
            "readOnly",
            "rect",
        ]
        if element.get(key) not in (None, "")
    }


def _find_preview_element(
    elements: List[Dict[str, Any]],
    query: str = "",
    predicate: Callable[[Dict[str, Any]], bool] | None = None,
) -> Dict[str, Any] | None:
    for element in elements:
        if not isinstance(element, dict):
            continue
        if predicate is not None and not predicate(element):
            continue
        if _preview_element_matches(element, query):
            return element
    return None


def _extract_preview_natural_target(assertion: str, nouns: List[str]) -> str:
    text = _preview_text(assertion)
    if not text:
        return ""
    noun_pattern = "|".join(re.escape(noun) for noun in nouns)
    patterns = [
        rf"(?:确认|检查|确保|验证|看看|看下|请)?\s*([^，。,.、\s]{{1,40}}?)(?:{noun_pattern})",
        rf"([A-Za-z0-9 _-]{{1,60}}?)\s+(?:{noun_pattern})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        target = match.group(1).strip()
        target = re.sub(r"^(页面|当前|这个|那个|有|存在|出现|the|a|an)\s*", "", target, flags=re.IGNORECASE)
        target = re.sub(r"(是否|有没有|能否|可以)?$", "", target).strip()
        if target:
            return target
    return ""


def _extract_preview_visible_text(assertion: str) -> str:
    text = _preview_text(assertion)
    if not text:
        return ""
    quoted = re.search(r"[\"'“”‘’「」『』]([^\"'“”‘’「」『』]{1,80})[\"'“”‘’「」『』]", text)
    if quoted:
        return quoted.group(1).strip()
    patterns = [
        r"(?:出现|显示|看到|包含|提示)\s*([^，。,.；;!?！？]{1,80}?)(?:提示|文案|文本|toast|message)?(?:$|[，。,.；;!?！？])",
        r"(?:success|successful|succeeded|submitted|saved)\s+([^，。,.；;!?！？]{1,80})",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if not match:
            continue
        value = match.group(1).strip()
        value = re.sub(r"^(成功|错误|失败|the|a|an)\s*", "", value, flags=re.IGNORECASE).strip()
        if value and value not in {"成功", "成功提示", "提示", "页面"}:
            return value
    if any(word in text for word in ["成功提示", "提交成功", "保存成功", "已提交", "已保存"]):
        for value in ["提交成功", "保存成功", "成功", "已提交", "已保存"]:
            if value in text:
                return value
        return "成功"
    return ""


def _preview_assertion_has_any(assertion: str, words: List[str]) -> bool:
    folded = _preview_casefold(assertion)
    return any(_preview_casefold(word) in folded for word in words)


def _preview_diagnostics_has_console_errors(diagnostics: Dict[str, Any]) -> bool:
    counts = diagnostics.get("counts", {}) if isinstance(diagnostics, dict) else {}
    return bool(
        int(counts.get("console_errors") or 0) > 0
        or int(counts.get("exceptions") or 0) > 0
    )


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
        win2_attempt: dict[str, Any] | None = None

        def _compact_win2_attempt(payload: dict[str, Any] | None) -> dict[str, Any] | None:
            if not isinstance(payload, dict):
                return None
            return {
                "provider": payload.get("provider"),
                "ok": bool(payload.get("ok")),
                "status": payload.get("status"),
                "error": payload.get("error", ""),
                "fallback_recommended": bool(payload.get("fallback_recommended", False)),
                "hwnd": payload.get("hwnd"),
                "title": payload.get("title"),
                "steps": payload.get("steps"),
            }

        if exec_mode in ("auto", "skill") and os.environ.get("METIS_DESKTOP_WIN2_AUTO", "1").strip().lower() not in {"0", "false", "no", "off"}:
            from backend.tools.desk_automation.providers.win2_loop import (
                format_tool_result,
                run_task as _run_win2_task,
            )

            win2_result = _run_win2_task(goal=goal, max_steps=max_steps)
            win2_attempt = _compact_win2_attempt(win2_result)
            if win2_result.get("ok") or not win2_result.get("fallback_recommended", False):
                return format_tool_result(win2_result)

        from backend.tools.desk_automation.orchestrator.vision_loop import get_state, start

        start_result = start(goal=goal, max_steps=max_steps, exec_mode=exec_mode)
        if not start_result.get("ok"):
            if win2_attempt:
                start_result = dict(start_result)
                start_result["win2_attempt"] = win2_attempt
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
        if win2_attempt:
            summary["win2_attempt"] = win2_attempt
        return json.dumps(summary, ensure_ascii=False, indent=2)

    def desktop_win2_status() -> str:
        """Report Window2-style provider health and visible windows."""
        from backend.tools.desk_automation.providers.win2_loop import format_tool_result, status

        return format_tool_result(status())

    def desktop_win2_observe(
        hwnd: int = 0,
        title: str = "",
        include_ocr: bool = False,
    ) -> str:
        """Capture one target window and return structured observation."""
        from backend.tools.desk_automation.providers.win2_loop import format_tool_result, observe

        return format_tool_result(observe(hwnd=hwnd, title=title, include_ocr=include_ocr))

    def desktop_win2_action(
        hwnd: int,
        action: str,
        x: int = 0,
        y: int = 0,
        text: str = "",
        key: str = "",
        keys: list[str] | str | None = None,
        scroll_delta: int = 0,
        start_x: int = 0,
        start_y: int = 0,
        end_x: int = 0,
        end_y: int = 0,
    ) -> str:
        """Run one Window2-style action against a target window."""
        from backend.tools.desk_automation.providers.win2_loop import act as _win2_act, format_tool_result

        return format_tool_result(
            _win2_act(
                hwnd=hwnd,
                action=action,
                x=x,
                y=y,
                text=text,
                key=key,
                keys=keys,
                scroll_delta=scroll_delta,
                start_x=start_x,
                start_y=start_y,
                end_x=end_x,
                end_y=end_y,
            )
        )

    def desktop_win2_task(
        goal: str,
        max_steps: int = 20,
    ) -> str:
        """Run a Window2-style observe-plan-act-verify loop."""
        from backend.tools.desk_automation.providers.win2_loop import format_tool_result, run_task

        return format_tool_result(run_task(goal=goal, max_steps=max_steps))

    def desktop_win2_verify(
        hwnd: int = 0,
        title: str = "",
        assertion: str = "",
        text_contains: str = "",
        title_contains: str = "",
        exe_contains: str = "",
        require_foreground: bool = False,
        require_text_visible: bool = False,
        require_screenshot: bool = True,
        include_ocr: bool = False,
    ) -> str:
        """Verify a desktop window state with screenshot/accessibility evidence."""
        from backend.tools.desk_automation.providers.win2_loop import format_tool_result, verify as _win2_verify

        return format_tool_result(
            _win2_verify(
                hwnd=hwnd,
                title=title,
                assertion=assertion,
                text_contains=text_contains,
                title_contains=title_contains,
                exe_contains=exe_contains,
                require_foreground=require_foreground,
                require_text_visible=require_text_visible,
                require_screenshot=require_screenshot,
                include_ocr=include_ocr,
            )
        )

    def _preview_bridge_json(kind: str, payload: dict[str, Any] | None = None, timeout: float = 12.0) -> str:
        from backend.web.preview_bridge import request_preview_command

        result = request_preview_command(kind, payload or {}, timeout=timeout)
        if isinstance(result, dict):
            result = {**result, **_preview_debug_info(result)}
        return json.dumps(result, ensure_ascii=False, indent=2)

    def preview_browser_status() -> str:
        """Report the Electron Preview bridge health."""
        from backend.web.preview_bridge import preview_bridge_status

        return json.dumps(preview_bridge_status(), ensure_ascii=False, indent=2)

    def preview_browser_navigate(url: str = "", tab_id: str = "", timeout: int = 15) -> str:
        """Navigate the right-rail Preview browser to a URL."""
        return _preview_bridge_json("navigate", {"url": url, "tabId": tab_id}, timeout=timeout)

    def preview_browser_observe(max_elements: int = 80, include_text: bool = True, timeout: int = 12) -> str:
        """Observe the active right-rail Preview page as structured browser state."""
        return _preview_bridge_json(
            "observe",
            {"maxElements": max_elements, "includeText": include_text},
            timeout=timeout,
        )

    def preview_browser_action(
        action: str,
        element_id: str = "",
        x: int = 0,
        y: int = 0,
        text: str = "",
        key: str = "",
        scroll_y: int = 0,
        timeout: int = 12,
    ) -> str:
        """Perform one action inside the right-rail Preview browser."""
        return _preview_bridge_json(
            "action",
            {
                "action": action,
                "elementId": element_id,
                "x": x,
                "y": y,
                "text": text,
                "key": key,
                "scrollY": scroll_y,
            },
            timeout=timeout,
        )

    def preview_browser_screenshot(timeout: int = 12) -> str:
        """Capture the right-rail Preview page and save it to a local PNG."""
        import base64
        import re

        from backend.web.preview_bridge import request_preview_command

        result = request_preview_command("screenshot", {}, timeout=timeout)
        if not result.get("ok"):
            return json.dumps(result, ensure_ascii=False, indent=2)
        data_url = str(result.get("dataUrl") or "")
        match = re.match(r"^data:image/png;base64,(.+)$", data_url)
        if not match:
            result = dict(result)
            result.pop("dataUrl", None)
            result["ok"] = False
            result["error"] = "preview screenshot did not return a PNG data URL"
            return json.dumps(result, ensure_ascii=False, indent=2)
        png_bytes = base64.b64decode(match.group(1))
        path = os.path.join(tempfile.gettempdir(), f"metis_preview_browser_{int(time.time() * 1000)}.png")
        with open(path, "wb") as handle:
            handle.write(png_bytes)
        width, height = _png_dimensions(png_bytes)
        compact = {
            "ok": True,
            "path": path,
            "width": width or result.get("width", 0),
            "height": height or result.get("height", 0),
            "url": result.get("url", ""),
            "title": result.get("title", ""),
            "viewport": result.get("viewport", None),
            "page_health": result.get("page_health", {}),
            "screenshot_health": result.get("screenshot_health", {}),
            "diagnostics": _compact_preview_diagnostics(result.get("diagnostics", {})),
            "browser_activity": _compact_preview_browser_activity(result.get("browser_activity", {})),
            **_preview_debug_info(result),
        }
        return json.dumps(compact, ensure_ascii=False, indent=2)

    def preview_browser_verify(
        text_contains: str = "",
        url_contains: str = "",
        title_contains: str = "",
        assertion: str = "",
        button_text: str = "",
        input_label: str = "",
        visible_text: str = "",
        not_visible_text: str = "",
        require_button: bool = False,
        require_button_clickable: bool = False,
        require_input: bool = False,
        require_input_editable: bool = False,
        require_no_blank: bool = False,
        require_no_console_errors: bool = False,
        require_no_network_failures: bool = False,
        require_screenshot_not_blank: bool = False,
        timeout: int = 12,
    ) -> str:
        """Verify structured acceptance checks against the current Preview browser page."""
        from backend.web.preview_bridge import request_preview_command

        assertion_text = _preview_text(assertion)
        if assertion_text:
            if not visible_text:
                visible_text = _extract_preview_visible_text(assertion_text)
            if not button_text:
                button_text = _extract_preview_natural_target(assertion_text, ["按钮", "button"])
            if not input_label:
                input_label = _extract_preview_natural_target(
                    assertion_text,
                    ["输入框", "输入栏", "文本框", "input", "field", "textbox"],
                )
            if _preview_assertion_has_any(assertion_text, ["按钮", "button"]):
                require_button = True
            if _preview_assertion_has_any(assertion_text, ["可点击", "能点击", "clickable", "can click"]):
                require_button_clickable = True
            if _preview_assertion_has_any(assertion_text, ["输入框", "输入栏", "文本框", "input", "field", "textbox"]):
                require_input = True
            if _preview_assertion_has_any(assertion_text, ["可输入", "能输入", "可编辑", "editable", "typeable", "can type"]):
                require_input_editable = True
            if _preview_assertion_has_any(assertion_text, ["白屏", "空白页", "blank page", "not blank", "no blank"]):
                require_no_blank = True
            if _preview_assertion_has_any(assertion_text, ["console", "控制台", "报错", "错误"]) and _preview_assertion_has_any(
                assertion_text,
                ["没有", "无", "no", "not", "error", "错误", "报错"],
            ):
                require_no_console_errors = True
            if _preview_assertion_has_any(assertion_text, ["network", "请求失败", "failed request"]) and _preview_assertion_has_any(
                assertion_text,
                ["没有", "无", "no", "not"],
            ):
                require_no_network_failures = True
            if _preview_assertion_has_any(assertion_text, ["截图", "screenshot"]) and _preview_assertion_has_any(
                assertion_text,
                ["纯白", "纯黑", "空白", "blank", "white", "black"],
            ):
                require_screenshot_not_blank = True

        if button_text:
            require_button = True
        if require_button_clickable:
            require_button = True
        if input_label:
            require_input = True
        if require_input_editable:
            require_input = True

        observed = request_preview_command(
            "observe",
            {"maxElements": 80, "includeText": True},
            timeout=timeout,
        )
        if not observed.get("ok"):
            return json.dumps(observed, ensure_ascii=False, indent=2)
        haystack = str(observed.get("text") or "")
        url = str(observed.get("url") or "")
        title = str(observed.get("title") or "")
        diagnostics = observed.get("diagnostics", {})
        page_health = observed.get("page_health", {})
        dom_summary = observed.get("dom_summary", {})
        elements = [element for element in (observed.get("elements") or []) if isinstance(element, dict)]
        checks: Dict[str, bool] = {}
        check_details: Dict[str, Any] = {}

        def add_check(name: str, ok: bool, detail: Dict[str, Any] | None = None) -> None:
            checks[name] = bool(ok)
            if detail is not None:
                check_details[name] = detail

        add_check("text_contains", _preview_contains(haystack, text_contains), {"query": text_contains} if text_contains else None)
        add_check("url_contains", _preview_contains(url, url_contains), {"query": url_contains, "url": url} if url_contains else None)
        add_check("title_contains", _preview_contains(title, title_contains), {"query": title_contains, "title": title} if title_contains else None)

        if visible_text:
            visible_element = _find_preview_element(elements, visible_text)
            add_check(
                "visible_text",
                _preview_contains(haystack, visible_text) or visible_element is not None,
                {"query": visible_text, "matched_element": _compact_preview_element(visible_element)},
            )

        if not_visible_text:
            hidden_match = _find_preview_element(elements, not_visible_text)
            add_check(
                "not_visible_text",
                not _preview_contains(haystack, not_visible_text) and hidden_match is None,
                {"query": not_visible_text, "matched_visible_element": _compact_preview_element(hidden_match)},
            )

        button = _find_preview_element(elements, button_text, _preview_element_is_button)
        if require_button:
            add_check(
                "button_visible",
                button is not None,
                {"query": button_text, "matched_element": _compact_preview_element(button)},
            )
        if require_button_clickable:
            add_check(
                "button_clickable",
                button is not None and _preview_element_clickable(button),
                {"query": button_text, "matched_element": _compact_preview_element(button)},
            )

        input_element = _find_preview_element(elements, input_label, _preview_element_is_input)
        if require_input:
            add_check(
                "input_visible",
                input_element is not None,
                {"query": input_label, "matched_element": _compact_preview_element(input_element)},
            )
        if require_input_editable:
            add_check(
                "input_editable",
                input_element is not None and _preview_element_editable(input_element),
                {"query": input_label, "matched_element": _compact_preview_element(input_element)},
            )

        if require_no_blank:
            reasons = page_health.get("reasons", []) if isinstance(page_health, dict) else []
            blank = bool(page_health.get("blank")) if isinstance(page_health, dict) else False
            add_check(
                "page_not_blank",
                not blank,
                {"page_health": page_health, "blank_reasons": reasons},
            )

        if require_no_console_errors:
            counts = diagnostics.get("counts", {}) if isinstance(diagnostics, dict) else {}
            add_check(
                "no_console_errors",
                not _preview_diagnostics_has_console_errors(diagnostics if isinstance(diagnostics, dict) else {}),
                {"counts": counts},
            )

        if require_no_network_failures:
            counts = diagnostics.get("counts", {}) if isinstance(diagnostics, dict) else {}
            add_check(
                "no_network_failures",
                int(counts.get("network_failed") or 0) == 0,
                {"counts": counts},
            )

        screenshot_summary: Dict[str, Any] = {}
        if require_screenshot_not_blank:
            screenshot_result = request_preview_command("screenshot", {}, timeout=timeout)
            screenshot_health = screenshot_result.get("screenshot_health", {}) if isinstance(screenshot_result, dict) else {}
            screenshot_summary = {
                "ok": bool(screenshot_result.get("ok")) if isinstance(screenshot_result, dict) else False,
                "url": screenshot_result.get("url", "") if isinstance(screenshot_result, dict) else "",
                "title": screenshot_result.get("title", "") if isinstance(screenshot_result, dict) else "",
                "width": screenshot_result.get("width", 0) if isinstance(screenshot_result, dict) else 0,
                "height": screenshot_result.get("height", 0) if isinstance(screenshot_result, dict) else 0,
                "page_health": screenshot_result.get("page_health", {}) if isinstance(screenshot_result, dict) else {},
                "screenshot_health": screenshot_health,
            }
            add_check(
                "screenshot_not_blank",
                bool(screenshot_result.get("ok")) and isinstance(screenshot_health, dict) and not bool(screenshot_health.get("appears_blank")),
                {"screenshot_health": screenshot_health},
            )

        diagnostics_compact = _compact_preview_diagnostics(diagnostics)
        evidence_seed: list[dict[str, Any]] = [
            {
                "kind": "page",
                "ok": True,
                "url": url,
                "title": title,
                "summary": f"Observed Preview page: {title or url or 'current page'}",
                "detail": {"dom_summary": dom_summary, "page_health": page_health},
            },
            {
                "kind": "diagnostics",
                "ok": True,
                "summary": "Preview diagnostics captured",
                "detail": diagnostics_compact,
            },
        ]
        if require_screenshot_not_blank:
            evidence_seed.append(
                {
                    "kind": "screenshot",
                    "ok": bool(screenshot_summary.get("ok")),
                    "url": screenshot_summary.get("url", ""),
                    "title": screenshot_summary.get("title", ""),
                    "summary": "Preview screenshot captured for visual verification",
                    "detail": screenshot_summary,
                }
            )
        verification = build_verifier_evidence_payload(
            surface="preview_browser",
            assertion=assertion_text,
            checks=checks,
            check_details=check_details,
            evidence=evidence_seed,
            subject={"url": url, "title": title},
        )
        payload = {
            "ok": bool(verification["verdict"]["ok"]),
            "checks": checks,
            "check_details": check_details,
            "assertion": assertion_text,
            "url": url,
            "title": title,
            "text_preview": haystack[:1000],
            "matched_elements": {
                "button": _compact_preview_element(button),
                "input": _compact_preview_element(input_element),
            },
            "dom_summary": dom_summary,
            "page_health": page_health,
            "screenshot": screenshot_summary,
            "diagnostics": diagnostics_compact,
            "browser_activity": _compact_preview_browser_activity(observed.get("browser_activity", {})),
            **_preview_debug_info(
                {
                    **observed,
                    "screenshot_health": screenshot_summary.get("screenshot_health", {}),
                }
            ),
            **verification,
            "evidence_chain": verification["evidence_chain_v2"],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)

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
            name="desktop_win2_status",
            description=(
                "Read-only health check for the Window2-style desktop provider. "
                "Lists visible windows and launch shortcuts without taking actions."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=desktop_win2_status,
            source="desktop",
            requires_approval=False,
            destructive=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="desktop_win2_observe",
            description=(
                "Observe a specific desktop window using the Window2-style provider. "
                "Returns window metadata and a saved screenshot path. Coordinates for "
                "later desktop_win2_action calls are relative to this screenshot."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "hwnd": {"type": "integer", "description": "Window handle."},
                    "title": {"type": "string", "description": "Title substring if hwnd is not known."},
                    "include_ocr": {"type": "boolean", "description": "Best-effort OCR text extraction."},
                },
                "required": [],
            },
            execute_fn=desktop_win2_observe,
            source="desktop",
            requires_approval=False,
            destructive=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="desktop_win2_action",
            description=(
                "Perform a single Window2-style action inside a specific window. "
                "Use window-relative coordinates from desktop_win2_observe. "
                "Supported actions: activate, click, double_click, right_click, "
                "type, key, hotkey, scroll, drag, move, wait. Scroll actions verify "
                "visible movement and fall back to PageUp/PageDown or Ctrl+Home/End "
                "when wheel scrolling does not visibly move the target."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "hwnd": {"type": "integer", "description": "Window handle."},
                    "action": {
                        "type": "string",
                        "enum": [
                            "activate",
                            "click",
                            "double_click",
                            "right_click",
                            "type",
                            "key",
                            "hotkey",
                            "scroll",
                            "drag",
                            "move",
                            "wait",
                        ],
                    },
                    "x": {"type": "integer", "description": "Window-relative X."},
                    "y": {"type": "integer", "description": "Window-relative Y."},
                    "text": {"type": "string", "description": "Text to type."},
                    "key": {"type": "string", "description": "Key name to press."},
                    "keys": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Keys for hotkey actions, e.g. ['ctrl', 'l'].",
                    },
                    "scroll_delta": {"type": "integer", "description": "Scroll amount. Negative = down."},
                    "start_x": {"type": "integer", "description": "Drag start X, window-relative."},
                    "start_y": {"type": "integer", "description": "Drag start Y, window-relative."},
                    "end_x": {"type": "integer", "description": "Drag end X, window-relative."},
                    "end_y": {"type": "integer", "description": "Drag end Y, window-relative."},
                },
                "required": ["hwnd", "action"],
            },
            execute_fn=desktop_win2_action,
            source="desktop",
        )
    )
    registry.register(
        ToolDefinition(
            name="desktop_win2_task",
            description=(
                "Preferred high-level Computer Use tool for desktop apps. Runs a "
                "Window2-style observe -> plan -> act -> verify loop with window "
                "capture and window-relative actions, then falls back to legacy "
                "vision when the target window cannot be resolved."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "goal": {"type": "string", "description": "Desktop task goal."},
                    "max_steps": {"type": "integer", "description": "Maximum action steps."},
                },
                "required": ["goal"],
            },
            execute_fn=desktop_win2_task,
            source="desktop",
        )
    )
    registry.register(
        ToolDefinition(
            name="desktop_win2_verify",
            description=(
                "Computer Use verifier for a desktop window. Captures the target "
                "window and returns checks plus an evidence_chain explaining why "
                "the task is considered complete or failed. Use after clicks, "
                "window switches, and text entry to verify title/exe, visible text, "
                "foreground focus, OCR/accessibility matches, and screenshot evidence."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "hwnd": {"type": "integer", "description": "Window handle. Optional when title is provided."},
                    "title": {"type": "string", "description": "Window title substring when hwnd is unknown."},
                    "assertion": {"type": "string", "description": "One-sentence acceptance assertion, e.g. 检查输入框是否真的写入 hello."},
                    "text_contains": {"type": "string", "description": "Text expected in title/accessibility/OCR."},
                    "title_contains": {"type": "string", "description": "Window title substring expected."},
                    "exe_contains": {"type": "string", "description": "Executable name substring expected."},
                    "require_foreground": {"type": "boolean", "description": "Require the window to be foreground/focused."},
                    "require_text_visible": {"type": "boolean", "description": "Require text_contains/assertion text to be visible."},
                    "require_screenshot": {"type": "boolean", "description": "Require a captured screenshot path and dimensions."},
                    "include_ocr": {"type": "boolean", "description": "Run OCR for text verification when accessibility is insufficient."},
                },
                "required": [],
            },
            execute_fn=desktop_win2_verify,
            source="desktop",
            requires_approval=False,
            destructive=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="preview_browser_status",
            description=(
                "Read-only health check for the right-rail Preview browser bridge. "
                "Use before preview_browser_* tools if Preview actions time out."
            ),
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=preview_browser_status,
            source="desktop",
            requires_approval=False,
            destructive=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="preview_browser_navigate",
            description=(
                "Navigate the right-rail Preview browser. This uses the built-in "
                "Preview card, not an external browser. Local URLs are auto-resolved: "
                "bare localhost/current page requests can use the current Preview URL, "
                "METIS_DESKTOP_DEV_SERVER, running dev-server status, or common ports "
                "5173/5174/3000/4200/8000/8080 when the requested port is missing or down."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string", "description": "URL to open in Preview. May be blank/current, bare localhost, localhost:PORT, or http(s)."},
                    "tab_id": {"type": "string", "description": "Optional Preview tab id."},
                    "timeout": {"type": "integer", "description": "Bridge timeout in seconds."},
                },
                "required": [],
            },
            execute_fn=preview_browser_navigate,
            source="desktop",
            requires_approval=False,
            destructive=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="preview_browser_observe",
            description=(
                "Observe the current right-rail Preview page. Returns URL, title, "
                "viewport, visible text, and interactable elements with element_id "
                "and coordinates for preview_browser_action. Also returns diagnostics "
                "for console warnings/errors, JavaScript exceptions, failed network requests, "
                "page load failures, DOM summary, and page_health for blank-page debugging."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "max_elements": {"type": "integer", "description": "Max interactable elements to return."},
                    "include_text": {"type": "boolean", "description": "Include visible page text."},
                    "timeout": {"type": "integer", "description": "Bridge timeout in seconds."},
                },
                "required": [],
            },
            execute_fn=preview_browser_observe,
            source="desktop",
            requires_approval=False,
            destructive=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="preview_browser_action",
            description=(
                "Perform one action inside the right-rail Preview browser, then observe "
                "again to verify. Supported actions: click, double_click, type, key, "
                "scroll, wait. Use element_id from preview_browser_observe when possible, "
                "or x/y viewport coordinates. The Electron execution layer hard-blocks "
                "risky webpage actions (login/OAuth, submit, upload, send, purchase, "
                "delete, payment, password/file inputs) and asks the user to confirm "
                "before any input event is sent."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["click", "double_click", "type", "key", "scroll", "wait"],
                    },
                    "element_id": {"type": "string", "description": "Element id from preview_browser_observe."},
                    "x": {"type": "integer", "description": "Preview viewport X coordinate."},
                    "y": {"type": "integer", "description": "Preview viewport Y coordinate."},
                    "text": {"type": "string", "description": "Text for type action."},
                    "key": {"type": "string", "description": "Key name for key action, e.g. Enter, Tab, Escape."},
                    "scroll_y": {"type": "integer", "description": "Scroll amount in CSS pixels; positive scrolls down."},
                    "timeout": {"type": "integer", "description": "Bridge timeout in seconds."},
                },
                "required": ["action"],
            },
            execute_fn=preview_browser_action,
            source="desktop",
            requires_approval=False,
            destructive=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="preview_browser_screenshot",
            description=(
                "Capture the current right-rail Preview page and save a PNG for visual "
                "verification. The result includes URL, title, viewport, page_health, "
                "screenshot_health for pure white/black detection, and compact diagnostics "
                "for failed requests and page errors."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "timeout": {"type": "integer", "description": "Bridge timeout in seconds."},
                },
                "required": [],
            },
            execute_fn=preview_browser_screenshot,
            source="desktop",
            requires_approval=False,
            destructive=False,
        )
    )
    registry.register(
        ToolDefinition(
            name="preview_browser_verify",
            description=(
                "Browser Verifier for the current Preview page after a browser action. "
                "Checks URL/title/text, button visibility/clickability, input editability, "
                "visible or hidden text, no blank page, no console/network errors, and "
                "screenshot not pure white/black. Supports one-sentence assertions such as "
                "'确认登录按钮可见并可点击'. Returns DOM summary plus diagnostics."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text_contains": {"type": "string", "description": "Visible text that should be present."},
                    "url_contains": {"type": "string", "description": "URL substring that should be present."},
                    "title_contains": {"type": "string", "description": "Title substring that should be present."},
                    "assertion": {"type": "string", "description": "One-sentence acceptance assertion, e.g. 确认登录按钮可见并可点击."},
                    "button_text": {"type": "string", "description": "Button label/name/aria text that should be visible."},
                    "input_label": {"type": "string", "description": "Input label/placeholder/name text that should be visible or editable."},
                    "visible_text": {"type": "string", "description": "Text or element label that should be visible."},
                    "not_visible_text": {"type": "string", "description": "Text or element label that should not be visible."},
                    "require_button": {"type": "boolean", "description": "Require a matching visible button."},
                    "require_button_clickable": {"type": "boolean", "description": "Require the matching button to be clickable/not disabled."},
                    "require_input": {"type": "boolean", "description": "Require a matching visible input/control."},
                    "require_input_editable": {"type": "boolean", "description": "Require the matching input/control to accept typing."},
                    "require_no_blank": {"type": "boolean", "description": "Require page_health.blank to be false."},
                    "require_no_console_errors": {"type": "boolean", "description": "Require console error and JS exception counts to be zero."},
                    "require_no_network_failures": {"type": "boolean", "description": "Require failed network request count to be zero."},
                    "require_screenshot_not_blank": {"type": "boolean", "description": "Capture a screenshot and require it not to be pure white/black/flat."},
                    "timeout": {"type": "integer", "description": "Bridge timeout in seconds."},
                },
                "required": [],
            },
            execute_fn=preview_browser_verify,
            source="desktop",
            requires_approval=False,
            destructive=False,
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

    elif canonical == "web_research":
        if "query" in kwargs and "question" not in kwargs:
            kwargs["question"] = kwargs.pop("query")
        if "search_term" in kwargs and "question" not in kwargs:
            kwargs["question"] = kwargs.pop("search_term")

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

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List

from backend.bridges.model_capability import detect_from_model_name
from backend.bridges.provider_registry import resolve_provider_for_config
from backend.runtime.sandbox_boundary import boundary_policy_for_task


@dataclass(frozen=True)
class TaskRoute:
    task_type: str
    model_role: str
    selected_model: str
    execution_boundary: str = "direct"
    runtime_policy: Dict[str, Any] = field(default_factory=dict)
    fallback_models: List[str] = field(default_factory=list)
    preferred_tools: List[str] = field(default_factory=list)
    reason: str = ""
    tool_guidance: str = ""


ROUTE_MARKER = "[Metis routing]"

_PREVIEW_BROWSER_TOOLS = [
    "preview_browser_status",
    "preview_browser_navigate",
    "preview_browser_observe",
    "preview_browser_action",
    "preview_browser_verify",
    "preview_browser_screenshot",
]
_DESKTOP_TOOLS = [
    "desktop_win2_status",
    "desktop_win2_observe",
    "desktop_win2_action",
    "desktop_win2_verify",
    "desktop_win2_task",
    "desktop_vision_task",
    "desktop_screenshot",
    "desktop_action",
]
_DESKTOP_OBSERVE_TOOLS = [
    "desktop_win2_status",
    "desktop_win2_observe",
    "desktop_win2_verify",
    "desktop_screenshot",
    "desktop_inventory",
    "desktop_window_list",
    "desktop_window_capture",
]
_DESKTOP_CONTROL_TOOLS = [
    "desktop_win2_action",
    "desktop_win2_task",
    "desktop_vision_task",
    "desktop_action",
    "desktop_window_action",
]
_CODE_TOOLS = [
    "metis_runtime_job",
    "metis_runtime_job_status",
    "generate_repo_map",
    "grep_search",
    "glob_search",
    "read_file",
    "read_multiple_files",
    "robust_replace_in_file",
    "write_file",
    "append_to_file",
    "run_tests",
    "check_git_status",
    "git_diff",
    "metis_rootfs_asset_status",
    "metis_rootfs_source_status",
    "metis_rootfs_asset_download",
    "metis_rootfs_asset_register",
    "metis_rootfs_builder_status",
    "metis_rootfs_build",
    "metis_rootfs_image_builder_status",
    "metis_rootfs_image_build",
    "metis_runtime_bundle_package",
    "metis_runtime_bundle_package_v2",
    "metis_runtime_bundle_prepare",
    "metis_vm_direct_assets_prepare",
    "metis_vm_direct_runner_prepare",
    "metis_vm_direct_runner_smoke",
    "metis_vm_hcs_starter_prepare",
    "metis_vm_hcs_starter_start",
    "metis_vm_guest_handshake_prepare",
    "metis_vm_guest_handshake_verify",
    "metis_vm_rootfs_boot_verifier_prepare",
    "metis_vm_rootfs_boot_verify",
    "metis_vm_bundle_status",
    "metis_vm_pack_adopt_reference",
    "metis_vm_pack_scaffold",
    "metis_wsl_runtime_status",
    "metis_wsl_runtime_import",
    "metis_sandbox_status",
    "metis_runtime_create",
    "metis_runtime_run",
    "metis_runtime_collect_artifacts",
    "metis_runtime_export_patch",
    "metis_runtime_export_diagnostics",
]
_ARTIFACT_WORKFLOW_TOOLS = [
    "metis_runtime_job",
    "metis_runtime_job_status",
    "load_skill",
    "read_file",
    "read_multiple_files",
    "write_file",
    "append_to_file",
    "metis_sandbox_status",
    "metis_runtime_create",
    "metis_runtime_run",
    "metis_runtime_collect_artifacts",
    "metis_runtime_export_patch",
    "metis_runtime_export_diagnostics",
    "metis_runtime_status",
    "office_report_from_code_run",
    "docx_create",
    "docx_edit",
    "docx_inspect_layout",
    "docx_render_pages",
    "docx_to_pdf",
    "pdf_create",
    "pdf_extract_text",
    "pdf_info",
    "pdf_render_pages",
    "pdf_screenshot_page",
    "pdf_merge_split",
    "execute_bash_command",
    "run_tests",
    "glob_search",
    "grep_search",
    "generate_repo_map",
    "todo_write",
]
_EXTERNAL_WEB_TOOLS = ["web_search", "web_fetch", "web_research", "browse_web", "browse_and_extract"]
_LONG_CONTEXT_TOOLS = [
    "read_file_chunk",
    "semantic_search",
    "read_multiple_files",
    "read_file",
    "grep_search",
    "todo_write",
]


def router_enabled() -> bool:
    return os.environ.get("METIS_TASK_ROUTER", "1").strip().lower() not in {"0", "false", "no", "off"}


def build_task_route(
    messages: List[Dict[str, Any]],
    *,
    llm_backend: str,
    llm_base_url: str = "",
    llm_model: str = "",
    deep_research: bool = False,
) -> TaskRoute:
    text = _latest_user_text(messages)
    task_type, reason = classify_task(text)
    model_role = _model_role_for_task(task_type, text)
    runtime_policy = runtime_policy_for_task(task_type, text)
    selected_model, fallback_models = _select_models(
        model_role,
        llm_backend=llm_backend,
        llm_base_url=llm_base_url,
        current_model=llm_model,
    )
    preferred_tools = _preferred_tools_for_task(task_type, deep_research=deep_research)
    return TaskRoute(
        task_type=task_type,
        model_role=model_role,
        selected_model=selected_model or llm_model,
        execution_boundary=str(runtime_policy.get("execution_boundary") or "direct"),
        runtime_policy=runtime_policy,
        fallback_models=fallback_models,
        preferred_tools=preferred_tools,
        reason=reason,
        tool_guidance=_tool_guidance_for_task(task_type, deep_research=deep_research),
    )


def render_route_hint(route: TaskRoute) -> str:
    if not route.task_type:
        return ""
    lines = [
        ROUTE_MARKER,
        f"Task type: {route.task_type}.",
        f"Model role: {route.model_role}. Selected model: {route.selected_model or 'current'}.",
        f"Default execution boundary: {route.execution_boundary}.",
    ]
    if route.runtime_policy:
        lines.append(
            "Runtime policy: "
            + "; ".join(
                part
                for part in [
                    f"recommended_tool={route.runtime_policy.get('recommended_tool')}",
                    f"sandbox_mode={route.runtime_policy.get('sandbox_mode')}",
                    f"fallback_mode={route.runtime_policy.get('fallback_mode')}",
                    f"fallback={','.join(route.runtime_policy.get('fallback_order') or [])}",
                    f"desktop_control_allowed={route.runtime_policy.get('desktop_control_allowed')}",
                ]
                if part and not part.endswith("=None")
            )
        )
    if route.reason:
        lines.append(f"Why: {route.reason}")
    if route.tool_guidance:
        lines.append("Tool route: " + route.tool_guidance)
    if route.preferred_tools:
        lines.append("Prefer tools in this order when relevant: " + ", ".join(route.preferred_tools[:12]) + ".")
    if route.fallback_models:
        lines.append("Model fallback order: " + ", ".join(route.fallback_models[:5]) + ".")
    lines.append(
        "If the preferred route clearly does not fit the visible evidence, state the mismatch briefly and choose the next safer route."
    )
    return "\n".join(lines)


def runtime_policy_for_task(task_type: str, text: str = "") -> Dict[str, Any]:
    """Claude-style execution-boundary policy for the selected task route.

    This is intentionally deterministic: users should not need to say "run this
    in the sandbox" for code execution, tests, builds, or generated artifacts.
    """
    boundary = boundary_policy_for_task(task_type, text).to_dict()
    if task_type == "artifact_workflow":
        return {
            **boundary,
            "sandbox_required": True,
            "permission_mode": "ask_for_network_cross_drive_project_write",
            "sandbox_for": ["code_run", "test", "build", "chart", "report", "doc_render", "pdf_render"],
        }
    if task_type == "code":
        return {
            **boundary,
            "sandbox_required": False,
            "permission_mode": "ask_for_destructive_or_cross_boundary",
            "sandbox_for": ["test", "build", "script", "repro", "generated_artifact"],
        }
    if task_type == "desktop":
        return {
            **boundary,
            "sandbox_required": False,
            "permission_mode": "ask_for_side_effects",
            "sandbox_for": [],
        }
    if task_type == "browser":
        return {
            **boundary,
            "sandbox_required": False,
            "permission_mode": "confirm_sensitive_web_side_effects",
            "sandbox_for": [],
        }
    if task_type == "external_lookup":
        return {
            **boundary,
            "sandbox_required": False,
            "permission_mode": "read_only_web_by_default",
            "sandbox_for": [],
        }
    if task_type == "long_context":
        return {
            **boundary,
            "sandbox_required": False,
            "permission_mode": "read_only_by_default",
            "sandbox_for": [],
        }
    return {
        **boundary,
        "sandbox_required": False,
        "permission_mode": "normal",
        "sandbox_for": [],
    }


def classify_task(text: str) -> tuple[str, str]:
    value = _normalized(text)
    if not value:
        return "chat", "empty or greeting-like request"
    artifact_like = _looks_like_artifact_workflow_task(value)
    if artifact_like and not _requires_desktop_control(value):
        return (
            "artifact_workflow",
            "document/report/PDF/code-output deliverable detected; background artifact workflow should run before Computer Use",
        )
    code_like = _looks_like_code_task(value)
    if code_like and not _has_any(value, _STRONG_DESKTOP_KEYWORDS):
        return "code", "repository, file, implementation, test, or git keywords detected"
    if _requires_desktop_control(value) or _has_any(value, _DESKTOP_KEYWORDS):
        return "desktop", "desktop/window/screen operation keywords detected"
    if _looks_like_local_browser_task(value):
        return "browser", "local page, Preview, localhost, DOM, console, or in-app browser keywords detected"
    if code_like:
        return "code", "repository, file, implementation, test, or git keywords detected"
    if _looks_like_long_context_task(value):
        return "long_context", "long document or large-context keywords detected"
    if _looks_like_external_lookup(value):
        return "external_lookup", "fresh external information/search keywords detected"
    return "chat", "general conversation or lightweight reasoning"


def prioritized_tools_for_route(tools: List[Dict[str, Any]], preferred_tools: Iterable[str]) -> List[Dict[str, Any]]:
    order = {name: index for index, name in enumerate(preferred_tools)}
    if not order:
        return tools

    def key(schema: Dict[str, Any]) -> tuple[int, int, str]:
        name = str(((schema.get("function") or {}).get("name") or ""))
        return (0 if name in order else 1, order.get(name, 10_000), name)

    return sorted(tools, key=key)


def _latest_user_text(messages: List[Dict[str, Any]]) -> str:
    for message in reversed(messages or []):
        if not isinstance(message, dict) or message.get("role") != "user":
            continue
        return _message_content_text(message.get("content"))
    return ""


def _message_content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                value = item.get("text") or item.get("content") or item.get("name") or item.get("path") or ""
                if value:
                    parts.append(str(value))
        return "\n".join(parts)
    if isinstance(content, dict):
        return str(content.get("text") or content.get("content") or "")
    return str(content or "")


def _normalized(text: str) -> str:
    return str(text or "").strip().lower()


def _has_any(text: str, needles: Iterable[str]) -> bool:
    return any(needle in text for needle in needles)


_DESKTOP_KEYWORDS = {
    "computer use",
    "desktop",
    "win2",
    "桌面",
    "屏幕",
    "鼠标",
    "键盘",
    "窗口",
    "前台",
    "点击桌面",
    "打开软件",
    "应用窗口",
    "控制电脑",
    "接管屏幕",
}

_STRONG_DESKTOP_KEYWORDS = {
    "computer use",
    "win2",
    "屏幕",
    "鼠标",
    "键盘",
    "窗口",
    "前台",
    "点击桌面",
    "打开软件",
    "应用窗口",
    "控制电脑",
    "接管屏幕",
}

_BROWSER_KEYWORDS = {
    "/browser",
    "/broswer",
    "browser use",
    "broswer use",
    "preview browser",
    "preview",
    "localhost",
    "127.0.0.1",
    "页面白屏",
    "白屏",
    "console error",
    "console",
    "network failed",
    "failed request",
    "dom",
    "按钮可见",
    "可点击",
    "右栏",
    "预览",
    "网页",
    "浏览器",
}

_CODE_KEYWORDS = {
    "代码",
    "仓库",
    "项目",
    "文件",
    "修复",
    "实现",
    "施工",
    "开工",
    "typecheck",
    "pytest",
    "npm",
    "git",
    "commit",
    "readme",
    "tsx",
    "typescript",
    "python",
    "backend",
    "frontend",
    "css",
}

_EXTERNAL_LOOKUP_KEYWORDS = {
    "最新",
    "今天",
    "现在",
    "新闻",
    "价格",
    "搜索",
    "联网",
    "官网",
    "查一下",
    "查清楚",
    "github",
    "gmail",
    "oauth",
}

_LONG_CONTEXT_KEYWORDS = {
    "长文档",
    "大文档",
    "长上下文",
    "整份文档",
    "全文",
    "pdf",
    "docx",
    "报告",
}

_ARTIFACT_WORKFLOW_KEYWORDS = {
    "实验报告",
    "课程报告",
    "作业",
    "报告",
    "文档",
    "word",
    "wps",
    "docx",
    "pdf",
    "论文",
    "实验结果",
    "生成图表",
    "图表",
    "画图",
    "绘图",
    "跑代码",
    "运行代码",
    "写代码",
    "matlab",
    "python",
    "仿真",
    "数据分析",
    "离散时间",
    "频域",
    "时域",
    "零极点",
    "稳定性",
}

_DESKTOP_CONTROL_INTENT_KEYWORDS = {
    "点击",
    "点一下",
    "帮我点",
    "按按钮",
    "提交按钮",
    "提交表单",
    "发送按钮",
    "滚动",
    "拖动",
    "切换窗口",
    "最大化",
    "最小化",
    "关闭窗口",
    "菜单",
    "鼠标",
    "键盘",
    "接管屏幕",
    "控制电脑",
    "computer use",
    "win2",
}


def _looks_like_local_browser_task(text: str) -> bool:
    if _has_any(text, _BROWSER_KEYWORDS):
        if _looks_like_code_task(text) and not _has_any(text, {"测试页面", "检查页面", "验收页面", "打开页面", "预览", "白屏"}):
            return False
        return True
    return bool(re.search(r"https?://(?:localhost|127\.0\.0\.1|0\.0\.0\.0|::1)", text))


def _looks_like_code_task(text: str) -> bool:
    if _has_any(text, _CODE_KEYWORDS):
        return True
    if re.search(r"\b[\w.-]+\.(?:py|ts|tsx|js|jsx|css|md|json|yml|yaml|toml|rs|go|java|cs)\b", text):
        return True
    return bool(re.search(r"[a-z]:\\|/src/|backend/|desktop/src", text, flags=re.IGNORECASE))


def _looks_like_artifact_workflow_task(text: str) -> bool:
    if not _has_any(text, _ARTIFACT_WORKFLOW_KEYWORDS):
        return False
    if _has_any(text, {"实验报告", "课程报告", "docx", "pdf", "word", "wps", "作业", "图表"}):
        return True
    return _has_any(text, {"跑代码", "运行代码", "写代码", "matlab", "python", "仿真", "数据分析"})


def _requires_desktop_control(text: str) -> bool:
    if not _has_any(text, _DESKTOP_CONTROL_INTENT_KEYWORDS):
        return False
    if _has_any(text, {"后台", "自动运行", "生成报告", "生成文档"}) and not _has_any(text, {"点击", "滚动", "提交按钮", "接管屏幕"}):
        return False
    return True


def _looks_like_long_context_task(text: str) -> bool:
    return len(text) > 6000 or _has_any(text, _LONG_CONTEXT_KEYWORDS)


def _looks_like_external_lookup(text: str) -> bool:
    if _has_any(text, _EXTERNAL_LOOKUP_KEYWORDS):
        return not _looks_like_code_task(text)
    return False


def _model_role_for_task(task_type: str, text: str) -> str:
    if task_type == "artifact_workflow":
        return "code"
    if task_type == "desktop":
        return "vision"
    if task_type == "browser":
        if _has_any(text, {"截图", "视觉", "错位", "白屏", "看图", "图"}):
            return "vision"
        return "fast"
    if task_type == "code":
        return "code"
    if task_type == "long_context":
        return "long_context"
    return "fast"


def _preferred_tools_for_task(task_type: str, *, deep_research: bool = False) -> List[str]:
    if task_type == "artifact_workflow":
        return list(_ARTIFACT_WORKFLOW_TOOLS) + list(_DESKTOP_OBSERVE_TOOLS)
    if task_type == "desktop":
        return list(_DESKTOP_TOOLS)
    if task_type == "browser":
        return list(_PREVIEW_BROWSER_TOOLS) + ["browse_web", "browse_and_extract", "web_fetch"]
    if task_type == "code":
        return list(_CODE_TOOLS)
    if task_type == "external_lookup":
        if deep_research:
            return ["web_research"] + [tool for tool in _EXTERNAL_WEB_TOOLS if tool != "web_research"]
        return list(_EXTERNAL_WEB_TOOLS)
    if task_type == "long_context":
        return list(_LONG_CONTEXT_TOOLS)
    return []


def _tool_guidance_for_task(task_type: str, *, deep_research: bool = False) -> str:
    if task_type == "artifact_workflow":
        return (
            "Use a background artifact workflow first: call metis_runtime_job for code execution, tests, builds, chart generation, report generation, and document/PDF rendering. "
            "It creates a runtime session, runs inside the isolated workspace, collects artifacts, exports patch/diagnostics, and returns verifier evidence. "
            "write DOCX/PDF artifacts, and verify outputs. Do not use Computer Use to control PyCharm/WPS "
            "unless the user asks for a specific UI-only action such as clicking a button, submitting a form, "
            "or operating an already-open app window."
        )
    if task_type == "desktop":
        return "Use Computer Use first: desktop_win2_status -> desktop_win2_observe/action/verify, or desktop_win2_task for high-level Windows app work."
    if task_type == "browser":
        return "Use the in-app Preview Browser first: preview_browser_status/navigate/observe/action/verify. Use external browse_web only when Preview cannot cover the target."
    if task_type == "code":
        return "Use repo tools for direct reads/edits. Use metis_runtime_job for commands, tests, builds, repros, generated artifacts, or risky experiments before applying changes to the source project."
    if task_type == "external_lookup":
        if deep_research:
            return (
                "Deep research is explicitly enabled for this turn. Prefer web_research first for multi-source evidence, then use web_fetch for known URLs. "
                "Use web_search only for a cheap supplementary query, and escalate to browse_web only for dynamic or interactive pages."
            )
        return (
            "Use web_search/web_fetch for cheap fresh facts. Use web_research when the user asks to check multiple sources or prove claims. "
            "If cheap search results are thin or contradictory, you may escalate from web_search to web_research once this turn and pass a concise reason argument for the diagnostic audit. "
            "Escalate to browse_web only for dynamic or interactive pages."
        )
    if task_type == "long_context":
        return "Use chunked reading and semantic/repo search. Avoid loading huge documents into one tool result."
    return "Answer directly unless a tool is clearly needed."


def _select_models(
    role: str,
    *,
    llm_backend: str,
    llm_base_url: str,
    current_model: str,
) -> tuple[str, List[str]]:
    try:
        profile = resolve_provider_for_config(llm_backend, base_url=llm_base_url, model=current_model)
    except Exception:
        profile = None

    provider_id = str(getattr(profile, "provider_id", "") or llm_backend or "").strip()
    candidates = _dedupe(
        [
            current_model,
            getattr(profile, "default_model", "") if profile else "",
            *(getattr(profile, "fallback_models", ()) if profile else ()),
            *list((getattr(profile, "model_context_windows", {}) or {}).keys() if profile else []),
        ]
    )
    override = _model_override_for_role(role, provider_id)
    if override:
        candidates = _dedupe([override, *candidates])
        selected = override
    else:
        # Respect the user's concrete model selection. Role routing still
        # controls tools/runtime boundaries, but it must not silently downgrade
        # an explicitly selected Pro/strong model to a "fast" candidate.
        selected = current_model or _select_model_for_role(role, candidates, profile, current_model)

    if not selected:
        selected = current_model or (candidates[0] if candidates else "")
    fallback_models = _dedupe([selected, *candidates, current_model])
    return selected, fallback_models


def _model_override_for_role(role: str, provider_id: str) -> str:
    keys = [
        f"METIS_ROUTER_{role.upper()}_MODEL",
        f"METIS_{role.upper()}_MODEL",
    ]
    if role == "fast":
        keys.extend(["METIS_ROUTER_CHAT_MODEL", "METIS_FAST_MODEL"])
    if role == "code":
        keys.append("METIS_CODING_MODEL")
    if role == "vision":
        keys.append("METIS_VISION_MODEL")
    if role == "long_context":
        keys.extend(["METIS_LONG_CONTEXT_MODEL", "METIS_LONG_MODEL"])
    for key in keys:
        value = os.environ.get(key, "").strip()
        if not value:
            continue
        routed_provider, routed_model = _split_provider_model(value)
        if routed_provider and provider_id and routed_provider.lower() != provider_id.lower():
            continue
        return routed_model
    return ""


def _split_provider_model(value: str) -> tuple[str, str]:
    if "::" in value:
        provider, model = value.split("::", 1)
        return provider.strip(), model.strip()
    if "|" in value:
        provider, model = value.split("|", 1)
        return provider.strip(), model.strip()
    return "", value.strip()


def _select_model_for_role(role: str, candidates: List[str], profile: Any, current_model: str) -> str:
    if not candidates:
        return current_model
    if role == "fast":
        return _best_by_keywords(candidates, ["flash", "mini", "haiku", "turbo", "lite"]) or candidates[0]
    if role == "code":
        return (
            _best_by_keywords(candidates, ["coder", "v4-pro", "pro", "sonnet", "opus", "gpt-4.1", "qwen3-coder", "max"])
            or current_model
            or candidates[0]
        )
    if role == "vision":
        if profile is not None and not bool(getattr(profile, "supports_vision", False)):
            return current_model or candidates[0]
        return _best_by_keywords(candidates, ["gpt-4o", "gpt-4.1", "gemini", "claude", "vision", "vl"]) or current_model or candidates[0]
    if role == "long_context":
        return _largest_context_model(candidates, profile) or current_model or candidates[0]
    return current_model or candidates[0]


def _best_by_keywords(candidates: List[str], keywords: List[str]) -> str:
    lowered = [(model, model.lower()) for model in candidates if model]
    for keyword in keywords:
        for model, value in lowered:
            if keyword in value:
                return model
    return ""


def _largest_context_model(candidates: List[str], profile: Any) -> str:
    windows = dict(getattr(profile, "model_context_windows", {}) or {}) if profile is not None else {}
    best_model = ""
    best_context = -1
    for model in candidates:
        if not model:
            continue
        context = int(windows.get(model, 0) or 0)
        if context <= 0:
            context = detect_from_model_name(model).effective_context
        if context > best_context:
            best_model = model
            best_context = context
    return best_model


def _dedupe(values: Iterable[str]) -> List[str]:
    out: List[str] = []
    seen: set[str] = set()
    for value in values:
        item = str(value or "").strip()
        if not item:
            continue
        key = item.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def desktop_control_tools() -> frozenset[str]:
    return frozenset(_DESKTOP_CONTROL_TOOLS)


def should_block_desktop_control(task_type: str) -> bool:
    if os.environ.get("METIS_COMPUTER_USE_ROUTER_GUARD", "1").strip().lower() in {"0", "false", "no", "off"}:
        return False
    return str(task_type or "").strip().lower() == "artifact_workflow"


# Office/document generation tools — a heavy cluster (~12 tools, ~2k schema
# tokens) that only document tasks need. They stay out of the schema for every
# other route so strong models don't re-send them every turn.
_DOCUMENT_WORKFLOW_TOOLS = frozenset(
    {
        "pdf_info",
        "pdf_extract_text",
        "pdf_render_pages",
        "pdf_screenshot_page",
        "pdf_merge_split",
        "pdf_create",
        "docx_create",
        "docx_edit",
        "docx_to_pdf",
        "docx_render_pages",
        "docx_inspect_layout",
        "office_report_from_code_run",
    }
)


def document_workflow_tools() -> frozenset[str]:
    return _DOCUMENT_WORKFLOW_TOOLS


def should_block_document_tools(task_type: str) -> bool:
    """Block document tools unless the route is a document/artifact task."""
    if os.environ.get("METIS_LEAN_DOCUMENT_TOOLS", "1").strip().lower() in {"0", "false", "no", "off"}:
        return False
    return str(task_type or "").strip().lower() != "artifact_workflow"

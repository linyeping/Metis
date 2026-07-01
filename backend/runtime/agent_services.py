from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Mapping
from typing import Any, Dict, List, Sequence


RISK_LOW = "LOW"
RISK_MEDIUM = "MEDIUM"
RISK_HIGH = "HIGH"

_READ_ONLY_PREFIXES = (
    "read",
    "list",
    "grep",
    "glob",
    "search",
    "find",
    "git_diff",
    "check_git_status",
    "preview_browser_observe",
    "preview_browser_screenshot",
)

_WRITE_HINTS = ("write", "edit", "replace", "append", "patch", "create", "move", "rename")
_DELETE_HINTS = ("delete", "remove", "rm", "rmdir", "del")
_BROWSER_HINTS = ("browser", "browse", "preview")
_DESKTOP_HINTS = ("desktop", "win2", "computer", "screen")

_HIGH_RISK_COMMAND_PATTERNS = (
    r"\brm\s+-rf\b",
    r"\bdel\s+/[sq]\b",
    r"\brmdir\s+/s\b",
    r"\bremove-item\b.*-recurse\b",
    r"\bformat\b",
    r"\bdrop\s+table\b",
    r"\btruncate\s+table\b",
    r"\breg\s+(add|delete)\b",
    r"\bshutdown\b",
    r"\bnet\s+user\b",
    r"\bgit\s+push\b.*\b--force\b",
)

_SENSITIVE_PATH_MARKERS = (
    ".env",
    "id_rsa",
    "id_ed25519",
    ".ssh",
    "credentials",
    "token",
    "secret",
    "key.pem",
)

_VERIFICATION_ALLOWED_PREFIXES = (
    "python -m pytest",
    "python -m py_compile",
    "pytest",
    "npm run typecheck",
    "npm run build",
    "npx vitest run",
    "node scripts\\desktop-contract-tests.mjs",
    "node scripts/desktop-contract-tests.mjs",
    "git diff --check",
    "rg ",
)

_VERIFICATION_DENY_PATTERNS = (
    r"\b(git\s+add|git\s+commit|git\s+push|git\s+checkout|git\s+reset|git\s+clean)\b",
    r"\b(npm\s+install|pnpm\s+install|yarn\s+install)\b",
    r"\b(remove-item|rm|del|rmdir)\b",
    r"\b(set-content|out-file|new-item|copy-item|move-item)\b",
    r"[;&|>]",
)

_TOOL_CONTRACT_VERSION = "tool-contract-v3"
_COORDINATOR_VERSION = "coordinator-worker-v2"
_PROMPT_RUNTIME_PROFILE_VERSION = "prompt-runtime-v2"
_PROACTIVE_VERSION = "proactive-long-task-v1"

_IMPLEMENTATION_HINTS = ("write", "edit", "replace", "append", "patch", "create", "move", "rename")
_VERIFIER_HINTS = ("test", "verify", "build", "typecheck", "lint", "screenshot", "inspect_layout")
_RESEARCH_HINTS = ("read", "list", "grep", "glob", "search", "find", "repo_map", "observe")
_LONG_TASK_HINTS = (
    "long task",
    "background",
    "keep going",
    "run overnight",
    "长任务",
    "后台",
    "持续",
    "回来",
    "自动继续",
)


def explain_permission(
    *,
    tool_name: str,
    arguments: Mapping[str, Any] | None = None,
    decision: Mapping[str, Any] | None = None,
    path_safety: Mapping[str, Any] | None = None,
    workspace_root: str = "",
) -> Dict[str, Any]:
    """Return a compact permission explanation for the approval dialog."""

    args = dict(arguments or {})
    classifier = classify_autoguard(
        tool_name=tool_name,
        arguments=args,
        mode=str((decision or {}).get("mode") or "auto"),
        path_safety=path_safety,
        workspace_root=workspace_root,
    )
    risk_level = _max_risk(
        classifier.get("riskLevel"),
        _risk_from_decision(decision),
        _risk_from_path_safety(path_safety),
    )
    explanation = _tool_explanation(tool_name, args)
    reason = str((decision or {}).get("reason") or classifier.get("reason") or "").strip()
    if not reason:
        reason = "I need this tool call to continue the requested task."
    elif not reason.lower().startswith("i "):
        reason = f"I am asking because {reason[:1].lower() + reason[1:]}"
    risk = _risk_sentence(risk_level, tool_name, args, path_safety)
    return {
        "explanation": explanation,
        "reasoning": _truncate(reason, 220),
        "risk": risk,
        "riskLevel": risk_level,
        "risk_level": risk_level.lower(),
        "autoguard": classifier,
    }


def classify_autoguard(
    *,
    tool_name: str,
    arguments: Mapping[str, Any] | None = None,
    mode: str = "auto",
    path_safety: Mapping[str, Any] | None = None,
    workspace_root: str = "",
) -> Dict[str, Any]:
    """Deterministic AutoGuard v2 classification.

    This mirrors the structured classifier contract without depending on an
    extra model call. The caller can treat ``shouldBlock`` as "ask or block"
    depending on the surrounding permission mode.
    """

    args = dict(arguments or {})
    name = str(tool_name or "tool")
    lowered = name.lower()
    risk_level = RISK_LOW
    reasons: List[str] = []
    should_block = False

    if _is_read_only_tool(lowered):
        reasons.append("read-only tool")
    if any(hint in lowered for hint in _WRITE_HINTS):
        risk_level = _max_risk(risk_level, RISK_MEDIUM)
        reasons.append("can modify files or state")
    if any(hint in lowered for hint in _DELETE_HINTS):
        risk_level = _max_risk(risk_level, RISK_HIGH)
        reasons.append("can delete data")
        should_block = True
    if any(hint in lowered for hint in _DESKTOP_HINTS):
        risk_level = _max_risk(risk_level, RISK_MEDIUM)
        reasons.append("controls visible desktop state")
    if any(hint in lowered for hint in _BROWSER_HINTS):
        risk_level = _max_risk(risk_level, RISK_MEDIUM)
        reasons.append("can navigate or interact with pages")

    command = _first_arg(args, "command", "cmd", "script", "shell")
    if command and _looks_high_risk_command(command):
        risk_level = RISK_HIGH
        reasons.append("command matches destructive pattern")
        should_block = True

    path_text = _path_text(args)
    if path_text and _looks_sensitive_path(path_text):
        risk_level = RISK_HIGH
        reasons.append("touches sensitive path")
        should_block = True

    if _path_outside_workspace(path_safety):
        risk_level = _max_risk(risk_level, RISK_HIGH)
        reasons.append("target is outside the active workspace")
        should_block = True

    normalized_mode = str(mode or "auto").lower().replace("-", "_")
    if normalized_mode not in {"auto_guard", "autoguard", "guarded_auto"}:
        should_block = False

    if not reasons:
        reasons.append("no risky pattern detected")

    return {
        "thinking": "; ".join(reasons[:4]),
        "shouldBlock": bool(should_block),
        "should_block": bool(should_block),
        "reason": _truncate("; ".join(reasons), 180),
        "riskLevel": risk_level,
        "risk_level": risk_level.lower(),
    }


def generate_tool_label(
    tool_name: str,
    arguments: Mapping[str, Any] | None = None,
    result: Any = None,
    *,
    status: str = "",
) -> str:
    """Generate a short, single-row activity label for a tool call/result."""

    args = dict(arguments or {})
    name = str(tool_name or "tool")
    lowered = name.lower()
    target = _target_label(args, result)
    failed = str(status or "").lower() == "error" or _result_looks_failed(result)

    if failed:
        return _clip_label(f"{_tool_verb(name, past=True)} failed")
    if "run_tests" in lowered:
        return _clip_label("Ran tests")
    if "verify" in lowered:
        return _clip_label("Verified result")
    if "read" in lowered:
        return _clip_label(f"Read {target}" if target else "Read file")
    if any(hint in lowered for hint in ("grep", "search", "find", "glob")):
        return _clip_label(f"Searched {target}" if target else "Searched project")
    if any(hint in lowered for hint in _WRITE_HINTS):
        return _clip_label(f"Updated {target}" if target else "Updated file")
    if any(hint in lowered for hint in _DELETE_HINTS):
        return _clip_label(f"Deleted {target}" if target else "Deleted item")
    if "execute" in lowered or "bash" in lowered or "shell" in lowered:
        command = _first_arg(args, "command", "cmd")
        return _clip_label(f"Ran {command.split()[0]}" if command else "Ran command")
    if "browser" in lowered or "preview" in lowered:
        return _clip_label(f"Checked {target}" if target else "Used browser")
    if "desktop" in lowered or "win2" in lowered:
        return _clip_label("Controlled desktop")
    if "pdf" in lowered:
        return _clip_label(f"Handled {target}" if target else "Handled PDF")
    if "docx" in lowered or "document" in lowered:
        return _clip_label(f"Handled {target}" if target else "Handled document")
    return _clip_label(f"{_tool_verb(name, past=True)} {target}".strip() or name)


def generate_session_title(history: Sequence[Mapping[str, Any]]) -> str:
    """Generate a concise recognizable session title from user messages."""

    user_texts = [_message_text(message) for message in history if str(message.get("role") or "") == "user"]
    source = next((text for text in reversed(user_texts) if text.strip()), "")
    if not source:
        return ""
    source = _clean_prompt_text(source)
    cjk_count = sum(1 for char in source if "\u4e00" <= char <= "\u9fff")
    if cjk_count >= max(2, len(source) // 4):
        title = re.split(r"[。！？\n\r]", source, maxsplit=1)[0].strip()
        title = re.sub(r"^(请|帮我|你|我们|先|继续|执行|做一下|给我)", "", title).strip(" ：:，,")
        return _truncate(title or source, 18)
    words = re.findall(r"[A-Za-z0-9_#./-]+", source)
    if not words:
        return _truncate(source, 40)
    title_words = words[:7]
    title = " ".join(title_words)
    if title:
        title = title[:1].upper() + title[1:].lower()
    return _truncate(title, 48)


def should_auto_title(current_title: str) -> bool:
    value = str(current_title or "").strip()
    if not value:
        return True
    if value in {"新会话", "新的会话", "未命名会话", "Untitled", "Untitled chat"}:
        return True
    return bool(
        re.match(r"^(Chat|New chat|Metis Chat)(\s+\d{4}-\d{2}-\d{2}.*)?$", value, re.I)
        or re.match(r"^Chat\s+\d{4}[/.-]\d{1,2}[/.-]\d{1,2}", value, re.I)
    )


def generate_away_summary(
    history: Sequence[Mapping[str, Any]],
    compact_state: Mapping[str, Any] | None = None,
) -> str:
    user_text = _last_role_text(history, "user")
    assistant_text = _last_role_text(history, "assistant")
    task = _truncate(_clean_prompt_text(user_text), 70) or "这个会话正在推进一个开发任务"
    next_step = _next_step_from_text(assistant_text) or _next_step_from_text(user_text) or "继续从最近的待办或错误处接着做。"
    if compact_state and str(compact_state.get("summary") or "").strip():
        return f"{task}。上下文已经整理过，下一步是{_lower_first(next_step)}"
    return f"{task}。下一步是{_lower_first(next_step)}"


def generate_prompt_suggestions(history: Sequence[Mapping[str, Any]]) -> List[str]:
    assistant_text = _last_role_text(history, "assistant").lower()
    user_text = _last_role_text(history, "user")
    suggestions: List[str] = []
    if any(marker in assistant_text for marker in ("error", "failed", "traceback", "失败", "报错")):
        suggestions.extend(["查看错误日志", "重试这一步"])
    if any(marker in assistant_text for marker in ("test", "pytest", "typecheck", "测试", "验证", "检查")):
        suggestions.extend(["跑完整测试", "看一下 diff"])
    if any(marker in assistant_text for marker in ("commit", "提交", "git")):
        suggestions.append("提交这些改动")
    if any(marker in user_text for marker in ("先讨论", "探讨", "方案", "规划")):
        suggestions.extend(["开始施工", "拆成阶段"])
    suggestions.extend(["继续优化", "帮我验收"])
    return _dedupe_suggestions(suggestions)[:3]


def build_verification_agent_report(
    *,
    task: str,
    changed_files: Sequence[str] | None = None,
    checks: Sequence[Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    """Build a verification-only report shell for an independent verifier."""

    normalized_checks = [dict(item) for item in checks or [] if isinstance(item, Mapping)]
    failures = [
        item
        for item in normalized_checks
        if str(item.get("result") or item.get("status") or "").lower() in {"fail", "failed", "error"}
    ]
    verdict = "FAIL" if failures else "PASS" if normalized_checks else "PARTIAL"
    return {
        "role": "verification_agent",
        "mode": "verification_only",
        "task": str(task or "").strip(),
        "changed_files": list(changed_files or []),
        "checks": normalized_checks,
        "required_output": "VERDICT: PASS | VERDICT: FAIL | VERDICT: PARTIAL",
        "disallowed_actions": [
            "modify project files",
            "install dependencies",
            "git add",
            "git commit",
            "git push",
        ],
        "verdict": verdict,
        "summary": _verification_summary(verdict, normalized_checks, failures),
    }


def verification_command_policy(command: str) -> Dict[str, Any]:
    text = " ".join(str(command or "").strip().split())
    lowered = text.lower()
    if not text:
        return {"allowed": False, "reason": "empty command"}
    for pattern in _VERIFICATION_DENY_PATTERNS:
        if re.search(pattern, lowered):
            return {"allowed": False, "reason": f"blocked by verifier policy: {pattern}"}
    if any(lowered == prefix or lowered.startswith(prefix + " ") for prefix in _VERIFICATION_ALLOWED_PREFIXES):
        return {"allowed": True, "reason": "allowed verification command"}
    return {
        "allowed": False,
        "reason": "command is not in the verification allowlist",
        "allowed_prefixes": list(_VERIFICATION_ALLOWED_PREFIXES),
    }


def scratchpad_dir(workspace_root: str = "", session_id: str = "") -> str:
    """Return a per-session scratchpad path and create it when possible."""

    root = os.path.abspath(workspace_root or os.getcwd())
    safe_session = re.sub(r"[^A-Za-z0-9_.-]+", "-", str(session_id or "default")).strip("-") or "default"
    path = os.path.join(root, ".metis", "scratch", safe_session)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return path
    return path


def build_prompt_runtime_profile(
    *,
    workspace_root: str = "",
    session_id: str = "",
    mode: str = "auto",
    model: str = "",
    compact_state: Mapping[str, Any] | None = None,
    message_count: int = 0,
) -> Dict[str, Any]:
    """Describe the prompt runtime boundary without injecting volatile data."""

    compact = dict(compact_state or {})
    compact_count = _safe_int(compact.get("compact_count", compact.get("compactCount")), 0)
    compact_mode = str(compact.get("mode") or "partial_older")
    workspace = os.path.abspath(workspace_root or os.getcwd())
    return {
        "version": _PROMPT_RUNTIME_PROFILE_VERSION,
        "cachePolicy": "stable-prefix-plus-session-suffix",
        "cache_policy": "stable-prefix-plus-session-suffix",
        "stablePrefix": [
            "base system prompt",
            "fixed execution rules",
            "tool strategy contracts",
            "skills index",
            "project profile",
            "user METIS.md",
        ],
        "stable_prefix": [
            "base system prompt",
            "fixed execution rules",
            "tool strategy contracts",
            "skills index",
            "project profile",
            "user METIS.md",
        ],
        "sessionSuffix": [
            f"workspace={workspace}",
            f"mode={mode or 'auto'}",
            f"model={model or 'current'}",
            f"messages={max(0, int(message_count or 0))}",
            f"compact={compact_mode}:{compact_count}",
        ],
        "session_suffix": [
            f"workspace={workspace}",
            f"mode={mode or 'auto'}",
            f"model={model or 'current'}",
            f"messages={max(0, int(message_count or 0))}",
            f"compact={compact_mode}:{compact_count}",
        ],
        "requestSuffix": [
            "todo state is refreshed at the tail",
            "open files and terminal state are fetched on demand",
            "tool results may be compacted into a ledger",
        ],
        "request_suffix": [
            "todo state is refreshed at the tail",
            "open files and terminal state are fetched on demand",
            "tool results may be compacted into a ledger",
        ],
        "scratchpadPath": scratchpad_dir(workspace, session_id),
        "scratchpad_path": scratchpad_dir(workspace, session_id),
        "compactMode": compact_mode,
        "compact_mode": compact_mode,
        "compactCount": compact_count,
        "compact_count": compact_count,
    }


def build_tool_contract(
    tool_name: str,
    arguments: Mapping[str, Any] | None = None,
    *,
    workspace_root: str = "",
    mode: str = "auto",
) -> Dict[str, Any]:
    """Return the behavioral contract Metis should apply to one tool call."""

    args = dict(arguments or {})
    autoguard = classify_autoguard(
        tool_name=tool_name,
        arguments=args,
        mode=mode,
        workspace_root=workspace_root,
    )
    category = _tool_category(tool_name)
    risk_level = _max_risk(str(autoguard.get("riskLevel") or RISK_LOW), _category_base_risk(category))
    read_before_edit = category in {"edit", "shell", "git", "runtime"}
    verify_after = category in {"edit", "shell", "browser", "desktop", "artifact", "git", "runtime"}
    return {
        "version": _TOOL_CONTRACT_VERSION,
        "tool": str(tool_name or "tool"),
        "category": category,
        "riskLevel": risk_level,
        "risk_level": risk_level.lower(),
        "preferredSurface": _preferred_tool_surface(category),
        "preferred_surface": _preferred_tool_surface(category),
        "readBeforeEdit": read_before_edit,
        "read_before_edit": read_before_edit,
        "verifyAfter": verify_after,
        "verify_after": verify_after,
        "requiresPermission": risk_level in {RISK_MEDIUM, RISK_HIGH} or bool(autoguard.get("shouldBlock")),
        "requires_permission": risk_level in {RISK_MEDIUM, RISK_HIGH} or bool(autoguard.get("shouldBlock")),
        "why": _tool_contract_reason(category, tool_name),
        "saferAlternative": _safer_tool_alternative(category),
        "safer_alternative": _safer_tool_alternative(category),
        "autoguard": autoguard,
    }


def build_tool_contracts_summary(tool_names: Sequence[str] | None = None) -> Dict[str, Any]:
    names = list(tool_names or [
        "read_file",
        "grep_search",
        "robust_replace_in_file",
        "execute_bash_command",
        "preview_browser_verify",
        "desktop_action",
        "docx_create",
        "pdf_render_pages",
    ])
    return {
        "version": _TOOL_CONTRACT_VERSION,
        "guidance": [
            "Prefer dedicated file/search/edit tools over shell for ordinary repo work.",
            "Read the relevant file before editing existing content.",
            "After changes, verify with the narrowest useful test or inspection.",
            "Shell and git actions must explain risk and avoid destructive defaults.",
        ],
        "items": [build_tool_contract(name) for name in names[:12]],
    }


def build_worker_prompt(
    *,
    goal: str,
    worker_type: str = "implementation",
    workspace_root: str = "",
    files: Sequence[str] | None = None,
    done_criteria: Sequence[str] | None = None,
) -> str:
    """Build a self-contained prompt for a worker/subagent."""

    normalized_type = _normalize_worker_type(worker_type)
    lines = [
        f"Worker type: {normalized_type}",
        f"Workspace: {os.path.abspath(workspace_root or os.getcwd())}",
        "",
        "Goal:",
        str(goal or "").strip() or "Complete the assigned Metis task.",
    ]
    file_items = [str(item).strip() for item in files or [] if str(item).strip()]
    if file_items:
        lines.extend(["", "Relevant files:"])
        lines.extend(f"- {item}" for item in file_items[:20])
    criteria = [str(item).strip() for item in done_criteria or [] if str(item).strip()]
    if not criteria:
        criteria = _default_done_criteria(normalized_type)
    lines.extend(["", "Done criteria:"])
    lines.extend(f"- {item}" for item in criteria)
    lines.extend(
        [
            "",
            "Boundaries:",
            "- Treat this prompt as self-contained; do not assume hidden context.",
            "- Report blockers and evidence explicitly.",
            "- Verification workers must not modify project files.",
        ]
    )
    return "\n".join(lines).strip()


def build_coordinator_board(
    history: Sequence[Mapping[str, Any]],
    *,
    task: str = "",
) -> Dict[str, Any]:
    """Summarize coordinator/worker state from the transcript."""

    tools = _history_tool_records(history)
    lanes = [
        _worker_lane("research", tools, _RESEARCH_HINTS),
        _worker_lane("implementation", tools, _IMPLEMENTATION_HINTS),
        _worker_lane("verification", tools, _VERIFIER_HINTS),
    ]
    has_failure = any(str(tool.get("status") or "").lower() == "error" for tool in tools)
    active = any(lane["status"] == "running" for lane in lanes)
    next_action = "Resolve the failing worker and retry with a different strategy." if has_failure else (
        "Continue the active worker." if active else "Start with research, then implementation, then fresh verification."
    )
    return {
        "version": _COORDINATOR_VERSION,
        "mode": "coordinator-worker-verifier",
        "task": task or _last_role_text(history, "user"),
        "nextAction": next_action,
        "next_action": next_action,
        "workers": lanes,
        "freshVerifier": {
            "enabled": True,
            "role": "verification",
            "rule": "verify from clean context and do not edit files",
        },
        "fresh_verifier": {
            "enabled": True,
            "role": "verification",
            "rule": "verify from clean context and do not edit files",
        },
    }


def build_proactive_long_task_state(
    history: Sequence[Mapping[str, Any]],
    *,
    active_run: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Return an opt-in long-task autonomy policy for the UI and runtime."""

    latest_user = _last_role_text(history, "user")
    requested = any(marker in latest_user.lower() for marker in _LONG_TASK_HINTS)
    active = bool(active_run and str(active_run.get("status") or "") not in {"", "done", "failed", "canceled"})
    enabled = requested or active
    state = "running" if active else "available" if requested else "opt_in_required"
    return {
        "version": _PROACTIVE_VERSION,
        "enabled": enabled,
        "optInRequired": not enabled,
        "opt_in_required": not enabled,
        "state": state,
        "tickSeconds": 15,
        "tick_seconds": 15,
        "policies": [
            "continue only the task the user already authorized",
            "sleep silently when waiting for build/test/server output",
            "summarize progress when the user returns",
            "ask before external side effects or sensitive data transfer",
        ],
        "lastActivityAt": float(active_run.get("updated_at") or active_run.get("updatedAt") or time.time()) if active_run else 0.0,
        "last_activity_at": float(active_run.get("updated_at") or active_run.get("updatedAt") or time.time()) if active_run else 0.0,
    }


def build_agent_runtime_profile(
    *,
    history: Sequence[Mapping[str, Any]],
    workspace_root: str = "",
    session_id: str = "",
    mode: str = "auto",
    model: str = "",
    compact_state: Mapping[str, Any] | None = None,
    active_run: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    """Aggregate the four prompt-pattern systems into one UI/API payload."""

    prompt_runtime = build_prompt_runtime_profile(
        workspace_root=workspace_root,
        session_id=session_id,
        mode=mode,
        model=model,
        compact_state=compact_state,
        message_count=len(history),
    )
    tool_contracts = build_tool_contracts_summary(_recent_tool_names(history))
    coordinator = build_coordinator_board(history)
    proactive = build_proactive_long_task_state(history, active_run=active_run)
    return {
        "ok": True,
        "promptRuntime": prompt_runtime,
        "prompt_runtime": prompt_runtime,
        "toolContracts": tool_contracts,
        "tool_contracts": tool_contracts,
        "coordinator": coordinator,
        "proactive": proactive,
    }


def _tool_category(tool_name: str) -> str:
    lowered = str(tool_name or "").lower()
    if any(hint in lowered for hint in _DELETE_HINTS):
        return "delete"
    if "git" in lowered:
        return "git"
    if "shell" in lowered or "bash" in lowered or "execute" in lowered or "command" in lowered:
        return "shell"
    if lowered == "metis_sandbox_status" or lowered.startswith("metis_runtime_"):
        return "runtime"
    if any(hint in lowered for hint in _DESKTOP_HINTS):
        return "desktop"
    if any(hint in lowered for hint in _BROWSER_HINTS):
        return "browser"
    if "pdf" in lowered or "docx" in lowered or "document" in lowered or "office" in lowered:
        return "artifact"
    if any(hint in lowered for hint in _IMPLEMENTATION_HINTS):
        return "edit"
    if any(hint in lowered for hint in _VERIFIER_HINTS):
        return "verify"
    if any(hint in lowered for hint in _RESEARCH_HINTS) or _is_read_only_tool(lowered):
        return "read"
    return "general"


def _preferred_tool_surface(category: str) -> str:
    return {
        "read": "dedicated-read-search",
        "edit": "dedicated-edit-tool",
        "verify": "verifier-command-or-browser",
        "browser": "preview-browser",
        "desktop": "computer-use-fallback",
        "artifact": "artifact-tool-plus-render-verify",
        "runtime": "isolated-runtime-workspace",
        "shell": "shell-only-when-dedicated-tool-is-insufficient",
        "git": "git-guarded-workflow",
        "delete": "explicit-permission-required",
    }.get(category, "best-dedicated-tool")


def _category_base_risk(category: str) -> str:
    if category == "delete":
        return RISK_HIGH
    if category in {"edit", "shell", "git", "desktop", "browser", "artifact", "runtime"}:
        return RISK_MEDIUM
    return RISK_LOW


def _tool_contract_reason(category: str, tool_name: str) -> str:
    if category == "read":
        return f"{tool_name} gathers context without changing local state."
    if category == "edit":
        return f"{tool_name} changes files, so it must be grounded in a prior read and followed by verification."
    if category == "shell":
        return f"{tool_name} can run arbitrary local commands, so Metis should explain why shell is needed."
    if category == "git":
        return f"{tool_name} affects repository history or remote state and must follow status/diff discipline."
    if category == "desktop":
        return f"{tool_name} controls visible apps and should be used after background tools are insufficient."
    if category == "browser":
        return f"{tool_name} inspects or interacts with page state and should capture evidence after actions."
    if category == "artifact":
        return f"{tool_name} creates or inspects deliverables and should render/verify final output."
    if category == "runtime":
        return f"{tool_name} works in an isolated runtime workspace and should return artifacts, diagnostics, or patches instead of directly changing the source project."
    if category == "delete":
        return f"{tool_name} can remove data and requires explicit narrow approval."
    return f"{tool_name} follows the generic Metis tool contract."


def _safer_tool_alternative(category: str) -> str:
    return {
        "shell": "Use dedicated tools such as read_file, grep_search, robust_replace_in_file, run_tests, or a domain tool first.",
        "desktop": "Use file/code/document/browser tools in the background before taking over the desktop.",
        "runtime": "Prefer copy-mode runtime sessions and export a patch before writing back to the source project.",
        "delete": "Prefer moving to a backup location or asking for exact confirmation.",
        "git": "Run check_git_status and git_diff before any commit or push.",
        "edit": "Use read_file plus diff-mode replacement instead of whole-file rewrites.",
    }.get(category, "")


def _normalize_worker_type(worker_type: str) -> str:
    value = str(worker_type or "").strip().lower().replace("-", "_")
    if value in {"research", "explore"}:
        return "research"
    if value in {"verify", "verification", "verifier"}:
        return "verification"
    if value in {"review", "fresh_eyes"}:
        return "fresh_verifier"
    return "implementation"


def _default_done_criteria(worker_type: str) -> List[str]:
    if worker_type == "research":
        return ["Relevant files and facts are identified.", "Uncertainties are listed with source paths."]
    if worker_type in {"verification", "fresh_verifier"}:
        return ["Checks were run or explicitly blocked.", "Verdict is PASS, FAIL, or PARTIAL with evidence."]
    return ["Requested change is implemented.", "Changed files are listed.", "Verification evidence is provided."]


def _history_tool_records(history: Sequence[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    tools: List[Dict[str, Any]] = []
    for message in history:
        tool = _tool_record(message)
        if tool:
            tools.append(tool)
    return tools


def _tool_record(message: Mapping[str, Any]) -> Dict[str, Any]:
    if message.get("metis_kind") == "tool" and isinstance(message.get("metis_tool"), Mapping):
        return dict(message.get("metis_tool") or {})
    if str(message.get("role") or "") == "tool":
        return {
            "name": str(message.get("name") or "tool"),
            "arguments": {},
            "result": _message_text(message),
            "status": "observed",
        }
    return {}


def _recent_tool_names(history: Sequence[Mapping[str, Any]]) -> List[str]:
    names: List[str] = []
    for tool in reversed(_history_tool_records(history)):
        name = str(tool.get("name") or "").strip()
        if name and name not in names:
            names.append(name)
        if len(names) >= 8:
            break
    return list(reversed(names))


def _worker_lane(name: str, tools: Sequence[Mapping[str, Any]], hints: Sequence[str]) -> Dict[str, Any]:
    matched = [
        tool
        for tool in tools
        if any(hint in str(tool.get("name") or "").lower() for hint in hints)
    ]
    if not matched:
        status = "pending"
        summary = "Not started yet."
    elif any(str(tool.get("status") or "").lower() in {"running", "waiting_approval"} for tool in matched):
        status = "running"
        summary = f"{len(matched)} related tool step(s), current worker running."
    elif any(str(tool.get("status") or "").lower() == "error" for tool in matched):
        status = "error"
        summary = f"{len(matched)} related tool step(s), last worker needs attention."
    else:
        status = "done"
        summary = f"{len(matched)} related tool step(s) completed."
    return {
        "id": name,
        "name": name,
        "status": status,
        "progress": 0 if status == "pending" else 50 if status == "running" else 100 if status == "done" else 75,
        "summary": summary,
        "toolCount": len(matched),
        "tool_count": len(matched),
    }


def _tool_explanation(tool_name: str, args: Mapping[str, Any]) -> str:
    name = str(tool_name or "tool")
    lowered = name.lower()
    target = _target_label(args, None)
    if _is_read_only_tool(lowered):
        return f"{name} reads project or runtime state{f' from {target}' if target else ''}."
    if any(hint in lowered for hint in _WRITE_HINTS):
        return f"{name} may modify files or local state{f' at {target}' if target else ''}."
    if any(hint in lowered for hint in _DELETE_HINTS):
        return f"{name} may remove files or local state{f' at {target}' if target else ''}."
    if any(hint in lowered for hint in _DESKTOP_HINTS):
        return f"{name} can control visible desktop applications."
    if any(hint in lowered for hint in _BROWSER_HINTS):
        return f"{name} can inspect or interact with browser content."
    if "execute" in lowered or "shell" in lowered or "bash" in lowered:
        command = _first_arg(args, "command", "cmd")
        return f"{name} runs a local command{f': {command}' if command else ''}."
    return f"{name} performs a local tool action."


def _risk_sentence(
    risk_level: str,
    tool_name: str,
    args: Mapping[str, Any],
    path_safety: Mapping[str, Any] | None,
) -> str:
    if _path_outside_workspace(path_safety):
        return "May write outside workspace"
    if risk_level == RISK_HIGH:
        return "Could change or delete sensitive data"
    if risk_level == RISK_MEDIUM:
        return "Recoverable local state change"
    if _first_arg(args, "command", "cmd"):
        return "Command output may be noisy"
    return "Read-only or low impact"


def _risk_from_decision(decision: Mapping[str, Any] | None) -> str:
    raw = str((decision or {}).get("risk_level") or (decision or {}).get("riskLevel") or "").upper()
    if raw in {RISK_LOW, RISK_MEDIUM, RISK_HIGH}:
        return raw
    return RISK_LOW


def _risk_from_path_safety(path_safety: Mapping[str, Any] | None) -> str:
    if not path_safety:
        return RISK_LOW
    if not bool(path_safety.get("allowed", True)):
        return RISK_HIGH
    if _path_outside_workspace(path_safety):
        return RISK_HIGH
    return RISK_LOW


def _path_outside_workspace(path_safety: Mapping[str, Any] | None) -> bool:
    if not path_safety:
        return False
    return bool(path_safety.get("outside_workspace") or path_safety.get("outsideWorkspace")) or str(
        path_safety.get("code") or ""
    ) == "PATH_OUTSIDE_WORKSPACE"


def _is_read_only_tool(lowered_name: str) -> bool:
    return any(lowered_name == prefix or lowered_name.startswith(prefix) for prefix in _READ_ONLY_PREFIXES)


def _looks_high_risk_command(command: str) -> bool:
    lowered = str(command or "").lower()
    return any(re.search(pattern, lowered) for pattern in _HIGH_RISK_COMMAND_PATTERNS)


def _looks_sensitive_path(path: str) -> bool:
    lowered = str(path or "").lower().replace("/", os.sep)
    return any(marker in lowered for marker in _SENSITIVE_PATH_MARKERS)


def _path_text(args: Mapping[str, Any]) -> str:
    values: List[str] = []
    for key in ("path", "file", "file_path", "target", "target_path", "root_path", "cwd"):
        value = args.get(key)
        if isinstance(value, str):
            values.append(value)
    return " ".join(values)


def _first_arg(args: Mapping[str, Any], *keys: str) -> str:
    for key in keys:
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _target_label(args: Mapping[str, Any], result: Any) -> str:
    for key in ("path", "file", "file_path", "target", "target_path", "url", "cwd"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return _short_path(value)
    if isinstance(result, str):
        match = re.search(r"([A-Za-z]:\\[^:*?\"<>|\r\n]+|[\w./-]+\.(?:py|ts|tsx|js|md|json|css|html|docx|pdf))", result)
        if match:
            return _short_path(match.group(1))
    return ""


def _short_path(path: str) -> str:
    clean = str(path or "").strip().rstrip("\\/")
    if not clean:
        return ""
    if clean.startswith("http://") or clean.startswith("https://"):
        return clean.split("?", 1)[0].rstrip("/").rsplit("/", 1)[-1] or clean
    return clean.replace("\\", "/").rsplit("/", 1)[-1] or clean


def _tool_verb(name: str, *, past: bool = False) -> str:
    lowered = str(name or "").lower()
    if "read" in lowered:
        return "Read"
    if "write" in lowered or "edit" in lowered:
        return "Updated" if past else "Update"
    if "search" in lowered or "grep" in lowered:
        return "Searched" if past else "Search"
    if "browser" in lowered:
        return "Checked" if past else "Check"
    if "desktop" in lowered or "win2" in lowered:
        return "Controlled" if past else "Control"
    return "Ran" if past else "Run"


def _result_looks_failed(result: Any) -> bool:
    text = result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, default=str)[:500]
    lowered = str(text or "").lower()
    return any(marker in lowered for marker in ("traceback", "error", "failed", "exception", "permission denied"))


def _clip_label(value: str) -> str:
    return _truncate(" ".join(str(value or "").split()), 42)


def _message_text(message: Mapping[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping):
                parts.append(str(block.get("text") or block.get("content") or ""))
        return "\n".join(part for part in parts if part)
    return str(content or "")


def _last_role_text(history: Sequence[Mapping[str, Any]], role: str) -> str:
    for message in reversed(history):
        if str(message.get("role") or "") == role:
            text = _message_text(message).strip()
            if text:
                return text
    return ""


def _clean_prompt_text(text: str) -> str:
    cleaned = re.sub(r"\s+", " ", str(text or "")).strip()
    cleaned = re.sub(r"^[/#]\w+\s*", "", cleaned).strip()
    return cleaned.strip(" \"'`")


def _next_step_from_text(text: str) -> str:
    for line in str(text or "").splitlines():
        stripped = line.strip(" -\t")
        if not stripped:
            continue
        lowered = stripped.lower()
        if any(marker in lowered for marker in ("next", "下一步", "继续", "建议", "todo")):
            for marker in ("下一步是", "下一步:", "下一步：", "下一步", "Next:", "next:"):
                if marker in stripped:
                    stripped = stripped.split(marker, 1)[1].strip(" ：:，,。.")
                    break
            return _truncate(stripped, 80)
    return ""


def _dedupe_suggestions(values: Sequence[str]) -> List[str]:
    output: List[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in output:
            continue
        if len(text) > 16:
            text = _truncate(text, 16)
        output.append(text)
    return output


def _verification_summary(verdict: str, checks: Sequence[Mapping[str, Any]], failures: Sequence[Mapping[str, Any]]) -> str:
    if verdict == "PASS":
        return f"Verifier ran {len(checks)} checks and found no failing check."
    if verdict == "FAIL":
        return f"Verifier found {len(failures)} failing check(s)."
    return "Verifier needs runnable checks before issuing PASS or FAIL."


def _lower_first(text: str) -> str:
    if not text:
        return text
    return text[:1].lower() + text[1:]


def _max_risk(*levels: Any) -> str:
    rank = {RISK_LOW: 0, "low": 0, RISK_MEDIUM: 1, "medium": 1, RISK_HIGH: 2, "high": 2}
    selected = RISK_LOW
    selected_rank = 0
    for level in levels:
        text = str(level or "").strip()
        score = rank.get(text, rank.get(text.upper(), 0))
        if score > selected_rank:
            selected_rank = score
            selected = {0: RISK_LOW, 1: RISK_MEDIUM, 2: RISK_HIGH}[score]
    return selected


def _truncate(text: str, limit: int) -> str:
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    return value[: max(0, limit - 3)].rstrip() + "..."


def _safe_int(value: Any, fallback: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback

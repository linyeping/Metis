from __future__ import annotations

import fnmatch
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Mapping, Optional


PERMISSION_ACTIONS = {"allow", "deny", "ask"}
READ_ONLY_MODES = {"read_only", "readonly", "chat"}
AUTO_GUARD_MODES = {"auto_guard", "autoguard", "guarded_auto"}
# Modes that allow tools outright when no rule/registry gate decides.
# (plan is NOT here — it researches read-only; edit/auto_guard gate per tool.)
ALLOWING_MODES = {"auto", "bypass"}

# "Accept edits" (edit) auto-applies file edits but still asks before these
# system-affecting actions: shell, desktop control, and live web browsing.
EDIT_ASK_TOOLS = {
    "execute_bash_command",
    "start_long_running_process",
    "stop_long_running_process",
    "desktop_action",
    "desktop_vision_task",
    "desktop_win2_action",
    "desktop_win2_task",
    "browse_web",
    "browse_and_extract",
}

# "Auto" (auto_guard) runs autonomously — even shell + edits — and only stops to
# ask before the genuinely destructive / full-machine-control actions.
VERY_DANGEROUS_TOOLS = {
    "delete_directory",
    "desktop_action",
    "desktop_vision_task",
    "desktop_win2_action",
    "desktop_win2_task",
}

READ_ONLY_TOOL_PREFIXES = (
    "read",
    "list",
    "grep",
    "glob",
    "search",
    "semantic_search",
    "generate_repo_map",
    "git_diff",
    "check_git_status",
    "web_search",
    "web_research",
    "web_fetch",
)

DANGEROUS_BROAD_ALLOW_TOOLS = {
    "*",
    "execute_bash_command",
    "desktop_action",
    "desktop_vision_task",
    "desktop_win2_task",
    "browse_web",
    "delete_file",
    "delete_directory",
}

DANGEROUS_SHELL_PREFIXES = (
    "python",
    "python3",
    "py",
    "node",
    "npm",
    "pnpm",
    "yarn",
    "powershell",
    "pwsh",
    "cmd",
    "bash",
    "sh",
)


@dataclass(frozen=True)
class PermissionDecision:
    action: str
    source: str
    reason: str
    mode: str = "auto"
    risk_level: str = "low"
    rule_id: str = ""
    rule_source: str = ""
    hard_denied: bool = False
    skipped_rule_ids: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "action": self.action,
            "source": self.source,
            "reason": self.reason,
            "mode": self.mode,
            "risk_level": self.risk_level,
            "rule_id": self.rule_id,
            "rule_source": self.rule_source,
            "hard_denied": self.hard_denied,
            "skipped_rule_ids": list(self.skipped_rule_ids),
        }


def normalize_permission_mode(mode: str) -> str:
    value = str(mode or "auto").strip().lower().replace("-", "_")
    if value == "full":
        return "bypass"
    if value in {"manual", "always_ask"}:
        return "ask"
    if value in READ_ONLY_MODES:
        return "read_only"
    if value in AUTO_GUARD_MODES:
        return "auto_guard"
    if value in {"ask", "edit", "plan", "auto", "bypass"}:
        return value
    return "auto"


def normalize_permission_rule(rule: Mapping[str, Any]) -> Dict[str, Any]:
    now = time.time()
    action = str(rule.get("action") or "ask").strip().lower()
    if action not in PERMISSION_ACTIONS:
        action = "ask"
    args_match = rule.get("args_match", {})
    if not isinstance(args_match, Mapping):
        args_match = {}
    return {
        "id": str(rule.get("id") or uuid.uuid4()),
        "tool": str(rule.get("tool") or "*").strip() or "*",
        "action": action,
        "args_match": {
            str(key): str(value)
            for key, value in args_match.items()
            if str(key).strip() and str(value).strip()
        },
        "source": str(rule.get("source") or "workspace").strip() or "workspace",
        "created_at": float(rule.get("created_at") or now),
        "updated_at": float(rule.get("updated_at") or rule.get("created_at") or now),
    }


def permission_rule_payload(rule: Mapping[str, Any]) -> Dict[str, Any]:
    normalized = normalize_permission_rule(rule)
    return {
        "id": str(normalized.get("id") or ""),
        "tool": str(normalized.get("tool") or "*"),
        "action": str(normalized.get("action") or "ask"),
        "args_match": dict(normalized.get("args_match") or {}),
        "source": str(normalized.get("source") or "workspace"),
        "created_at": float(normalized.get("created_at") or 0),
        "updated_at": float(normalized.get("updated_at") or 0),
    }


def permission_rule_matches(rule: Mapping[str, Any], tool_name: str, arguments: Mapping[str, Any]) -> bool:
    rule_tool = str(rule.get("tool", ""))
    if rule_tool and rule_tool != "*" and not fnmatch.fnmatch(str(tool_name or ""), rule_tool):
        return False
    args_match = rule.get("args_match", {})
    if isinstance(args_match, Mapping) and args_match:
        for key, pattern in args_match.items():
            value = str(arguments.get(str(key), ""))
            if not fnmatch.fnmatch(value, str(pattern)):
                return False
    return True


def sort_permission_rules(rules: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    normalized = [normalize_permission_rule(rule) for rule in rules if isinstance(rule, Mapping)]
    action_rank = {"deny": 0, "ask": 1, "allow": 2}
    return sorted(
        normalized,
        key=lambda rule: (
            action_rank.get(str(rule.get("action") or "ask"), 1),
            0 if dict(rule.get("args_match") or {}) else 1,
            0 if str(rule.get("source") or "") == "composer_access" else 1,
            -float(rule.get("updated_at") or rule.get("created_at") or 0),
        ),
    )


def is_dangerous_allow_rule(rule: Mapping[str, Any]) -> bool:
    normalized = normalize_permission_rule(rule)
    if normalized["action"] != "allow":
        return False
    tool = normalized["tool"]
    args_match = dict(normalized.get("args_match") or {})
    if tool in DANGEROUS_BROAD_ALLOW_TOOLS and not args_match:
        return True
    if tool in {"execute_bash_command", "desktop_action", "desktop_vision_task", "desktop_win2_task"}:
        if not args_match:
            return True
        for key in ("command", "cmd", "action", "goal"):
            pattern = str(args_match.get(key) or "").strip().lower()
            if pattern in {"*", "**"}:
                return True
            if any(pattern.startswith(f"{prefix}*") or pattern.startswith(f"{prefix}:*") for prefix in DANGEROUS_SHELL_PREFIXES):
                return True
    return False


def tool_is_read_only(tool_name: str) -> bool:
    name = str(tool_name or "").strip()
    return any(name == prefix or name.startswith(prefix) for prefix in READ_ONLY_TOOL_PREFIXES)


def evaluate_rule_layer(
    *,
    tool_name: str,
    arguments: Mapping[str, Any],
    rules: Iterable[Mapping[str, Any]],
    mode: str = "auto",
    hard_deny_reason: str = "",
) -> PermissionDecision:
    normalized_mode = normalize_permission_mode(mode)
    if hard_deny_reason:
        return PermissionDecision(
            action="deny",
            source="hard_deny",
            reason=hard_deny_reason,
            mode=normalized_mode,
            risk_level="high",
            hard_denied=True,
        )

    skipped: List[str] = []
    for rule in sort_permission_rules(rules):
        if not permission_rule_matches(rule, tool_name, arguments):
            continue
        action = str(rule.get("action") or "ask")
        if normalized_mode == "auto_guard" and action == "allow" and is_dangerous_allow_rule(rule):
            skipped.append(str(rule.get("id") or ""))
            continue
        return PermissionDecision(
            action=action,
            source="rule",
            reason=f"Matched {action} rule for {rule.get('tool') or '*'}",
            mode=normalized_mode,
            risk_level=_risk_for_rule(rule),
            rule_id=str(rule.get("id") or ""),
            rule_source=str(rule.get("source") or ""),
            skipped_rule_ids=skipped,
        )

    if skipped:
        return PermissionDecision(
            action="ask",
            source="auto_guard",
            reason="AutoGuard skipped a broad allow rule for a high-risk tool.",
            mode=normalized_mode,
            risk_level="high",
            skipped_rule_ids=skipped,
        )
    return PermissionDecision(
        action="none",
        source="no_rule",
        reason="No workspace permission rule matched.",
        mode=normalized_mode,
        risk_level="low",
    )


def evaluate_permission(
    *,
    mode: str,
    tool_name: str,
    arguments: Mapping[str, Any],
    rules: Optional[Iterable[Mapping[str, Any]]] = None,
    hard_deny_reason: str = "",
    hook_action: str = "",
    hook_reason: str = "",
    registry_requires_approval: Optional[bool] = None,
) -> PermissionDecision:
    normalized_mode = normalize_permission_mode(mode)
    if hard_deny_reason:
        return PermissionDecision(
            action="deny",
            source="hard_deny",
            reason=hard_deny_reason,
            mode=normalized_mode,
            risk_level="high",
            hard_denied=True,
        )

    if normalized_mode == "read_only" and not tool_is_read_only(tool_name):
        return PermissionDecision(
            action="deny",
            source="mode",
            reason="Read-only mode blocks tools that may change state.",
            mode=normalized_mode,
            risk_level="medium",
        )

    # Plan mode: research and present a plan without changing anything. Reading,
    # searching, asking, and todo/plan bookkeeping are fine; edits/shell/desktop
    # are blocked until the user leaves plan mode. This wins over allow rules.
    if normalized_mode == "plan" and _tool_looks_high_risk(tool_name):
        return PermissionDecision(
            action="deny",
            source="mode",
            reason="Plan mode researches and plans without making changes. Switch out of plan mode to apply.",
            mode=normalized_mode,
            risk_level="medium",
        )

    normalized_hook_action = str(hook_action or "").strip().lower()
    if normalized_hook_action in PERMISSION_ACTIONS:
        return PermissionDecision(
            action=normalized_hook_action,
            source="hook",
            reason=hook_reason or f"Hook requested {normalized_hook_action}.",
            mode=normalized_mode,
            risk_level="medium" if normalized_hook_action != "allow" else "low",
        )

    if rules is not None:
        rule_decision = evaluate_rule_layer(
            tool_name=tool_name,
            arguments=arguments,
            rules=rules,
            mode=normalized_mode,
        )
        if rule_decision.action != "none":
            return rule_decision

    # Mode-driven decision: the user's explicit mode choice wins over the
    # registry's per-tool approval default (so Accept-edits truly auto-applies
    # edits, Auto truly runs autonomously, etc.).
    if normalized_mode == "ask":
        return PermissionDecision(action="ask", source="mode", reason="Ask mode requires approval for every tool.", mode=normalized_mode)
    if normalized_mode in ALLOWING_MODES:
        return PermissionDecision(action="allow", source="mode", reason=f"{normalized_mode} mode allows this tool.", mode=normalized_mode)
    if normalized_mode == "auto_guard":
        if _tool_is_very_dangerous(tool_name):
            return PermissionDecision(
                action="ask",
                source="auto_guard",
                reason="Auto mode runs autonomously but asks before destructive or full-machine-control actions.",
                mode=normalized_mode,
                risk_level="high",
            )
        return PermissionDecision(action="allow", source="mode", reason="Auto mode allows this tool.", mode=normalized_mode)
    if normalized_mode == "edit":
        if _tool_is_edit_ask(tool_name):
            return PermissionDecision(action="ask", source="mode", reason="Accept-edits mode auto-applies file edits but asks before shell, desktop, and web actions.", mode=normalized_mode, risk_level="medium")
        return PermissionDecision(action="allow", source="mode", reason="Accept-edits mode auto-applies file edits.", mode=normalized_mode)

    # Fallback for any non-canonical mode: honor registry approval metadata.
    if registry_requires_approval is not None:
        return PermissionDecision(
            action="ask" if registry_requires_approval else "allow",
            source="registry",
            reason="Tool registry approval metadata was applied.",
            mode=normalized_mode,
            risk_level="medium" if registry_requires_approval else "low",
        )
    return PermissionDecision(action="allow", source="mode", reason="No permission gate matched.", mode=normalized_mode)


def permission_control_payload(*, mode: str, rules: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    normalized_rules = sort_permission_rules(rules)
    dangerous = [
        permission_rule_payload(rule)
        for rule in normalized_rules
        if is_dangerous_allow_rule(rule)
    ]
    return {
        "version": "v2",
        "mode": normalize_permission_mode(mode),
        "decision_order": [
            "hard_deny",
            "mode_constraints",
            "hooks",
            "workspace_rules",
            "autoguard",
            "registry_metadata",
            "user_prompt",
        ],
        # The 5 user-facing modes (mirrors Claude Code): ask / edit (accept
        # edits) / plan / auto_guard (auto) / bypass. auto + read_only stay as
        # accepted aliases for older sessions.
        "available_modes": ["ask", "edit", "plan", "auto_guard", "bypass"],
        "dangerous_allow_rules": dangerous,
        "dangerous_allow_count": len(dangerous),
        "notes": [
            "Path safety hard-deny always wins over saved allow rules.",
            "Deny rules take precedence over ask and allow rules.",
            "AutoGuard ignores broad allow rules for high-risk tools and asks instead.",
        ],
    }


def _risk_for_rule(rule: Mapping[str, Any]) -> str:
    action = str(rule.get("action") or "ask")
    if action == "deny":
        return "high"
    if is_dangerous_allow_rule(rule):
        return "high"
    if action == "ask":
        return "medium"
    return "low"


def _tool_is_edit_ask(tool_name: str) -> bool:
    """System-affecting tools that 'Accept edits' mode still asks about."""
    name = str(tool_name or "")
    if name in EDIT_ASK_TOOLS:
        return True
    return any(marker in name for marker in ("shell", "bash", "desktop", "browser", "browse"))


def _tool_is_very_dangerous(tool_name: str) -> bool:
    """Destructive / full-machine-control tools that 'Auto' mode asks about."""
    return str(tool_name or "") in VERY_DANGEROUS_TOOLS


def _tool_looks_high_risk(tool_name: str) -> bool:
    name = str(tool_name or "")
    if name in DANGEROUS_BROAD_ALLOW_TOOLS:
        return True
    return any(marker in name for marker in ("write", "edit", "delete", "shell", "bash", "desktop", "browser"))

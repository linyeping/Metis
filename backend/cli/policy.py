from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping


class CliPolicyError(ValueError):
    pass


def build_permission_checker(workspace: Path, policy_path: str = "") -> Callable[[str, Dict[str, Any]], str | None]:
    rules: List[Dict[str, Any]] = []
    workspace_doc = workspace / ".metis" / "permissions.json"
    legacy_doc = workspace / ".miro" / "permissions.json"
    if workspace_doc.is_file():
        rules.extend(_load_rules(workspace_doc, explicit=False))
    elif legacy_doc.is_file():
        rules.extend(_load_rules(legacy_doc, explicit=False))
    if policy_path:
        path = Path(policy_path).expanduser()
        if not path.is_absolute():
            path = workspace / path
        rules.extend(_load_rules(path.resolve(strict=False), explicit=True))

    def check(tool_name: str, arguments: Dict[str, Any]) -> str | None:
        if not rules:
            return None
        from backend.runtime.permission_control import evaluate_rule_layer

        decision = evaluate_rule_layer(
            tool_name=tool_name,
            arguments=arguments,
            rules=rules,
        )
        return decision.action if decision.action in {"allow", "ask", "deny"} else None

    return check


def _load_rules(path: Path, *, explicit: bool) -> List[Dict[str, Any]]:
    if not path.is_file():
        if explicit:
            raise CliPolicyError(f"policy file not found: {path}")
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliPolicyError(f"invalid policy file {path}: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("rules", []), list):
        raise CliPolicyError(f"policy file must contain a rules array: {path}")
    return [_normalize_rule(item, path=path) for item in data.get("rules", []) if isinstance(item, dict)]


def _normalize_rule(rule: Mapping[str, Any], *, path: Path) -> Dict[str, Any]:
    if "match" not in rule:
        action = str(rule.get("action") or "ask").strip().lower()
        if action not in {"allow", "ask", "deny"}:
            raise CliPolicyError(f"unsupported rule action in {path}: {action!r}")
        return {
            "id": str(rule.get("id") or ""),
            "tool": str(rule.get("tool") or "*").strip() or "*",
            "action": action,
            "args_match": dict(rule.get("args_match") or {}),
            "source": str(rule.get("source") or "cli_policy"),
        }

    match = rule.get("match")
    if not isinstance(match, Mapping):
        raise CliPolicyError(f"rule match must be an object in {path}")
    unsupported = set(match) - {"tool", "cmd", "path"}
    if unsupported:
        names = ", ".join(sorted(str(item) for item in unsupported))
        raise CliPolicyError(f"policy match fields are not supported by CLI P0: {names}")
    effect = str(rule.get("effect") or "ask").strip().lower()
    if effect not in {"allow", "ask", "deny"}:
        raise CliPolicyError(f"unsupported rule effect in {path}: {effect!r}")
    args_match: Dict[str, str] = {}
    if match.get("cmd") not in (None, ""):
        args_match["command"] = str(match["cmd"])
    if match.get("path") not in (None, ""):
        args_match["path"] = str(match["path"])
    return {
        "id": str(rule.get("id") or ""),
        "tool": str(match.get("tool") or "*").strip() or "*",
        "action": effect,
        "args_match": args_match,
        "source": "cli_policy",
    }

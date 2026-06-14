"""Execute configured Miro hooks after tool calls."""
from __future__ import annotations

import fnmatch
import json
import os
import subprocess
from typing import Any, Dict, List, Optional


def load_hooks() -> List[Dict[str, Any]]:
    """Load hooks from .miro/hooks.json on each call."""
    hooks_path = os.path.join(os.getcwd(), ".miro", "hooks.json")
    if not os.path.isfile(hooks_path):
        return []
    try:
        with open(hooks_path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except (json.JSONDecodeError, OSError):
        return []
    hooks = data.get("hooks", []) if isinstance(data, dict) else []
    return hooks if isinstance(hooks, list) else []


def run_post_hooks(
    tool_name: str,
    arguments: Dict[str, Any],
    result: str,
) -> Optional[str]:
    """Run matching post-hooks and return their display output."""
    outputs: List[str] = []
    for hook in load_hooks():
        if not isinstance(hook, dict):
            continue
        trigger = str(hook.get("trigger", ""))
        if not trigger.startswith("post:"):
            continue
        hook_tool = trigger[5:]
        if hook_tool != "*" and not fnmatch.fnmatch(tool_name, hook_tool):
            continue
        if not _condition_matches(hook.get("condition", {}), arguments):
            continue

        command = str(hook.get("command", "")).strip()
        if not command:
            continue
        command = _substitute_args(command, arguments, result)
        description = str(hook.get("description") or command)

        try:
            proc = subprocess.run(
                command,
                shell=True,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=os.getcwd(),
            )
            if proc.returncode == 0:
                hook_text = (proc.stdout or "").strip()
                suffix = f": {hook_text[:200]}" if hook_text else ""
                outputs.append(f"🪝 Hook [{description}]: ✅{suffix}")
            else:
                hook_text = (proc.stderr or proc.stdout or "").strip()
                outputs.append(f"🪝 Hook [{description}]: ⚠️ {hook_text[:200]}")
        except subprocess.TimeoutExpired:
            outputs.append(f"🪝 Hook [{description}]: timeout")
        except Exception as exc:
            outputs.append(f"🪝 Hook error [{description}]: {exc}")

    return "\n".join(outputs) if outputs else None


def _condition_matches(condition: Any, arguments: Dict[str, Any]) -> bool:
    if not isinstance(condition, dict) or not condition:
        return True
    for key, pattern in condition.items():
        value = str(arguments.get(str(key), ""))
        if not fnmatch.fnmatch(value, str(pattern)):
            return False
    return True


def _substitute_args(command: str, arguments: Dict[str, Any], result: str) -> str:
    rendered = command.replace("{result}", result[:1000])
    for key, value in arguments.items():
        rendered = rendered.replace(f"{{{key}}}", str(value))
    return rendered

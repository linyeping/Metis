from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional


READ_TOOLS = {
    "read_file",
    "read_multiple_files",
    "list_directory",
    "search_in_file",
    "search_in_codebase",
    "find_files",
    "grep_search",
    "glob_search",
    "semantic_search",
}

WRITE_TOOLS = {
    "write_file",
    "append_to_file",
    "robust_replace_in_file",
    "edit_code_ast",
    "rename_file_update_refs",
    "delete_file",
    "delete_directory",
    "create_directory",
}

PATH_KEYS = (
    "path",
    "file_path",
    "directory_path",
    "target_path",
    "source_path",
    "old_path",
    "new_path",
    "root",
    "cwd",
)

LIST_PATH_KEYS = ("paths", "files", "file_paths")

SECRET_BASENAMES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    ".env.test",
    ".env.staging",
    ".envrc",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".pgpass",
    "id_rsa",
    "id_ed25519",
    "auth.json",
    "auth.lock",
}

SECRET_EXTENSIONS = {
    ".key",
    ".pem",
    ".pfx",
    ".p12",
    ".ppk",
}

SECRET_DIRS = {".ssh", ".gnupg"}

CONTROL_DIRS = {".metis", ".miro"}
CONTROL_FILE_NAMES = {"permissions.json", "hooks.json", "config.json", "config.yaml"}


@dataclass(frozen=True)
class PathSafetyDecision:
    allowed: bool
    code: str = ""
    message: str = ""
    path: str = ""

    def error_text(self) -> str:
        if self.code == "PATH_OUTSIDE_WORKSPACE":
            return f"错误：路径在工作区之外，已拒绝。{self.message}"
        if self.code == "PATH_SENSITIVE":
            return f"错误：目标路径受保护，已拒绝访问。{self.message}"
        return f"Error: Access denied [{self.code}]: {self.message}"


def validate_tool_paths(
    tool_name: str,
    arguments: Dict[str, Any],
    *,
    workspace_root: Optional[str] = None,
) -> PathSafetyDecision:
    action = _tool_action(tool_name)
    if action is None:
        return PathSafetyDecision(True)

    root = _resolve_path(workspace_root or os.getcwd())
    for raw_path in _extract_paths(arguments):
        decision = validate_path_access(raw_path, action=action, workspace_root=root)
        if not decision.allowed:
            return decision
    return PathSafetyDecision(True)


def validate_path_access(
    path: str,
    *,
    action: str,
    workspace_root: Optional[str] = None,
) -> PathSafetyDecision:
    if not str(path or "").strip():
        return PathSafetyDecision(True)

    root = _resolve_path(workspace_root or os.getcwd())
    target = _resolve_path(path, root=root)
    target_text = str(target)

    secret_reason = _sensitive_reason(target)
    if secret_reason:
        return PathSafetyDecision(
            False,
            "PATH_SENSITIVE",
            f"{target_text} is protected ({secret_reason}).",
            target_text,
        )

    if action == "write" and not _is_within(target, root):
        return PathSafetyDecision(
            False,
            "PATH_OUTSIDE_WORKSPACE",
            f"{target_text} is outside the active workspace {root}.",
            target_text,
        )

    if action == "write":
        raw_target = _unresolved_path(path, root=root)
        if raw_target.is_symlink():
            return PathSafetyDecision(
                False,
                "PATH_SYMLINK_WRITE",
                f"Cannot write to symlink {raw_target}.",
                str(raw_target),
            )

    return PathSafetyDecision(True)


def _tool_action(tool_name: str) -> Optional[str]:
    if tool_name in WRITE_TOOLS:
        return "write"
    if tool_name in READ_TOOLS:
        return "read"
    return None


def _extract_paths(arguments: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    for key in PATH_KEYS:
        value = arguments.get(key)
        if isinstance(value, str):
            paths.append(value)
    for key in LIST_PATH_KEYS:
        value = arguments.get(key)
        if isinstance(value, str):
            paths.append(value)
        elif isinstance(value, Iterable):
            paths.extend(item for item in value if isinstance(item, str))
    return paths


def _resolve_path(path: str, *, root: Optional[Path] = None) -> Path:
    raw = Path(os.path.expanduser(str(path)))
    if not raw.is_absolute():
        raw = (root or Path.cwd()) / raw
    return raw.resolve(strict=False)


def _unresolved_path(path: str, *, root: Optional[Path] = None) -> Path:
    raw = Path(os.path.expanduser(str(path)))
    if not raw.is_absolute():
        raw = (root or Path.cwd()) / raw
    return raw


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _sensitive_reason(path: Path) -> str:
    name = path.name.lower()
    if name in SECRET_BASENAMES:
        return "secret-bearing filename"
    if path.suffix.lower() in SECRET_EXTENSIONS:
        return "secret-bearing extension"

    parts = [part.lower() for part in path.parts]
    if any(part in SECRET_DIRS for part in parts):
        return "secret-bearing directory"

    for index, part in enumerate(parts):
        if part not in CONTROL_DIRS:
            continue
        tail = parts[index + 1 :]
        if not tail:
            return "Metis control directory"
        if tail[0] in {"audit", "mcp-tokens", "pairing"}:
            return "Metis control data"
        if path.name.lower() in CONTROL_FILE_NAMES:
            return "Metis control file"
    return ""

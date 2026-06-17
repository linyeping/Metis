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
    "pdf_info",
    "pdf_extract_text",
    "pdf_render_pages",
    "pdf_screenshot_page",
    "docx_to_pdf",
    "docx_render_pages",
    "docx_inspect_layout",
    "metis_rootfs_asset_status",
    "metis_rootfs_source_status",
    "metis_rootfs_builder_status",
    "metis_rootfs_image_builder_status",
    "metis_vm_bundle_status",
    "metis_wsl_runtime_status",
    "metis_sandbox_status",
    "metis_runtime_status",
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
    "pdf_create",
    "pdf_merge_split",
    "docx_create",
    "docx_edit",
    "office_report_from_code_run",
    "metis_rootfs_asset_download",
    "metis_rootfs_build",
    "metis_rootfs_image_build",
    "metis_rootfs_asset_register",
    "metis_runtime_bundle_package",
    "metis_runtime_bundle_package_v2",
    "metis_runtime_bundle_prepare",
    "metis_vm_direct_assets_prepare",
    "metis_vm_pack_scaffold",
    "metis_vm_direct_runner_prepare",
    "metis_vm_direct_runner_smoke",
    "metis_vm_hcs_starter_prepare",
    "metis_vm_hcs_starter_start",
    "metis_vm_guest_handshake_prepare",
    "metis_vm_guest_handshake_verify",
    "metis_vm_rootfs_boot_verifier_prepare",
    "metis_vm_rootfs_boot_verify",
    "metis_vm_pack_adopt_reference",
    "metis_wsl_runtime_import",
    "metis_runtime_create",
    "metis_runtime_run",
    "metis_runtime_collect_artifacts",
    "metis_runtime_export_patch",
    "metis_runtime_export_diagnostics",
}

PATH_KEYS = (
    "path",
    "file_path",
    "directory_path",
    "target_path",
    "source_path",
    "old_path",
    "new_path",
    "output_path",
    "output_dir",
    "script_path",
    "working_dir",
    "artifacts_dir",
    "root",
    "cwd",
)

LIST_PATH_KEYS = ("paths", "files", "file_paths", "input_paths")

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
    suggested_root: str = ""

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
    writable_roots: Optional[Iterable[str]] = None,
) -> PathSafetyDecision:
    action = _tool_action(tool_name)
    if action is None:
        return PathSafetyDecision(True)

    root = _resolve_path(workspace_root or os.getcwd())
    roots = _resolve_writable_roots(writable_roots, root=root)
    for path_key, raw_path in _extract_path_items(arguments):
        decision = validate_path_access(
            raw_path,
            action=action,
            workspace_root=root,
            writable_roots=roots,
            tool_name=tool_name,
            path_key=path_key,
        )
        if not decision.allowed:
            return decision
    return PathSafetyDecision(True)


def validate_path_access(
    path: str,
    *,
    action: str,
    workspace_root: Optional[str] = None,
    writable_roots: Optional[Iterable[str | Path]] = None,
    tool_name: str = "",
    path_key: str = "",
) -> PathSafetyDecision:
    if not str(path or "").strip():
        return PathSafetyDecision(True)

    root = _resolve_path(workspace_root or os.getcwd())
    target = _resolve_path(path, root=root)
    target_text = str(target)
    roots = _resolve_writable_roots(writable_roots, root=root)

    secret_reason = _sensitive_reason(target)
    if secret_reason:
        return PathSafetyDecision(
            False,
            "PATH_SENSITIVE",
            f"{target_text} is protected ({secret_reason}).",
            target_text,
        )

    if action == "write" and not _is_within(target, root) and not _is_within_any(target, roots):
        suggested_root = suggest_writable_root_for_path(target, tool_name=tool_name, path_key=path_key)
        return PathSafetyDecision(
            False,
            "PATH_OUTSIDE_WORKSPACE",
            (
                f"{target_text} is outside the active workspace {root}. "
                f"Authorized writable roots: {_format_roots_for_message(roots)}."
            ),
            target_text,
            suggested_root,
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


def extract_tool_paths(arguments: Dict[str, Any]) -> List[str]:
    return [path for _key, path in _extract_path_items(arguments)]


def suggest_writable_root_for_path(path: str | Path, *, tool_name: str = "", path_key: str = "") -> str:
    target = _resolve_path(str(path))
    key = str(path_key or "").lower()
    tool = str(tool_name or "").lower()
    directory_keys = {"directory_path", "output_dir", "artifacts_dir", "root", "cwd", "working_dir"}
    directory_tools = {"create_directory", "delete_directory", "list_directory"}
    if key in directory_keys or tool in directory_tools:
        return str(target)
    if target.suffix:
        return str(target.parent)
    if target.exists() and target.is_dir():
        return str(target)
    return str(target.parent)


def _extract_paths(arguments: Dict[str, Any]) -> List[str]:
    return [path for _key, path in _extract_path_items(arguments)]


def _extract_path_items(arguments: Dict[str, Any]) -> List[tuple[str, str]]:
    items: List[tuple[str, str]] = []
    for key in PATH_KEYS:
        value = arguments.get(key)
        if isinstance(value, str):
            items.append((key, value))
    for key in LIST_PATH_KEYS:
        value = arguments.get(key)
        if isinstance(value, str):
            items.append((key, value))
        elif isinstance(value, Iterable):
            items.extend((key, item) for item in value if isinstance(item, str))
    return items


def _resolve_path(path: str, *, root: Optional[Path] = None) -> Path:
    raw = Path(os.path.expanduser(str(path)))
    if not raw.is_absolute():
        raw = (root or Path.cwd()) / raw
    return raw.resolve(strict=False)


def _resolve_writable_roots(paths: Optional[Iterable[str | Path]], *, root: Path) -> List[Path]:
    roots: List[Path] = []
    seen: set[str] = set()
    for raw in paths or []:
        value = str(raw or "").strip()
        if not value:
            continue
        resolved = _resolve_path(value, root=root)
        key = str(resolved).lower()
        if key in seen:
            continue
        seen.add(key)
        roots.append(resolved)
    return roots


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


def _is_within_any(path: Path, roots: Iterable[Path]) -> bool:
    return any(_is_within(path, root) for root in roots)


def _format_roots_for_message(roots: Iterable[Path]) -> str:
    values = [str(root) for root in roots]
    return ", ".join(values) if values else "none"


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

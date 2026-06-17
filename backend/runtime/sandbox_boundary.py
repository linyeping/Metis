from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List


SANDBOX_BOUNDARY_SCHEMA = "metis.sandbox_boundary_policy.v1"

DEFAULT_READ_DENY_PATTERNS = [
    ".git/**",
    ".metis/**",
    ".env",
    ".env.*",
    "*.key",
    "*.pem",
    "*.pfx",
    "*.p12",
    "id_rsa",
    "id_ed25519",
    "node_modules/**",
    "dist/**",
    "build/**",
    "release/**",
]

DEFAULT_NETWORK_DENY_HOSTS = [
    "169.254.169.254",
    "metadata.google.internal",
]

_STRICT_MARKERS = {
    "strict sandbox",
    "strict_sandbox",
    "严格沙箱",
    "强隔离",
    "不要回退",
    "禁止回退",
    "不能回退",
    "fail closed",
    "fail-closed",
}


@dataclass(frozen=True)
class SandboxBoundaryPolicy:
    execution_boundary: str
    recommended_tool: str
    sandbox_mode: str = "copy"
    strict_sandbox: bool = False
    desktop_control_allowed: bool = False
    fallback_order: List[str] = field(default_factory=list)
    read_deny_patterns: List[str] = field(default_factory=lambda: list(DEFAULT_READ_DENY_PATTERNS))
    write_allow_roots: List[str] = field(default_factory=list)
    network_allow_hosts: List[str] = field(default_factory=list)
    network_deny_hosts: List[str] = field(default_factory=lambda: list(DEFAULT_NETWORK_DENY_HOSTS))
    role_boundary: str = "main"
    why: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": SANDBOX_BOUNDARY_SCHEMA,
            "execution_boundary": self.execution_boundary,
            "recommended_tool": self.recommended_tool,
            "sandbox_mode": self.sandbox_mode,
            "strict_sandbox": self.strict_sandbox,
            "fallback_mode": "strict" if self.strict_sandbox else "regular",
            "fallback_order": list(self.fallback_order),
            "desktop_control_allowed": self.desktop_control_allowed,
            "read_deny_patterns": list(self.read_deny_patterns),
            "write_allow_roots": list(self.write_allow_roots),
            "network_allow_hosts": list(self.network_allow_hosts),
            "network_deny_hosts": list(self.network_deny_hosts),
            "role_boundary": self.role_boundary,
            "why": self.why,
        }


def strict_sandbox_requested(text: str = "") -> bool:
    value = str(text or "").strip().lower()
    env_value = os.environ.get("METIS_RUNTIME_STRICT_SANDBOX", "").strip().lower()
    if env_value in {"1", "true", "yes", "on", "strict"}:
        return True
    return any(marker in value for marker in _STRICT_MARKERS)


def boundary_policy_for_task(task_type: str, text: str = "") -> SandboxBoundaryPolicy:
    value = str(task_type or "").strip().lower()
    strict = strict_sandbox_requested(text)
    runtime_fallback = ["metis_wsl", "wsl", "docker"] if strict else ["metis_wsl", "wsl", "docker", "local-copy"]
    if value == "artifact_workflow":
        return SandboxBoundaryPolicy(
            execution_boundary="metis_runtime",
            recommended_tool="metis_runtime_job",
            sandbox_mode="copy",
            strict_sandbox=strict,
            fallback_order=runtime_fallback,
            desktop_control_allowed=False,
            write_allow_roots=["runtime_workspace", "runtime_artifacts", "runtime_diagnostics"],
            why="code/report/artifact workflow runs in background isolation and returns artifacts plus verifier evidence",
        )
    if value == "code":
        return SandboxBoundaryPolicy(
            execution_boundary="repo_plus_runtime",
            recommended_tool="metis_runtime_job",
            sandbox_mode="copy",
            strict_sandbox=strict,
            fallback_order=runtime_fallback,
            desktop_control_allowed=False,
            write_allow_roots=["runtime_workspace", "runtime_artifacts", "runtime_diagnostics"],
            why="repo tools can read/edit source, while commands/tests/builds should prefer isolated runtime execution",
        )
    if value == "desktop":
        return SandboxBoundaryPolicy(
            execution_boundary="desktop",
            recommended_tool="desktop_win2_task",
            sandbox_mode="none",
            strict_sandbox=False,
            fallback_order=["desktop_win2", "desktop_window", "screenshot"],
            desktop_control_allowed=True,
            read_deny_patterns=[],
            write_allow_roots=[],
            why="explicit UI/window/mouse/keyboard operation requested",
        )
    if value == "browser":
        return SandboxBoundaryPolicy(
            execution_boundary="preview_browser",
            recommended_tool="preview_browser_verify",
            sandbox_mode="browser_tab",
            strict_sandbox=False,
            fallback_order=["preview_browser", "browse_web", "web_fetch"],
            desktop_control_allowed=False,
            read_deny_patterns=[],
            write_allow_roots=[],
            why="browser/local page task should use the in-app preview browser first",
        )
    if value == "external_lookup":
        return SandboxBoundaryPolicy(
            execution_boundary="web",
            recommended_tool="web_fetch",
            sandbox_mode="none",
            strict_sandbox=False,
            fallback_order=["web_fetch", "web_search", "browse_web"],
            desktop_control_allowed=False,
            read_deny_patterns=[],
            write_allow_roots=[],
            why="fresh external information requested",
        )
    if value == "long_context":
        return SandboxBoundaryPolicy(
            execution_boundary="context",
            recommended_tool="semantic_search",
            sandbox_mode="none",
            strict_sandbox=False,
            fallback_order=["read_file_chunk", "semantic_search", "read_multiple_files"],
            desktop_control_allowed=False,
            write_allow_roots=[],
            why="large context work should use chunking and compaction",
        )
    return SandboxBoundaryPolicy(
        execution_boundary="direct",
        recommended_tool="",
        sandbox_mode="none",
        strict_sandbox=False,
        fallback_order=[],
        desktop_control_allowed=False,
        read_deny_patterns=[],
        write_allow_roots=[],
        why="general chat or lightweight reasoning",
    )


def runtime_manifest_boundary(
    *,
    workspace_dir: Path,
    artifacts_dir: Path,
    diagnostics_dir: Path,
    source_root: Path,
    backend: str,
    mode: str,
    allow_network: bool,
    strict_sandbox: bool,
) -> Dict[str, Any]:
    write_roots = [str(workspace_dir), str(artifacts_dir), str(diagnostics_dir)]
    read_roots = [str(workspace_dir), str(artifacts_dir), str(diagnostics_dir)]
    if str(mode or "").lower() == "mount":
        read_roots.append(str(source_root))
    return {
        "schema": SANDBOX_BOUNDARY_SCHEMA,
        "backend": str(backend or ""),
        "mode": str(mode or "copy"),
        "strict_sandbox": bool(strict_sandbox),
        "read_allow_roots": _dedupe(read_roots),
        "read_deny_patterns": list(DEFAULT_READ_DENY_PATTERNS),
        "write_allow_roots": _dedupe(write_roots),
        "network": {
            "allow": bool(allow_network),
            "allow_hosts": [],
            "deny_hosts": list(DEFAULT_NETWORK_DENY_HOSTS),
        },
        "local_copy_warning": (
            "local-copy isolates project files by snapshot but is not an OS sandbox"
            if str(backend or "").lower() == "local"
            else ""
        ),
    }


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


__all__ = [
    "SANDBOX_BOUNDARY_SCHEMA",
    "SandboxBoundaryPolicy",
    "boundary_policy_for_task",
    "runtime_manifest_boundary",
    "strict_sandbox_requested",
]

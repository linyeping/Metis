from __future__ import annotations

import importlib.util
import json
import os
import platform
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, Mapping, TextIO
from urllib.parse import urlsplit

from backend.bridges.provider_contract import ProviderRegistryError
from backend.bridges.provider_registry import resolve_provider_for_config
from backend.core.credential_store import CredentialStoreError, is_available, read_api_key
from backend.version import __version__

from .args import CliUsageError, DoctorCommandArgs, SandboxCommandArgs
from .headless import EXIT_ENVIRONMENT, EXIT_SUCCESS

DOCTOR_SCHEMA = "metis.cli_doctor.v1"
SANDBOX_STATUS_SCHEMA = "metis.cli_sandbox_status.v1"
SANDBOX_REPAIR_SCHEMA = "metis.cli_sandbox_repair.v1"

_API_KEY_ENV = (
    "METIS_LLM_API_KEY",
    "MIRO_LLM_API_KEY",
    "DEEPSEEK_API_KEY",
    "OPENAI_API_KEY",
    "ANTHROPIC_API_KEY",
    "GEMINI_API_KEY",
    "DOUBAO_API_KEY",
    "ARK_API_KEY",
    "VOLCENGINE_API_KEY",
    "OLLAMA_API_KEY",
)
_BACKEND_ENV = ("METIS_LLM_BACKEND", "MIRO_LLM_BACKEND")
_BASE_URL_ENV = (
    "METIS_LLM_BASE_URL",
    "MIRO_LLM_BASE_URL",
    "DEEPSEEK_BASE_URL",
    "DEEPSEEK_API_URL",
    "OPENAI_BASE_URL",
    "DOUBAO_BASE_URL",
    "ARK_BASE_URL",
    "VOLCENGINE_BASE_URL",
    "OLLAMA_BASE_URL",
)
_MODEL_ENV = (
    "METIS_LLM_MODEL",
    "MIRO_LLM_MODEL",
    "DEEPSEEK_CHAT_MODEL",
    "OPENAI_MODEL",
    "ANTHROPIC_MODEL",
    "GEMINI_MODEL",
    "DOUBAO_MODEL",
    "ARK_MODEL",
    "OLLAMA_MODEL",
)


def handle_diagnostic_command(
    args: DoctorCommandArgs | SandboxCommandArgs,
    *,
    stdout: TextIO,
) -> int:
    workspace = _workspace_path(args.workspace)
    if isinstance(args, DoctorCommandArgs):
        payload = doctor_payload(workspace=workspace, deep=args.deep)
        _render(payload, args.output_format, stdout, title="Metis doctor")
        return EXIT_SUCCESS if payload["ok"] else EXIT_ENVIRONMENT

    if args.action == "status":
        payload = sandbox_status_payload(workspace=workspace, deep=args.deep)
        _render(payload, args.output_format, stdout, title="Metis sandbox")
        return EXIT_SUCCESS if payload["ready"] else EXIT_ENVIRONMENT
    if args.action == "repair":
        payload = sandbox_repair_payload(
            workspace=workspace,
            allow_download=args.allow_download,
            force=args.force,
        )
        _render(payload, args.output_format, stdout, title="Metis sandbox repair")
        return EXIT_SUCCESS if payload["ok"] else EXIT_ENVIRONMENT
    raise CliUsageError(f"unsupported sandbox action: {args.action}")


def doctor_payload(*, workspace: Path, deep: bool = False) -> Dict[str, Any]:
    home = _metis_home()
    settings, settings_check = _settings_check(home=home, workspace=workspace)
    credential_check, has_api_key = _credential_check(settings)
    checks = [
        _identity_check(),
        _path_check("metis_home", "Metis home", home, allow_missing=True),
        settings_check,
        _session_database_check(home / "session-state.db"),
        credential_check,
        _model_check(settings, has_api_key=has_api_key),
        _path_check("workspace", "Workspace", workspace, allow_missing=False),
        _sandbox_doctor_check(workspace=workspace, deep=deep),
        _desktop_tools_check(),
        _mcp_check(home),
    ]
    counts = {level: sum(1 for item in checks if item["status"] == level) for level in ("pass", "warn", "fail")}
    return {
        "schema": DOCTOR_SCHEMA,
        "ok": counts["fail"] == 0,
        "generated_at": time.time(),
        "version": __version__,
        "deep": bool(deep),
        "summary": counts,
        "checks": checks,
    }


def sandbox_status_payload(*, workspace: Path, deep: bool = False) -> Dict[str, Any]:
    from backend.runtime.runtime_provision import EXPECTED_SERVICE_PROTOCOL, provision_status

    raw = provision_status(deep=deep)
    payload: Dict[str, Any] = {
        "schema": SANDBOX_STATUS_SCHEMA,
        "ok": True,
        "ready": bool(raw.get("ready")),
        "supported": bool(raw.get("supported")),
        "generated_at": time.time(),
        "workspace": str(workspace),
        "hcs": {
            "available": bool(raw.get("hcs_available")),
            "reason": str(raw.get("hcs_reason") or raw.get("reason") or ""),
            "vm_platform_enabled": bool(raw.get("vm_platform_enabled")),
            "virtualization_ok": raw.get("virtualization_ok"),
            "permission_denied": bool(raw.get("permission_denied")),
        },
        "service": {
            "installed": bool(raw.get("service_installed")),
            "running": bool(raw.get("service_running")),
            "responding": bool(raw.get("service_responding")),
            "pipe_responding": bool(raw.get("service_pipe_responding")),
            "version": str(raw.get("service_version") or ""),
            "expected_version": str(raw.get("service_expected_version") or ""),
            "protocol": str(raw.get("service_protocol") or ""),
            "expected_protocol": EXPECTED_SERVICE_PROTOCOL,
            "upgrade_required": bool(raw.get("service_upgrade_required")),
        },
        "runtime_pack": {
            "installed": bool(raw.get("bundle_installed")),
            "path": str(raw.get("bundle_path") or ""),
        },
        "reboot_required": bool(raw.get("reboot_required")),
        "reboot_pending": raw.get("reboot_pending"),
        "needs": [str(item) for item in raw.get("needs") or []],
        "actions": [dict(item) for item in raw.get("actions") or [] if isinstance(item, Mapping)],
        "message": str(raw.get("ux_summary") or ""),
    }
    if deep:
        payload["runtime_manager"] = _runtime_manager_compact(workspace)
    return payload


def sandbox_repair_payload(
    *,
    workspace: Path,
    allow_download: bool = False,
    force: bool = False,
) -> Dict[str, Any]:
    from backend.runtime.runtime_manager import runtime_manager_repair
    from backend.runtime.runtime_provision import run_provision_elevated

    before = sandbox_status_payload(workspace=workspace, deep=True)
    needs = set(before.get("needs") or [])
    steps: list[Dict[str, Any]] = []

    if "enable_virtualization_bios" in needs:
        return {
            "schema": SANDBOX_REPAIR_SCHEMA,
            "ok": False,
            "ready": False,
            "generated_at": time.time(),
            "before": before,
            "after": before,
            "steps": steps,
            "code": "METIS_VIRTUALIZATION_DISABLED",
            "message": "Enable CPU virtualization in BIOS/UEFI, then run repair again.",
        }

    elevated = [
        item
        for item in ("enable_vm_platform", "install_service", "upgrade_service", "repair_service")
        if item in needs or (force and item == "repair_service" and before["service"]["installed"])
    ]
    if elevated:
        result = run_provision_elevated(elevated)
        steps.append(
            {
                "id": "host_prerequisites",
                "ok": bool(result.get("ok")),
                "actions": elevated,
                "message": _safe_message(result.get("error") or result.get("note") or "Host repair completed."),
            }
        )

    if "install_pack" in needs or force:
        result = runtime_manager_repair(
            root=str(workspace),
            source="auto",
            allow_download=allow_download,
            force=force,
        )
        source = result.get("source") if isinstance(result.get("source"), Mapping) else {}
        steps.append(
            {
                "id": "runtime_pack",
                "ok": bool(result.get("ok")),
                "source": str(source.get("kind") or ("installed" if result.get("already_installed") else "")),
                "code": str(result.get("code") or ""),
                "message": _safe_message(result.get("message") or result.get("error") or "Runtime pack repair completed."),
            }
        )

    if not steps:
        steps.append({"id": "noop", "ok": True, "message": "Sandbox prerequisites are already ready."})

    after = sandbox_status_payload(workspace=workspace, deep=True)
    step_ok = all(bool(item.get("ok")) for item in steps)
    ready = bool(after.get("ready"))
    message = "Sandbox repair completed."
    if after.get("reboot_required") or after.get("reboot_pending"):
        message = "Repair steps completed; restart Windows before checking the sandbox again."
    elif not ready and not allow_download and "install_pack" in set(after.get("needs") or []):
        message = "No local runtime pack is available. Re-run with --allow-download to fetch one."
    elif not ready:
        message = "Repair did not make the sandbox ready; inspect the remaining needs."
    return {
        "schema": SANDBOX_REPAIR_SCHEMA,
        "ok": bool(step_ok and ready),
        "ready": ready,
        "generated_at": time.time(),
        "allow_download": bool(allow_download),
        "force": bool(force),
        "before": before,
        "after": after,
        "steps": steps,
        "message": message,
    }


def _identity_check() -> Dict[str, Any]:
    executable = Path(sys.executable).resolve(strict=False)
    frozen = bool(getattr(sys, "frozen", False))
    pe_image = False
    if sys.platform == "win32" and executable.is_file():
        try:
            with executable.open("rb") as handle:
                pe_image = handle.read(2) == b"MZ"
        except OSError:
            pe_image = False
    return _check(
        "identity",
        "CLI identity",
        "pass",
        f"Metis {__version__} on {platform.system()} {platform.machine() or 'unknown'}.",
        packaged=frozen,
        executable=str(executable),
        pe_image=pe_image,
        python_runtime=platform.python_version(),
    )


def _path_check(check_id: str, name: str, path: Path, *, allow_missing: bool) -> Dict[str, Any]:
    exists = path.exists()
    is_dir = path.is_dir() if exists else False
    probe = path if exists else _nearest_existing_parent(path)
    readable = bool(exists and os.access(path, os.R_OK))
    writable = bool(probe and os.access(probe, os.W_OK))
    ok = (exists and is_dir and readable and writable) or (allow_missing and not exists and writable)
    detail = f"{path} is readable and writable." if exists and ok else f"{path} can be created." if ok else f"{path} is unavailable or not writable."
    return _check(check_id, name, "pass" if ok else "fail", detail, path=str(path), exists=exists, readable=readable, writable=writable)


def _settings_check(*, home: Path, workspace: Path) -> tuple[Dict[str, Any], Dict[str, Any]]:
    paths = (home / "config.json", home / "settings.json", workspace / ".metis" / "settings.json")
    merged: Dict[str, Any] = {}
    sources: Dict[str, str] = {}
    invalid: list[str] = []
    existing: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        existing.append(str(path))
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise ValueError("root must be an object")
        except (OSError, ValueError, json.JSONDecodeError):
            invalid.append(str(path))
            continue
        for key, item in value.items():
            merged[str(key)] = item
            sources[str(key)] = "workspace" if path.parent.name == ".metis" and path.parent.parent == workspace else "user"

    for target, names in (("backend", _BACKEND_ENV), ("base_url", _BASE_URL_ENV), ("model", _MODEL_ENV), ("api_key", _API_KEY_ENV)):
        for name in names:
            if os.environ.get(name):
                merged[target] = os.environ[name]
                sources[target] = "environment"
                break
    merged["_sources"] = sources
    status = "fail" if invalid else "pass"
    detail = f"{len(invalid)} settings file(s) are invalid." if invalid else f"Parsed {len(existing)} settings file(s)."
    return merged, _check("settings", "Settings", status, detail, files=existing, invalid=invalid)


def _credential_check(settings: Mapping[str, Any]) -> tuple[Dict[str, Any], bool]:
    configured = bool(str(settings.get("api_key") or "").strip())
    source = str((settings.get("_sources") or {}).get("api_key") or "")
    available = is_available()
    stored = False
    error = ""
    try:
        stored = bool(read_api_key()) if available else False
    except CredentialStoreError:
        error = "Windows Credential Manager could not be read."
    if not configured and stored:
        configured = True
        source = "credential_manager"
    if error:
        status, detail = "warn", error
    elif configured:
        status, detail = "pass", f"API key is configured via {source or 'settings'}."
    else:
        status, detail = "warn", "No API key is configured; local providers may still work."
    return _check(
        "credentials",
        "Credentials",
        status,
        detail,
        credential_manager_available=available,
        api_key_configured=configured,
        source=source or "none",
    ), configured


def _model_check(settings: Mapping[str, Any], *, has_api_key: bool) -> Dict[str, Any]:
    backend = str(settings.get("backend") or "openai").strip()
    base_url = str(settings.get("base_url") or "").strip()
    model = str(settings.get("model") or "").strip()
    try:
        profile = resolve_provider_for_config(backend, base_url=base_url, model=model)
    except ProviderRegistryError:
        return _check("model", "Model configuration", "fail", "The configured model provider is unknown.", backend=backend)
    resolved_base = base_url or str(profile.base_url or "")
    resolved_model = model or str(profile.default_model or "")
    missing: list[str] = []
    if not resolved_base and str(profile.provider_id) not in {"fake"}:
        missing.append("base_url")
    if not resolved_model:
        missing.append("model")
    if profile.api_key_required and not has_api_key:
        missing.append("api_key")
    embedded_credentials = False
    try:
        parsed = urlsplit(resolved_base)
        embedded_credentials = bool(parsed.username or parsed.password)
    except ValueError:
        missing.append("valid_base_url")
    if embedded_credentials:
        missing.append("base_url_without_embedded_credentials")
    return _check(
        "model",
        "Model configuration",
        "fail" if missing else "pass",
        "Missing or unsafe fields: " + ", ".join(dict.fromkeys(missing)) + "." if missing else "Provider, endpoint, model, and credential requirements are complete.",
        provider=str(profile.provider_id),
        model_configured=bool(resolved_model),
        base_url_configured=bool(resolved_base),
        api_key_required=bool(profile.api_key_required),
        api_key_configured=bool(has_api_key),
        embedded_credentials=embedded_credentials,
    )


def _session_database_check(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return _check("session_database", "Session database", "pass", "No session database yet; it will be created on first use.", path=str(path), exists=False)
    if not path.is_file():
        return _check("session_database", "Session database", "fail", "The session database path is not a file.", path=str(path), exists=True)
    try:
        uri = path.resolve(strict=False).as_uri() + "?mode=ro"
        with sqlite3.connect(uri, uri=True, timeout=5.0) as connection:
            row = connection.execute("PRAGMA quick_check").fetchone()
            integrity = str(row[0] if row else "")
            journal = str(connection.execute("PRAGMA journal_mode").fetchone()[0])
            schema_row = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
            schema_version = int(schema_row[0] or 0) if schema_row else 0
        ok = integrity.lower() == "ok"
        return _check(
            "session_database",
            "Session database",
            "pass" if ok else "fail",
            "SQLite quick_check passed." if ok else "SQLite quick_check reported corruption.",
            path=str(path),
            exists=True,
            integrity=integrity,
            journal_mode=journal,
            schema_version=schema_version,
            wal_present=path.with_name(path.name + "-wal").exists(),
        )
    except (OSError, sqlite3.Error, ValueError):
        return _check("session_database", "Session database", "fail", "The session database could not be opened read-only.", path=str(path), exists=True)


def _sandbox_doctor_check(*, workspace: Path, deep: bool) -> Dict[str, Any]:
    try:
        status = sandbox_status_payload(workspace=workspace, deep=deep)
    except Exception as exc:
        return _check("sandbox", "Sandbox", "warn", f"Sandbox probe failed ({type(exc).__name__}).")
    if status["ready"]:
        level, detail = "pass", "HCS, privileged service, protocol, and runtime pack are ready."
    elif not status["supported"]:
        level, detail = "warn", "The HCS sandbox is not supported on this platform."
    else:
        level, detail = "warn", status.get("message") or "Sandbox setup is incomplete."
    return _check(
        "sandbox",
        "Sandbox",
        level,
        str(detail),
        ready=bool(status["ready"]),
        supported=bool(status["supported"]),
        needs=list(status.get("needs") or []),
        service=status.get("service"),
    )


def _desktop_tools_check() -> Dict[str, Any]:
    disabled = _env_truthy("METIS_DISABLE_DESKTOP_TOOLS") or _env_truthy("MIRO_DISABLE_DESKTOP_TOOLS")
    bundled = importlib.util.find_spec("backend.tools.desk_automation") is not None
    supported = sys.platform == "win32"
    available = supported and bundled and not disabled
    level = "pass" if available else "warn"
    detail = "Desktop-control modules are available." if available else "Desktop-control tools are disabled or unavailable on this platform."
    return _check("desktop_tools", "Desktop tools", level, detail, available=available, disabled=disabled, supported=supported)


def _mcp_check(home: Path) -> Dict[str, Any]:
    disabled = _env_truthy("METIS_DISABLE_MCP") or _env_truthy("MIRO_DISABLE_MCP")
    path = Path(os.environ.get("METIS_MCP_CONFIG") or (home / "mcp.json")).expanduser().resolve(strict=False)
    if disabled:
        return _check("mcp", "MCP", "warn", "MCP loading is disabled by environment.", enabled=False, path=str(path), configured_servers=0)
    if not path.is_file():
        return _check("mcp", "MCP", "pass", "MCP is enabled; no server config is installed.", enabled=True, path=str(path), configured_servers=0)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        servers = value.get("mcpServers", value) if isinstance(value, dict) else {}
        if not isinstance(servers, dict):
            raise ValueError("invalid MCP config")
        count = sum(1 for item in servers.values() if isinstance(item, dict) and not item.get("disabled"))
    except (OSError, ValueError, json.JSONDecodeError):
        return _check("mcp", "MCP", "warn", "MCP config exists but is invalid.", enabled=True, path=str(path), configured_servers=0)
    return _check("mcp", "MCP", "pass", f"MCP is enabled with {count} configured server(s).", enabled=True, path=str(path), configured_servers=count)


def _runtime_manager_compact(workspace: Path) -> Dict[str, Any]:
    try:
        from backend.runtime.runtime_manager import runtime_manager_status

        raw = runtime_manager_status(root=str(workspace))
    except Exception as exc:
        return {"ok": False, "error": f"runtime manager probe failed ({type(exc).__name__})"}
    health = raw.get("health") if isinstance(raw.get("health"), Mapping) else {}
    vm_runtime = raw.get("vm_runtime") if isinstance(raw.get("vm_runtime"), Mapping) else {}
    return {
        "ok": bool(raw.get("ok")),
        "ready": bool(health.get("ready")),
        "preferred_backend": str(health.get("preferred_backend") or ""),
        "metis_wsl_ready": bool(health.get("metis_wsl_ready")),
        "wsl_available": bool(health.get("wsl_available")),
        "docker_available": bool(health.get("docker_available")),
        "vm_runtime_installed": bool(health.get("vm_runtime_installed")),
        "vm_assets_verified": bool(health.get("vm_assets_verified")),
        "guest_protocol_ready": bool(vm_runtime.get("guest_protocol_ready")),
        "actions": [str(item.get("id") or item.get("action") or "") for item in raw.get("actions") or [] if isinstance(item, Mapping)],
    }


def _render(payload: Mapping[str, Any], output_format: str, stdout: TextIO, *, title: str) -> None:
    if output_format == "json":
        stdout.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        stdout.flush()
        return
    stdout.write(f"{title} {payload.get('version') or __version__}\n")
    checks = payload.get("checks")
    if isinstance(checks, list):
        for item in checks:
            if not isinstance(item, Mapping):
                continue
            stdout.write(f"[{str(item.get('status') or 'info').upper():4}] {item.get('name')}: {item.get('message')}\n")
        summary = payload.get("summary") if isinstance(payload.get("summary"), Mapping) else {}
        stdout.write(f"Summary: {summary.get('pass', 0)} pass, {summary.get('warn', 0)} warn, {summary.get('fail', 0)} fail.\n")
    else:
        ready = bool(payload.get("ready"))
        stdout.write(f"[{'PASS' if ready else 'WARN'}] {'ready' if ready else 'not ready'}\n")
        if payload.get("message"):
            stdout.write(str(payload["message"]) + "\n")
        for item in payload.get("steps") or []:
            if isinstance(item, Mapping):
                stdout.write(f"[{'PASS' if item.get('ok') else 'FAIL'}] {item.get('id')}: {item.get('message', '')}\n")
        needs = payload.get("needs") or (payload.get("after") or {}).get("needs") or []
        if needs:
            stdout.write("Needs: " + ", ".join(str(item) for item in needs) + "\n")
    stdout.flush()


def _check(check_id: str, name: str, status: str, message: str, **details: Any) -> Dict[str, Any]:
    return {"id": check_id, "name": name, "status": status, "ok": status != "fail", "message": message, "details": details}


def _metis_home() -> Path:
    return Path(os.environ.get("METIS_HOME") or (Path.home() / ".metis")).expanduser().resolve(strict=False)


def _workspace_path(value: str) -> Path:
    path = Path(value or ".").expanduser().resolve(strict=False)
    if not path.exists():
        raise CliUsageError(f"workspace does not exist: {path}")
    if not path.is_dir():
        raise CliUsageError(f"workspace is not a directory: {path}")
    return path


def _nearest_existing_parent(path: Path) -> Path | None:
    current = path
    while not current.exists() and current != current.parent:
        current = current.parent
    return current if current.exists() else None


def _env_truthy(name: str) -> bool:
    return str(os.environ.get(name) or "").strip().lower() in {"1", "true", "yes", "on"}


def _safe_message(value: Any) -> str:
    from backend.runtime.llm_backends._common import sanitize_for_log

    text = " ".join(sanitize_for_log(value).split())
    return text[:500]


__all__ = [
    "DOCTOR_SCHEMA",
    "SANDBOX_REPAIR_SCHEMA",
    "SANDBOX_STATUS_SCHEMA",
    "doctor_payload",
    "handle_diagnostic_command",
    "sandbox_repair_payload",
    "sandbox_status_payload",
]

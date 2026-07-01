"""Connector management surface: list / connect / test / disconnect.

This is the runtime bridge between the connector registry and the live MCP
layer. It activates a connector's MCP server, registers its tools into the
agent's tool registry, verifies connectivity, and tears it down again.

Security boundary (see token_store.py): this module never *persists* tokens.
Writing/clearing connector secrets is the desktop's job (safeStorage in
desktop/electron/oauth.cjs). Here a token is either:
  - already present in the process env (desktop injected it at backend spawn) and
    read via token_store, or
  - passed in for this session only (e.g. forwarded from a fresh desktop
    authorize) and held in memory on the MCPServerConfig — not written to disk.
"""

from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from . import token_store
from .registry import AUTH_CREDENTIALS_FILE, AUTH_ENV_SECRETS, connector_catalog, get_connector
from ..mcp_client import (
    MCPServerConfig,
    MCPSession,
    MCPManager,
    _safe_name,
    get_mcp_manager,
)
from ..tool_registry import ToolDefinition, ToolRegistry, get_registry

ALLOWED_DIR_PLACEHOLDER = "<ALLOWED_DIR>"


def _ensure_global_manager() -> MCPManager:
    """Reuse the process-wide MCP manager (so connector tools live alongside the
    config-file MCP tools), creating one if MCP was never initialized."""
    manager = get_mcp_manager()
    if manager is not None:
        return manager
    import backend.runtime.mcp_client as mcp_client

    manager = MCPManager()
    mcp_client._global_manager = manager
    return manager


def _connector_args(service_id: str, allowed_dir: str = "") -> List[str]:
    connector = get_connector(service_id)
    mcp = (connector.mcp if connector else {}) or {}
    args = list(mcp.get("args") or [])
    if allowed_dir:
        args = [allowed_dir if arg == ALLOWED_DIR_PLACEHOLDER else arg for arg in args]
    return args


def _requires_allowed_dir(service_id: str) -> bool:
    connector = get_connector(service_id)
    mcp = (connector.mcp if connector else {}) or {}
    return any(arg == ALLOWED_DIR_PLACEHOLDER for arg in (mcp.get("args") or []))


def build_config(service_id: str, token: str = "", allowed_dir: str = "") -> MCPServerConfig:
    """Build an MCPServerConfig for a connector from the registry.

    token (optional, in-memory only) overrides the env/token_store value for this
    session. allowed_dir substitutes the <ALLOWED_DIR> placeholder (filesystem).
    """
    connector = get_connector(service_id)
    if connector is None:
        raise ValueError(f"unknown connector: {service_id}")
    mcp = connector.mcp or {}
    # Registry env values are non-secret defaults (for example xurl's loopback
    # redirect URI). User-specific secret values are read from the desktop-
    # injected process env below and override these defaults.
    extra_env: Dict[str, str] = {
        str(key): str(value)
        for key, value in ((mcp.get("env") or {}) if isinstance(mcp.get("env"), dict) else {}).items()
        if str(key).strip() and str(value).strip()
    }
    # credentials_file connectors get their key-file PATH env vars forwarded to
    # the spawned server (the paths are user-specific, read from the env).
    if connector.auth_kind == AUTH_CREDENTIALS_FILE:
        for name in connector.credentials_envs or []:
            value = os.environ.get(name, "").strip()
            if value:
                extra_env[name] = value
    if connector.auth_kind == AUTH_ENV_SECRETS:
        for name in [*(connector.secret_envs or []), *(connector.optional_secret_envs or [])]:
            value = os.environ.get(name, "").strip()
            if value:
                extra_env[name] = value
    return MCPServerConfig(
        name=service_id,
        command=mcp.get("command"),
        args=_connector_args(service_id, allowed_dir),
        env=extra_env,
        url=mcp.get("url"),
        token_env=connector.token_env,
        service_id=service_id,
        auth_token=token or "",
    )


def list_connectors() -> List[Dict[str, Any]]:
    """Catalog + live state: has_token (credential present) and active (MCP session up)."""
    manager = get_mcp_manager()
    status = manager.get_status() if manager else {}
    out: List[Dict[str, Any]] = []
    for item in connector_catalog():
        service_id = item["service_id"]
        server_status = status.get(service_id, {})
        out.append(
            {
                **item,
                "has_token": token_store.is_connected(service_id),
                "active": bool(server_status.get("connected")),
                "tools_count": int(server_status.get("tools_count", 0) or 0),
                "tools": list(server_status.get("tools") or []),
                "last_error": str(server_status.get("last_error", "") or ""),
            }
        )
    return out


def connect(
    service_id: str,
    token: str = "",
    allowed_dir: str = "",
    registry: Optional[ToolRegistry] = None,
) -> Dict[str, Any]:
    """Activate a connector's MCP server and register its tools.

    Idempotent: reconnecting replaces the previous session and its tools.
    Returns {"ok": True, "service_id", "tools": [...]} or {"ok": False, "error"}.
    """
    connector = get_connector(service_id)
    if connector is None:
        return {"ok": False, "error": f"unknown connector: {service_id}"}

    if connector.token_env:
        effective_token = token or (token_store.get_token(service_id) or "")
        if not effective_token:
            return {
                "ok": False,
                "error": (
                    f"{service_id} has no token — authorize it in the desktop app "
                    "(Settings → Connectors) first, then restart the backend."
                ),
            }
    if connector.auth_kind == AUTH_CREDENTIALS_FILE and not token_store.credentials_ready(service_id):
        envs = ", ".join(connector.credentials_envs or [])
        return {
            "ok": False,
            "error": (
                f"{service_id} needs its OAuth keys file(s) and a one-time auth: set "
                f"{envs} to existing file paths and run the server's one-time auth "
                "(e.g. `npx -y <server> auth`) in a browser. See the connector notes."
            ),
        }
    if connector.auth_kind == AUTH_ENV_SECRETS and not token_store.env_secrets_ready(service_id):
        envs = ", ".join(connector.secret_envs or [])
        return {
            "ok": False,
            "error": (
                f"{service_id} needs encrypted connector config first: provide {envs} "
                "in Settings → Connectors, then restart the backend so Electron can inject them."
            ),
        }
    if _requires_allowed_dir(service_id) and not allowed_dir:
        return {"ok": False, "error": f"{service_id} requires an allowed_dir"}

    registry = registry or get_registry()
    manager = _ensure_global_manager()

    # Idempotency: drop any prior session + its registered tools first.
    _teardown_session(manager, registry, service_id)

    config = build_config(service_id, token=token, allowed_dir=allowed_dir)
    session = MCPSession(config)
    try:
        tools = session.list_tools()  # connects (initialize handshake) + lists
    except Exception as exc:  # noqa: BLE001 — surface any startup failure as error
        try:
            session.disconnect()
        except Exception:
            pass
        return {"ok": False, "error": f"connect failed: {type(exc).__name__}: {exc}"}

    manager.sessions[service_id] = session
    if not any(cfg.name == service_id for cfg in manager._all_configs):
        manager._all_configs.append(config)

    tool_names = _register_session_tools(manager, registry, service_id, tools)
    manager.start_health_monitor()
    return {"ok": True, "service_id": service_id, "tools": tool_names}


def test(service_id: str) -> Dict[str, Any]:
    """Verify a connector's live session with a lightweight tools/list call."""
    manager = get_mcp_manager()
    session = manager.sessions.get(service_id) if manager else None
    if session is None:
        return {"ok": False, "error": f"{service_id} is not connected"}
    try:
        tools = session.list_tools()
        return {"ok": True, "service_id": service_id, "tools_count": len(tools)}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}


def disconnect(service_id: str, registry: Optional[ToolRegistry] = None) -> Dict[str, Any]:
    """Tear down a connector's MCP session and unregister its tools."""
    manager = get_mcp_manager()
    if manager is None:
        return {"ok": False, "error": "no active MCP manager"}
    registry = registry or get_registry()
    existed = service_id in manager.sessions
    removed = _teardown_session(manager, registry, service_id)
    if not existed:
        return {"ok": False, "error": f"{service_id} was not connected"}
    return {"ok": True, "service_id": service_id, "tools_removed": removed}


def _teardown_session(manager: MCPManager, registry: ToolRegistry, service_id: str) -> int:
    session = manager.sessions.pop(service_id, None)
    if session is not None:
        try:
            session.disconnect()
        except Exception:
            pass
    manager._all_configs = [cfg for cfg in manager._all_configs if cfg.name != service_id]
    return registry.remove_tools_by_source(f"mcp:{service_id}")


def _register_session_tools(
    manager: MCPManager, registry: ToolRegistry, service_id: str, tools: List[Any]
) -> List[str]:
    """Register a connector's MCP tools into the registry (mirrors register_mcp_tools)."""
    names: List[str] = []
    for tool in tools:
        qualified_name = f"mcp_{_safe_name(service_id)}_{_safe_name(tool.name)}"

        def make_executor(srv_name: str, tool_name: str) -> Any:
            def executor(**kwargs: Any) -> str:
                return manager.call_tool(srv_name, tool_name, kwargs)

            return executor

        registry.register(
            ToolDefinition(
                name=qualified_name,
                description=f"[MCP:{service_id}] {tool.description}",
                parameters=tool.input_schema,
                execute_fn=make_executor(service_id, tool.name),
                source=f"mcp:{service_id}",
            )
        )
        registry.register_alias(tool.name, qualified_name)
        names.append(qualified_name)
    return names

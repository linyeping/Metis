from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.core.paths import metis_path

from .tool_registry import ToolDefinition, ToolRegistry


_global_manager: Optional["MCPManager"] = None
MAX_MCP_TOOLS = 500


@dataclass
class MCPServerConfig:
    name: str
    command: Optional[str] = None
    args: List[str] = field(default_factory=list)
    env: Dict[str, str] = field(default_factory=dict)
    url: Optional[str] = None
    auth_token: str = ""
    token_env: str = ""
    # Optional connector service id (e.g. "github"). When set and auth_token is
    # empty, the token is resolved from the connector token_store (the desktop
    # injects the decrypted token into our env at spawn). Lets a config point at
    # a connector without baking the secret into the config file.
    service_id: str = ""


@dataclass
class MCPTool:
    name: str
    description: str
    input_schema: Dict[str, Any]
    server_name: str


class MCPSession:
    """A connection to a single MCP server."""

    def __init__(self, config: MCPServerConfig) -> None:
        self.config = config
        self.tools: List[MCPTool] = []
        self.resources: List[Dict[str, Any]] = []
        self._process: Optional[subprocess.Popen[str]] = None
        self._connected = False
        self._transport = "stdio" if config.command else "sse"
        self._server_capabilities: Dict[str, Any] = {}
        self._last_error = ""
        self._last_connected_at = 0.0
        self._last_checked_at = 0.0
        self._request_id = 0
        self._rpc_lock = threading.Lock()
        self._stdout_queue: "queue.Queue[str]" = queue.Queue()
        self._stderr_tail: List[str] = []
        self._http_rpc_url = ""

    def connect(self) -> None:
        if self.config.command:
            self._connect_stdio()
        elif self.config.url:
            self._connect_sse()
        else:
            raise ValueError(f"MCP server '{self.config.name}' has no command or url")

    def _connect_stdio(self) -> None:
        command = self.config.command
        if not command:
            raise ValueError(f"MCP server '{self.config.name}' has no command")
        env = _stdio_env_for_config(self.config)
        # Resolve the command via PATH so Windows finds launcher shims like
        # npx.cmd/node.cmd (Popen without shell=True won't append .cmd itself).
        # Falls back to the bare name on POSIX or when not found.
        resolved = shutil.which(command, path=env.get("PATH")) or command
        cmd = [resolved] + list(self.config.args)

        self._process = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )
        try:
            self._start_reader_threads()
            init_data = self._send_jsonrpc(
                "initialize",
                {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                    "clientInfo": {"name": "metis", "version": "2.0.0"},
                },
            )
            result = init_data.get("result") if isinstance(init_data, dict) else {}
            self._server_capabilities = dict((result or {}).get("capabilities") or {})
            self._send_notification("notifications/initialized", {})
            self._connected = True
            self._transport = "stdio"
            self._last_connected_at = time.time()
            self._last_error = ""
        except Exception:
            self.disconnect()
            raise

    def _connect_sse(self) -> None:
        try:
            url = self._sse_base_url()
            initialize_payload = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}, "resources": {}, "prompts": {}},
                    "clientInfo": {"name": "metis", "version": "3.0.0"},
                },
            }
            initialized_payload = {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
                "params": {},
            }
            try:
                # Streamable HTTP MCP servers (including X Docs) accept JSON-RPC
                # directly at the configured URL and may wrap responses in
                # text/event-stream `data:` frames.
                self._http_rpc_url = self._sse_endpoint("")
                init_data = self._post_jsonrpc_http(self._http_rpc_url, initialize_payload, timeout=30)
                try:
                    self._post_jsonrpc_http(
                        self._http_rpc_url,
                        initialized_payload,
                        timeout=10,
                        expect_response=False,
                    )
                except Exception:
                    pass
            except Exception:
                # Backward compatibility for older pseudo-SSE test servers that
                # exposed /initialize and /initialized endpoints.
                self._http_rpc_url = self._sse_endpoint("")
                init_data = self._post_jsonrpc_http(self._sse_endpoint("initialize"), initialize_payload, timeout=30)
                try:
                    self._post_jsonrpc_http(
                        self._sse_endpoint("initialized"),
                        initialized_payload,
                        timeout=10,
                        expect_response=False,
                    )
                except Exception:
                    pass
            if "error" in init_data:
                raise RuntimeError(f"MCP initialize error: {init_data['error']}")
            result = init_data.get("result") if isinstance(init_data, dict) else {}
            self._server_capabilities = dict((result or {}).get("capabilities") or {})
            self._connected = True
            self._transport = "sse"
            self._last_connected_at = time.time()
            self._last_error = ""
            print(f"MCP: SSE connected to '{self.config.name}' at {url}")
        except Exception as exc:
            self._last_error = str(exc)
            self._connected = False
            raise

    def list_tools(self) -> List[MCPTool]:
        if not self._connected:
            self.connect()

        response = self._send_rpc("tools/list", {})
        tools_data = (response.get("result") or {}).get("tools") or []
        if len(tools_data) > MAX_MCP_TOOLS:
            raise ValueError(
                f"MCP server returned {len(tools_data)} tools, limit is {MAX_MCP_TOOLS}"
            )
        normalized_tools: List[Dict[str, Any]] = []
        for tool in tools_data:
            if not isinstance(tool, dict):
                continue
            schema = tool.get("inputSchema")
            if schema is not None and not isinstance(schema, dict):
                tool = {**tool, "inputSchema": {"type": "object", "properties": {}}}
            normalized_tools.append(tool)
        self.tools = [
            MCPTool(
                name=str(tool.get("name", "")),
                description=str(tool.get("description", "")),
                input_schema=tool.get("inputSchema")
                or {"type": "object", "properties": {}, "required": []},
                server_name=self.config.name,
            )
            for tool in normalized_tools
            if tool.get("name")
        ]
        return self.tools

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> str:
        if not self._connected:
            self.connect()
        response = self._send_rpc(
            "tools/call",
            {"name": name, "arguments": arguments},
        )
        result = response.get("result") or {}
        content = result.get("content") or []

        rendered: List[str] = []
        for block in content:
            if not isinstance(block, dict):
                rendered.append(json.dumps(block, ensure_ascii=False))
                continue
            block_type = block.get("type")
            if block_type == "text":
                rendered.append(str(block.get("text", "")))
            elif block_type == "image":
                rendered.append(f"[Image: {block.get('mimeType', 'image/png')}]")
            elif block_type == "resource":
                rendered.append(f"[Resource: {block.get('resource', '')}]")
            else:
                rendered.append(json.dumps(block, ensure_ascii=False))

        if rendered:
            prefix = "MCP tool returned an error:\n" if result.get("isError") else ""
            return prefix + "\n".join(rendered)
        return json.dumps(result, ensure_ascii=False)

    def list_resources(self) -> List[Dict[str, Any]]:
        if not self._connected:
            self.connect()
        if self._server_capabilities and "resources" not in self._server_capabilities:
            self.resources = []
            return []
        response = self._send_rpc("resources/list", {})
        resources = (response.get("result") or {}).get("resources") or []
        self.resources = [item for item in resources if isinstance(item, dict)]
        return self.resources

    def read_resource(self, uri: str) -> str:
        if not self._connected:
            self.connect()
        response = self._send_rpc("resources/read", {"uri": uri})
        contents = (response.get("result") or {}).get("contents") or []
        rendered: List[str] = []
        for item in contents:
            if not isinstance(item, dict):
                continue
            if item.get("text") is not None:
                rendered.append(str(item.get("text") or ""))
            elif item.get("blob") is not None:
                rendered.append(f"[Blob resource: {item.get('mimeType', 'application/octet-stream')}]")
        return "\n".join(rendered)

    def is_healthy(self) -> bool:
        self._last_checked_at = time.time()
        if not self._connected:
            return False
        if self._transport == "stdio":
            return bool(self._process and self._process.poll() is None)
        try:
            self._send_rpc("tools/list", {})
            self._last_error = ""
            return True
        except Exception as exc:
            self._last_error = str(exc)
            self._connected = False
            return False

    def disconnect(self) -> None:
        process = self._process
        if process:
            try:
                if process.stdin:
                    process.stdin.close()
                process.terminate()
                process.wait(timeout=5)
            except Exception:
                process.kill()
            self._process = None
        self._connected = False

    def _send_rpc(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if self._transport == "sse":
            return self._send_jsonrpc_sse(method, params)
        return self._send_jsonrpc(method, params)

    def _send_jsonrpc(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        process = self._process
        if not process or not process.stdin:
            raise RuntimeError("Not connected (no subprocess)")

        with self._rpc_lock:
            self._request_id += 1
            request_id = self._request_id
            request = {
                "jsonrpc": "2.0",
                "id": request_id,
                "method": method,
                "params": params,
            }
            process.stdin.write(json.dumps(request, ensure_ascii=False) + "\n")
            process.stdin.flush()

            deadline = time.time() + 30
            while time.time() < deadline:
                if process.poll() is not None:
                    stderr = "".join(self._stderr_tail[-20:]).strip()
                    detail = f": {stderr}" if stderr else ""
                    raise RuntimeError(
                        f"MCP server '{self.config.name}' exited with code "
                        f"{process.returncode}{detail}"
                    )
                remaining = max(0.1, deadline - time.time())
                try:
                    line = self._stdout_queue.get(timeout=min(remaining, 1.0))
                except queue.Empty:
                    continue
                try:
                    message = json.loads(line.strip())
                except json.JSONDecodeError:
                    continue
                if message.get("id") != request_id:
                    continue
                if "error" in message:
                    error = message["error"]
                    raise RuntimeError(f"MCP error: {error.get('message', error)}")
                return message

        raise TimeoutError(f"MCP server '{self.config.name}' did not respond within 30s")

    def _send_jsonrpc_sse(self, method: str, params: Dict[str, Any]) -> Dict[str, Any]:
        if not self.config.url:
            raise RuntimeError("SSE MCP server has no URL")

        with self._rpc_lock:
            request = {
                "jsonrpc": "2.0",
                "id": self._next_id(),
                "method": method,
                "params": params,
            }
        message = self._post_jsonrpc_http(self._http_rpc_url or self._sse_endpoint(""), request, timeout=60)
        if "error" in message:
            error = message["error"]
            if isinstance(error, dict):
                raise RuntimeError(f"MCP error: {error.get('message', error)}")
            raise RuntimeError(f"MCP error: {error}")
        return message

    def _post_jsonrpc_http(
        self,
        url: str,
        payload: Dict[str, Any],
        timeout: int = 60,
        expect_response: bool = True,
    ) -> Dict[str, Any]:
        import requests

        response = requests.post(
            url,
            json=payload,
            headers=self._http_headers(),
            timeout=timeout,
        )
        response.raise_for_status()
        if not expect_response or not response.text.strip():
            return {}
        return _decode_jsonrpc_http_response(response)

    def _http_headers(self) -> Dict[str, str]:
        headers = {
            "Accept": "application/json, text/event-stream",
            "Content-Type": "application/json",
        }
        token = _resolve_auth_token(self.config)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _next_id(self) -> int:
        self._request_id += 1
        return self._request_id

    def _sse_base_url(self) -> str:
        return str(self.config.url or "").strip().rstrip("/")

    def _sse_endpoint(self, suffix: str) -> str:
        base = self._sse_base_url()
        if not base:
            raise ValueError("SSE transport requires a URL")
        suffix = suffix.strip("/")
        if not suffix:
            return base
        if base.endswith(f"/{suffix}"):
            return base
        return f"{base}/{suffix}"

    def _send_notification(self, method: str, params: Dict[str, Any]) -> None:
        process = self._process
        if not process or not process.stdin:
            return
        notification = {"jsonrpc": "2.0", "method": method, "params": params}
        process.stdin.write(json.dumps(notification, ensure_ascii=False) + "\n")
        process.stdin.flush()

    def _start_reader_threads(self) -> None:
        process = self._process
        if not process:
            return

        def read_stdout() -> None:
            if not process.stdout:
                return
            for line in process.stdout:
                self._stdout_queue.put(line)

        def read_stderr() -> None:
            if not process.stderr:
                return
            for line in process.stderr:
                self._stderr_tail.append(line)
                del self._stderr_tail[:-50]

        threading.Thread(target=read_stdout, daemon=True, name=f"mcp-{self.config.name}-out").start()
        threading.Thread(target=read_stderr, daemon=True, name=f"mcp-{self.config.name}-err").start()


class MCPManager:
    """Manages multiple MCP server connections."""

    def __init__(self) -> None:
        self.sessions: Dict[str, MCPSession] = {}
        self._all_configs: List[MCPServerConfig] = []
        self._health_thread: Optional[threading.Thread] = None
        self._stop_health = threading.Event()

    def load_config(self, config_path: str = "") -> List[MCPServerConfig]:
        paths = [Path(config_path).expanduser()] if config_path else _default_config_paths()
        for path in paths:
            if path.exists():
                return self._load_config_file(path)
        return []

    def connect_all(self, configs: List[MCPServerConfig]) -> Dict[str, List[MCPTool]]:
        self._all_configs = list(configs)
        all_tools: Dict[str, List[MCPTool]] = {}
        for config in configs:
            session = MCPSession(config)
            self.sessions[config.name] = session
            try:
                tools = session.list_tools()
                all_tools[config.name] = tools
                print(f"MCP: Connected to '{config.name}' ({len(tools)} tools)")
            except Exception as exc:
                print(f"MCP: Failed to connect to '{config.name}': {exc}")
        self.start_health_monitor()
        return all_tools

    def disconnect_all(self) -> None:
        self.stop_health_monitor()
        for session in self.sessions.values():
            session.disconnect()
        self.sessions.clear()

    def start_health_monitor(self, interval: int = 60) -> None:
        if self._health_thread and self._health_thread.is_alive():
            return
        self._stop_health.clear()

        def monitor() -> None:
            while not self._stop_health.wait(max(5, interval)):
                for name, session in list(self.sessions.items()):
                    if session.is_healthy():
                        continue
                    print(f"MCP: Server '{name}' unhealthy, reconnecting...")
                    try:
                        session.disconnect()
                        session.connect()
                        session.list_tools()
                        print(f"MCP: Server '{name}' reconnected")
                    except Exception as exc:
                        session._last_error = str(exc)
                        print(f"MCP: Reconnect failed for '{name}': {exc}")

        self._health_thread = threading.Thread(target=monitor, daemon=True, name="mcp-health-monitor")
        self._health_thread.start()

    def stop_health_monitor(self) -> None:
        self._stop_health.set()
        if self._health_thread and self._health_thread.is_alive():
            self._health_thread.join(timeout=2)
        self._health_thread = None

    def call_tool(self, server_name: str, tool_name: str, arguments: Dict[str, Any]) -> str:
        session = self.sessions.get(server_name)
        if not session:
            return f"Error: MCP server '{server_name}' not connected"
        try:
            return session.call_tool(tool_name, arguments)
        except Exception as exc:
            return f"Error calling MCP tool '{server_name}.{tool_name}': {exc}"

    def get_status(self) -> Dict[str, Any]:
        """Return connection status for all configured or connected servers."""
        result: Dict[str, Any] = {}
        config_names = {config.name for config in self._all_configs}
        names = sorted(config_names | set(self.sessions))
        for name in names:
            session = self.sessions.get(name)
            config = session.config if session else next(
                (item for item in self._all_configs if item.name == name),
                MCPServerConfig(name=name),
            )
            tools = session.tools if session else []
            result[name] = {
                "connected": bool(session and session._connected),
                "healthy": bool(session and session.is_healthy()),
                "transport": getattr(session, "_transport", "stdio") if session else ("sse" if config.url else "stdio"),
                "tools_count": len(tools),
                "tools": [
                    {"name": tool.name, "description": tool.description}
                    for tool in tools
                ],
                "resources_count": len(session.resources) if session else 0,
                "resources": list(session.resources[:50]) if session else [],
                "last_error": getattr(session, "_last_error", "") if session else "",
                "last_connected_at": getattr(session, "_last_connected_at", 0.0) if session else 0.0,
                "last_checked_at": getattr(session, "_last_checked_at", 0.0) if session else 0.0,
                "config": {
                    "command": config.command,
                    "args": config.args,
                    "url": config.url,
                },
            }
        return result

    def reconnect(self, server_name: str) -> Dict[str, Any]:
        """Disconnect and reconnect a single MCP server."""
        session = self.sessions.get(server_name)
        if not session:
            return {"error": f"Unknown server: {server_name}"}
        try:
            session.disconnect()
            session.connect()
            tools = session.list_tools()
            return {"success": True, "tools_count": len(tools)}
        except Exception as exc:
            return {"error": str(exc)}

    def disconnect_one(self, server_name: str) -> Dict[str, Any]:
        """Disconnect a single MCP server."""
        session = self.sessions.get(server_name)
        if not session:
            return {"error": f"Unknown server: {server_name}"}
        session.disconnect()
        return {"success": True}

    def list_resources(self, server_name: str = "") -> Dict[str, Any]:
        targets = (
            [(server_name, self.sessions[server_name])]
            if server_name and server_name in self.sessions
            else list(self.sessions.items())
        )
        resources: Dict[str, Any] = {}
        for name, session in targets:
            try:
                resources[name] = session.list_resources()
            except Exception as exc:
                resources[name] = {"error": str(exc)}
        return resources

    def read_resource(self, server_name: str, uri: str) -> Dict[str, Any]:
        session = self.sessions.get(server_name)
        if not session:
            return {"error": f"Unknown server: {server_name}"}
        try:
            return {"ok": True, "content": session.read_resource(uri)}
        except Exception as exc:
            return {"ok": False, "error": str(exc)}

    def get_config_sources(self, config_path: str = "") -> List[Dict[str, Any]]:
        """Return possible MCP config files and whether each exists."""
        paths = [Path(config_path).expanduser()] if config_path else _default_config_paths()
        return [
            {
                "path": str(path),
                "exists": path.exists(),
                "label": _config_source_label(path),
            }
            for path in paths
        ]

    def _load_config_file(self, path: Path) -> List[MCPServerConfig]:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"MCP: Failed to read config {path}: {exc}")
            return []

        servers = data.get("mcpServers", data if isinstance(data, dict) else {})
        configs: List[MCPServerConfig] = []
        for name, raw_config in servers.items():
            if not isinstance(raw_config, dict) or raw_config.get("disabled"):
                continue
            configs.append(
                MCPServerConfig(
                    name=str(name),
                    command=raw_config.get("command"),
                    args=list(raw_config.get("args") or []),
                    env={str(k): str(v) for k, v in (raw_config.get("env") or {}).items()},
                    url=raw_config.get("url"),
                    auth_token=str(raw_config.get("auth_token") or raw_config.get("authToken") or ""),
                    token_env=str(raw_config.get("token_env") or raw_config.get("tokenEnv") or ""),
                    service_id=str(raw_config.get("service_id") or raw_config.get("serviceId") or ""),
                )
            )
        return configs


def register_mcp_tools(registry: ToolRegistry, config_path: str = "") -> int:
    """Load MCP config, connect to servers, and register discovered tools."""
    global _global_manager
    manager = MCPManager()
    _global_manager = manager
    import_external_mcp_servers()  # 首次启动把其他工具的 MCP server 吸收进 Metis 自己的配置
    configs = manager.load_config(config_path)
    if not configs:
        return 0

    all_tools = manager.connect_all(configs)
    count = 0
    for server_name, tools in all_tools.items():
        for tool in tools:
            qualified_name = f"mcp_{_safe_name(server_name)}_{_safe_name(tool.name)}"

            def make_executor(srv_name: str, tool_name: str) -> Any:
                def executor(**kwargs: Any) -> str:
                    return manager.call_tool(srv_name, tool_name, kwargs)

                return executor

            registry.register(
                ToolDefinition(
                    name=qualified_name,
                    description=f"[MCP:{server_name}] {tool.description}",
                    parameters=tool.input_schema,
                    execute_fn=make_executor(server_name, tool.name),
                    source=f"mcp:{server_name}",
                )
            )
            registry.register_alias(tool.name, qualified_name)
            count += 1
    return count


def get_mcp_manager() -> Optional[MCPManager]:
    return _global_manager


def _decode_jsonrpc_http_response(response: Any) -> Dict[str, Any]:
    content_type = str(response.headers.get("content-type", "")).lower()
    text = str(response.text or "")
    if "text/event-stream" not in content_type and not text.lstrip().startswith(("event:", "data:")):
        return response.json()

    messages: List[str] = []
    current: List[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip("\r")
        if line.startswith("data:"):
            data = line[5:].strip()
            if data and data != "[DONE]":
                current.append(data)
            continue
        if not line.strip() and current:
            messages.append("\n".join(current))
            current = []
    if current:
        messages.append("\n".join(current))

    for raw_message in messages:
        try:
            parsed = json.loads(raw_message)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("MCP HTTP response did not contain a JSON-RPC message")


def _stdio_env_for_config(config: MCPServerConfig) -> Dict[str, str]:
    env = {**os.environ, **config.env}
    token_env = str(config.token_env or "").strip()
    # Resolution order: connector token_store (desktop-injected token, keyed by
    # service_id) -> explicit auth_token from the config file. The token always
    # travels via env (never command args), so it can't leak into args/status.
    auth_token = _resolve_auth_token(config)
    if token_env and auth_token:
        env[token_env] = auth_token
    return env


def _resolve_auth_token(config: MCPServerConfig) -> str:
    service_id = str(config.service_id or "").strip()
    if service_id:
        try:
            from .connectors.token_store import get_token

            token = get_token(service_id)
            if token:
                return token
        except Exception:
            # token_store is best-effort; fall back to any explicit auth_token.
            pass
    return str(config.auth_token or "")


def _cleanup_mcp_on_exit() -> None:
    """FABLEADV-18: atexit hook — disconnect all MCP servers (stdio subprocesses
    + health monitor threads) so the backend never leaves orphaned MCP processes."""
    manager = _global_manager
    if manager is None:
        return
    try:
        manager.disconnect_all()
    except Exception:
        pass


import atexit as _atexit

_atexit.register(_cleanup_mcp_on_exit)


def _default_config_paths() -> List[Path]:
    # 运行时只读 Metis 自己的 MCP 配置——不实时依赖 Cursor / Claude Desktop / 旧版等其他产品的文件。
    paths: List[Path] = []
    env_path = os.environ.get("METIS_MCP_CONFIG")
    if env_path:
        paths.append(Path(env_path).expanduser())
    paths.append(metis_path("mcp.json"))
    return paths


def _external_mcp_candidates() -> List[Path]:
    # 仅用于「一次性导入」：其他工具里用户配置过的 MCP server。
    home = Path.home()
    out = [home / ".cursor" / "mcp.json", home / ".miro" / "mcp.json"]
    appdata = os.environ.get("APPDATA")
    if appdata:
        out.append(Path(appdata) / "Claude" / "claude_desktop_config.json")
    return out


def import_external_mcp_servers() -> int:
    """一次性把其他工具(Cursor/Claude Desktop/旧版)里配置的 MCP server 吸收进 Metis 自己的 mcp.json。
    吸收后 Metis 拥有它们，运行时不再依赖外部文件；同名 server 不覆盖；只跑一次(marker)。"""
    try:
        own_path = metis_path("mcp.json")
        marker = metis_path(".mcp-imported")
        if marker.exists():
            return 0
        own: Dict[str, Any] = {}
        if own_path.exists():
            try:
                loaded = json.loads(own_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    own = loaded
            except (OSError, json.JSONDecodeError):
                own = {}
        servers = own.get("mcpServers")
        if not isinstance(servers, dict):
            servers = {}
        added = 0
        for path in _external_mcp_candidates():
            if not path.exists():
                continue
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            ext = data.get("mcpServers") if isinstance(data, dict) else None
            if not isinstance(ext, dict):
                continue
            for name, cfg in ext.items():
                if name not in servers and isinstance(cfg, dict):
                    servers[name] = cfg
                    added += 1
        own["mcpServers"] = servers
        own_path.parent.mkdir(parents=True, exist_ok=True)
        own_path.write_text(json.dumps(own, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            marker.write_text(json.dumps({"added": added}), encoding="utf-8")
        except OSError:
            pass
        if added:
            print(f"MCP: imported {added} external server(s) into Metis config")
        return added
    except Exception:
        return 0


def _config_source_label(path: Path) -> str:
    if ".metis" in str(path).lower():
        return "Metis"
    return "Custom"


def _safe_name(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", value.strip())
    return safe.strip("_") or "tool"

from __future__ import annotations

import ctypes
import http.client
import json
import os
import socket
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Generator, Mapping, TextIO
from urllib.parse import urlencode

from backend.bridges.event_contract import EVENT_SCHEMA
from backend.core.cli_attach_path import cli_attach_discovery_path

from .args import CliUsageError, ParsedCliArgs
from .headless import HeadlessRenderer, HeadlessResult, drive_headless

ATTACH_PROTOCOL = "metis.cli_attach.v1"
DISCOVERY_SCHEMA = "metis.cli_attach.discovery.v1"
HELLO_SCHEMA = "metis.cli_attach.hello.v1"


class CliAttachError(RuntimeError):
    pass


@dataclass(frozen=True)
class AttachEndpoint:
    host: str
    port: int
    pid: int
    instance_id: str
    token: str = field(repr=False)
    path: Path = field(repr=False)


class AttachClient:
    def __init__(self, endpoint: AttachEndpoint, *, timeout: float = 30.0) -> None:
        self.endpoint = endpoint
        self.timeout = max(2.0, float(timeout))

    def handshake(self) -> Dict[str, Any]:
        payload = self._json_request("GET", "/api/cli/v1/hello")
        if (
            payload.get("schema") != HELLO_SCHEMA
            or payload.get("protocol") != ATTACH_PROTOCOL
            or payload.get("event_schema") != EVENT_SCHEMA
            or str(payload.get("instance_id") or "") != self.endpoint.instance_id
            or int(payload.get("pid") or 0) != self.endpoint.pid
        ):
            raise CliAttachError("desktop attach handshake is incompatible or stale")
        return payload

    def prepare_session(
        self,
        *,
        workspace: Path | None,
        resume_id: str = "",
        continue_session: bool = False,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "mode": "code",
            "title": "CLI session",
        }
        if workspace is not None:
            body["workspace"] = str(workspace)
        if resume_id:
            body["session_id"] = resume_id
        if continue_session:
            body["continue_session"] = True
        payload = self._json_request("POST", "/api/cli/v1/sessions", body)
        if payload.get("schema") != "metis.cli_attach.session.v1" or not payload.get("session_id"):
            raise CliAttachError("desktop did not return a valid attached session")
        return payload

    def create_run(self, *, prompt: str, session: Mapping[str, Any]) -> Dict[str, Any]:
        created = bool(session.get("created"))
        body: Dict[str, Any] = {
            "message": prompt,
            "session_id": str(session.get("session_id") or ""),
            "assistant_id": f"cli-attached-{int(time.time() * 1000)}",
        }
        if created:
            body["surface_mode"] = "code"
            body["execution_profile"] = "local_direct"
        payload = self._json_request("POST", "/api/cli/v1/runs", body)
        run_id = str(payload.get("run_id") or payload.get("id") or "")
        if not payload.get("ok") or not run_id:
            raise CliAttachError("desktop did not create an attached run")
        return {**payload, "run_id": run_id}

    def events(self, run_id: str) -> Generator[Any, Any, None]:
        last_seq = 0
        complete = False
        reconnects = 0
        try:
            while not complete:
                try:
                    for item in self._event_stream_once(run_id, after_seq=last_seq):
                        if item is None:
                            complete = True
                            break
                        seq = int(item.get("seq") or 0)
                        last_seq = max(last_seq, seq)
                        reconnects = 0
                        yield _event_object(item)
                except (OSError, http.client.HTTPException, socket.timeout) as exc:
                    reconnects += 1
                    if reconnects > 8:
                        raise CliAttachError(f"desktop event stream disconnected ({type(exc).__name__})") from exc
                    time.sleep(min(0.25 * (2 ** (reconnects - 1)), 4.0))
                    continue
                if complete:
                    break
                status = self._json_request("GET", f"/api/cli/v1/runs/{run_id}")
                if str(status.get("status") or "") in {"done", "failed", "canceled"}:
                    complete = True
                else:
                    reconnects += 1
                    if reconnects > 8:
                        raise CliAttachError("desktop event stream ended before the run completed")
                    time.sleep(min(0.25 * (2 ** (reconnects - 1)), 4.0))
        finally:
            if not complete:
                self.cancel(run_id)

    def cancel(self, run_id: str) -> None:
        try:
            self._json_request("POST", f"/api/cli/v1/runs/{run_id}/cancel", {})
        except CliAttachError:
            pass

    def _event_stream_once(self, run_id: str, *, after_seq: int) -> Generator[Dict[str, Any] | None, None, None]:
        query = urlencode({"after": max(0, int(after_seq)), "schema": "v1"})
        connection = self._connection()
        try:
            connection.request(
                "GET",
                f"/api/cli/v1/runs/{run_id}/events?{query}",
                headers=self._headers(accept="text/event-stream"),
            )
            response = connection.getresponse()
            if response.status != 200:
                response.read(64 * 1024)
                raise CliAttachError(f"desktop event stream returned HTTP {response.status}")
            data_lines: list[str] = []
            while True:
                raw = response.readline()
                if not raw:
                    break
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                if not line:
                    if not data_lines:
                        continue
                    data = "\n".join(data_lines)
                    data_lines = []
                    if data == "[DONE]":
                        yield None
                        return
                    try:
                        payload = json.loads(data)
                    except json.JSONDecodeError as exc:
                        raise CliAttachError("desktop emitted invalid SSE JSON") from exc
                    if isinstance(payload, dict):
                        yield payload
                    continue
                if line.startswith("data:"):
                    data_lines.append(line[5:].lstrip())
        finally:
            connection.close()

    def _json_request(self, method: str, path: str, body: Mapping[str, Any] | None = None) -> Dict[str, Any]:
        encoded = None if body is None else json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        headers = self._headers(accept="application/json")
        if encoded is not None:
            headers["Content-Type"] = "application/json"
            headers["Content-Length"] = str(len(encoded))
        connection = self._connection()
        try:
            connection.request(method, path, body=encoded, headers=headers)
            response = connection.getresponse()
            raw = response.read(2 * 1024 * 1024)
        except (OSError, http.client.HTTPException, socket.timeout) as exc:
            raise CliAttachError(f"could not reach the Metis desktop backend ({type(exc).__name__})") from exc
        finally:
            connection.close()
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CliAttachError(f"desktop returned invalid JSON (HTTP {response.status})") from exc
        if response.status < 200 or response.status >= 300:
            message = str(payload.get("error") or f"HTTP {response.status}") if isinstance(payload, dict) else f"HTTP {response.status}"
            raise CliAttachError(_safe_server_message(message))
        if not isinstance(payload, dict):
            raise CliAttachError("desktop returned a non-object JSON response")
        return payload

    def _connection(self) -> http.client.HTTPConnection:
        return http.client.HTTPConnection(self.endpoint.host, self.endpoint.port, timeout=self.timeout)

    def _headers(self, *, accept: str) -> Dict[str, str]:
        return {
            "Accept": accept,
            "X-Metis-CLI-Token": self.endpoint.token,
            "User-Agent": "Metis-CLI-Attach/1",
        }


def run_attached(
    args: ParsedCliArgs,
    *,
    prompt: str,
    workspace: Path | None,
    stdout: TextIO,
    stderr: TextIO,
) -> HeadlessResult:
    validate_attach_args(args)
    endpoint = load_attach_endpoint()
    client = AttachClient(endpoint)
    client.handshake()
    session = client.prepare_session(
        workspace=workspace,
        resume_id=args.resume_id,
        continue_session=args.continue_session,
    )
    run = client.create_run(prompt=prompt, session=session)
    renderer = HeadlessRenderer(
        output_format=args.output_format,
        stdout=stdout,
        stderr=stderr,
        serializer=lambda event: event._raw,
    )
    return drive_headless(
        client.events(str(run["run_id"])),
        renderer=renderer,
        session_id=str(session["session_id"]),
        permission_fail_fast=False,
    )


def validate_attach_args(args: ParsedCliArgs) -> None:
    unsupported = []
    for name, value in (
        ("--permission-mode", args.permission_mode),
        ("--allowed-tools", args.allowed_tools),
        ("--policy", args.policy),
        ("--backend", args.backend),
        ("--base-url", args.base_url),
        ("--model", args.model),
        ("--max-turns", args.max_turns),
        ("--no-desktop", args.no_desktop),
        ("--no-mcp", args.no_mcp),
    ):
        if value not in (None, "", False):
            unsupported.append(name)
    if unsupported:
        raise CliUsageError(
            "attached runs use the desktop runtime configuration; unsupported override(s): " + ", ".join(unsupported)
        )


def load_attach_endpoint(path: Path | None = None) -> AttachEndpoint:
    discovery = path or _discovery_path()
    try:
        if discovery.stat().st_size > 64 * 1024:
            raise CliAttachError("desktop attach discovery is invalid")
        payload = json.loads(discovery.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise CliAttachError("Metis desktop is not running or attach discovery is unavailable") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise CliAttachError("desktop attach discovery could not be read") from exc
    if not isinstance(payload, dict) or payload.get("schema") != DISCOVERY_SCHEMA or payload.get("protocol") != ATTACH_PROTOCOL:
        raise CliAttachError("desktop attach discovery has an incompatible schema")
    host = str(payload.get("host") or "")
    try:
        port = int(payload.get("port") or 0)
        pid = int(payload.get("pid") or 0)
    except (TypeError, ValueError) as exc:
        raise CliAttachError("desktop attach discovery contains invalid endpoint data") from exc
    instance_id = str(payload.get("instance_id") or "")
    token = str(payload.get("token") or "")
    if host != "127.0.0.1" or port < 1 or port > 65535 or pid < 1 or len(instance_id) < 16 or len(token) < 32:
        raise CliAttachError("desktop attach discovery contains invalid endpoint data")
    if not _process_alive(pid):
        raise CliAttachError("desktop attach discovery is stale")
    return AttachEndpoint(host=host, port=port, pid=pid, instance_id=instance_id, token=token, path=discovery)


def _discovery_path() -> Path:
    return cli_attach_discovery_path()


def _process_alive(pid: int) -> bool:
    if sys.platform != "win32":
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False
    process_query_limited_information = 0x1000
    still_active = 259
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel32.GetExitCodeProcess.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel32.OpenProcess(process_query_limited_information, False, pid)
    if not handle:
        return False
    try:
        code = wintypes.DWORD(0)
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == still_active
    finally:
        kernel32.CloseHandle(handle)


def _event_object(raw: Mapping[str, Any]) -> SimpleNamespace:
    values: Dict[str, Any] = {}
    payload = raw.get("payload")
    if isinstance(payload, Mapping):
        values.update(payload)
    error = raw.get("error")
    if isinstance(error, Mapping):
        values.update(error)
    values.update(raw)
    kind = str(values.get("kind") or values.get("type") or "event")
    values["kind"] = kind
    values["type"] = kind
    if kind in {"tool_call", "tool_result", "permission_request"}:
        values["tool_name"] = str(
            values.get("tool_name")
            or values.get("tool")
            or values.get("toolName")
            or values.get("name")
            or ""
        )
    if kind in {"tool_call", "permission_request"}:
        values["arguments"] = values.get("arguments", values.get("args", {}))
    if kind == "done":
        usage = values.get("usage") if isinstance(values.get("usage"), Mapping) else {}
        values["total_turns"] = int(values.get("total_turns") or values.get("turns") or 0)
        values["total_tool_calls"] = int(values.get("total_tool_calls") or values.get("tool_calls") or 0)
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "prompt_cache_hit_tokens",
            "prompt_cache_miss_tokens",
        ):
            values[key] = int(values.get(key) or usage.get(key) or 0)
    values["_raw"] = dict(raw)
    return SimpleNamespace(**values)


def _safe_server_message(value: str) -> str:
    from backend.runtime.llm_backends._common import sanitize_for_log

    return sanitize_for_log(value).replace("\r", " ").replace("\n", " ")[:500]


__all__ = [
    "ATTACH_PROTOCOL",
    "AttachClient",
    "AttachEndpoint",
    "CliAttachError",
    "load_attach_endpoint",
    "run_attached",
    "validate_attach_args",
]

from __future__ import annotations

import atexit
import csv
import hmac
import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict

from backend.bridges.event_contract import EVENT_SCHEMA
from backend.core.cli_attach_path import cli_attach_discovery_path, cli_data_home_pointer_path
from backend.core.paths import metis_home
from backend.version import __version__

ATTACH_PROTOCOL = "metis.cli_attach.v1"
ATTACH_DISCOVERY_SCHEMA = "metis.cli_attach.discovery.v1"
ATTACH_HELLO_SCHEMA = "metis.cli_attach.hello.v1"
ATTACH_EVENT_SCHEMA = EVENT_SCHEMA
ATTACH_EVENT_SCHEMAS = (EVENT_SCHEMA, "metis.agent_event.v2")

_ATTACH_TOKEN = str(os.environ.get("METIS_CLI_ATTACH_TOKEN") or secrets.token_urlsafe(32))
_ATTACH_INSTANCE_ID = str(os.environ.get("METIS_CLI_ATTACH_INSTANCE_ID") or secrets.token_hex(16))
_PUBLISHED_PATH: Path | None = None


def attach_enabled() -> bool:
    return str(os.environ.get("METIS_CLI_ATTACH_DISABLED") or "").strip().lower() not in {
        "1",
        "true",
        "yes",
        "on",
    }


def authorize_attach_request(remote_addr: str, provided_token: str) -> bool:
    if not attach_enabled() or not _is_loopback(remote_addr):
        return False
    token = str(provided_token or "")
    return bool(token) and hmac.compare_digest(_ATTACH_TOKEN, token)


def attach_hello_payload() -> Dict[str, Any]:
    return {
        "schema": ATTACH_HELLO_SCHEMA,
        "ok": True,
        "protocol": ATTACH_PROTOCOL,
        "instance_id": _ATTACH_INSTANCE_ID,
        "pid": os.getpid(),
        "metis_version": __version__,
        "event_schema": ATTACH_EVENT_SCHEMA,
        "event_schemas": list(ATTACH_EVENT_SCHEMAS),
        "capabilities": {
            "runs": True,
            "sessions": True,
            "resume": True,
            "cancel": True,
            "desktop_permissions": True,
        },
    }


def discovery_path() -> Path:
    return cli_attach_discovery_path()


def publish_attach_discovery(*, host: str, port: int) -> Path | None:
    global _PUBLISHED_PATH
    if not attach_enabled() or not _is_loopback(host):
        return None
    normalized_port = int(port)
    if normalized_port < 1 or normalized_port > 65535:
        raise ValueError("attach discovery port is invalid")
    path = discovery_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": ATTACH_DISCOVERY_SCHEMA,
        "protocol": ATTACH_PROTOCOL,
        "instance_id": _ATTACH_INSTANCE_ID,
        "pid": os.getpid(),
        "host": "127.0.0.1",
        "port": normalized_port,
        "token": _ATTACH_TOKEN,
        "metis_version": __version__,
        "event_schema": ATTACH_EVENT_SCHEMA,
        "created_at": time.time(),
    }
    temp = path.with_name(f".{path.name}.{_ATTACH_INSTANCE_ID}.tmp")
    try:
        temp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        if sys.platform == "win32" and not _restrict_windows_acl(path):
            path.unlink(missing_ok=True)
            raise OSError("could not restrict CLI attach discovery ACL to the current Windows user")
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
    _PUBLISHED_PATH = path
    _publish_data_home_pointer()
    return path


def _publish_data_home_pointer() -> Path | None:
    if str(os.environ.get("METIS_CLI_ATTACH_DISCOVERY") or "").strip():
        return None
    if str(os.environ.get("METIS_CLI_ATTACH_CHANNEL") or "").strip().lower() == "dev":
        return None
    path = cli_data_home_pointer_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "schema": "metis.cli_data_home.v1",
        "metis_home": str(metis_home()),
        "updated_at": time.time(),
    }
    temp = path.with_name(f".{path.name}.{_ATTACH_INSTANCE_ID}.tmp")
    try:
        temp.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
        try:
            os.chmod(temp, 0o600)
        except OSError:
            pass
        os.replace(temp, path)
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
        if sys.platform == "win32" and not _restrict_windows_acl(path):
            path.unlink(missing_ok=True)
            raise OSError("could not restrict CLI data-home pointer ACL to the current Windows user")
    finally:
        try:
            temp.unlink(missing_ok=True)
        except OSError:
            pass
    return path


def clear_attach_discovery() -> bool:
    global _PUBLISHED_PATH
    path = _PUBLISHED_PATH or discovery_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        _PUBLISHED_PATH = None
        return False
    if str(data.get("instance_id") or "") != _ATTACH_INSTANCE_ID:
        _PUBLISHED_PATH = None
        return False
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    _PUBLISHED_PATH = None
    return True


def _restrict_windows_acl(path: Path) -> bool:
    if sys.platform != "win32":
        return True
    sid = _current_user_sid()
    if not sid:
        return False
    try:
        result = subprocess.run(
            ["icacls.exe", str(path), "/inheritance:r", "/grant:r", f"*{sid}:F"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return result.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _current_user_sid() -> str:
    try:
        result = subprocess.run(
            ["whoami.exe", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        row = next(csv.reader([str(result.stdout or "").strip()]), [])
        return str(row[1] if len(row) > 1 else "").strip()
    except (OSError, subprocess.SubprocessError, csv.Error):
        return ""


def _is_loopback(host: str) -> bool:
    return str(host or "").strip().lower() in {"127.0.0.1", "::1", "localhost"}


atexit.register(clear_attach_discovery)


__all__ = [
    "ATTACH_DISCOVERY_SCHEMA",
    "ATTACH_EVENT_SCHEMA",
    "ATTACH_HELLO_SCHEMA",
    "ATTACH_PROTOCOL",
    "attach_enabled",
    "attach_hello_payload",
    "authorize_attach_request",
    "clear_attach_discovery",
    "discovery_path",
    "publish_attach_discovery",
]

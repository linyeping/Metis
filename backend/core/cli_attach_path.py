from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any


def cli_runtime_dir() -> Path:
    override = str(os.environ.get("METIS_CLI_ATTACH_DISCOVERY") or "").strip()
    if override:
        return Path(override).expanduser().resolve(strict=False).parent
    if sys.platform == "win32":
        local_app_data = str(os.environ.get("LOCALAPPDATA") or "").strip()
        if local_app_data:
            return (Path(local_app_data) / "Metis" / "runtime").resolve(strict=False)
    return (Path.home() / ".metis" / "runtime").resolve(strict=False)


def cli_attach_discovery_path() -> Path:
    override = str(os.environ.get("METIS_CLI_ATTACH_DISCOVERY") or "").strip()
    if override:
        return Path(override).expanduser().resolve(strict=False)
    channel = str(os.environ.get("METIS_CLI_ATTACH_CHANNEL") or "").strip().lower()
    filename = "desktop-attach-dev.json" if channel == "dev" else "desktop-attach.json"
    return cli_runtime_dir() / filename


def cli_data_home_pointer_path() -> Path:
    return cli_runtime_dir() / "data-home.json"


def cli_shared_metis_home() -> Path | None:
    path = cli_data_home_pointer_path()
    try:
        if path.stat().st_size > 64 * 1024:
            return None
        payload: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or payload.get("schema") != "metis.cli_data_home.v1":
        return None
    value = str(payload.get("metis_home") or "").strip()
    if not value:
        return None
    resolved = Path(value).expanduser().resolve(strict=False)
    return resolved if resolved.is_dir() else None


__all__ = [
    "cli_attach_discovery_path",
    "cli_data_home_pointer_path",
    "cli_runtime_dir",
    "cli_shared_metis_home",
]

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path
from typing import Mapping


def configured_python_executable() -> Path | None:
    """Return the user-selected Python interpreter, if it is configured and exists."""
    for raw in _candidate_values():
        resolved = resolve_python_executable(raw)
        if resolved:
            return resolved
    return None


def python_executable() -> str:
    selected = configured_python_executable()
    return str(selected) if selected else sys.executable


def subprocess_env_with_configured_python(base: Mapping[str, str] | None = None) -> dict[str, str]:
    env = dict(base or os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    selected = configured_python_executable()
    if not selected:
        return env

    env["METIS_PYTHON"] = str(selected)
    env["MIRO_PYTHON"] = str(selected)
    path_entries = _python_path_entries(selected)
    current_path = [part for part in env.get("PATH", "").split(os.pathsep) if part]
    env["PATH"] = os.pathsep.join(_dedupe_path_entries([*path_entries, *current_path]))
    return env


def shell_command_with_configured_python(command: str) -> str:
    selected = configured_python_executable()
    text = str(command or "")
    if not selected or not text.strip():
        return text

    py = _quote_for_shell(str(selected))

    def replace_py_probe(match: re.Match[str]) -> str:
        prefix = match.group("prefix") or ""
        return f'{prefix}{py} -c "import sys; print(sys.executable)"'

    def replace_python(match: re.Match[str]) -> str:
        prefix = match.group("prefix") or ""
        return f"{prefix}{py}"

    def replace_pip(match: re.Match[str]) -> str:
        prefix = match.group("prefix") or ""
        return f"{prefix}{py} -m pip"

    boundary = r"(?P<prefix>^|(?:&&|\|\||[;&])\s*)"
    text = re.sub(boundary + r"py\s+-0p\b", replace_py_probe, text, flags=re.IGNORECASE)
    text = re.sub(boundary + r"(?:python|python3|py)(?=\s|$)", replace_python, text, flags=re.IGNORECASE)
    text = re.sub(boundary + r"(?:pip|pip3)(?=\s|$)", replace_pip, text, flags=re.IGNORECASE)
    return text


def resolve_python_executable(value: str | os.PathLike[str] | None) -> Path | None:
    raw = str(value or "").strip().strip('"')
    if not raw:
        return None
    path = Path(raw).expanduser()
    candidates: list[Path]
    if path.is_dir():
        candidates = [
            path / "python.exe",
            path / "Scripts" / "python.exe",
            path / "bin" / "python",
        ]
    else:
        candidates = [path]
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=False)
        except OSError:
            continue
        if resolved.is_file():
            return resolved
    return None


def _candidate_values() -> list[str]:
    values: list[str] = []
    for name in ("METIS_PYTHON", "MIRO_PYTHON"):
        raw = os.environ.get(name, "").strip()
        if raw:
            values.append(raw)
    values.extend(_config_python_values())
    return values


def _config_python_values() -> list[str]:
    try:
        from backend.core.paths import legacy_miro_path, metis_path
    except Exception:
        return []

    values: list[str] = []
    for path in (metis_path("config.json"), legacy_miro_path("config.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        raw = data.get("python_path") if isinstance(data, dict) else ""
        if raw:
            values.append(str(raw))
    return values


def _python_path_entries(python: Path) -> list[str]:
    exe_dir = python.parent
    env_root = exe_dir.parent if exe_dir.name.lower() in {"scripts", "bin"} else exe_dir
    entries = [
        exe_dir,
        env_root / "Scripts",
        env_root / "bin",
        env_root / "Library" / "bin",
    ]
    return [str(path) for path in entries if path.exists()]


def _dedupe_path_entries(entries: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for entry in entries:
        key = os.path.normcase(os.path.abspath(entry))
        if key in seen:
            continue
        seen.add(key)
        out.append(entry)
    return out


def _quote_for_shell(path: str) -> str:
    if os.name == "nt":
        return f'"{path}"'
    return "'" + path.replace("'", "'\"'\"'") + "'"

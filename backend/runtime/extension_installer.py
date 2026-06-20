"""Install Metis skills and MCP servers into the right home locations.

Without these tools an agent told to "download + install a skill/MCP" just
shells out and drops files into the workspace, where they are never loaded.
These install into the user's Metis home instead:

  - skills      -> global_skills_root()  (metis_dir("skills"))
  - MCP servers -> metis_path("mcp.json")  ({"mcpServers": {...}}), then reload
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import zipfile
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.request import urlopen

from backend.core.paths import metis_path
from backend.runtime.skill_loader import global_skills_root


_NAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe_name(name: str) -> str:
    cleaned = _NAME_RE.sub("-", str(name or "").strip()).strip("-._")
    return cleaned[:64]


def _result(ok: bool, **fields: Any) -> str:
    return json.dumps({"ok": ok, **fields}, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Skills
# ---------------------------------------------------------------------------

def _find_skill_dir(root: Path) -> Optional[Path]:
    """Locate the directory that actually contains SKILL.md (root or one level down)."""
    if (root / "SKILL.md").is_file():
        return root
    children = [p for p in root.iterdir() if p.is_dir()] if root.is_dir() else []
    for child in children:
        if (child / "SKILL.md").is_file():
            return child
    return None


def install_skill(source: str, name: str = "") -> str:
    """Install a skill into the global skills home so it loads after restart.

    `source` may be a local directory, a local/remote .zip, or a git URL. The
    skill must contain a SKILL.md. Returns a JSON status.
    """
    src = str(source or "").strip()
    if not src:
        return _result(False, error="source is required (a directory, .zip, or git URL)")

    target_root = global_skills_root()
    target_root.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="metis_skill_") as tmp:
        tmp_dir = Path(tmp)
        staged: Optional[Path] = None
        try:
            if src.startswith(("http://", "https://")) and src.endswith(".git") or src.startswith("git@"):
                staged = tmp_dir / "repo"
                subprocess.run(["git", "clone", "--depth", "1", src, str(staged)], check=True,
                               capture_output=True, text=True, timeout=180)
            elif src.startswith(("http://", "https://")):
                # download (expects a .zip)
                zpath = tmp_dir / "skill.zip"
                with urlopen(src, timeout=120) as resp, open(zpath, "wb") as fh:
                    shutil.copyfileobj(resp, fh)
                staged = tmp_dir / "unzipped"
                with zipfile.ZipFile(zpath) as zf:
                    zf.extractall(staged)
            elif src.lower().endswith(".zip") and os.path.isfile(src):
                staged = tmp_dir / "unzipped"
                with zipfile.ZipFile(src) as zf:
                    zf.extractall(staged)
            elif os.path.isdir(src):
                staged = Path(src)
            else:
                return _result(False, error=f"source not found or unsupported: {src}")
        except subprocess.CalledProcessError as exc:
            return _result(False, error=f"git clone failed: {exc.stderr or exc}")
        except Exception as exc:  # noqa: BLE001
            return _result(False, error=f"{type(exc).__name__}: {exc}")

        skill_dir = _find_skill_dir(staged) if staged else None
        if not skill_dir:
            return _result(False, error="no SKILL.md found in the source")

        skill_name = _safe_name(name) or _safe_name(skill_dir.name)
        if not skill_name:
            return _result(False, error="could not derive a valid skill name")
        dest = target_root / skill_name
        if dest.exists():
            shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(skill_dir, dest, dirs_exist_ok=True)

    return _result(
        True,
        installed="skill",
        name=skill_name,
        path=str(dest),
        message=f"Skill '{skill_name}' installed. It is available now (load_skill) and after restart.",
    )


# ---------------------------------------------------------------------------
# MCP servers
# ---------------------------------------------------------------------------

def install_mcp_server(
    name: str,
    command: str = "",
    args: Optional[List[str]] = None,
    env: Optional[Dict[str, str]] = None,
    url: str = "",
) -> str:
    """Register an MCP server in Metis's own mcp.json and reload tools.

    Provide either a stdio launcher (`command` + `args`) or a remote `url`.
    Secrets belong in `env` as values, never hard-coded into args.
    """
    server_name = _safe_name(name)
    if not server_name:
        return _result(False, error="a valid server name is required")
    if not command and not url:
        return _result(False, error="provide either command (+args) or url")

    config_path = metis_path("mcp.json")
    try:
        existing: Dict[str, Any] = json.loads(config_path.read_text(encoding="utf-8")) if config_path.is_file() else {}
    except (OSError, json.JSONDecodeError):
        existing = {}
    servers = existing.get("mcpServers")
    if not isinstance(servers, dict):
        servers = {}

    entry: Dict[str, Any] = {}
    if command:
        entry["command"] = command
        entry["args"] = list(args or [])
    if url:
        entry["url"] = url
    if env:
        entry["env"] = {str(k): str(v) for k, v in env.items()}

    replaced = server_name in servers
    servers[server_name] = entry
    existing["mcpServers"] = servers
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")

    reload_info: Dict[str, Any] = {}
    try:
        from backend.runtime.tool_registry import reload_mcp_tools

        reload_info = reload_mcp_tools(config_path=str(config_path)) or {}
    except Exception as exc:  # noqa: BLE001
        reload_info = {"reload_error": f"{type(exc).__name__}: {exc}"}

    return _result(
        True,
        installed="mcp_server",
        name=server_name,
        replaced=replaced,
        config_path=str(config_path),
        reload=reload_info,
        message=f"MCP server '{server_name}' {'updated' if replaced else 'registered'} in mcp.json and reloaded.",
    )


__all__ = ["install_skill", "install_mcp_server"]

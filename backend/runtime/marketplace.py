"""Metis extension marketplace, registry aggregation, and lifecycle management.

The marketplace is deliberately host-controlled: catalog metadata may come from
bundled manifests or the public MCP Registry, but installation always lands in
METIS_HOME and third-party code is disabled until the user explicitly enables it.
Secrets are represented by environment-variable names only; plaintext values are
owned by Electron safeStorage and injected into the backend process at startup.
"""
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import shutil
import subprocess
import tempfile
import time
import zipfile
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple
from urllib.parse import quote, urlencode, urlparse
from urllib.request import Request, urlopen

from backend.core.paths import metis_dir, metis_path
from backend.runtime.extension_installer import install_skill
from backend.runtime.skill_loader import global_skills_root, resolve_skill_by_id, skill_disabled_marker
from backend.runtime.marketplace_sources import load_source_items


SCHEMA = "metis.marketplace.v1"
MCP_REGISTRY_URL = "https://registry.modelcontextprotocol.io/v0.1/servers"
_ID_RE = re.compile(r"[^a-zA-Z0-9._:@/-]+")
_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_FS_UNSAFE_RE = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')
_WINDOWS_RESERVED_NAMES = {"CON", "PRN", "AUX", "NUL", *(f"COM{index}" for index in range(1, 10)), *(f"LPT{index}" for index in range(1, 10))}
_REGISTRY_CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
_DYNAMIC_ITEMS: Dict[str, Dict[str, Any]] = {}
_MAX_ARCHIVE_BYTES = 128 * 1024 * 1024
_MAX_ZIP_FILES = 10_000
_MAX_ZIP_UNCOMPRESSED_BYTES = 512 * 1024 * 1024
_MAX_ZIP_MEMBER_BYTES = 128 * 1024 * 1024
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024


class MarketplaceError(RuntimeError):
    pass


def marketplace_root() -> Path:
    return Path(__file__).resolve().parents[1] / "resources" / "marketplace"


def marketplace_manifest_path() -> Path:
    override = os.environ.get("METIS_MARKETPLACE_MANIFEST", "").strip()
    return Path(override).expanduser().resolve(strict=False) if override else marketplace_root() / "marketplace.json"


def marketplace_state_path() -> Path:
    return metis_path("marketplace-state.json")


def plugins_root() -> Path:
    return metis_dir("plugins")


def load_manifest(path: Optional[Path] = None) -> Dict[str, Any]:
    target = path or marketplace_manifest_path()
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MarketplaceError(f"Marketplace manifest unavailable: {exc}") from exc
    validate_manifest(data)
    return data


@lru_cache(maxsize=1)
def _description_catalog() -> Dict[str, Dict[str, str]]:
    path = marketplace_root() / "descriptions.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(payload, dict) or payload.get("schema") != "metis.marketplace.descriptions.v1":
        return {}
    raw_items = payload.get("items")
    if not isinstance(raw_items, dict):
        return {}
    result: Dict[str, Dict[str, str]] = {}
    for item_id, raw_descriptions in raw_items.items():
        if not isinstance(raw_descriptions, dict):
            continue
        descriptions = {
            str(language): str(value).strip()
            for language, value in raw_descriptions.items()
            if isinstance(value, str) and value.strip()
        }
        if descriptions:
            result[str(item_id)] = descriptions
    return result


def _item_descriptions(item: Dict[str, Any]) -> Dict[str, str]:
    item_id = str(item.get("id") or "")
    descriptions = dict(_description_catalog().get(item_id, {}))
    supplied = item.get("descriptions")
    if isinstance(supplied, dict):
        descriptions.update(
            {
                str(language): str(value).strip()
                for language, value in supplied.items()
                if isinstance(value, str) and value.strip()
            }
        )
    source_description = str(item.get("description") or "").strip()
    if source_description:
        language = "zh" if _CJK_RE.search(source_description) else "en"
        descriptions.setdefault(language, source_description)
    return descriptions


def validate_manifest(data: Any) -> None:
    if not isinstance(data, dict):
        raise MarketplaceError("Marketplace manifest must be an object")
    if data.get("schema") != SCHEMA:
        raise MarketplaceError(f"Unsupported marketplace schema: {data.get('schema')!r}")
    if not isinstance(data.get("items"), list):
        raise MarketplaceError("Marketplace manifest items must be a list")
    seen: set[str] = set()
    for item in data["items"]:
        if not isinstance(item, dict):
            raise MarketplaceError("Marketplace item must be an object")
        item_id = str(item.get("id") or "").strip()
        kind = str(item.get("kind") or "").strip()
        if not item_id or item_id in seen:
            raise MarketplaceError(f"Duplicate or missing marketplace item id: {item_id!r}")
        if kind not in {"skill", "mcp", "plugin"}:
            raise MarketplaceError(f"Unsupported marketplace item kind: {kind!r}")
        version = str(item.get("version") or "0.0.0")
        if not _SEMVER_RE.match(version):
            raise MarketplaceError(f"Invalid version for {item_id}: {version}")
        seen.add(item_id)


def _read_state() -> Dict[str, Any]:
    path = marketplace_state_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("items"), dict):
        data["items"] = {}
    data.setdefault("schema", SCHEMA)
    return data


def _write_state(state: Dict[str, Any]) -> None:
    path = marketplace_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _catalog_items() -> List[Dict[str, Any]]:
    manifest = load_manifest()
    rows = [dict(item) for item in manifest["items"]]
    rows.extend(dict(item) for item in load_source_items())
    rows.extend(dict(item) for item in _DYNAMIC_ITEMS.values())
    by_id: Dict[str, Dict[str, Any]] = {}
    for item in rows:
        item_id = str(item.get("id") or "")
        if item_id:
            by_id[item_id] = item
    state = _read_state()
    for item_id, installed in state["items"].items():
        snapshot = installed.get("manifest") if isinstance(installed, dict) else None
        if not isinstance(snapshot, dict):
            continue
        current = by_id.get(item_id)
        if current is None:
            by_id[item_id] = dict(snapshot)
            continue
        merged = {**snapshot, **current}
        for key in ("plugin", "_packageManifest", "_packagePath"):
            if key in snapshot and key not in current:
                merged[key] = snapshot[key]
        by_id[item_id] = merged
    return list(by_id.values())


def get_item(item_id: str) -> Dict[str, Any]:
    target = str(item_id or "").strip()
    for item in _catalog_items():
        if str(item.get("id")) == target:
            return _merge_status(item)
    raise MarketplaceError(f"Marketplace item not found: {target}")


def list_catalog(query: str = "", kind: str = "", source: str = "") -> Dict[str, Any]:
    q = str(query or "").strip().lower()
    kind_filter = str(kind or "").strip().lower()
    source_filter = str(source or "").strip().lower()
    rows: List[Dict[str, Any]] = []
    for item in _catalog_items():
        if kind_filter and kind_filter != "all" and item.get("kind") != kind_filter:
            continue
        source_data = item.get("source") if isinstance(item.get("source"), dict) else {}
        item_source = str(source_data.get("type") or "official").lower()
        marketplace_source = str(source_data.get("marketplace") or "").lower()
        is_metis_official = not marketplace_source and item_source in {"bundled", "bundled-plugin", "official"}
        if source_filter == "metis-official" and not is_metis_official:
            continue
        if source_filter and source_filter != "metis-official" and source_filter not in {"all", item_source, marketplace_source}:
            continue
        haystack = " ".join(
            [str(item.get(field) or "") for field in ("id", "name", "description", "publisher", "category")]
            + list(_item_descriptions(item).values())
        ).lower()
        if q and q not in haystack:
            continue
        rows.append(_merge_status(item))
    rows.sort(key=lambda row: (not bool(row.get("featured")), str(row.get("name") or "").lower()))
    return {
        "schema": SCHEMA,
        "items": rows,
        "counts": {name: sum(1 for row in rows if row.get("kind") == name) for name in ("skill", "mcp", "plugin")},
    }


def _merge_status(item: Dict[str, Any]) -> Dict[str, Any]:
    row = {key: value for key, value in item.items() if not str(key).startswith("_")}
    row["iconDataUrl"] = _icon_data_url(item)
    row["descriptions"] = _item_descriptions(item)
    saved = _read_state()["items"].get(str(item.get("id")), {})
    installed = bool(saved.get("installed"))
    installed_version = str(saved.get("installedVersion") or "")
    row.update(
        {
            "installed": installed,
            "enabled": bool(saved.get("enabled")) if installed else False,
            "needsSetup": bool(saved.get("needsSetup")) if installed else False,
            "installedVersion": installed_version,
            "updateAvailable": bool(installed_version and installed_version != str(item.get("version") or "")),
            "error": str(saved.get("error") or ""),
            "configuredEnv": list(saved.get("configuredEnv") or []),
        }
    )
    if item.get("kind") == "plugin":
        component_ids = list((item.get("plugin") or {}).get("skills", [])) + list((item.get("plugin") or {}).get("mcpServers", []))
        components: List[Dict[str, Any]] = []
        for component_id in component_ids:
            try:
                components.append(_merge_status(_raw_item(str(component_id))))
            except MarketplaceError:
                continue
        row["components"] = components
    return row


def _icon_data_url(item: Dict[str, Any]) -> str:
    icon = str(item.get("icon") or "").strip()
    if icon.startswith("data:image/"):
        return icon
    if not icon:
        return ""
    path = _resolve_manifest_relative(icon)
    if not path.is_file() or path.stat().st_size > 512_000:
        return ""
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if mime not in {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}:
        return ""
    content = path.read_bytes()
    if mime == "image/svg+xml" and not _safe_svg(content):
        return ""
    encoded = base64.b64encode(content).decode("ascii")
    return f"data:{mime};base64,{encoded}"


def _resolve_manifest_relative(value: str) -> Path:
    root = marketplace_root().resolve(strict=False)
    path = (root / value).resolve(strict=False)
    if path != root and root not in path.parents:
        raise MarketplaceError("Marketplace path escapes its manifest root")
    return path


def _resolve_resource_relative(value: str) -> Path:
    root = marketplace_root().parent.resolve(strict=False)
    path = (marketplace_root() / value).resolve(strict=False)
    if path != root and root not in path.parents:
        raise MarketplaceError("Bundled source escapes the resources root")
    return path


def search_mcp_registry(search: str = "", cursor: str = "", limit: int = 24) -> Dict[str, Any]:
    params = {"limit": str(max(1, min(int(limit or 24), 100)))}
    if search:
        params["search"] = str(search)
    if cursor:
        params["cursor"] = str(cursor)
    url = f"{MCP_REGISTRY_URL}?{urlencode(params)}"
    cached = _REGISTRY_CACHE.get(url)
    if cached and time.time() - cached[0] < 300:
        return cached[1]
    try:
        request = Request(url, headers={"Accept": "application/json", "User-Agent": "Metis/1.0"})
        with urlopen(request, timeout=15) as response:
            raw = json.loads(response.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        if cached:
            return cached[1]
        raise MarketplaceError(f"MCP Registry request failed: {exc}") from exc
    servers = raw.get("servers") if isinstance(raw, dict) else []
    latest: Dict[str, Dict[str, Any]] = {}
    for wrapper in servers or []:
        if not isinstance(wrapper, dict):
            continue
        server = wrapper.get("server") if isinstance(wrapper.get("server"), dict) else wrapper
        name = str(server.get("name") or "")
        current = latest.get(name)
        current_server = current.get("server") if current and isinstance(current.get("server"), dict) else current
        if not current or _version_key(str(server.get("version") or "")) >= _version_key(str((current_server or {}).get("version") or "")):
            latest[name] = wrapper
    items = [_registry_server_item(server) for server in latest.values()]
    for item in items:
        _DYNAMIC_ITEMS[str(item["id"])] = item
    result = {
        "items": [_merge_status(item) for item in items],
        "metadata": raw.get("metadata", {}) if isinstance(raw, dict) else {},
    }
    _REGISTRY_CACHE[url] = (time.time(), result)
    return result


def _registry_server_item(wrapper: Dict[str, Any]) -> Dict[str, Any]:
    server = wrapper.get("server") if isinstance(wrapper.get("server"), dict) else wrapper
    name = str(server.get("name") or "mcp-server")
    version = str(server.get("version") or "0.0.0")
    if not _SEMVER_RE.match(version):
        version = "0.0.0"
    encoded = quote(name, safe="")
    packages = server.get("packages") if isinstance(server.get("packages"), list) else []
    remotes = server.get("remotes") if isinstance(server.get("remotes"), list) else []
    mcp: Dict[str, Any] = {"serverName": _safe_id(name).replace("/", "-")[-64:]}
    variables: List[Dict[str, Any]] = []
    if packages:
        package = next((row for row in packages if isinstance(row, dict)), {})
        registry_type = str(package.get("registryType") or "")
        identifier = str(package.get("identifier") or "")
        runtime_hint = str(package.get("runtimeHint") or "")
        if registry_type == "npm" or runtime_hint in {"npx", "node"}:
            mcp["command"] = "npx"
            mcp["args"] = ["-y", f"{identifier}@{package.get('version') or version}"]
        elif registry_type == "pypi" or runtime_hint in {"uvx", "python"}:
            mcp["command"] = "uvx"
            mcp["args"] = [f"{identifier}=={package.get('version') or version}"]
        variables = _normalize_variables(package.get("environmentVariables"))
    if not mcp.get("command") and remotes:
        remote = next((row for row in remotes if isinstance(row, dict) and row.get("url")), {})
        mcp["url"] = str(remote.get("url") or "")
    mcp["environmentVariables"] = variables
    return {
        "id": f"registry:{encoded}@{version}",
        "kind": "mcp",
        "name": str(server.get("title") or name.split("/")[-1]),
        "version": version,
        "description": str(server.get("description") or "MCP Registry server"),
        "publisher": name.split("/")[0].replace("io.github.", ""),
        "category": "MCP Registry",
        "brandColor": "#7C3AED",
        "source": {"type": "registry", "url": str(server.get("repository", {}).get("url") or "") if isinstance(server.get("repository"), dict) else ""},
        "mcp": mcp,
    }


def _normalize_variables(value: Any) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for raw in value if isinstance(value, list) else []:
        if not isinstance(raw, dict):
            continue
        name = str(raw.get("name") or "").strip()
        if not _ENV_RE.match(name):
            continue
        rows.append(
            {
                "name": name,
                "description": str(raw.get("description") or ""),
                "required": bool(raw.get("isRequired", raw.get("required", False))),
                "secret": bool(raw.get("isSecret", raw.get("secret", False))),
                "default": str(raw.get("default") or ""),
            }
        )
    return rows


def _version_key(value: str) -> Tuple[int, int, int, str]:
    match = re.match(r"^(\d+)\.(\d+)\.(\d+)(.*)$", str(value or ""))
    if not match:
        return (0, 0, 0, str(value or ""))
    return (int(match.group(1)), int(match.group(2)), int(match.group(3)), match.group(4))


def install_item(item_id: str, *, config: Optional[Dict[str, Any]] = None, owner: str = "", direct: bool = True) -> Dict[str, Any]:
    item = _raw_item(item_id)
    kind = str(item.get("kind"))
    if kind == "skill":
        _install_skill_item(item)
    elif kind == "mcp":
        _install_mcp_item(item, config or {})
    elif kind == "plugin":
        _install_plugin_item(item)
    else:
        raise MarketplaceError(f"Unsupported item kind: {kind}")
    state = _read_state()
    saved = state["items"].setdefault(item_id, {})
    saved.update(
        {
            "installed": True,
            "installedVersion": str(item.get("version") or "0.0.0"),
            "enabled": False,
            "manifest": item,
            "error": "",
            "directInstalled": bool(saved.get("directInstalled")) or direct,
        }
    )
    if item.get("_packagePath"):
        saved["packagePath"] = str(item["_packagePath"])
    owners = set(saved.get("owners") or [])
    if owner:
        owners.add(owner)
    saved["owners"] = sorted(owners)
    saved["needsSetup"] = _item_needs_setup(item, saved)
    _write_state(state)
    return get_item(item_id)


def configure_item(item_id: str, values: Dict[str, Any], secret_configured: Iterable[str] = ()) -> Dict[str, Any]:
    item = _raw_item(item_id)
    if item.get("kind") not in {"mcp", "plugin"}:
        raise MarketplaceError("Only MCP and Plugin items accept configuration")
    state = _read_state()
    saved = state["items"].get(item_id)
    if not isinstance(saved, dict) or not saved.get("installed"):
        raise MarketplaceError("Install the item before configuring it")
    configured = set(saved.get("configuredEnv") or [])
    configured.update(name for name in secret_configured if _ENV_RE.match(str(name)))
    if item.get("kind") == "mcp":
        _configure_mcp_entry(item, values)
        configured.update(name for name, value in values.items() if _ENV_RE.match(str(name)) and str(value) != "")
    else:
        for component_id in (item.get("plugin") or {}).get("mcpServers", []):
            component_values = values.get(str(component_id), values) if isinstance(values, dict) else {}
            configure_item(str(component_id), component_values if isinstance(component_values, dict) else {}, secret_configured)
            component_state = _read_state()["items"].get(str(component_id), {})
            configured.update(component_state.get("configuredEnv") or [])
        state = _read_state()
        saved = state["items"].get(item_id, saved)
    saved["configuredEnv"] = sorted(configured)
    saved["needsSetup"] = _item_needs_setup(item, saved)
    saved["error"] = ""
    _write_state(state)
    return get_item(item_id)


def set_item_enabled(item_id: str, enabled: bool) -> Dict[str, Any]:
    item = _raw_item(item_id)
    state = _read_state()
    saved = state["items"].get(item_id)
    if not isinstance(saved, dict) or not saved.get("installed"):
        raise MarketplaceError("Item is not installed")
    if enabled and _item_needs_setup(item, saved):
        saved["needsSetup"] = True
        _write_state(state)
        raise MarketplaceError("Required MCP configuration is incomplete")
    try:
        kind = item.get("kind")
        if kind == "skill":
            _set_skill_enabled(item, enabled)
        elif kind == "mcp":
            _set_mcp_enabled(item, enabled)
        elif kind == "plugin":
            components = list((item.get("plugin") or {}).get("skills", [])) + list((item.get("plugin") or {}).get("mcpServers", []))
            previous = {
                str(component_id): bool(_read_state()["items"].get(str(component_id), {}).get("enabled"))
                for component_id in components
            }
            changed: List[str] = []
            try:
                for component_id in components:
                    set_item_enabled(str(component_id), enabled)
                    changed.append(str(component_id))
            except Exception:
                for component_id in reversed(changed):
                    try:
                        set_item_enabled(component_id, previous[component_id])
                    except Exception:
                        pass
                raise
            state = _read_state()
            saved = state["items"].get(item_id, saved)
        saved["enabled"] = enabled
        saved["error"] = ""
    except Exception as exc:  # noqa: BLE001
        saved["enabled"] = False
        saved["error"] = str(exc)
        _write_state(state)
        raise
    _write_state(state)
    return get_item(item_id)


def uninstall_item(item_id: str, *, owner: str = "", force: bool = False) -> Dict[str, Any]:
    item = _raw_item(item_id)
    state = _read_state()
    saved = state["items"].get(item_id)
    if not isinstance(saved, dict):
        return _merge_status(item)
    owners = set(saved.get("owners") or [])
    if owner:
        owners.discard(owner)
        saved["owners"] = sorted(owners)
    if not force and (owners or (saved.get("directInstalled") and owner)):
        _write_state(state)
        return get_item(item_id)
    if item.get("kind") == "plugin":
        components = list((item.get("plugin") or {}).get("skills", [])) + list((item.get("plugin") or {}).get("mcpServers", []))
        for component_id in components:
            uninstall_item(str(component_id), owner=item_id)
        package_path = str(saved.get("packagePath") or "")
        if package_path:
            _safe_remove_tree(Path(package_path), plugins_root())
    elif item.get("kind") == "skill":
        _remove_skill(item)
    elif item.get("kind") == "mcp":
        _remove_mcp(item)
    state = _read_state()
    state["items"].pop(item_id, None)
    _write_state(state)
    return _merge_status(item)


def install_source(source: str, name: str = "") -> Dict[str, Any]:
    src = str(source or "").strip()
    if not src:
        raise MarketplaceError("Source is required")
    with _staged_source(src) as staged:
        plugin_root = _find_plugin_root(staged)
        if plugin_root:
            item = _plugin_item_from_package(plugin_root)
            _DYNAMIC_ITEMS[str(item["id"])] = item
            destination = plugins_root() / _safe_fs_name(str(item["name"]))
            if destination.exists():
                shutil.rmtree(destination)
            shutil.copytree(plugin_root, destination)
            installed = _install_packaged_plugin(item, destination)
            state = _read_state()
            state["items"].setdefault(str(item["id"]), {}).update({"packagePath": str(destination)})
            _write_state(state)
            return installed
        skill_root = _find_skill_root(staged)
        if skill_root:
            item_id = f"source:{_safe_id(name or skill_root.name)}"
            item = {
                "id": item_id,
                "kind": "skill",
                "name": name or skill_root.name,
                "version": "0.0.0",
                "description": "Installed from a custom source",
                "publisher": "Custom",
                "category": "Custom",
                "source": {"type": "local", "path": str(skill_root)},
            }
            _DYNAMIC_ITEMS[item_id] = item
            return install_item(item_id)
    raise MarketplaceError("Source contains neither SKILL.md nor .codex-plugin/plugin.json")


def _raw_item(item_id: str) -> Dict[str, Any]:
    for item in _catalog_items():
        if str(item.get("id")) == str(item_id):
            return dict(item)
    raise MarketplaceError(f"Marketplace item not found: {item_id}")


def _install_skill_item(item: Dict[str, Any]) -> None:
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    source_type = str(source.get("type") or "")
    if source_type == "remote-skill":
        raw_path = str(source.get("url") or source.get("path") or "")
    else:
        raw_path = str(source.get("path") or source.get("url") or "")
    if source_type == "bundled":
        raw_path = str(_resolve_resource_relative(raw_path))
    if source_type == "remote-skill":
        with _staged_source(raw_path) as staged:
            skill_root = _find_skill_root(staged)
            if skill_root is None:
                raise MarketplaceError("Remote source contains no SKILL.md")
            result = json.loads(install_skill(str(skill_root), name=_component_name(item)))
    else:
        result = json.loads(install_skill(raw_path, name=_component_name(item)))
    if not result.get("ok"):
        raise MarketplaceError(str(result.get("error") or "Skill installation failed"))
    marker = skill_disabled_marker(Path(str(result.get("path"))))
    marker.touch(exist_ok=True)


def _install_mcp_item(item: Dict[str, Any], config: Dict[str, Any]) -> None:
    mcp = dict(item.get("mcp") or {})
    mcp.update({key: value for key, value in config.items() if key in {"command", "args", "url"} and value})
    server_name = _mcp_server_name(item)
    if not mcp.get("command") and not mcp.get("url"):
        raise MarketplaceError("MCP item does not define a supported command or URL")
    entry: Dict[str, Any] = {"disabled": True, "_metis_marketplace_id": item["id"]}
    for key in ("command", "args", "url"):
        if mcp.get(key):
            entry[key] = mcp[key]
    _write_mcp_entry(server_name, entry)


def _install_plugin_item(item: Dict[str, Any]) -> None:
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    if source.get("type") == "remote-plugin":
        _install_remote_plugin_item(item, str(source.get("url") or ""))
        return
    plugin = item.get("plugin") if isinstance(item.get("plugin"), dict) else {}
    component_ids = list(plugin.get("skills") or []) + list(plugin.get("mcpServers") or [])
    installed: List[str] = []
    try:
        for component_id in component_ids:
            install_item(str(component_id), owner=str(item["id"]), direct=False)
            installed.append(str(component_id))
    except Exception:
        for component_id in reversed(installed):
            uninstall_item(component_id, owner=str(item["id"]))
        raise


def _install_remote_plugin_item(item: Dict[str, Any], source_url: str) -> None:
    with _staged_source(source_url) as staged:
        plugin_root = _find_plugin_root(staged)
        if plugin_root is None:
            raise MarketplaceError("Remote plugin is missing .codex-plugin/plugin.json")
        destination = plugins_root() / _safe_fs_name(str(item.get("id") or item.get("name")))
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(plugin_root, destination)
    packaged = _plugin_item_from_package(destination)
    item["_packageManifest"] = packaged["_packageManifest"]
    item["_packagePath"] = str(destination)
    item["icon"] = packaged.get("icon") or item.get("icon") or ""
    item["brandColor"] = packaged.get("brandColor") or item.get("brandColor") or "#2563EB"
    _prepare_packaged_plugin(item, destination)
    plugin = item.get("plugin") if isinstance(item.get("plugin"), dict) else {}
    installed: List[str] = []
    try:
        for component_id in list(plugin.get("skills") or []) + list(plugin.get("mcpServers") or []):
            install_item(str(component_id), owner=str(item["id"]), direct=False)
            installed.append(str(component_id))
    except Exception:
        for component_id in reversed(installed):
            uninstall_item(component_id, owner=str(item["id"]))
        raise


def _install_packaged_plugin(item: Dict[str, Any], root: Path) -> Dict[str, Any]:
    _prepare_packaged_plugin(item, root)
    return install_item(str(item["id"]))


def _prepare_packaged_plugin(item: Dict[str, Any], root: Path) -> None:
    components: List[str] = []
    manifest = item["_packageManifest"]
    raw_skills = manifest.get("skills", "./skills")
    skill_paths: List[str] = []
    if isinstance(raw_skills, str):
        skill_paths = [raw_skills]
    elif isinstance(raw_skills, list):
        for value in raw_skills:
            if isinstance(value, str):
                skill_paths.append(value)
            elif isinstance(value, dict) and value.get("path"):
                skill_paths.append(str(value["path"]))
    skill_dirs: List[Path] = []
    seen_skill_dirs: set[Path] = set()
    for skill_path in skill_paths:
        skills_dir = _safe_package_path(root, skill_path)
        if not skills_dir.is_dir():
            continue
        candidates = [skills_dir] if (skills_dir / "SKILL.md").is_file() else [
            path for path in skills_dir.iterdir() if path.is_dir() and (path / "SKILL.md").is_file()
        ]
        for skill_dir in candidates:
            resolved = skill_dir.resolve(strict=False)
            if resolved not in seen_skill_dirs:
                seen_skill_dirs.add(resolved)
                skill_dirs.append(skill_dir)
    for skill_dir in skill_dirs:
        component_id = f"{item['id']}:skill:{_safe_id(skill_dir.name)}"
        component = {
            "id": component_id, "kind": "skill", "name": skill_dir.name, "version": item["version"],
            "description": item["description"], "publisher": item["publisher"], "category": item["category"],
            "brandColor": item.get("brandColor", "#2563EB"), "source": {"type": "local", "path": str(skill_dir), "installName": skill_dir.name},
        }
        _DYNAMIC_ITEMS[component_id] = component
        components.append(component_id)
    mcp_value = manifest.get("mcpServers")
    mcp_data: Dict[str, Any] = {}
    if isinstance(mcp_value, str):
        mcp_path = _safe_package_path(root, mcp_value)
        if mcp_path.is_file():
            loaded = json.loads(mcp_path.read_text(encoding="utf-8"))
            mcp_data = loaded.get("mcpServers", loaded) if isinstance(loaded, dict) else {}
    elif isinstance(mcp_value, dict):
        mcp_data = mcp_value
    for server_name, entry in mcp_data.items():
        if not isinstance(entry, dict):
            continue
        component_id = f"{item['id']}:mcp:{_safe_id(str(server_name))}"
        variables = _normalize_variables(entry.get("environmentVariables"))
        if not variables:
            variables = [{"name": key, "required": True, "secret": True} for key in (entry.get("env") or {})]
        component = {
            "id": component_id, "kind": "mcp", "name": str(server_name), "version": item["version"],
            "description": item["description"], "publisher": item["publisher"], "category": item["category"],
            "brandColor": item.get("brandColor", "#7C3AED"),
            "source": {"type": "plugin", "path": str(root)},
            "mcp": {**{key: entry.get(key) for key in ("command", "args", "url") if entry.get(key)}, "serverName": str(server_name), "environmentVariables": variables},
        }
        _DYNAMIC_ITEMS[component_id] = component
        components.append(component_id)
    item["plugin"] = {"skills": [value for value in components if ":skill:" in value], "mcpServers": [value for value in components if ":mcp:" in value]}
    _DYNAMIC_ITEMS[str(item["id"])] = item


def _plugin_item_from_package(root: Path) -> Dict[str, Any]:
    manifest_path = root / ".codex-plugin" / "plugin.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MarketplaceError(f"Invalid plugin manifest: {exc}") from exc
    name = _safe_id(str(manifest.get("name") or root.name))
    version = str(manifest.get("version") or "0.0.0")
    if not _SEMVER_RE.match(version):
        raise MarketplaceError("Plugin version must use semantic versioning")
    interface = manifest.get("interface") if isinstance(manifest.get("interface"), dict) else {}
    author = manifest.get("author") if isinstance(manifest.get("author"), dict) else {}
    icon = _package_asset_data_url(root, str(interface.get("logo") or interface.get("composerIcon") or ""))
    return {
        "id": f"plugin:{name}", "kind": "plugin", "name": str(interface.get("displayName") or name),
        "version": version, "description": str(interface.get("shortDescription") or manifest.get("description") or name),
        "publisher": str(interface.get("developerName") or author.get("name") or "Custom"),
        "category": str(interface.get("category") or "Plugin"), "brandColor": str(interface.get("brandColor") or "#2563EB"),
        "icon": icon, "source": {"type": "plugin", "path": str(root)}, "_packageManifest": manifest,
    }


def _package_asset_data_url(root: Path, value: str) -> str:
    if not value:
        return ""
    target = _safe_package_path(root, value)
    try:
        if not target.is_file() or target.stat().st_size > 512_000:
            return ""
        mime = mimetypes.guess_type(target.name)[0] or ""
        if mime not in {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}:
            return ""
        content = target.read_bytes()
        if mime == "image/svg+xml" and not _safe_svg(content):
            return ""
        return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"
    except OSError:
        return ""


def _item_needs_setup(item: Dict[str, Any], saved: Dict[str, Any]) -> bool:
    if item.get("kind") == "plugin":
        state = _read_state()["items"]
        ids = list((item.get("plugin") or {}).get("mcpServers", []))
        return any(_item_needs_setup(_raw_item(str(item_id)), state.get(str(item_id), {})) for item_id in ids)
    if item.get("kind") != "mcp":
        return False
    configured = set(saved.get("configuredEnv") or [])
    variables = _normalize_variables((item.get("mcp") or {}).get("environmentVariables"))
    return any(variable["required"] and not variable.get("default") and variable["name"] not in configured and not os.environ.get(variable["name"]) for variable in variables)


def _configure_mcp_entry(item: Dict[str, Any], values: Dict[str, Any]) -> None:
    server_name = _mcp_server_name(item)
    data = _read_mcp_config()
    entry = data["mcpServers"].get(server_name)
    if not isinstance(entry, dict):
        raise MarketplaceError("MCP server config is missing")
    variables = {row["name"]: row for row in _normalize_variables((item.get("mcp") or {}).get("environmentVariables"))}
    public_env = dict(entry.get("env") or {})
    for name, value in values.items():
        variable = variables.get(str(name))
        if variable and not variable["secret"]:
            public_env[str(name)] = str(value)
    if public_env:
        entry["env"] = public_env
    data["mcpServers"][server_name] = entry
    _write_mcp_config(data)


def _set_skill_enabled(item: Dict[str, Any], enabled: bool) -> None:
    skill = resolve_skill_by_id(_component_name(item))
    if skill is None:
        raise MarketplaceError("Installed skill is missing")
    marker = skill_disabled_marker(Path(skill.directory))
    if enabled:
        marker.unlink(missing_ok=True)
    else:
        marker.touch(exist_ok=True)


def _set_mcp_enabled(item: Dict[str, Any], enabled: bool) -> None:
    data = _read_mcp_config()
    server_name = _mcp_server_name(item)
    entry = data["mcpServers"].get(server_name)
    if not isinstance(entry, dict):
        raise MarketplaceError("Installed MCP server is missing")
    entry["disabled"] = not enabled
    data["mcpServers"][server_name] = entry
    _write_mcp_config(data)
    from backend.runtime.tool_registry import reload_mcp_tools
    result = reload_mcp_tools(config_path=str(metis_path("mcp.json")))
    if not result.get("ok"):
        raise MarketplaceError(str(result.get("error") or "MCP reload failed"))


def _remove_skill(item: Dict[str, Any]) -> None:
    skill = resolve_skill_by_id(_component_name(item))
    if skill and skill.source != "project":
        _safe_remove_tree(Path(skill.directory), global_skills_root())


def _remove_mcp(item: Dict[str, Any]) -> None:
    data = _read_mcp_config()
    data["mcpServers"].pop(_mcp_server_name(item), None)
    _write_mcp_config(data)
    from backend.runtime.tool_registry import reload_mcp_tools
    reload_mcp_tools(config_path=str(metis_path("mcp.json")))


def _read_mcp_config() -> Dict[str, Any]:
    path = metis_path("mcp.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not isinstance(data.get("mcpServers"), dict):
        data["mcpServers"] = {}
    return data


def _write_mcp_config(data: Dict[str, Any]) -> None:
    path = metis_path("mcp.json")
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _write_mcp_entry(name: str, entry: Dict[str, Any]) -> None:
    data = _read_mcp_config()
    data["mcpServers"][name] = entry
    _write_mcp_config(data)


def _component_name(item: Dict[str, Any]) -> str:
    source = item.get("source") if isinstance(item.get("source"), dict) else {}
    return _safe_fs_name(str(source.get("installName") or item.get("skillName") or item.get("id") or item.get("name")), max_length=64)


def _mcp_server_name(item: Dict[str, Any]) -> str:
    return _safe_id(str((item.get("mcp") or {}).get("serverName") or item.get("id") or item.get("name"))).replace("/", "-")[-64:]


def _safe_id(value: str) -> str:
    cleaned = _ID_RE.sub("-", str(value or "").strip()).strip("-./")
    return cleaned[:160] or "extension"


def _safe_fs_name(value: str, *, max_length: int = 96) -> str:
    cleaned = _FS_UNSAFE_RE.sub("-", str(value or "").strip()).strip(" .-")
    if not cleaned:
        cleaned = "extension"
    if cleaned.split(".", 1)[0].upper() in _WINDOWS_RESERVED_NAMES:
        cleaned = f"_{cleaned}"
    return cleaned[:max(1, max_length)].rstrip(" .") or "extension"


def _safe_remove_tree(path: Path, root: Path) -> None:
    target = path.resolve(strict=False)
    allowed = root.resolve(strict=False)
    if target == allowed or allowed not in target.parents:
        raise MarketplaceError("Refusing to remove a path outside the extension root")
    shutil.rmtree(target, ignore_errors=True)


def _safe_package_path(root: Path, value: str) -> Path:
    relative = str(value or "").replace("\\", "/")
    while relative.startswith("./"):
        relative = relative[2:]
    target = (root / relative).resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    if target != resolved_root and resolved_root not in target.parents:
        raise MarketplaceError("Plugin path escapes the package root")
    return target


def _find_skill_root(root: Path) -> Optional[Path]:
    if (root / "SKILL.md").is_file():
        return root
    return next((path for path in root.iterdir() if path.is_dir() and (path / "SKILL.md").is_file()), None)


def _find_plugin_root(root: Path) -> Optional[Path]:
    if (root / ".codex-plugin" / "plugin.json").is_file():
        return root
    return next((path for path in root.iterdir() if path.is_dir() and (path / ".codex-plugin" / "plugin.json").is_file()), None)


@contextmanager
def _staged_source(source: str) -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="metis_marketplace_") as temp:
        target = Path(temp) / "source"
        parsed = urlparse(source)
        staged_root = target
        if parsed.scheme in {"http", "https"} and "github.com" in parsed.netloc and "/tree/" in parsed.path:
            parts = [part for part in parsed.path.strip("/").split("/") if part]
            if len(parts) < 4:
                raise MarketplaceError("Invalid GitHub tree URL")
            owner, repo, _tree, branch, *subpath = parts
            if _sparse_checkout_github_tree(owner, repo, branch, subpath, target):
                staged_root = target.joinpath(*subpath) if subpath else target
            else:
                archive_url = f"https://github.com/{owner}/{repo}/archive/refs/heads/{branch}.zip"
                archive = Path(temp) / "download.zip"
                with urlopen(Request(archive_url, headers={"User-Agent": "Metis/1.0"}), timeout=120) as response:
                    _download_limited(response, archive)
                _safe_extract_zip(archive, target)
                extracted = next((path for path in target.iterdir() if path.is_dir()), target)
                staged_root = extracted.joinpath(*subpath) if subpath else extracted
            if not staged_root.is_dir():
                raise MarketplaceError("GitHub tree path was not found in the downloaded repository")
        elif parsed.scheme in {"http", "https"} and (source.endswith(".git") or "github.com" in parsed.netloc):
            subprocess.run(["git", "clone", "--depth", "1", source, str(target)], check=True, capture_output=True, text=True, timeout=180)
        elif parsed.scheme in {"http", "https"}:
            archive = Path(temp) / "download.zip"
            with urlopen(Request(source, headers={"User-Agent": "Metis/1.0"}), timeout=120) as response:
                _download_limited(response, archive)
            _safe_extract_zip(archive, target)
        else:
            local = Path(source).expanduser().resolve(strict=False)
            if local.is_dir():
                _validate_staged_tree(local)
                shutil.copytree(local, target)
            elif local.is_file() and local.suffix.lower() == ".zip":
                _safe_extract_zip(local, target)
            else:
                raise MarketplaceError(f"Unsupported source: {source}")
        _validate_staged_tree(staged_root)
        yield staged_root


def _sparse_checkout_github_tree(owner: str, repo: str, branch: str, subpath: List[str], target: Path) -> bool:
    relative = "/".join(subpath)
    if not relative or any(part in {"", ".", ".."} for part in subpath):
        return False
    repository = f"https://github.com/{owner}/{repo.removesuffix('.git')}.git"
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--filter=blob:none", "--sparse", "--branch", branch, repository, str(target)],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        subprocess.run(
            ["git", "-C", str(target), "sparse-checkout", "set", "--no-cone", relative],
            check=True,
            capture_output=True,
            text=True,
            timeout=180,
        )
        return True
    except (OSError, subprocess.SubprocessError):
        if target.exists():
            _safe_remove_tree(target, target.parent)
        return False


def _validate_staged_tree(root: Path) -> None:
    files = 0
    total_size = 0
    for path in root.rglob("*"):
        if path.is_symlink():
            raise MarketplaceError("Extension source contains a symbolic link")
        if not path.is_file():
            continue
        files += 1
        if files > _MAX_ZIP_FILES:
            raise MarketplaceError("Extension source contains too many files")
        size = path.stat().st_size
        if size > _MAX_ZIP_MEMBER_BYTES:
            raise MarketplaceError("Extension source contains an oversized file")
        total_size += size
        if total_size > _MAX_ZIP_UNCOMPRESSED_BYTES:
            raise MarketplaceError("Extension source exceeds the installation size limit")


def _safe_extract_zip(archive: Path, destination: Path) -> None:
    try:
        archive_size = archive.stat().st_size
    except OSError as exc:
        raise MarketplaceError(f"Unable to inspect ZIP archive: {exc}") from exc
    if archive_size > _MAX_ARCHIVE_BYTES:
        raise MarketplaceError("Extension archive exceeds the compressed size limit")
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve(strict=False)
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if len(members) > _MAX_ZIP_FILES:
            raise MarketplaceError("Extension archive contains too many files")
        total_size = 0
        for member in members:
            if member.file_size > _MAX_ZIP_MEMBER_BYTES:
                raise MarketplaceError("Extension archive contains an oversized file")
            total_size += member.file_size
            if total_size > _MAX_ZIP_UNCOMPRESSED_BYTES:
                raise MarketplaceError("Extension archive exceeds the extraction size limit")
            target = (destination / member.filename).resolve(strict=False)
            if target != root and root not in target.parents:
                raise MarketplaceError("ZIP archive contains an unsafe path")
        package.extractall(destination)


def _download_limited(response: Any, destination: Path) -> None:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > _MAX_ARCHIVE_BYTES:
                raise MarketplaceError("Extension archive exceeds the compressed size limit")
        except ValueError:
            pass
    written = 0
    with destination.open("wb") as stream:
        while True:
            chunk = response.read(_DOWNLOAD_CHUNK_BYTES)
            if not chunk:
                break
            written += len(chunk)
            if written > _MAX_ARCHIVE_BYTES:
                raise MarketplaceError("Extension archive exceeds the compressed size limit")
            stream.write(chunk)


def _safe_svg(content: bytes) -> bool:
    try:
        text = content.decode("utf-8", errors="strict").lower()
    except UnicodeDecodeError:
        return False
    forbidden = ("<script", "<foreignobject", "javascript:", " onload=", " onerror=", "href=\"http", "href='http")
    return not any(token in text for token in forbidden)


__all__ = [
    "MarketplaceError", "configure_item", "get_item", "install_item", "install_source",
    "list_catalog", "load_manifest", "search_mcp_registry", "set_item_enabled", "uninstall_item", "validate_manifest",
]

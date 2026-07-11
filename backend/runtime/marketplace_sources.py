"""Remote marketplace source adapters and local catalog cache."""
from __future__ import annotations

import base64
import json
import mimetypes
import re
import subprocess
import tempfile
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import urlparse
from urllib.request import Request, urlopen

from backend.core.paths import metis_dir, metis_path
from backend.runtime.skill_loader import parse_frontmatter


SOURCE_SCHEMA = "metis.marketplace.sources.v1"
CATALOG_SCHEMA = "metis.marketplace.v1"
_SAFE_ID_RE = re.compile(r"[^a-zA-Z0-9._-]+")
_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+(?:[-+][0-9A-Za-z.-]+)?$")
_MAX_ARCHIVE_BYTES = 512 * 1024 * 1024
_MAX_ZIP_FILES = 50_000
_MAX_ZIP_UNCOMPRESSED_BYTES = 2 * 1024 * 1024 * 1024
_MAX_ZIP_MEMBER_BYTES = 256 * 1024 * 1024
_DOWNLOAD_CHUNK_BYTES = 1024 * 1024
_MAX_DETAIL_CONTENT_CHARS = 80_000

DEFAULT_SOURCES: List[Dict[str, Any]] = [
    {
        "id": "openai-plugins",
        "name": "OpenAI 官方",
        "adapter": "openai",
        "repository": "https://github.com/openai/plugins",
        "manifestUrl": "https://raw.githubusercontent.com/openai/plugins/main/.agents/plugins/marketplace.json",
        "ref": "main",
        "trust": "official",
        "brandColor": "#10A37F",
        "builtin": True,
        "enabled": True,
    },
    {
        "id": "anthropic-skills",
        "name": "Anthropic 官方",
        "adapter": "anthropic",
        "repository": "https://github.com/anthropics/skills",
        "manifestUrl": "https://raw.githubusercontent.com/anthropics/skills/main/.claude-plugin/marketplace.json",
        "ref": "main",
        "trust": "official",
        "brandColor": "#D97757",
        "builtin": True,
        "enabled": True,
    },
]


class MarketplaceSourceError(RuntimeError):
    pass


def sources_state_path() -> Path:
    return metis_path("marketplace-sources.json")


def sources_cache_root() -> Path:
    return metis_dir("marketplace-cache")


def _read_state() -> Dict[str, Any]:
    try:
        data = json.loads(sources_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("schema", SOURCE_SCHEMA)
    if not isinstance(data.get("sources"), dict):
        data["sources"] = {}
    return data


def _write_state(data: Dict[str, Any]) -> None:
    path = sources_state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _all_sources() -> List[Dict[str, Any]]:
    state = _read_state()
    rows = [dict(source) for source in DEFAULT_SOURCES]
    known = {row["id"] for row in rows}
    for source_id, saved in state["sources"].items():
        if not isinstance(saved, dict):
            continue
        if source_id in known:
            row = next(item for item in rows if item["id"] == source_id)
            row.update(saved)
        else:
            rows.append({"id": source_id, **saved})
    return rows


def list_sources() -> Dict[str, Any]:
    state = _read_state()
    rows: List[Dict[str, Any]] = []
    for source in _all_sources():
        saved = state["sources"].get(source["id"], {})
        cached = _read_cache(str(source["id"]))
        rows.append(
            {
                **source,
                "lastRefreshedAt": float(saved.get("lastRefreshedAt") or cached.get("refreshedAt") or 0),
                "revision": str(saved.get("revision") or cached.get("revision") or ""),
                "itemCount": len(cached.get("items") or []),
                "error": str(saved.get("error") or ""),
            }
        )
    return {"schema": SOURCE_SCHEMA, "sources": rows}


def add_source(name: str, url: str, adapter: str = "") -> Dict[str, Any]:
    raw_url = str(url or "").strip()
    if not raw_url.startswith(("http://", "https://")):
        raise MarketplaceSourceError("Marketplace source must be an HTTP(S) URL")
    inferred = str(adapter or "").strip().lower()
    if not inferred:
        inferred = "openai" if "github.com/openai/plugins" in raw_url else "anthropic" if "github.com/anthropics/skills" in raw_url else "metis"
    if inferred not in {"openai", "anthropic", "metis"}:
        raise MarketplaceSourceError(f"Unsupported marketplace adapter: {inferred}")
    source_name = str(name or "").strip() or urlparse(raw_url).netloc
    source_id = _safe_id(source_name)
    state = _read_state()
    if source_id in {row["id"] for row in DEFAULT_SOURCES}:
        raise MarketplaceSourceError("A built-in marketplace source already uses that name")
    state["sources"][source_id] = {
        "name": source_name,
        "adapter": inferred,
        "repository": raw_url if "github.com" in raw_url and not raw_url.endswith(".json") else "",
        "manifestUrl": raw_url,
        "ref": "main",
        "trust": "community",
        "brandColor": "#64748B",
        "builtin": False,
        "enabled": True,
    }
    _write_state(state)
    return next(row for row in list_sources()["sources"] if row["id"] == source_id)


def delete_source(source_id: str) -> None:
    target = str(source_id or "").strip()
    if target in {row["id"] for row in DEFAULT_SOURCES}:
        raise MarketplaceSourceError("Built-in marketplace sources cannot be removed")
    state = _read_state()
    if target not in state["sources"]:
        raise MarketplaceSourceError("Marketplace source not found")
    state["sources"].pop(target, None)
    _write_state(state)
    _cache_path(target).unlink(missing_ok=True)


def refresh_source(source_id: str) -> Dict[str, Any]:
    source = next((row for row in _all_sources() if row["id"] == source_id), None)
    if source is None:
        raise MarketplaceSourceError("Marketplace source not found")
    state = _read_state()
    saved = state["sources"].setdefault(source_id, {})
    try:
        adapter = str(source.get("adapter") or "metis")
        if adapter == "openai":
            items, revision = _refresh_openai(source)
        elif adapter == "anthropic":
            items, revision = _refresh_anthropic(source)
        else:
            items, revision = _refresh_metis(source)
        now = time.time()
        _write_cache(source_id, {"schema": CATALOG_SCHEMA, "source": source_id, "revision": revision, "refreshedAt": now, "items": items})
        saved.update({"lastRefreshedAt": now, "revision": revision, "error": ""})
        _write_state(state)
    except Exception as exc:  # noqa: BLE001
        saved["error"] = str(exc)
        _write_state(state)
        raise MarketplaceSourceError(f"Failed to refresh {source.get('name')}: {exc}") from exc
    return next(row for row in list_sources()["sources"] if row["id"] == source_id)


def load_source_items() -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for source in _all_sources():
        if not source.get("enabled", True):
            continue
        cached = _read_cache(str(source["id"]))
        for item in cached.get("items") or []:
            if isinstance(item, dict):
                rows.append(dict(item))
    return rows


def _refresh_openai(source: Dict[str, Any]) -> tuple[List[Dict[str, Any]], str]:
    with _repository_snapshot(source) as root:
        manifest = _read_json(root / ".agents" / "plugins" / "marketplace.json")
        revision = _repository_revision(source)
        items: List[Dict[str, Any]] = []
        for position, entry in enumerate(manifest.get("plugins") or []):
            if not isinstance(entry, dict):
                continue
            local_source = entry.get("source") if isinstance(entry.get("source"), dict) else {}
            plugin_path = str(local_source.get("path") or "").removeprefix("./")
            plugin_root = _safe_child(root, plugin_path)
            manifest_path = plugin_root / ".codex-plugin" / "plugin.json"
            if not manifest_path.is_file():
                continue
            plugin = _read_json(manifest_path)
            interface = plugin.get("interface") if isinstance(plugin.get("interface"), dict) else {}
            author = plugin.get("author") if isinstance(plugin.get("author"), dict) else {}
            name = str(plugin.get("name") or entry.get("name") or plugin_root.name)
            version = str(plugin.get("version") or "0.0.0")
            if not _SEMVER_RE.match(version):
                version = "0.0.0"
            icon = _asset_data_url(plugin_root, str(interface.get("logo") or interface.get("composerIcon") or ""))
            items.append(
                {
                    "id": f"openai:{_safe_id(name)}",
                    "kind": "plugin",
                    "name": str(interface.get("displayName") or name.replace("-", " ").title()),
                    "version": version,
                    "description": str(interface.get("shortDescription") or plugin.get("description") or name),
                    "content": _plugin_detail_content(plugin_root, plugin, interface),
                    "publisher": str(interface.get("developerName") or author.get("name") or "OpenAI"),
                    "category": str(interface.get("category") or entry.get("category") or "Plugins"),
                    "featured": position < 12,
                    "brandColor": str(interface.get("brandColor") or source.get("brandColor") or "#10A37F"),
                    "icon": icon,
                    "license": str(plugin.get("license") or ""),
                    "homepage": str(plugin.get("homepage") or interface.get("websiteURL") or ""),
                    "revision": revision,
                    "trust": source.get("trust", "official"),
                    "marketplaceName": source.get("name", "OpenAI 官方"),
                    "source": {
                        "type": "remote-plugin",
                        "marketplace": source["id"],
                        "url": f"{str(source['repository']).rstrip('/')}/tree/{source.get('ref', 'main')}/{plugin_path}",
                        "repository": source.get("repository", ""),
                        "path": plugin_path,
                        "ref": source.get("ref", "main"),
                        "revision": revision,
                    },
                }
            )
        return items, revision


def _refresh_anthropic(source: Dict[str, Any]) -> tuple[List[Dict[str, Any]], str]:
    with _repository_snapshot(source) as root:
        manifest = _read_json(root / ".claude-plugin" / "marketplace.json")
        revision = _repository_revision(source)
        items: List[Dict[str, Any]] = []
        seen: set[str] = set()
        for group in manifest.get("plugins") or []:
            if not isinstance(group, dict):
                continue
            for raw_path in group.get("skills") or []:
                skill_path = str(raw_path or "").removeprefix("./")
                skill_root = _safe_child(root, skill_path)
                skill_file = skill_root / "SKILL.md"
                if not skill_file.is_file():
                    continue
                content = skill_file.read_text(encoding="utf-8", errors="replace")
                frontmatter, body = parse_frontmatter(content)
                name = str(frontmatter.get("name") or skill_root.name)
                item_id = f"anthropic:{_safe_id(name)}"
                if item_id in seen:
                    continue
                seen.add(item_id)
                interface = frontmatter.get("interface") if isinstance(frontmatter.get("interface"), dict) else {}
                icon_value = str(interface.get("icon_large") or interface.get("icon_small") or frontmatter.get("icon") or "")
                items.append(
                    {
                        "id": item_id,
                        "kind": "skill",
                        "name": name.replace("-", " ").title(),
                        "skillName": name,
                        "version": "0.0.0",
                        "description": str(frontmatter.get("description") or group.get("description") or name),
                        "content": _trim_detail_content(body),
                        "publisher": "Anthropic",
                        "category": str(group.get("name") or "Agent Skills").replace("-", " ").title(),
                        "featured": len(items) < 12,
                        "brandColor": str(interface.get("brand_color") or interface.get("brandColor") or source.get("brandColor") or "#D97757"),
                        "icon": _asset_data_url(skill_root, icon_value),
                        "license": str(frontmatter.get("license") or "See source repository"),
                        "revision": revision,
                        "trust": source.get("trust", "official"),
                        "marketplaceName": source.get("name", "Anthropic 官方"),
                        "source": {
                            "type": "remote-skill",
                            "marketplace": source["id"],
                            "url": f"{str(source['repository']).rstrip('/')}/tree/{source.get('ref', 'main')}/{skill_path}",
                            "repository": source.get("repository", ""),
                            "path": skill_path,
                            "installName": name,
                            "ref": source.get("ref", "main"),
                            "revision": revision,
                        },
                    }
                )
        return items, revision


def _refresh_metis(source: Dict[str, Any]) -> tuple[List[Dict[str, Any]], str]:
    url = str(source.get("manifestUrl") or "")
    request = Request(url, headers={"Accept": "application/json", "User-Agent": "Metis/1.0"})
    with urlopen(request, timeout=30) as response:
        raw = json.loads(response.read().decode("utf-8"))
        revision = str(response.headers.get("ETag") or response.headers.get("Last-Modified") or "")
    if not isinstance(raw, dict) or raw.get("schema") != CATALOG_SCHEMA or not isinstance(raw.get("items"), list):
        raise MarketplaceSourceError("Remote source is not a metis.marketplace.v1 manifest")
    items: List[Dict[str, Any]] = []
    for value in raw["items"]:
        if not isinstance(value, dict):
            continue
        item = dict(value)
        item["id"] = f"{source['id']}:{_safe_id(str(item.get('id') or item.get('name') or 'extension'))}"
        item["marketplaceName"] = source.get("name", source["id"])
        item["trust"] = source.get("trust", "community")
        item["revision"] = revision
        item_source = item.get("source") if isinstance(item.get("source"), dict) else {}
        item["source"] = {**item_source, "marketplace": source["id"]}
        items.append(item)
    return items, revision


class _RepositorySnapshot:
    def __init__(self, source: Dict[str, Any]) -> None:
        self.source = source
        self.temp: tempfile.TemporaryDirectory[str] | None = None
        self.root: Path | None = None

    def __enter__(self) -> Path:
        repository = str(self.source.get("repository") or "").rstrip("/")
        parsed = urlparse(repository)
        parts = [part for part in parsed.path.strip("/").split("/") if part]
        if parsed.netloc.lower() != "github.com" or len(parts) < 2:
            raise MarketplaceSourceError("The OpenAI and Anthropic adapters require a GitHub repository URL")
        owner, repo = parts[0], parts[1].removesuffix(".git")
        ref = str(self.source.get("ref") or "main")
        self.temp = tempfile.TemporaryDirectory(prefix="metis_marketplace_source_")
        temp_root = Path(self.temp.name)
        archive = temp_root / "source.zip"
        target = temp_root / "unzipped"
        url = f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{ref}"
        request = Request(url, headers={"User-Agent": "Metis/1.0"})
        with urlopen(request, timeout=180) as response:
            _download_limited(response, archive)
        _safe_extract_zip(archive, target)
        self.root = next((path for path in target.iterdir() if path.is_dir()), target)
        return self.root

    def __exit__(self, *_: object) -> None:
        if self.temp is not None:
            self.temp.cleanup()


def _repository_snapshot(source: Dict[str, Any]) -> _RepositorySnapshot:
    return _RepositorySnapshot(source)


def _repository_revision(source: Dict[str, Any]) -> str:
    repository = str(source.get("repository") or "")
    ref = str(source.get("ref") or "main")
    try:
        result = subprocess.run(
            ["git", "ls-remote", repository, f"refs/heads/{ref}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
        return result.stdout.strip().split()[0]
    except Exception:  # noqa: BLE001
        return ""


def _cache_path(source_id: str) -> Path:
    return sources_cache_root() / f"{_safe_id(source_id)}.json"


def _read_cache(source_id: str) -> Dict[str, Any]:
    try:
        value = json.loads(_cache_path(source_id).read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _write_cache(source_id: str, value: Dict[str, Any]) -> None:
    path = _cache_path(source_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise MarketplaceSourceError(f"Invalid marketplace file {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise MarketplaceSourceError(f"Marketplace file {path.name} must contain an object")
    return value


def _safe_child(root: Path, relative: str) -> Path:
    target = (root / str(relative or "")).resolve(strict=False)
    resolved_root = root.resolve(strict=False)
    if target != resolved_root and resolved_root not in target.parents:
        raise MarketplaceSourceError("Marketplace path escapes the repository root")
    return target


def _asset_data_url(root: Path, value: str) -> str:
    if not value or value.startswith(("http://", "https://", "data:")):
        return value if value.startswith("data:image/") else ""
    try:
        path = _safe_child(root, value.removeprefix("./"))
        if not path.is_file() or path.stat().st_size > 512_000:
            return ""
        mime = mimetypes.guess_type(path.name)[0] or ""
        if mime not in {"image/png", "image/jpeg", "image/webp", "image/svg+xml"}:
            return ""
        content = path.read_bytes()
        if mime == "image/svg+xml" and not _safe_svg(content):
            return ""
        return f"data:{mime};base64,{base64.b64encode(content).decode('ascii')}"
    except OSError:
        return ""


def _plugin_detail_content(plugin_root: Path, plugin: Dict[str, Any], interface: Dict[str, Any]) -> str:
    sections: List[str] = []
    overview = str(interface.get("longDescription") or plugin.get("description") or "").strip()
    if overview:
        sections.append(overview)

    raw_skills = plugin.get("skills", "./skills")
    skill_paths: List[str] = []
    if isinstance(raw_skills, str):
        skill_paths.append(raw_skills)
    elif isinstance(raw_skills, list):
        for value in raw_skills:
            if isinstance(value, str):
                skill_paths.append(value)
            elif isinstance(value, dict) and value.get("path"):
                skill_paths.append(str(value["path"]))

    seen: set[Path] = set()
    for raw_path in skill_paths:
        try:
            skill_root = _safe_child(plugin_root, raw_path.removeprefix("./"))
        except MarketplaceSourceError:
            continue
        candidates = [skill_root / "SKILL.md"] if (skill_root / "SKILL.md").is_file() else sorted(skill_root.glob("*/SKILL.md"))
        for skill_file in candidates:
            resolved = skill_file.resolve(strict=False)
            if resolved in seen:
                continue
            seen.add(resolved)
            try:
                raw_content = skill_file.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            frontmatter, body = parse_frontmatter(raw_content)
            body = body.strip()
            if not body:
                title = str(frontmatter.get("name") or skill_file.parent.name).replace("-", " ").title()
                description = str(frontmatter.get("description") or "").strip()
                body = f"## {title}\n\n{description}".strip()
            if body:
                sections.append(body)
    return _trim_detail_content("\n\n---\n\n".join(sections))


def _trim_detail_content(value: str) -> str:
    content = str(value or "").strip()
    if len(content) <= _MAX_DETAIL_CONTENT_CHARS:
        return content
    return f"{content[:_MAX_DETAIL_CONTENT_CHARS].rstrip()}\n\n…"


def _safe_extract_zip(archive: Path, destination: Path) -> None:
    try:
        archive_size = archive.stat().st_size
    except OSError as exc:
        raise MarketplaceSourceError(f"Unable to inspect ZIP archive: {exc}") from exc
    if archive_size > _MAX_ARCHIVE_BYTES:
        raise MarketplaceSourceError("Marketplace archive exceeds the compressed size limit")
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve(strict=False)
    with zipfile.ZipFile(archive) as package:
        members = package.infolist()
        if len(members) > _MAX_ZIP_FILES:
            raise MarketplaceSourceError("Marketplace archive contains too many files")
        total_size = 0
        for member in members:
            if member.file_size > _MAX_ZIP_MEMBER_BYTES:
                raise MarketplaceSourceError("Marketplace archive contains an oversized file")
            total_size += member.file_size
            if total_size > _MAX_ZIP_UNCOMPRESSED_BYTES:
                raise MarketplaceSourceError("Marketplace archive exceeds the extraction size limit")
            target = (destination / member.filename).resolve(strict=False)
            if target != root and root not in target.parents:
                raise MarketplaceSourceError("ZIP archive contains an unsafe path")
        package.extractall(destination)


def _download_limited(response: Any, destination: Path) -> None:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > _MAX_ARCHIVE_BYTES:
                raise MarketplaceSourceError("Marketplace archive exceeds the compressed size limit")
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
                raise MarketplaceSourceError("Marketplace archive exceeds the compressed size limit")
            stream.write(chunk)


def _safe_svg(content: bytes) -> bool:
    text = content.decode("utf-8", errors="ignore").lower()
    return not any(token in text for token in ("<script", "javascript:", "onload=", "onerror="))


def _safe_id(value: str) -> str:
    return _SAFE_ID_RE.sub("-", str(value or "").strip()).strip("-._")[:96] or "marketplace"


__all__ = [
    "MarketplaceSourceError",
    "add_source",
    "delete_source",
    "list_sources",
    "load_source_items",
    "refresh_source",
]

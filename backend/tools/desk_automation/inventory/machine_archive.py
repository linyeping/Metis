# -*- coding: utf-8 -*-
"""本机路径浏览缓存：同一路径默认不重复全盘扫描，按 TTL + 随机抽样偶尔刷新。

存档文件位于 ~/.miro/，文件名含 Windows MachineGuid（或回退指纹），便于多机区分、发给朋友时辨认是哪台电脑的数据。
JSON 一律 UTF-8，避免中文路径/文件名在 Windows 下与 GBK 控制台混淆（业务数据与控制台编码无关）。
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
import socket
import time
import uuid
from pathlib import Path
from typing import Any

# Windows 路径（含中文目录名）
_WIN_PATH_RE = re.compile(
    r"(?:[A-Za-z]:\\|\\\\[^\\/:\s]+\\[^\\/:\s]+\\)"
    r"(?:[^\\/:*?\"<>|\r\n]+\\)*"
    r"[^\\/:*?\"<>|\r\n]*"
)


def get_machine_file_token() -> tuple[str, str]:
    """返回 (用于文件名的安全 token, 人类可读说明)。"""
    guid = _read_windows_machine_guid()
    if guid:
        safe = re.sub(r"[^\w\-]", "_", guid.strip())
        return safe, f"MachineGuid={guid}"
    node = f"{socket.gethostname()}|{uuid.getnode()}"
    h = hashlib.sha256(node.encode("utf-8", errors="replace")).hexdigest()[:24]
    return h, f"fallback_sha256_24={h}"


def archive_json_path() -> Path:
    token, _ = get_machine_file_token()
    base = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~")) / ".miro"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"desk_path_cache_{token}.json"


def _read_windows_machine_guid() -> str | None:
    if os.name != "nt":
        return None
    try:
        import winreg  # type: ignore[import-not-found]

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SOFTWARE\Microsoft\Cryptography",
        ) as k:
            v, _ = winreg.QueryValueEx(k, "MachineGuid")
            if isinstance(v, str) and v.strip():
                return v.strip()
    except OSError:
        pass
    return None


def _load_doc() -> dict[str, Any]:
    p = archive_json_path()
    if not p.is_file():
        return {"version": 1, "paths": {}}
    try:
        raw = p.read_text(encoding="utf-8")
        doc = json.loads(raw)
    except (json.JSONDecodeError, OSError):
        return {"version": 1, "paths": {}}
    if not isinstance(doc, dict):
        return {"version": 1, "paths": {}}
    doc.setdefault("version", 1)
    doc.setdefault("paths", {})
    if not isinstance(doc["paths"], dict):
        doc["paths"] = {}
    return doc


def _save_doc(doc: dict[str, Any]) -> None:
    p = archive_json_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(
        json.dumps(doc, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    tmp.replace(p)


def _normalize_path(path: str) -> str | None:
    try:
        p = Path(path).expanduser()
        if not p.exists():
            return None
        return str(p.resolve())
    except OSError:
        return None


def _scan_dir_listing(norm: str, max_entries: int = 200) -> dict[str, Any]:
    """目录下第一层名称列表（不递归），超限截断。"""
    root = Path(norm)
    if not root.is_dir():
        return {"error": "not_a_directory", "dirs": [], "files": []}
    dirs: list[str] = []
    files: list[str] = []
    try:
        with os.scandir(root) as it:
            for ent in it:
                if len(dirs) + len(files) >= max_entries:
                    break
                try:
                    name = ent.name
                except OSError:
                    continue
                if ent.is_dir(follow_symlinks=False):
                    dirs.append(name)
                elif ent.is_file(follow_symlinks=False):
                    files.append(name)
    except OSError as e:
        return {"error": str(e), "dirs": [], "files": []}
    dirs.sort(key=lambda s: s.lower())
    files.sort(key=lambda s: s.lower())
    return {"dirs": dirs, "files": files, "truncated": len(dirs) + len(files) >= max_entries}


def extract_paths_from_text(text: str) -> list[str]:
    """从自然语言中提取可能的 Windows 绝对路径，并展开「桌面/文档」相对片段。"""
    found = list(dict.fromkeys(_WIN_PATH_RE.findall(text)))
    home = Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))

    def _try_add(p: Path) -> None:
        try:
            if p.exists():
                s = str(p.resolve())
                if s not in found:
                    found.append(s)
        except OSError:
            pass

    for m in re.finditer(r"桌面\\([^\\s]+)", text):
        _try_add(home / "Desktop" / m.group(1))
    for m in re.finditer(r"文档\\([^\\s]+)", text):
        _try_add(home / "Documents" / m.group(1))
    return found


def get_archive_status() -> dict[str, Any]:
    token, hint = get_machine_file_token()
    doc = _load_doc()
    return {
        "archive_file": str(archive_json_path()),
        "machine_token": token,
        "machine_hint": hint,
        "path_entries": len(doc.get("paths", {})),
    }


def get_or_refresh_listing(
    path: str,
    *,
    force: bool = False,
    ttl_sec: float | None = None,
    random_refresh_p: float | None = None,
) -> dict[str, Any]:
    """读取目录列表：优先用缓存；过期、随机命中或 force 时重新扫描磁盘。"""
    cfg = _archive_policy()
    ttl = float(ttl_sec if ttl_sec is not None else cfg["cache_ttl_sec"])
    prob = float(random_refresh_p if random_refresh_p is not None else cfg["random_refresh_p"])

    norm = _normalize_path(path)
    if norm is None:
        return {"ok": False, "error": "path_not_found", "path": path}

    doc = _load_doc()
    paths: dict[str, Any] = doc["paths"]
    now = time.time()
    entry = paths.get(norm)
    need_scan = force or entry is None

    if entry and not need_scan:
        updated = float(entry.get("updated_unix", 0))
        age = now - updated
        if age > ttl:
            need_scan = True
        elif prob > 0 and random.random() < prob:
            need_scan = True

    from_cache = not need_scan
    if need_scan:
        listing = _scan_dir_listing(norm)
        note = ""
        if isinstance(entry, dict):
            note = str(entry.get("note", ""))
        paths[norm] = {
            "updated_unix": now,
            "listing": listing,
            "note": note,
        }
        _save_doc(doc)
        entry = paths[norm]
    else:
        entry = paths[norm]

    listing = entry.get("listing", {})
    return {
        "ok": True,
        "path": norm,
        "from_cache": from_cache,
        "updated_unix": entry.get("updated_unix", 0),
        "note": entry.get("note", ""),
        "listing": listing,
    }


def set_path_note(path: str, note: str) -> dict[str, Any]:
    norm = _normalize_path(path)
    if norm is None:
        return {"ok": False, "error": "path_not_found"}
    doc = _load_doc()
    paths = doc["paths"]
    entry = paths.get(norm) or {"updated_unix": 0, "listing": {}, "note": ""}
    entry["note"] = note
    paths[norm] = entry
    _save_doc(doc)
    return {"ok": True, "path": norm}


def _archive_policy() -> dict[str, float]:
    try:
        from .. import config

        raw = config.load_config().get("path_archive") or {}
    except Exception:
        raw = {}
    return {
        "cache_ttl_sec": float(raw.get("cache_ttl_sec", 7200.0)),
        "random_refresh_p": float(raw.get("random_refresh_p", 0.06)),
    }


def build_context_snippet_for_goal(goal: str, max_chars: int = 2800) -> str:
    """把已缓存路径摘要注入多模态 prompt，减少「看不清资源管理器里有什么」导致的乱点。"""
    paths = extract_paths_from_text(goal)
    if not paths:
        return ""
    lines: list[str] = ["## 本机路径缓存（优先参考；若与截图不符以截图为准）"]
    used = 0
    for p in paths[:8]:
        r = get_or_refresh_listing(p, force=False)
        if not r.get("ok"):
            continue
        block = _format_listing_block(r)
        if used + len(block) > max_chars:
            break
        lines.append(block)
        used += len(block)
    if len(lines) <= 1:
        return ""
    return "\n".join(lines)


def _format_listing_block(r: dict[str, Any]) -> str:
    path = r.get("path", "")
    note = str(r.get("note", "")).strip()
    cache = "缓存" if r.get("from_cache") else "刚扫描"
    listing = r.get("listing") or {}
    if listing.get("error"):
        body = f"（无法列出: {listing.get('error')}）"
    else:
        ds = listing.get("dirs") or []
        fs = listing.get("files") or []
        d_part = ", ".join(ds[:40]) if ds else "（无子文件夹）"
        f_part = ", ".join(fs[:40]) if fs else "（无文件）"
        more = ""
        if listing.get("truncated"):
            more = " [条目过多已截断]"
        body = f"文件夹: {d_part}\n文件: {f_part}{more}"
    note_line = f"\n备注: {note}" if note else ""
    return f"- `{path}` [{cache}]{note_line}\n{body}"

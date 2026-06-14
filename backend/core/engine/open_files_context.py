"""
「当前打开文件」上下文（C 风格 IDE 对齐的可落地方案）。

约定（工作区根）：
- **`.metis_open_files.json`**：`{"paths": ["src/a.py", ...], "focus": "可选当前焦点路径"}`
- **`.metis_open_files.txt`**：一行一个路径，`#` 开头为注释

由 IDE 插件、脚本或 Agent 自行维护；无文件则不注入（零噪音）。

P3 HTTP：`web/app.py` POST `/internal/miro/open-files` 写入进程内缓存后，
`open_files_context_block` **优先**使用该缓存（工作区根须与 POST 时绑定根一致），
缓存未命中时再读磁盘（见 `engine/context_builder.py` 注释）。
"""
import json
import os
from typing import Any, List, Optional

from backend.tools.coding.workflow_features.agent_state.state_paths import (
    LEGACY_MIRO_OPEN_FILES_JSON,
    LEGACY_MIRO_OPEN_FILES_TXT,
    METIS_OPEN_FILES_JSON,
    METIS_OPEN_FILES_TXT,
)


def parse_open_files_payload(data: Any) -> tuple[List[str], Optional[str]]:
    """
    与 JSON 文件解析一致：顶层 array → 仅 paths；object → paths + 可选 focus。
    非法结构返回 ([], None)。
    """
    if isinstance(data, list):
        paths = [str(p).strip() for p in data if str(p).strip()]
        return paths, None
    if isinstance(data, dict):
        raw = data.get("paths", [])
        if not isinstance(raw, list):
            return [], None
        paths = [str(p).strip() for p in raw if str(p).strip()]
        focus = data.get("focus")
        focus_s = str(focus).strip() if focus else None
        return paths, focus_s
    return [], None


def _open_files_hint_enabled() -> bool:
    v = (
        os.environ.get("METIS_CONTEXT_OPEN_FILES_HINT")
        or os.environ.get("MIRO_CONTEXT_OPEN_FILES_HINT")
        or "1"
    ).strip().lower()
    return v not in ("0", "false", "no", "off")


def _read_open_files_raw(workspace_root: str) -> tuple[List[str], Optional[str]]:
    """返回 (paths, focus_or_none)。"""
    root = os.path.abspath(workspace_root)
    json_paths = [
        os.path.join(root, METIS_OPEN_FILES_JSON),
        os.path.join(root, LEGACY_MIRO_OPEN_FILES_JSON),
    ]
    txt_paths = [
        os.path.join(root, METIS_OPEN_FILES_TXT),
        os.path.join(root, LEGACY_MIRO_OPEN_FILES_TXT),
    ]

    json_path = next((path for path in json_paths if os.path.isfile(path)), "")
    if json_path:
        try:
            with open(json_path, "r", encoding="utf-8") as f:
                data: Any = json.load(f)
            return parse_open_files_payload(data)
        except Exception:
            return [], None

    txt_path = next((path for path in txt_paths if os.path.isfile(path)), "")
    if txt_path:
        paths: List[str] = []
        try:
            with open(txt_path, "r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    paths.append(s)
            return paths, None
        except Exception:
            return [], None

    return [], None


def _shorten_path(p: str, root: str) -> str:
    ap = os.path.abspath(os.path.join(root, p)) if not os.path.isabs(p) else os.path.abspath(p)
    root_abs = os.path.abspath(root)
    try:
        rel = os.path.relpath(ap, root_abs)
        if not rel.startswith(".."):
            return rel.replace("\\", "/")
    except ValueError:
        pass
    return p.replace("\\", "/")


def open_files_context_block(workspace_root: Optional[str]) -> str:
    if not _open_files_hint_enabled() or not workspace_root:
        return ""

    max_paths = max(1, int(os.environ.get("METIS_CONTEXT_OPEN_FILES_MAX") or os.environ.get("MIRO_CONTEXT_OPEN_FILES_MAX", "40")))
    max_line = max(20, int(os.environ.get("METIS_CONTEXT_OPEN_FILES_LINE_MAX") or os.environ.get("MIRO_CONTEXT_OPEN_FILES_LINE_MAX", "200")))
    total_cap = max(500, int(os.environ.get("METIS_CONTEXT_OPEN_FILES_MAX_CHARS") or os.environ.get("MIRO_CONTEXT_OPEN_FILES_MAX_CHARS", "6000")))

    from .open_files_http_cache import get_http_open_files

    http_snap = get_http_open_files(workspace_root)
    if http_snap is not None:
        paths, focus = http_snap
    else:
        paths, focus = _read_open_files_raw(workspace_root)
    if not paths and not focus:
        return ""

    root_abs = os.path.abspath(workspace_root)
    lines: List[str] = []
    if focus:
        lines.append(f"focus: {_shorten_path(focus, root_abs)[:max_line]}")
    shown = paths[:max_paths]
    for p in shown:
        lines.append(_shorten_path(p, root_abs)[:max_line])
    if len(paths) > max_paths:
        lines.append(f"... (+{len(paths) - max_paths} more paths omitted)")

    body = "\n".join(lines)
    if len(body) > total_cap:
        body = body[:total_cap] + "\n... [open files truncated]\n"

    return "\n---\n[Metis open files]\n" + body + "\n"

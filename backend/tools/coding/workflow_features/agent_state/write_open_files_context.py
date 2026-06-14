"""写入「打开文件」上下文约定文件（供 context_builder 注入 system）。"""
import json
import os
from typing import List, Optional

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution

from backend.tools.coding.workflow_features.agent_state.state_paths import METIS_OPEN_FILES_JSON


def _dedupe_preserve_order(items: List[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for x in items:
        s = str(x).strip()
        if not s or s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


@trace_execution
def write_open_files_context(
    paths: Optional[List[str]] = None,
    focus: Optional[str] = None,
    merge: bool = False,
    path: str = METIS_OPEN_FILES_JSON,
) -> str:
    """
    写入 `.metis_open_files.json`：`{"paths": [...], "focus": "可选"}`。
    merge=True 时与磁盘合并路径（去重保序）；paths 省略或空且 merge=True 时保留原 paths，仅更新 focus（若传入 focus）。
    """
    try:
        paths = paths or []
        existing_paths: List[str] = []
        existing_focus: Optional[str] = None

        if merge and os.path.isfile(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    raw = data.get("paths", [])
                    if isinstance(raw, list):
                        existing_paths = [str(p).strip() for p in raw if str(p).strip()]
                    fc = data.get("focus")
                    if fc is not None:
                        existing_focus = str(fc).strip() or None
                elif isinstance(data, list):
                    existing_paths = [str(p).strip() for p in data if str(p).strip()]
            except Exception:
                pass

        if merge:
            if paths:
                out_paths = _dedupe_preserve_order(existing_paths + paths)
            else:
                out_paths = list(existing_paths)
            if focus is not None:
                out_focus = str(focus).strip() or None
            else:
                out_focus = existing_focus
        else:
            out_paths = _dedupe_preserve_order(paths)
            out_focus = str(focus).strip() if focus is not None and str(focus).strip() else None

        out: dict = {"paths": out_paths}
        if out_focus:
            out["focus"] = out_focus

        with open(path, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False, indent=2)

        return f"✅ 已写入打开文件上下文 ({len(out_paths)} 条路径) → {path}"
    except Exception as e:
        return f"❌ write_open_files_context 失败: {str(e)}"

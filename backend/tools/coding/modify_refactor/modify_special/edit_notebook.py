"""安全修改 Jupyter .ipynb：兼容 C 的 is_new_cell / old_string 块替换 + 原有整 cell 替换。"""
import json
from typing import Any, Dict, List, Optional

from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError, validate_path
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution

_CELL_LANG = {
    "python": "code",
    "py": "code",
    "markdown": "markdown",
    "md": "markdown",
    "raw": "raw",
    "javascript": "code",
    "js": "code",
    "typescript": "code",
    "ts": "code",
    "shell": "code",
    "bash": "code",
    "json": "code",
}


def _source_to_str(source: Any) -> str:
    if isinstance(source, str):
        return source
    if isinstance(source, list):
        return "".join(source)
    return ""


def _set_cell_source(cell: Dict[str, Any], text: str) -> None:
    if not text:
        cell["source"] = []
    elif "\n" in text or text.endswith("\n"):
        cell["source"] = text.splitlines(keepends=True)
    else:
        cell["source"] = [text]


def _resolve_cell_type(
    cell_type: Optional[str], cell_language: Optional[str]
) -> str:
    if cell_type:
        if cell_type not in ("code", "markdown", "raw"):
            raise ValueError(f"不支持的 cell_type: {cell_type}")
        return cell_type
    if cell_language:
        key = cell_language.strip().lower()
        if key not in _CELL_LANG:
            raise ValueError(
                f"不支持的 cell_language: {cell_language!r}；"
                f"可用: {', '.join(sorted(_CELL_LANG))}"
            )
        return _CELL_LANG[key]
    return "code"


@trace_execution
def edit_notebook(
    path: str,
    cell_idx: int,
    new_source: str = "",
    cell_type: Optional[str] = None,
    *,
    is_new_cell: bool = False,
    old_string: Optional[str] = None,
    new_string: Optional[str] = None,
    cell_language: Optional[str] = None,
) -> str:
    """
    编辑 .ipynb。

    - C 模式 A：is_new_cell=true 时在 cell_idx 处插入新 cell（可在末尾用 idx=len(cells)）。
    - C 模式 B：提供 old_string + new_string 且在 cell 内唯一匹配，则做块替换。
    - 兼容模式：未提供 old_string 且 new_source 非空时，整 cell 替换为 new_source。
    """
    if not path.endswith(".ipynb"):
        return "❌ 仅支持 .ipynb 文件"
    try:
        nb_path, _ = validate_path(
            path, must_exist=True, allow_create=False, path_profile="notebook"
        )
    except PathSecurityError as e:
        return str(e)
    path = str(nb_path)
    if not nb_path.is_file():
        return f"❌ 文件不存在: {path}"

    try:
        with open(path, "r", encoding="utf-8") as f:
            nb: Dict[str, Any] = json.load(f)

        cells: List[Dict[str, Any]] = nb.get("cells")
        if not isinstance(cells, list):
            return "❌ 无效的 notebook：缺少 cells 数组"

        # ---- 插入新 cell ----
        if is_new_cell:
            try:
                ctype = _resolve_cell_type(cell_type, cell_language)
            except ValueError as e:
                return f"❌ {e}"
            body = new_string if new_string is not None else new_source
            new_cell: Dict[str, Any] = {
                "cell_type": ctype,
                "metadata": {},
                "source": [],
            }
            if ctype == "code":
                new_cell["outputs"] = []
                new_cell["execution_count"] = None
            _set_cell_source(new_cell, body or "")
            insert_at = min(max(0, cell_idx), len(cells))
            cells.insert(insert_at, new_cell)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(nb, f, ensure_ascii=False, indent=1)
                f.write("\n")
            return f"✅ 已在 {path} 的索引 {insert_at} 插入新 {ctype} cell"

        if cell_idx < 0 or cell_idx >= len(cells):
            return f"❌ cell_idx 越界: {cell_idx}（共 {len(cells)} 个 cell）"

        cell = cells[cell_idx]

        # ---- 块替换（C：old_string 唯一）----
        if old_string is not None and new_string is not None:
            if old_string == "":
                return "❌ old_string 不能为空；若需清空 cell 请用整 cell 替换并传 new_source"
            current = _source_to_str(cell.get("source"))
            if old_string not in current:
                return "❌ old_string 在当前 cell 中未找到，请检查索引与上下文"
            n = current.count(old_string)
            if n > 1:
                return (
                    f"❌ old_string 在当前 cell 中出现 {n} 次，请扩大上下文使片段唯一（C 规则）"
                )
            _set_cell_source(cell, current.replace(old_string, new_string, 1))
            if cell_type:
                if cell_type not in ("code", "markdown", "raw"):
                    return f"❌ 不支持的 cell_type: {cell_type}"
                cell["cell_type"] = cell_type
            elif cell_language:
                try:
                    cell["cell_type"] = _resolve_cell_type(None, cell_language)
                except ValueError as e:
                    return f"❌ {e}"
            with open(path, "w", encoding="utf-8") as f:
                json.dump(nb, f, ensure_ascii=False, indent=1)
                f.write("\n")
            return f"✅ 已在 {path} 第 {cell_idx} 个 cell 内完成块替换"

        # ---- 整 cell 替换（历史行为）----
        if cell_type:
            if cell_type not in ("code", "markdown", "raw"):
                return f"❌ 不支持的 cell_type: {cell_type}"
            cell["cell_type"] = cell_type
        elif cell_language:
            try:
                cell["cell_type"] = _resolve_cell_type(None, cell_language)
            except ValueError as e:
                return f"❌ {e}"

        if new_source:
            _set_cell_source(cell, new_source)
            with open(path, "w", encoding="utf-8") as f:
                json.dump(nb, f, ensure_ascii=False, indent=1)
                f.write("\n")
            return f"✅ 已更新 {path} 第 {cell_idx} 个 cell（整 cell 替换）"

        return "❌ 请提供 is_new_cell=true，或 old_string+new_string，或非空 new_source"
    except Exception as e:
        return f"❌ 编辑 notebook 失败: {str(e)}"

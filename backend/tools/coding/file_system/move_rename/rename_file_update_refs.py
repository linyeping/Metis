"""移动/重命名文件并在文本文件中做保守的字符串引用替换。"""
import shutil
from pathlib import Path
from typing import List, Optional, Set, Tuple

from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError, validate_path
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution

_TEXT_EXT = {
    ".py", ".ts", ".tsx", ".js", ".jsx", ".json", ".md", ".yaml", ".yml",
    ".txt", ".html", ".css", ".vue", ".java", ".kt", ".go", ".rs", ".c", ".h",
    ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift",
}


def _try_rel(p: Path, root: Path) -> Optional[str]:
    try:
        return p.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return None


@trace_execution
def rename_file_update_refs(
    old_path: str,
    new_path: str,
    *,
    workspace_root: str = ".",
    update_imports: bool = True,
    update_strings: bool = False,
    include_globs: Optional[Tuple[str, ...]] = None,
    exclude_globs: Optional[Tuple[str, ...]] = None,
    languages: Optional[Set[str]] = None,
    dry_run: bool = False,
    ref_strategy: str = "conservative",
) -> str:
    del include_globs, exclude_globs, languages, update_strings

    try:
        try:
            old_p, _ = validate_path(old_path, must_exist=True, allow_create=False)
            new_p, _ = validate_path(new_path, must_exist=False, allow_create=True)
        except PathSecurityError as e:
            return f"❌ {e}"

        root = Path(workspace_root).resolve()

        if not old_p.exists():
            return f"❌ 源不存在: {old_path}"

        o_rel = _try_rel(old_p, root)
        n_rel = _try_rel(new_p, root)

        if not dry_run:
            new_p.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(old_p), str(new_p))
            new_resolved = new_p.resolve()
        else:
            new_resolved = new_p.resolve()
        n_rel_after = _try_rel(new_resolved, root)

        replacements: List[Tuple[str, str]] = []
        if o_rel and (n_rel_after or n_rel):
            replacements.append((o_rel, n_rel_after or n_rel or ""))
        replacements.append((old_path.replace("\\", "/"), new_path.replace("\\", "/")))
        if ref_strategy == "aggressive":
            replacements.append(
                (str(old_p).replace("\\", "/"), str(new_resolved).replace("\\", "/"))
            )

        files_touched = 0
        if update_imports:
            for fp in root.rglob("*"):
                if not fp.is_file() or fp.suffix.lower() not in _TEXT_EXT:
                    continue
                try:
                    if fp.resolve() == new_resolved:
                        continue
                except OSError:
                    pass
                try:
                    text = fp.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                orig = text
                new_text = text
                for o, n in replacements:
                    if o and n and o != n:
                        new_text = new_text.replace(o, n)
                if new_text != orig:
                    files_touched += 1
                    if not dry_run:
                        fp.write_text(new_text, encoding="utf-8", newline="\n")

        mode = "[dry_run] " if dry_run else ""
        return (
            f"{mode}✅ 移动: {old_path} → {new_path}\n"
            f"引用扫描: {files_touched} 个文本文件需/已更新（策略={ref_strategy}）"
        )
    except Exception as e:
        return f"❌ rename_file_update_refs 失败: {str(e)}"

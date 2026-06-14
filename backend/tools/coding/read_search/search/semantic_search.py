# -*- coding: utf-8 -*-
"""语义搜索：有本地 JSON 索引时 TF 余弦检索；无索引时诚实降级（块4）。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, List, Optional, Sequence, Union

from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution

from .semantic_local import (
    DEFAULT_INDEX_NAME,
    auto_refresh_index,
    build_semantic_index,
    load_index,
    resolve_semantic_sidecar_path,
    resolve_semantic_workspace_root,
    search_semantic_local,
)


def _normalize_target_dirs(
    target_directories: Optional[Union[str, Sequence[Any]]],
) -> Optional[List[str]]:
    if target_directories is None:
        return None
    if isinstance(target_directories, str):
        return [target_directories] if target_directories.strip() else None
    out: List[str] = []
    for x in target_directories:
        s = str(x).strip()
        if s:
            out.append(s)
    return out or None


@trace_execution
def semantic_search(
    query: str,
    workspace_root: str = ".",
    top_k: int = 10,
    hint_paths: Optional[str] = None,
    target_directories: Optional[Union[str, Sequence[Any]]] = None,
    num_results: Optional[int] = None,
    index_path: Optional[str] = None,
) -> str:
    """
    按「含义」检索代码（本地索引：分块词频 + 余弦相似度）。

    - 若存在索引文件（默认 workspace 下 `.miro_semantic_index.json`），执行检索。
    - 否则返回与 C 一致的诚实降级说明，并建议 glob → grep → read。

    Args:
        query: 自然语言查询。
        workspace_root: 工作区根。
        top_k / num_results: 返回条数上限（num_results 为 C 别名）。
        hint_paths: 逗号分隔子目录提示（并入 target 过滤）。
        target_directories: C 形状，单路径 str 或路径列表；[] 或未传表示全仓。
        index_path: 自定义索引文件路径。
    """
    if num_results is not None:
        top_k = int(num_results)
    top_k = max(1, min(int(top_k), 50))

    try:
        root = resolve_semantic_workspace_root(workspace_root)
    except PathSecurityError as e:
        return str(e)

    try:
        if index_path:
            idx_file = resolve_semantic_sidecar_path(index_path, must_exist=False)
        else:
            idx_file = root / DEFAULT_INDEX_NAME
    except PathSecurityError as e:
        return str(e)

    prefixes = _normalize_target_dirs(target_directories)
    if hint_paths:
        extra = [p.strip() for p in hint_paths.split(",") if p.strip()]
        prefixes = (prefixes or []) + extra
        prefixes = list(dict.fromkeys(prefixes))

    doc = load_index(idx_file)
    # FABLEADV-26: 无索引→自动构建；有索引但文件有变更→自动增量刷新（仅默认索引路径，
    # 不对自定义 index_path 自动写盘）。失败安全降级。
    auto_note = ""
    if not index_path:
        doc, auto_note = auto_refresh_index(root, idx_file, doc)
    if not doc:
        return _fallback_message(query, str(root), idx_file, prefixes)

    hits = search_semantic_local(doc, query, top_k=top_k, path_prefixes=prefixes)
    if not hits:
        model_info = doc.get('model', '?')
        return (
            f"索引已加载（{idx_file}，模型 {model_info}），但查询 {query!r} 未匹配到 score>0 的片段（词面可能不重叠）。\n"
            "建议：换英文关键词/符号名，或使用 grep_search。\n"
        )

    lines = [
        f"语义检索（本地 TF 索引 `{idx_file.name}`，模型 {doc.get('model', '?')}）{auto_note}",
        f"查询: {query!r}",
        "",
    ]
    for i, h in enumerate(hits, 1):
        lines.append(
            f"{i}. score={h['score']}  {h['path']}:{h.get('line_start')}-{h.get('line_end')}"
        )
        lines.append(f"   {h.get('preview', '')[:500]}")
        lines.append("")
    return "\n".join(lines).rstrip()


def _fallback_message(query: str, workspace_root: str, idx_file: Path, prefixes: Optional[List[str]]) -> str:
    lines = [
        "⚠️ 未找到本地语义索引文件（默认路径）：",
        f"   {idx_file}",
        "",
        "构建索引（在工作区根执行）：",
        f'   python -m backend.tools.coding.read_search.search.semantic_search build --root "{workspace_root}"',
        "",
        f"查询: {query!r}",
        f"语义扫描根目录: {workspace_root}",
    ]
    if prefixes:
        lines.append(f"目标子目录过滤: {prefixes}")
    lines.extend(
        [
            "",
            "在无索引时：请使用 glob_search 缩小范围 → grep_search / search_in_files → read_file。",
        ]
    )
    return "\n".join(lines)


def _cli_build(args: argparse.Namespace) -> int:
    msg = build_semantic_index(
        workspace_root=args.root,
        output_path=args.out,
        extensions=tuple(args.ext.split(",")) if args.ext else None,
        incremental=args.incremental,
    )
    print(msg)
    return 0


def _cli_search(args: argparse.Namespace) -> int:
    try:
        root = resolve_semantic_workspace_root(args.root)
        if args.index:
            idx = resolve_semantic_sidecar_path(args.index, must_exist=False)
        else:
            idx = root / DEFAULT_INDEX_NAME
    except PathSecurityError as e:
        print(str(e), file=sys.stderr)
        return 2
    doc = load_index(idx)
    if not doc:
        print(f"无索引: {idx}", file=sys.stderr)
        return 1
    hits = search_semantic_local(doc, args.query, top_k=args.top_k, path_prefixes=None)
    print(json.dumps(hits, ensure_ascii=False, indent=2))
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="Miro 本地语义索引")
    sub = p.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="构建 JSON 索引")
    b.add_argument("--root", default=".", help="工作区根")
    b.add_argument("--out", default=None, help="输出 JSON 路径（默认 <root>/.miro_semantic_index.json）")
    b.add_argument("--ext", default="", help="扩展名逗号分隔，如 .py,.md（默认内置一组）")
    b.add_argument("--incremental", action="store_true", help="增量更新（仅处理新增/修改的文件）")
    b.set_defaults(func=_cli_build)

    s = sub.add_parser("search", help="CLI 调试检索")
    s.add_argument("--root", default=".")
    s.add_argument("--index", default=None)
    s.add_argument("--query", required=True)
    s.add_argument("--top_k", type=int, default=5)
    s.set_defaults(func=_cli_search)

    args = p.parse_args()
    raise SystemExit(args.func(args))


if __name__ == "__main__":
    main()

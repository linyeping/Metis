# -*- coding: utf-8 -*-
"""
本地语义索引（块4）：文件分块 + 词频向量 + 余弦相似度。无重型向量库依赖。
索引为 JSON，默认 `.miro_semantic_index.json` 位于 workspace 根目录。

增强模式（可选）：
- 设置环境变量 MIRO_SEMANTIC_MODEL=sentence-transformers 启用深度语义向量
- 需要安装：pip install sentence-transformers
- 默认模型：all-MiniLM-L6-v2（轻量级，22MB）

增量更新（2026-03-29）：
- 支持增量更新索引（仅处理新增/修改的文件）
- 基于文件修改时间（mtime）判断是否需要更新
- 大幅提升大型项目的索引更新速度
"""
from __future__ import annotations

import json
import math
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

INDEX_VERSION = 2
DEFAULT_INDEX_NAME = ".miro_semantic_index.json"
SKIP_DIRS = {".git", "__pycache__", ".venv", "venv", "node_modules", ".tox", "dist", "build"}
DEFAULT_EXTENSIONS = (".py", ".md", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java", ".kt")


def resolve_semantic_workspace_root(workspace_root: str) -> Path:
    """
    解析语义索引的「被扫描目录」：默认须落在配置工作区内；
    开启 allow_semantic_outside_workspace（或总闸）后可指向工作区外。
    """
    from backend.tools.coding.foundation.core_mechanisms.execution_boundary_context import (
        get_effective_sub_allow,
    )
    from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError, validate_path

    allow = get_effective_sub_allow("allow_semantic_outside_workspace")
    root, _ = validate_path(
        workspace_root,
        must_exist=True,
        allow_create=False,
        allow_paths_outside_workspace=allow,
    )
    if not root.is_dir():
        raise PathSecurityError(f"❌ 语义索引：必须是已存在的目录\n  {root}")
    return root


def resolve_semantic_sidecar_path(path: str, *, must_exist: bool) -> Path:
    """索引文件路径（自定义 --out / index_path）：受同一语义边界开关约束。"""
    from backend.tools.coding.foundation.core_mechanisms.execution_boundary_context import (
        get_effective_sub_allow,
    )
    from backend.tools.coding.foundation.core_mechanisms.path_security import validate_path

    allow = get_effective_sub_allow("allow_semantic_outside_workspace")
    normalized = str(Path(path).expanduser())
    p, _ = validate_path(
        normalized,
        must_exist=must_exist,
        allow_create=not must_exist,
        allow_paths_outside_workspace=allow,
    )
    return p


# 可选：sentence-transformers 支持
_SENTENCE_TRANSFORMER = None
_EMBEDDING_MODEL_NAME = "all-MiniLM-L6-v2"  # 轻量级，22MB


def _get_sentence_transformer():
    """延迟加载 sentence-transformers（仅在需要时）"""
    global _SENTENCE_TRANSFORMER
    if _SENTENCE_TRANSFORMER is not None:
        return _SENTENCE_TRANSFORMER
    
    try:
        from sentence_transformers import SentenceTransformer
        _SENTENCE_TRANSFORMER = SentenceTransformer(_EMBEDDING_MODEL_NAME)
        return _SENTENCE_TRANSFORMER
    except ImportError:
        return None


def _use_embeddings() -> bool:
    """检查是否启用深度语义向量"""
    mode = os.environ.get("MIRO_SEMANTIC_MODEL", "").strip().lower()
    return mode in ("sentence-transformers", "embeddings", "deep")


def tokenize(text: str) -> List[str]:
    return re.findall(r"[a-zA-Z_][a-zA-Z0-9_]{0,}", text.lower())


def term_frequency(tokens: List[str], max_terms: int = 400) -> Dict[str, int]:
    c = Counter(tokens)
    return dict(c.most_common(max_terms))


def cosine_tf(a: Dict[str, int], b: Dict[str, int]) -> float:
    if not a or not b:
        return 0.0
    dot = 0.0
    na = sum(f * f for f in a.values())
    nb = sum(f * f for f in b.values())
    if na <= 0 or nb <= 0:
        return 0.0
    for t, fa in a.items():
        if t in b:
            dot += fa * b[t]
    return dot / (math.sqrt(na) * math.sqrt(nb))


def cosine_embedding(a: List[float], b: List[float]) -> float:
    """计算两个嵌入向量的余弦相似度"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na <= 0 or nb <= 0:
        return 0.0
    return dot / (na * nb)


def iter_source_files(root: Path, extensions: Tuple[str, ...]) -> List[Path]:
    out: List[Path] = []
    root = root.resolve()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d
            for d in dirnames
            if d not in SKIP_DIRS and not d.startswith(".")
        ]
        for fn in filenames:
            if fn.endswith(extensions):
                out.append(Path(dirpath) / fn)
    return out


def chunk_file(path: Path, max_chars: int = 2400) -> List[Tuple[int, int, str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = text.splitlines()
    chunks: List[Tuple[int, int, str]] = []
    buf: List[str] = []
    buf_start = 1
    char_count = 0
    for i, line in enumerate(lines, start=1):
        buf.append(line)
        char_count += len(line) + 1
        if char_count >= max_chars:
            chunks.append((buf_start, i, "\n".join(buf)))
            buf = []
            buf_start = i + 1
            char_count = 0
    if buf:
        chunks.append((buf_start, len(lines), "\n".join(buf)))
    return chunks


def _build_chunk_with_embeddings(text: str, model) -> Optional[List[float]]:
    """使用 sentence-transformers 生成嵌入向量"""
    try:
        embedding = model.encode(text, convert_to_numpy=True)
        return embedding.tolist()
    except Exception:
        return None


def build_semantic_index(
    workspace_root: str,
    output_path: Optional[str] = None,
    extensions: Optional[Tuple[str, ...]] = None,
    incremental: bool = False,
) -> str:
    """
    构建语义索引。
    
    Args:
        workspace_root: 工作区根目录
        output_path: 输出索引文件路径（默认 <root>/.miro_semantic_index.json）
        extensions: 文件扩展名元组（默认常见代码文件）
        incremental: 是否增量更新（仅处理新增/修改的文件）
    """
    from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError

    try:
        root = resolve_semantic_workspace_root(workspace_root)
    except PathSecurityError as e:
        return str(e)

    if output_path:
        try:
            out = resolve_semantic_sidecar_path(output_path, must_exist=False)
        except PathSecurityError as e:
            return str(e)
    else:
        out = root / DEFAULT_INDEX_NAME
    exts = extensions or DEFAULT_EXTENSIONS
    
    # 检查是否使用深度语义向量
    use_embeddings = _use_embeddings()
    model = None
    model_name = "tf_cosine_v1"
    
    if use_embeddings:
        model = _get_sentence_transformer()
        if model:
            model_name = f"sentence-transformers/{_EMBEDDING_MODEL_NAME}"
            print(f"✓ 使用深度语义向量模型: {model_name}")
        else:
            print("⚠️ 未安装 sentence-transformers，回退到 TF 模式")
            print("   安装: pip install sentence-transformers")
            use_embeddings = False
    
    # 增量更新：加载现有索引
    existing_chunks: Dict[str, List[Dict[str, Any]]] = {}
    file_mtimes: Dict[str, float] = {}
    
    if incremental and out.is_file():
        try:
            existing_doc = json.loads(out.read_text(encoding="utf-8"))
            if existing_doc.get("version") == INDEX_VERSION:
                # 按文件路径组织现有 chunks
                for ch in existing_doc.get("chunks", []):
                    path = ch.get("path", "")
                    if path not in existing_chunks:
                        existing_chunks[path] = []
                    existing_chunks[path].append(ch)
                
                # 加载文件修改时间
                file_mtimes = existing_doc.get("file_mtimes", {})
                print(f"✓ 加载现有索引: {len(existing_chunks)} 个文件")
        except (json.JSONDecodeError, OSError):
            print("⚠️ 无法加载现有索引，执行全量构建")
            incremental = False
    
    # 收集所有源文件
    all_files = iter_source_files(root, exts)
    files_to_process: List[Path] = []
    files_to_keep: Set[str] = set()
    
    for fp in all_files:
        try:
            rel = str(fp.relative_to(root)).replace("\\", "/")
            files_to_keep.add(rel)
            
            # 检查是否需要更新
            if incremental:
                try:
                    current_mtime = fp.stat().st_mtime
                    old_mtime = file_mtimes.get(rel, 0)
                    
                    if current_mtime <= old_mtime:
                        # 文件未修改，跳过
                        continue
                except OSError:
                    pass
            
            files_to_process.append(fp)
        except ValueError:
            continue
    
    # 构建新 chunks
    new_chunks: List[Dict[str, Any]] = []
    new_mtimes: Dict[str, float] = {}
    
    for fp in files_to_process:
        try:
            rel = str(fp.relative_to(root)).replace("\\", "/")
            mtime = fp.stat().st_mtime
            new_mtimes[rel] = mtime
        except (ValueError, OSError):
            continue
        
        for ls, le, text in chunk_file(fp):
            # 基础 TF 向量（始终生成，用于兼容）
            tf = term_frequency(tokenize(text))
            if not tf:
                continue
            
            preview = text[:300].replace("\n", " ")
            if len(text) > 300:
                preview += "…"
            
            chunk_data = {
                "path": rel,
                "line_start": ls,
                "line_end": le,
                "preview": preview,
                "tf": tf,
            }
            
            # 可选：添加深度嵌入向量
            if use_embeddings and model:
                embedding = _build_chunk_with_embeddings(text, model)
                if embedding:
                    chunk_data["embedding"] = embedding
            
            new_chunks.append(chunk_data)
    
    # 合并：保留未修改的文件 + 新处理的文件
    final_chunks: List[Dict[str, Any]] = []
    final_mtimes: Dict[str, float] = {}
    
    if incremental:
        # 保留未修改的文件的 chunks
        for rel, chunks in existing_chunks.items():
            if rel in files_to_keep and rel not in new_mtimes:
                final_chunks.extend(chunks)
                if rel in file_mtimes:
                    final_mtimes[rel] = file_mtimes[rel]
    
    # 添加新处理的 chunks
    final_chunks.extend(new_chunks)
    final_mtimes.update(new_mtimes)
    
    # 写入索引
    doc = {
        "version": INDEX_VERSION,
        "workspace_root": str(root),
        "model": model_name,
        "file_mtimes": final_mtimes,
        "chunks": final_chunks,
    }
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    
    embed_info = ""
    if use_embeddings and model:
        embed_count = sum(1 for ch in final_chunks if "embedding" in ch)
        embed_info = f"，含深度向量 {embed_count} 条"
    
    mode_info = "增量更新" if incremental else "全量构建"
    processed_info = f"，处理 {len(files_to_process)} 个文件" if incremental else ""
    
    return f"索引已写入 {out}（{mode_info}{processed_info}），共 {len(final_chunks)} 条 chunk{embed_info}（根目录 {root}）。"


# --- FABLEADV-26: 自动构建 / 增量刷新 ---------------------------------------


def _auto_index_enabled() -> bool:
    """语义索引自动构建/刷新总开关（默认开）。"""
    value = os.environ.get("METIS_SEMANTIC_AUTO", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _max_auto_files() -> int:
    """超过该文件数则不自动构建/刷新（防超大仓库同步阻塞）。"""
    try:
        return int(os.environ.get("METIS_SEMANTIC_MAX_FILES", "8000"))
    except ValueError:
        return 8000


def index_is_stale(
    doc: Dict[str, Any],
    root: Path,
    files: Optional[List[Path]] = None,
    exts: Tuple[str, ...] = DEFAULT_EXTENSIONS,
) -> bool:
    """索引是否过期：有新增/修改（mtime 变大）或删除的源文件。"""
    stored: Dict[str, float] = doc.get("file_mtimes", {}) or {}
    if files is None:
        files = iter_source_files(root, exts)
    current: Dict[str, float] = {}
    root = root.resolve()
    for fp in files:
        try:
            rel = str(fp.relative_to(root)).replace("\\", "/")
            current[rel] = fp.stat().st_mtime
        except (OSError, ValueError):
            continue
    for rel, mtime in current.items():
        if mtime > float(stored.get(rel, 0.0)) + 1e-6:  # 新增或修改
            return True
    if set(stored) - set(current):  # 有删除
        return True
    return False


def auto_refresh_index(
    root: Path,
    idx_file: Path,
    existing_doc: Optional[Dict[str, Any]],
    exts: Tuple[str, ...] = DEFAULT_EXTENSIONS,
) -> Tuple[Optional[Dict[str, Any]], str]:
    """无索引→自动全量构建；有索引但过期→增量刷新。返回 (doc, 给用户的备注)。

    带文件数守卫与总开关；任何异常都安全降级（返回原 doc），绝不影响检索主流程。
    """
    if not _auto_index_enabled():
        return existing_doc, ""
    try:
        files = iter_source_files(root, exts)
        if len(files) > _max_auto_files():
            # 仓库太大，跳过自动构建（避免同步阻塞）；交由用户手动 build。
            return existing_doc, ""
        if existing_doc is None:
            build_semantic_index(str(root), incremental=False, extensions=exts)
            return load_index(idx_file), "（已自动构建语义索引）"
        if index_is_stale(existing_doc, root, files=files, exts=exts):
            build_semantic_index(str(root), incremental=True, extensions=exts)
            return load_index(idx_file), "（检测到文件变更，已自动增量刷新索引）"
        return existing_doc, ""
    except Exception:
        return existing_doc, ""


def load_index(path: Path) -> Optional[Dict[str, Any]]:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if isinstance(data, dict) and "chunks" in data:
        return data
    return None


def _path_allowed(rel: str, path_prefixes: Optional[List[str]]) -> bool:
    if not path_prefixes:
        return True
    rel = rel.replace("\\", "/")
    for p in path_prefixes:
        pre = p.rstrip("/").replace("\\", "/")
        if rel == pre or rel.startswith(pre + "/"):
            return True
    return False


def search_semantic_local(
    doc: Dict[str, Any],
    query: str,
    top_k: int = 10,
    path_prefixes: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    在本地索引中搜索。
    
    - 若索引含 embedding 字段，优先使用深度向量检索
    - 否则使用 TF 余弦相似度
    """
    model_name = doc.get("model", "tf_cosine_v1")
    use_embeddings = "sentence-transformers" in model_name
    
    # 检查索引是否包含嵌入向量
    chunks = doc.get("chunks", [])
    has_embeddings = any("embedding" in ch for ch in chunks)
    
    if use_embeddings and has_embeddings:
        return _search_with_embeddings(chunks, query, top_k, path_prefixes)
    else:
        return _search_with_tf(chunks, query, top_k, path_prefixes)


def _search_with_tf(
    chunks: List[Dict[str, Any]],
    query: str,
    top_k: int,
    path_prefixes: Optional[List[str]],
) -> List[Dict[str, Any]]:
    """使用 TF 余弦相似度搜索（默认模式）"""
    q_tf = term_frequency(tokenize(query), max_terms=120)
    scored: List[Tuple[float, Dict[str, Any]]] = []
    
    for ch in chunks:
        rel = str(ch.get("path", ""))
        if not _path_allowed(rel, path_prefixes):
            continue
        raw_tf = ch.get("tf") or {}
        tf = {k: int(v) for k, v in raw_tf.items()}
        s = cosine_tf(q_tf, tf)
        scored.append((s, ch))
    
    scored.sort(key=lambda x: x[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for s, ch in scored[:top_k]:
        if s <= 0:
            break
        out.append(
            {
                "score": round(float(s), 6),
                "path": ch.get("path"),
                "line_start": ch.get("line_start"),
                "line_end": ch.get("line_end"),
                "preview": ch.get("preview"),
            }
        )
    return out


def _search_with_embeddings(
    chunks: List[Dict[str, Any]],
    query: str,
    top_k: int,
    path_prefixes: Optional[List[str]],
) -> List[Dict[str, Any]]:
    """使用深度语义向量搜索（增强模式）"""
    model = _get_sentence_transformer()
    if not model:
        # 回退到 TF 模式
        return _search_with_tf(chunks, query, top_k, path_prefixes)
    
    try:
        query_embedding = model.encode(query, convert_to_numpy=True).tolist()
    except Exception:
        # 编码失败，回退到 TF
        return _search_with_tf(chunks, query, top_k, path_prefixes)
    
    scored: List[Tuple[float, Dict[str, Any]]] = []
    
    for ch in chunks:
        rel = str(ch.get("path", ""))
        if not _path_allowed(rel, path_prefixes):
            continue
        
        embedding = ch.get("embedding")
        if not embedding:
            # 该 chunk 没有嵌入向量，跳过或使用 TF
            continue
        
        s = cosine_embedding(query_embedding, embedding)
        scored.append((s, ch))
    
    scored.sort(key=lambda x: x[0], reverse=True)
    out: List[Dict[str, Any]] = []
    for s, ch in scored[:top_k]:
        if s <= 0:
            break
        out.append(
            {
                "score": round(float(s), 6),
                "path": ch.get("path"),
                "line_start": ch.get("line_start"),
                "line_end": ch.get("line_end"),
                "preview": ch.get("preview"),
            }
        )
    return out


def default_index_path(workspace_root: str, index_path: Optional[str] = None) -> Path:
    if index_path:
        return Path(index_path).expanduser().resolve()
    return Path(workspace_root).expanduser().resolve() / DEFAULT_INDEX_NAME

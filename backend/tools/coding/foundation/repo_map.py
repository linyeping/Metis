"""Repo Map 生成器 —— 用 tree-sitter 提取项目结构和签名，注入系统提示。

参考 Aider 的 repo-map 方案，生成紧凑的"仓库地图"让 LLM 一次了解项目结构。

输出格式::

    src/
      auth/
        login.py
          class LoginHandler
            def authenticate(self, username, password) -> bool
          class SessionManager
            def create_session(self, user) -> Session
      models/
        user.py
          class User(BaseModel)

缓存在 .metis/cache/repo_map.txt，文件变更后自动重建。
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from typing import Dict, List, Optional, Set

from .tree_sitter_parser import SignatureNode, detect_language, parse_file_signatures

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
_DEFAULT_MAX_TOKENS = 4000  # ≈ 16K chars
_MAX_FILES = 500  # 大项目裁剪上限
_CACHE_DIR_NAME = ".metis"
_CACHE_SUBDIR = "cache"
_CACHE_FILE = "repo_map.txt"
_HASH_FILE = "repo_map_hash.json"

_SKIP_DIRS: Set[str] = {
    ".git", ".svn", ".hg",
    "__pycache__", "node_modules", ".next",
    "venv", "env", ".venv", ".env",
    "dist", "build", ".tox", ".mypy_cache",
    ".pytest_cache", ".eggs", "*.egg-info",
    ".metis", ".miro",
}

_SOURCE_EXTENSIONS: Set[str] = {
    ".py", ".js", ".jsx", ".ts", ".tsx",
}


# ---------------------------------------------------------------------------
# 文件收集
# ---------------------------------------------------------------------------
def _should_skip_dir(dirname: str) -> bool:
    """是否跳过目录。"""
    if dirname.startswith("."):
        return True
    return dirname in _SKIP_DIRS


def _collect_source_files(workspace_root: str) -> List[str]:
    """收集工作区中的所有源文件（相对路径），按路径排序。"""
    result: List[str] = []
    abs_root = os.path.abspath(workspace_root)

    for dirpath, dirnames, filenames in os.walk(abs_root):
        # 原地修改 dirnames 来跳过目录
        dirnames[:] = sorted(d for d in dirnames if not _should_skip_dir(d))

        for fname in sorted(filenames):
            _, ext = os.path.splitext(fname)
            if ext.lower() in _SOURCE_EXTENSIONS:
                full = os.path.join(dirpath, fname)
                rel = os.path.relpath(full, abs_root)
                result.append(rel)

    return result[:_MAX_FILES]


# ---------------------------------------------------------------------------
# 文件指纹（用于缓存判断）
# ---------------------------------------------------------------------------
def _compute_files_hash(workspace_root: str, files: List[str]) -> str:
    """根据文件列表和 mtime 计算哈希，用于判断是否需要重新生成。"""
    hasher = hashlib.md5()
    abs_root = os.path.abspath(workspace_root)
    for rel in files:
        full = os.path.join(abs_root, rel)
        try:
            stat = os.stat(full)
            hasher.update(f"{rel}:{stat.st_mtime_ns}:{stat.st_size}\n".encode())
        except OSError:
            hasher.update(f"{rel}:missing\n".encode())
    return hasher.hexdigest()


# ---------------------------------------------------------------------------
# 格式化
# ---------------------------------------------------------------------------
def _format_signature_node(node: SignatureNode, indent: int) -> str:
    """格式化单个签名节点及其子节点。"""
    prefix = "  " * indent
    line = f"{prefix}{node.signature}\n"
    for child in node.children:
        line += _format_signature_node(child, indent + 1)
    return line


def _format_repo_map(
    workspace_root: str,
    file_signatures: Dict[str, List[SignatureNode]],
    source_files: List[str],
) -> str:
    """将签名数据格式化为目录树形式的文本。"""
    lines: List[str] = []

    # 构建目录结构
    dir_tree: Dict[str, List[str]] = {}
    for rel_path in source_files:
        parent = os.path.dirname(rel_path)
        if parent not in dir_tree:
            dir_tree[parent] = []
        dir_tree[parent].append(rel_path)

    # 按目录排序输出
    visited_dirs: Set[str] = set()

    for rel_path in source_files:
        parts = rel_path.replace("\\", "/").split("/")
        filename = parts[-1]
        dir_parts = parts[:-1]

        # 输出目录层级
        for i in range(len(dir_parts)):
            dir_prefix = "/".join(dir_parts[: i + 1])
            if dir_prefix not in visited_dirs:
                visited_dirs.add(dir_prefix)
                indent = "  " * i
                lines.append(f"{indent}{dir_parts[i]}/")

        # 输出文件名
        file_indent = "  " * len(dir_parts)
        lines.append(f"{file_indent}{filename}")

        # 输出签名
        sigs = file_signatures.get(rel_path, [])
        for sig in sigs:
            lines.append(_format_signature_node(sig, len(dir_parts) + 1).rstrip())

    return "\n".join(lines)


def _truncate_to_budget(text: str, max_tokens: int) -> str:
    """截断到 token 预算（近似 1 token ≈ 4 chars）。"""
    max_chars = max_tokens * 4
    if len(text) <= max_chars:
        return text
    # 按行截断，保留完整行
    lines = text.split("\n")
    result: List[str] = []
    total = 0
    for line in lines:
        if total + len(line) + 1 > max_chars:
            break
        result.append(line)
        total += len(line) + 1
    result.append(f"[... repo map truncated to ~{max_tokens} tokens]")
    return "\n".join(result)


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------
def _cache_dir(workspace_root: str) -> str:
    return os.path.join(os.path.abspath(workspace_root), _CACHE_DIR_NAME, _CACHE_SUBDIR)


def _read_cache(workspace_root: str, current_hash: str) -> Optional[str]:
    """读取缓存的 repo map，哈希不匹配则返回 None。"""
    cache_d = _cache_dir(workspace_root)
    hash_file = os.path.join(cache_d, _HASH_FILE)
    map_file = os.path.join(cache_d, _CACHE_FILE)

    try:
        with open(hash_file, "r", encoding="utf-8") as f:
            cached = json.load(f)
        if cached.get("hash") == current_hash:
            with open(map_file, "r", encoding="utf-8") as f:
                return f.read()
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    return None


def _write_cache(workspace_root: str, content: str, file_hash: str) -> None:
    """将 repo map 写入缓存。"""
    cache_d = _cache_dir(workspace_root)
    try:
        os.makedirs(cache_d, exist_ok=True)
        map_file = os.path.join(cache_d, _CACHE_FILE)
        hash_file = os.path.join(cache_d, _HASH_FILE)
        with open(map_file, "w", encoding="utf-8") as f:
            f.write(content)
        with open(hash_file, "w", encoding="utf-8") as f:
            json.dump({"hash": file_hash, "ts": time.time()}, f)
    except OSError as exc:
        logger.debug("Failed to write repo map cache: %s", exc)


# ---------------------------------------------------------------------------
# 公开 API
# ---------------------------------------------------------------------------
def generate_repo_map(workspace_root: str = ".", max_tokens: int = _DEFAULT_MAX_TOKENS) -> str:
    """生成仓库结构地图，用于注入系统提示。

    1. 收集所有源文件（.py/.ts/.tsx/.js/.jsx）
    2. 对每个文件用 tree-sitter 解析 AST，提取顶级定义
    3. 按目录结构组织
    4. 如果超过 max_tokens，截断

    Args:
        workspace_root: 工作区根路径。
        max_tokens: token 预算上限，默认 4000。

    Returns:
        格式化的 repo map 文本。
    """
    abs_root = os.path.abspath(workspace_root)
    if not os.path.isdir(abs_root):
        return f"(workspace not found: {workspace_root})"

    source_files = _collect_source_files(abs_root)
    if not source_files:
        return "(no source files found)"

    # 检查缓存
    file_hash = _compute_files_hash(abs_root, source_files)
    cached = _read_cache(abs_root, file_hash)
    if cached is not None:
        logger.debug("repo map cache hit for %s", abs_root)
        return cached

    # 解析每个文件
    t0 = time.time()
    file_signatures: Dict[str, List[SignatureNode]] = {}
    for rel_path in source_files:
        full_path = os.path.join(abs_root, rel_path)
        lang = detect_language(full_path)
        if lang:
            sigs = parse_file_signatures(full_path, lang)
            if sigs:
                file_signatures[rel_path] = sigs

    # 格式化
    map_text = _format_repo_map(abs_root, file_signatures, source_files)
    map_text = _truncate_to_budget(map_text, max_tokens)
    elapsed = time.time() - t0
    logger.info("repo map generated: %d files, %d with signatures, %.1fs", len(source_files), len(file_signatures), elapsed)

    # 写入缓存
    _write_cache(abs_root, map_text, file_hash)

    return map_text


def invalidate_cache(workspace_root: str) -> None:
    """标记缓存为 dirty —— 下次 generate 时重新构建。"""
    cache_d = _cache_dir(workspace_root)
    hash_file = os.path.join(cache_d, _HASH_FILE)
    try:
        if os.path.exists(hash_file):
            os.remove(hash_file)
    except OSError:
        pass

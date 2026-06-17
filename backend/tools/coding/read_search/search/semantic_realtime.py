# -*- coding: utf-8 -*-
"""
实时语义索引更新（2026-03-29）

当文件被修改时，自动更新语义索引中对应文件的 chunks。
通过 post_tool_hook 机制实现，无需手动重建索引。

特性：
- 监听文件修改工具（write_file, robust_replace, append_to_file 等）
- 自动更新受影响文件的索引条目
- 保持索引与文件系统同步
- 支持 TF 和深度向量两种模式

P5 审计说明（与 Tools/registry.py 对照）：
- FILE_MODIFICATION_TOOLS = WRITE_LIKE_TOOLS ∪ {rename_symbol, undo_last_edit}，**以下 frozenset 须与 registry 手工同步**（不在此文件 import registry，避免与 registry 尾部 register_realtime_index_hook 循环导入导致静默未注册 hook）。
- apply_patch：属 WRITE_LIKE_TOOLS；kwargs 无 file_path/path，本 hook 无法定位单文件，不触发实时更新（依赖后续单文件写或全量 build）。
- todo_write / write_open_files_context / switch_mode：写 JSON 状态文件，扩展名不在语义 chunk 规则内，不纳入本集合。
- execute_bash_command / delegate_explore / delegate_browser / delegate_shell / delegate_best_of_n / summon_context_gatherer / custom_agent_creator / task_dispatch / run_parallel_tasks：磁盘副作用路径不可由 kwargs 静态枚举，不纳入。
- auto_install_package / git_commit_pr：改环境与 git 对象，非工作区源码 chunk 索引职责，不纳入。
- extract_method：仅返回文本建议，不落盘，不纳入。
- generate_image：当前实现仅请求远程 API，不写工作区文件，不纳入。
- pdf_create / docx_create / office_report_from_code_run 等 artifact 工具可能写文件，但非源码扩展会在 update_index_for_file 中自然跳过。
- metis_runtime_* 默认写入 .metis/runtime 与 .metis/artifacts，不直接更新源码索引；导出的 patch 由后续真实文件编辑触发索引。
"""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Dict, FrozenSet, Optional

from backend.tools.coding.read_search.search.semantic_local import (
    INDEX_VERSION,
    chunk_file,
    default_index_path,
    term_frequency,
    tokenize,
    _get_sentence_transformer,
    _use_embeddings,
    _build_chunk_with_embeddings,
)


# 需要触发索引更新的工具（P5：须与 registry.WRITE_LIKE_TOOLS ∪ 下列 extra 一致；改 registry 时同步并跑 test_semantic_realtime）
FILE_MODIFICATION_TOOLS: FrozenSet[str] = frozenset(
    {
        "write_file",
        "append_to_file",
        "robust_replace_in_file",
        "apply_patch",
        "editCode",
        "edit_notebook",
        "rename_file_update_refs",
        "delete_file",
        "delete_directory",
        "pdf_create",
        "pdf_merge_split",
        "pdf_render_pages",
        "pdf_screenshot_page",
        "docx_create",
        "docx_edit",
        "docx_to_pdf",
        "docx_render_pages",
        "office_report_from_code_run",
        "metis_runtime_create",
        "metis_runtime_run",
        "metis_runtime_collect_artifacts",
        "metis_runtime_export_patch",
        "metis_runtime_export_diagnostics",
        "rename_symbol",
        "undo_last_edit",
    }
)


def _is_realtime_enabled() -> bool:
    """检查是否启用实时索引更新"""
    v = os.environ.get("MIRO_SEMANTIC_REALTIME", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


def _get_workspace_root() -> Optional[str]:
    """获取工作区根目录"""
    # 优先从环境变量获取
    root = os.environ.get("MIRO_WORKSPACE_ROOT", "").strip()
    if root:
        return root
    
    # 尝试从配置文件获取
    try:
        from backend.tools.coding.foundation.core_mechanisms.config import get_config
        cfg = get_config()
        return cfg.workspace_root
    except Exception:
        pass
    
    # 默认当前目录
    return "."


def update_index_for_file(file_path: str, workspace_root: Optional[str] = None) -> bool:
    """
    更新索引中指定文件的 chunks。
    
    Args:
        file_path: 文件路径（绝对或相对）
        workspace_root: 工作区根目录（可选）
    
    Returns:
        是否成功更新
    """
    if not _is_realtime_enabled():
        return False
    
    # 获取工作区根
    if not workspace_root:
        workspace_root = _get_workspace_root()
    
    if not workspace_root:
        return False
    
    root = Path(workspace_root).resolve()
    index_path = default_index_path(str(root))
    
    # 检查索引是否存在
    if not index_path.is_file():
        return False
    
    # 加载索引
    try:
        doc = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    
    if doc.get("version") != INDEX_VERSION:
        return False
    
    # 规范化文件路径
    try:
        fp = Path(file_path).resolve()
        rel_path = str(fp.relative_to(root)).replace("\\", "/")
    except (ValueError, OSError):
        return False
    
    # 检查文件是否存在
    if not fp.is_file():
        # 文件被删除，移除索引中的 chunks
        return _remove_file_from_index(doc, rel_path, index_path)
    
    # 检查文件扩展名
    if not fp.suffix in (".py", ".md", ".ts", ".tsx", ".js", ".jsx", ".rs", ".go", ".java", ".kt"):
        return False
    
    # 检查是否使用深度向量
    use_embeddings = _use_embeddings()
    model = None
    if use_embeddings:
        model = _get_sentence_transformer()
        if not model:
            use_embeddings = False
    
    # 重新处理文件
    try:
        mtime = fp.stat().st_mtime
        new_chunks = []
        
        for ls, le, text in chunk_file(fp):
            tf = term_frequency(tokenize(text))
            if not tf:
                continue
            
            preview = text[:300].replace("\n", " ")
            if len(text) > 300:
                preview += "…"
            
            chunk_data = {
                "path": rel_path,
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
        
        # 更新索引：移除旧 chunks，添加新 chunks
        chunks = doc.get("chunks", [])
        chunks = [ch for ch in chunks if ch.get("path") != rel_path]
        chunks.extend(new_chunks)
        doc["chunks"] = chunks
        
        # 更新文件修改时间
        file_mtimes = doc.get("file_mtimes", {})
        file_mtimes[rel_path] = mtime
        doc["file_mtimes"] = file_mtimes
        
        # 保存索引
        index_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        return True
        
    except Exception:
        return False


def _remove_file_from_index(doc: Dict[str, Any], rel_path: str, index_path: Path) -> bool:
    """从索引中移除指定文件的所有 chunks"""
    try:
        chunks = doc.get("chunks", [])
        original_count = len(chunks)
        chunks = [ch for ch in chunks if ch.get("path") != rel_path]
        
        if len(chunks) < original_count:
            doc["chunks"] = chunks
            
            # 移除文件修改时间记录
            file_mtimes = doc.get("file_mtimes", {})
            if rel_path in file_mtimes:
                del file_mtimes[rel_path]
                doc["file_mtimes"] = file_mtimes
            
            # 保存索引
            index_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
            return True
        
        return False
    except Exception:
        return False


def _invalidate_index_under_directory(dir_abs: str, workspace_root: Optional[str] = None) -> bool:
    """删除目录后：移除索引中 path == rel 或 path 以 rel/ 为前缀的所有 chunks。"""
    if not _is_realtime_enabled():
        return False
    if not workspace_root:
        workspace_root = _get_workspace_root()
    if not workspace_root:
        return False
    root = Path(workspace_root).resolve()
    index_path = default_index_path(str(root))
    if not index_path.is_file():
        return False
    try:
        doc = json.loads(index_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return False
    if doc.get("version") != INDEX_VERSION:
        return False
    try:
        d_abs = Path(dir_abs).resolve()
        rel_prefix = str(d_abs.relative_to(root)).replace("\\", "/")
    except (ValueError, OSError):
        return False

    chunks = doc.get("chunks", [])
    original = len(chunks)
    prefix = rel_prefix + "/"

    def keep(ch: Dict[str, Any]) -> bool:
        p = ch.get("path") or ""
        if p == rel_prefix:
            return False
        if p.startswith(prefix):
            return False
        return True

    new_chunks = [ch for ch in chunks if keep(ch)]
    if len(new_chunks) == original:
        return False
    doc["chunks"] = new_chunks
    mt = doc.get("file_mtimes", {})
    for k in list(mt.keys()):
        if k == rel_prefix or k.startswith(prefix):
            del mt[k]
    doc["file_mtimes"] = mt
    try:
        index_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        return True
    except OSError:
        return False


def _primary_tool_result_for_hook(result: str) -> str:
    """execute_tool 可能在 .py 写后追加 read_lints；失败判定只看主工具段，避免 linter 文案含「错误」误伤。"""
    sep = "--- read_lints (post-edit) ---"
    if sep in result:
        return result.split(sep, 1)[0]
    return result


def realtime_index_hook(tool_name: str, kwargs: Dict[str, Any], result: str) -> None:
    """
    Post-tool hook：文件修改后自动更新索引。

    单路径工具：file_path 或 path（edit_notebook 等由 registry.normalize 已写入 path）。
    rename_file_update_refs：old_path / new_path；delete_directory：path。
    apply_patch：无路径 kwargs，不进入有效分支。
    """
    if tool_name not in FILE_MODIFICATION_TOOLS:
        return

    head = _primary_tool_result_for_hook(result or "")
    if head and ("❌" in head or "错误" in head or "失败" in head):
        return

    workspace_root = _get_workspace_root()

    if tool_name == "rename_file_update_refs":
        if kwargs.get("dry_run"):
            return
        old_path = kwargs.get("old_path")
        new_path = kwargs.get("new_path")
        if not old_path or not new_path:
            return
        update_index_for_file(str(old_path), workspace_root)
        update_index_for_file(str(new_path), workspace_root)
        return

    if tool_name == "delete_directory":
        dir_path = kwargs.get("path")
        if not dir_path:
            return
        _invalidate_index_under_directory(str(dir_path), workspace_root)
        return

    file_path = kwargs.get("file_path") or kwargs.get("path") or kwargs.get("output_path")
    if not file_path:
        return

    update_index_for_file(str(file_path), workspace_root)


def register_realtime_index_hook() -> None:
    """注册实时索引更新钩子"""
    if not _is_realtime_enabled():
        return
    
    try:
        from backend.tools.coding.workflow_features.hooks.post_tool_hook import register_post_hook
        register_post_hook(realtime_index_hook)
    except Exception:
        pass

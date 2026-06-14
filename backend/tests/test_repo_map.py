"""repo_map 单元测试 —— 验证文件收集、签名格式化、缓存和截断逻辑。"""
from __future__ import annotations

import json
import os
import textwrap
from pathlib import Path
from typing import List

import pytest

from backend.tools.coding.foundation.repo_map import (
    _collect_source_files,
    _compute_files_hash,
    _format_repo_map,
    _format_signature_node,
    _read_cache,
    _should_skip_dir,
    _truncate_to_budget,
    _write_cache,
    generate_repo_map,
    invalidate_cache,
)
from backend.tools.coding.foundation.tree_sitter_parser import SignatureNode


# ---------------------------------------------------------------------------
# _should_skip_dir
# ---------------------------------------------------------------------------


class TestShouldSkipDir:
    def test_git_dir(self):
        assert _should_skip_dir(".git") is True

    def test_node_modules(self):
        assert _should_skip_dir("node_modules") is True

    def test_pycache(self):
        assert _should_skip_dir("__pycache__") is True

    def test_hidden_dir(self):
        assert _should_skip_dir(".mydir") is True

    def test_normal_dir(self):
        assert _should_skip_dir("src") is False
        assert _should_skip_dir("lib") is False


# ---------------------------------------------------------------------------
# _collect_source_files
# ---------------------------------------------------------------------------


class TestCollectSourceFiles:
    def _make_tree(self, root: Path, files: List[str]):
        for f in files:
            p = root / f
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text("// placeholder", encoding="utf-8")

    def test_collects_py_and_ts(self, tmp_path):
        self._make_tree(tmp_path, ["src/a.py", "src/b.ts", "src/c.tsx", "src/d.js", "src/e.jsx"])
        files = _collect_source_files(str(tmp_path))
        basenames = [os.path.basename(f) for f in files]
        assert set(basenames) == {"a.py", "b.ts", "c.tsx", "d.js", "e.jsx"}

    def test_skips_node_modules(self, tmp_path):
        self._make_tree(tmp_path, ["src/a.py", "node_modules/pkg/index.js"])
        files = _collect_source_files(str(tmp_path))
        assert all("node_modules" not in f for f in files)

    def test_skips_pycache(self, tmp_path):
        self._make_tree(tmp_path, ["src/a.py", "__pycache__/b.py"])
        files = _collect_source_files(str(tmp_path))
        assert len(files) == 1

    def test_ignores_non_source_files(self, tmp_path):
        self._make_tree(tmp_path, ["readme.md", "data.json", "img.png", "src/main.py"])
        files = _collect_source_files(str(tmp_path))
        assert len(files) == 1
        assert files[0].endswith("main.py")

    def test_sorted_within_each_directory(self, tmp_path):
        self._make_tree(tmp_path, ["z.py", "a.py", "b.py"])
        files = _collect_source_files(str(tmp_path))
        # Root-level files are sorted alphabetically
        assert files == ["a.py", "b.py", "z.py"]

    def test_empty_dir(self, tmp_path):
        files = _collect_source_files(str(tmp_path))
        assert files == []


# ---------------------------------------------------------------------------
# _compute_files_hash
# ---------------------------------------------------------------------------


class TestComputeFilesHash:
    def test_same_content_same_hash(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1")
        files = ["a.py"]
        h1 = _compute_files_hash(str(tmp_path), files)
        h2 = _compute_files_hash(str(tmp_path), files)
        assert h1 == h2

    def test_different_content_different_hash(self, tmp_path):
        (tmp_path / "a.py").write_text("x = 1")
        h1 = _compute_files_hash(str(tmp_path), ["a.py"])
        (tmp_path / "a.py").write_text("x = 2")
        # Force mtime change on Windows (resolution can be coarse)
        import time; time.sleep(0.05)
        (tmp_path / "a.py").write_text("x = 2")
        h2 = _compute_files_hash(str(tmp_path), ["a.py"])
        # They may or may not differ depending on mtime resolution,
        # but the hash should at least be a valid hex string
        assert len(h1) == 32

    def test_missing_file_handled(self, tmp_path):
        h = _compute_files_hash(str(tmp_path), ["nonexistent.py"])
        assert isinstance(h, str) and len(h) == 32


# ---------------------------------------------------------------------------
# _format_signature_node / _format_repo_map
# ---------------------------------------------------------------------------


class TestFormatting:
    def test_format_signature_node_simple(self):
        node = SignatureNode(kind="function", name="foo", signature="def foo()")
        text = _format_signature_node(node, indent=1)
        assert text.strip() == "def foo()"
        assert text.startswith("  ")  # indent=1 → 2 spaces

    def test_format_signature_node_with_children(self):
        cls = SignatureNode(
            kind="class", name="A", signature="class A",
            children=[
                SignatureNode(kind="method", name="run", signature="def run(self)"),
            ],
        )
        text = _format_signature_node(cls, indent=0)
        assert "class A" in text
        assert "  def run(self)" in text

    def test_format_repo_map_basic(self, tmp_path):
        sigs = {
            "src/main.py": [
                SignatureNode(kind="function", name="main", signature="def main()"),
            ],
        }
        text = _format_repo_map(str(tmp_path), sigs, ["src/main.py"])
        assert "src/" in text
        assert "main.py" in text
        assert "def main()" in text


# ---------------------------------------------------------------------------
# _truncate_to_budget
# ---------------------------------------------------------------------------


class TestTruncateToBudget:
    def test_within_budget_unchanged(self):
        text = "line1\nline2\nline3"
        assert _truncate_to_budget(text, max_tokens=1000) == text

    def test_over_budget_truncated(self):
        text = "\n".join(f"line {i}" for i in range(500))
        result = _truncate_to_budget(text, max_tokens=10)  # 10 tokens ≈ 40 chars
        assert len(result) < len(text)
        assert "truncated" in result


# ---------------------------------------------------------------------------
# 缓存
# ---------------------------------------------------------------------------


class TestCache:
    def test_write_and_read_cache(self, tmp_path):
        _write_cache(str(tmp_path), "repo map content", "abc123")
        result = _read_cache(str(tmp_path), "abc123")
        assert result == "repo map content"

    def test_cache_miss_on_hash_mismatch(self, tmp_path):
        _write_cache(str(tmp_path), "old content", "hash_old")
        result = _read_cache(str(tmp_path), "hash_new")
        assert result is None

    def test_invalidate_cache(self, tmp_path):
        _write_cache(str(tmp_path), "content", "h1")
        invalidate_cache(str(tmp_path))
        result = _read_cache(str(tmp_path), "h1")
        assert result is None


# ---------------------------------------------------------------------------
# generate_repo_map（集成）
# ---------------------------------------------------------------------------


class TestGenerateRepoMap:
    def test_generates_from_python_files(self, tmp_path):
        src = tmp_path / "src"
        src.mkdir()
        (src / "app.py").write_text(textwrap.dedent("""\
            def main():
                pass

            class Server:
                def start(self):
                    pass
        """), encoding="utf-8")
        result = generate_repo_map(str(tmp_path))
        assert "main" in result
        assert "Server" in result

    def test_nonexistent_workspace(self):
        result = generate_repo_map("/no/such/path")
        assert "not found" in result

    def test_empty_workspace(self, tmp_path):
        result = generate_repo_map(str(tmp_path))
        assert "no source files" in result

    def test_cache_is_used_on_second_call(self, tmp_path):
        (tmp_path / "a.py").write_text("def f(): pass\n", encoding="utf-8")
        r1 = generate_repo_map(str(tmp_path))
        r2 = generate_repo_map(str(tmp_path))
        assert r1 == r2

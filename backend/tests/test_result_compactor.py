"""result_compactor.py 单元测试 — 验证各压缩策略的行为。"""
from __future__ import annotations

import json
import pytest

from backend.runtime.result_compactor import ResultCompactor


@pytest.fixture
def compactor():
    return ResultCompactor()


# ---------------------------------------------------------------------------
# write_confirm 策略
# ---------------------------------------------------------------------------

class TestWriteConfirm:
    def test_short_result_unchanged(self, compactor):
        result = "File written successfully."
        assert compactor.compact("write_file", result) == result

    def test_long_result_truncated(self, compactor):
        result = "File written.\n" + "x" * 1000
        compacted = compactor.compact("write_file", result)
        assert "Write confirmed" in compacted
        assert len(compacted) < len(result)

    def test_robust_replace_uses_write_confirm(self, compactor):
        result = "OK\n" + "y" * 800
        compacted = compactor.compact("robust_replace_in_file", result)
        assert "Write confirmed" in compacted

    def test_apply_patch_uses_write_confirm(self, compactor):
        result = "Patched.\n" + "z" * 600
        compacted = compactor.compact("apply_patch", result)
        assert "Write confirmed" in compacted


# ---------------------------------------------------------------------------
# tail_heavy 策略
# ---------------------------------------------------------------------------

class TestTailHeavy:
    def test_short_shell_output_unchanged(self, compactor):
        result = "exit code: 0\nall good"
        assert compactor.compact("execute_bash_command", result) == result

    def test_long_shell_output_keeps_tail(self, compactor):
        # Each line ~40 chars, 200 lines = ~8000 chars (> 4000 threshold)
        lines = [f"line {i}: {'x' * 30} some output data" for i in range(200)]
        result = "\n".join(lines)
        compacted = compactor.compact("execute_bash_command", result)
        # Tail should be preserved
        assert "line 199" in compacted
        # Head too
        assert "line 0" in compacted
        # Middle is omitted
        assert "omitted" in compacted

    def test_run_tests_uses_tail_heavy(self, compactor):
        # Each line ~40 chars, enough to exceed 4000 threshold
        lines = [f"test_{i} PASSED {'.' * 30}" for i in range(200)]
        result = "\n".join(lines)
        compacted = compactor.compact("run_tests", result)
        assert "omitted" in compacted


# ---------------------------------------------------------------------------
# dedup_lines 策略
# ---------------------------------------------------------------------------

class TestDedupLines:
    def test_short_search_unchanged(self, compactor):
        result = "foo.py:10: match"
        assert compactor.compact("grep_search", result) == result

    def test_dedup_removes_duplicate_lines(self, compactor):
        lines = ["foo.py:10: match"] * 100 + ["bar.py:20: other"]
        result = "\n".join(lines)
        # With dedup, should be much shorter
        compacted = compactor.compact("grep_search", result)
        assert compacted.count("foo.py:10: match") == 1

    def test_dedup_then_truncate(self, compactor):
        # Many unique lines — each ~60 chars, 200 lines = ~12000 chars (> 8000 threshold)
        lines = [f"file_{i}.py:{i}: unique match {i} {'data' * 10}" for i in range(200)]
        result = "\n".join(lines)
        assert len(result) > 8_000, f"Test data too short: {len(result)}"
        compacted = compactor.compact("grep_search", result)
        assert "omitted" in compacted or "Narrow your search" in compacted

    def test_glob_search_uses_dedup(self, compactor):
        lines = ["src/a.py"] * 50 + ["src/b.py"]
        result = "\n".join(lines)
        compacted = compactor.compact("glob_search", result)
        assert compacted.count("src/a.py") == 1


# ---------------------------------------------------------------------------
# keep_structure 策略
# ---------------------------------------------------------------------------

class TestKeepStructure:
    def test_short_file_unchanged(self, compactor):
        result = "import os\ndef main():\n    pass"
        assert compactor.compact("read_file", result) == result

    def test_long_file_keeps_imports_and_signatures(self, compactor):
        # Each body line ~50 chars, 2000 lines = ~100K chars (> 24K max)
        lines = (
            ["import os", "import sys", "from pathlib import Path", ""]
            + ["class Foo:", "    def bar(self):"]
            + [f"        x_{i} = 'value_{i}' + compute(arg_{i})" for i in range(2000)]
            + ["def baz():", "    return 42"]
        )
        result = "\n".join(lines)
        assert len(result) > 24_000, f"Test data too short: {len(result)}"
        compacted = compactor.compact("read_file", result)
        assert len(compacted) < len(result)
        # Structure preserved
        assert "import os" in compacted


# ---------------------------------------------------------------------------
# tree_compact 策略
# ---------------------------------------------------------------------------

class TestTreeCompact:
    def test_short_listing_unchanged(self, compactor):
        result = "file1.py\nfile2.py"
        assert compactor.compact("list_directory", result) == result

    def test_long_listing_truncated(self, compactor):
        # Each line ~40 chars, 200 lines = ~8000 chars (> 3000 threshold)
        lines = [f"src/components/feature_{i}/index.py" for i in range(200)]
        result = "\n".join(lines)
        assert len(result) > 3_000
        compacted = compactor.compact("list_directory", result)
        assert "entries omitted" in compacted


# ---------------------------------------------------------------------------
# json_summary 策略
# ---------------------------------------------------------------------------

class TestJsonSummary:
    def test_short_json_unchanged(self, compactor):
        data = [{"name": "win1", "hwnd": 123}]
        result = json.dumps(data)
        assert compactor.compact("desktop_window_list", result) == result

    def test_long_json_extracts_key_fields(self, compactor):
        data = [
            {"name": f"Window {i}", "hwnd": i, "pid": 1000 + i,
             "class_name": "cls", "extra_field": "x" * 200}
            for i in range(50)
        ]
        result = json.dumps(data)
        compacted = compactor.compact("desktop_window_list", result)
        assert len(compacted) < len(result)
        # Key fields preserved
        assert "Window 0" in compacted
        # Extra field stripped
        assert "extra_field" not in compacted

    def test_non_json_falls_back_to_head_tail(self, compactor):
        # > 24000 chars so head_tail triggers
        result = "not json data here " * 2000
        assert len(result) > 24_000
        compacted = compactor.compact("desktop_window_list", result)
        assert "truncated" in compacted


# ---------------------------------------------------------------------------
# browser_cap 策略
# ---------------------------------------------------------------------------

class TestBrowserCap:
    def test_short_page_unchanged(self, compactor):
        result = "Hello page"
        assert compactor.compact("browse_web", result) == result

    def test_long_page_truncated(self, compactor):
        result = "a" * 10_000
        compacted = compactor.compact("browse_web", result)
        assert "truncated" in compacted
        assert len(compacted) < len(result)


# ---------------------------------------------------------------------------
# 文件去重
# ---------------------------------------------------------------------------

class TestFileDedup:
    def test_first_read_passes_through(self, compactor):
        result = "File: test.py\ncontent here"
        compacted = compactor.compact("read_file", result)
        assert "content here" in compacted

    def test_second_identical_read_deduped(self, compactor):
        result = "File: test.py\ncontent here"
        compactor.compact("read_file", result)
        compacted = compactor.compact("read_file", result)
        assert "already in context" in compacted

    def test_different_file_not_deduped(self, compactor):
        r1 = "File: a.py\ncontent a"
        r2 = "File: b.py\ncontent b"
        compactor.compact("read_file", r1)
        compacted = compactor.compact("read_file", r2)
        assert "already in context" not in compacted

    def test_reset_clears_dedup(self, compactor):
        result = "File: test.py\ncontent"
        compactor.compact("read_file", result)
        compactor.reset_seen_files()
        compacted = compactor.compact("read_file", result)
        assert "already in context" not in compacted


# ---------------------------------------------------------------------------
# head_tail 兜底
# ---------------------------------------------------------------------------

class TestHeadTail:
    def test_unknown_tool_uses_head_tail(self, compactor):
        result = "x" * 30_000
        compacted = compactor.compact("unknown_tool", result)
        assert "truncated" in compacted
        assert len(compacted) <= 24_500  # ~max + overhead

    def test_within_budget_unchanged(self, compactor):
        result = "short result"
        assert compactor.compact("unknown_tool", result) == result

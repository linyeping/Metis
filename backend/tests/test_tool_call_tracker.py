"""tool_call_tracker.py 单元测试 — 验证循环检测和效率建议。"""
from __future__ import annotations


from backend.runtime.tool_call_tracker import ToolCallTracker


# ---------------------------------------------------------------------------
# 基本记录
# ---------------------------------------------------------------------------

class TestRecord:
    def test_empty_tracker(self):
        t = ToolCallTracker()
        assert t.call_count == 0

    def test_record_increments_count(self):
        t = ToolCallTracker()
        t.record("read_file", {"path": "a.py"})
        assert t.call_count == 1
        t.record("read_file", {"path": "b.py"})
        assert t.call_count == 2

    def test_reset_clears_history(self):
        t = ToolCallTracker()
        t.record("read_file", {"path": "a.py"})
        t.reset()
        assert t.call_count == 0


# ---------------------------------------------------------------------------
# 连续重复检测
# ---------------------------------------------------------------------------

class TestConsecutiveRepeat:
    def test_no_repeat_initially(self):
        t = ToolCallTracker()
        t.record("read_file", {"path": "a.py"})
        assert t.detect_loop() is None

    def test_two_repeats_no_loop(self):
        t = ToolCallTracker()
        t.record("read_file", {"path": "a.py"})
        t.record("read_file", {"path": "a.py"})
        assert t.detect_loop() is None

    def test_three_repeats_detected(self):
        t = ToolCallTracker()
        for _ in range(3):
            t.record("read_file", {"path": "a.py"})
        hint = t.detect_loop()
        assert hint is not None
        assert "read_file" in hint
        assert "3 times" in hint

    def test_different_args_no_loop(self):
        t = ToolCallTracker()
        t.record("read_file", {"path": "a.py"})
        t.record("read_file", {"path": "b.py"})
        t.record("read_file", {"path": "c.py"})
        assert t.detect_loop() is None

    def test_custom_repeat_limit(self):
        t = ToolCallTracker(repeat_limit=2)
        t.record("grep_search", {"pattern": "foo"})
        t.record("grep_search", {"pattern": "foo"})
        assert t.detect_loop() is not None

    def test_is_consecutive_repeat(self):
        t = ToolCallTracker()
        t.record("read_file", {"path": "a.py"})
        assert not t.is_consecutive_repeat()
        t.record("read_file", {"path": "a.py"})
        assert t.is_consecutive_repeat()

    def test_consecutive_repeat_count(self):
        t = ToolCallTracker()
        assert t.consecutive_repeat_count() == 0
        t.record("read_file", {"path": "a.py"})
        assert t.consecutive_repeat_count() == 1
        t.record("read_file", {"path": "a.py"})
        assert t.consecutive_repeat_count() == 2
        t.record("grep_search", {"pattern": "x"})
        assert t.consecutive_repeat_count() == 1


# ---------------------------------------------------------------------------
# A-B 交替检测
# ---------------------------------------------------------------------------

class TestABAlternation:
    def test_ab_alternation_detected(self):
        t = ToolCallTracker()
        t.record("read_file", {"path": "a.py"})
        t.record("grep_search", {"pattern": "foo"})
        t.record("read_file", {"path": "a.py"})
        t.record("grep_search", {"pattern": "foo"})
        hint = t.detect_loop()
        assert hint is not None
        assert "alternating" in hint

    def test_different_args_no_alternation(self):
        t = ToolCallTracker()
        t.record("read_file", {"path": "a.py"})
        t.record("grep_search", {"pattern": "foo"})
        t.record("read_file", {"path": "b.py"})  # different args
        t.record("grep_search", {"pattern": "foo"})
        assert t.detect_loop() is None

    def test_three_elements_no_false_positive(self):
        t = ToolCallTracker()
        t.record("read_file", {"path": "a.py"})
        t.record("grep_search", {"pattern": "foo"})
        t.record("write_file", {"path": "a.py"})
        t.record("grep_search", {"pattern": "foo"})
        assert t.detect_loop() is None


# ---------------------------------------------------------------------------
# 效率建议
# ---------------------------------------------------------------------------

class TestEfficiencyHint:
    def test_no_hint_for_short_history(self):
        t = ToolCallTracker()
        t.record("read_file", {"path": "a.py"})
        assert t.get_efficiency_hint() is None

    def test_many_read_file_suggests_grep(self):
        t = ToolCallTracker()
        for i in range(5):
            t.record("read_file", {"path": f"file_{i}.py"})
        hint = t.get_efficiency_hint()
        assert hint is not None
        assert "grep_search" in hint

    def test_read_file_with_grep_no_hint(self):
        t = ToolCallTracker()
        t.record("read_file", {"path": "a.py"})
        t.record("read_file", {"path": "b.py"})
        t.record("grep_search", {"pattern": "x"})
        t.record("read_file", {"path": "c.py"})
        t.record("read_file", {"path": "d.py"})
        # grep is in recent 8 calls, so no hint
        hint = t.get_efficiency_hint()
        assert hint is None

    def test_many_shell_commands_hint(self):
        t = ToolCallTracker()
        for i in range(5):
            t.record("execute_bash_command", {"command": f"cmd_{i}"})
        hint = t.get_efficiency_hint()
        assert hint is not None
        assert "shell commands" in hint.lower() or "&&" in hint

    def test_many_edits_hint(self):
        t = ToolCallTracker()
        for i in range(5):
            t.record("robust_replace_in_file", {"path": f"f{i}.py"})
        hint = t.get_efficiency_hint()
        assert hint is not None
        assert "edit" in hint.lower()


# ---------------------------------------------------------------------------
# 摘要
# ---------------------------------------------------------------------------

class TestSummary:
    def test_empty_summary(self):
        t = ToolCallTracker()
        s = t.summary()
        assert s["total"] == 0
        assert s["unique_tools"] == 0

    def test_summary_counts(self):
        t = ToolCallTracker()
        t.record("read_file", {"path": "a.py"})
        t.record("read_file", {"path": "b.py"})
        t.record("grep_search", {"pattern": "x"})
        s = t.summary()
        assert s["total"] == 3
        assert s["unique_tools"] == 2
        assert s["tools"]["read_file"] == 2
        assert s["tools"]["grep_search"] == 1

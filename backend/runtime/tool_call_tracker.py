"""ToolCallTracker — 工具调用模式检测与效率建议。

跟踪 agent 的工具调用历史，检测循环模式（连续重复、A-B 交替）
并在需要时返回效率建议供注入到 LLM 上下文中。
"""
from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, List, Optional, Tuple


class ToolCallTracker:
    """跟踪工具调用模式，检测循环和低效行为。

    使用示例::

        tracker = ToolCallTracker()
        tracker.record("read_file", {"path": "foo.py"})
        tracker.record("read_file", {"path": "foo.py"})
        tracker.record("read_file", {"path": "foo.py"})
        hint = tracker.detect_loop()
        # -> "You've called read_file with the same arguments 3 times. ..."
    """

    # 真正推进交付/改动工作区的工具：调用这些会把"规划连击"清零。
    _PRODUCTIVE_TOOLS = frozenset({
        "write_file",
        "robust_replace_in_file",
        "apply_patch",
        "edit_file",
        "str_replace",
        "str_replace_editor",
        "create_file",
        "execute_bash_command",
        "run_terminal_cmd",
    })
    # 只是规划、不产出交付物的工具。
    _PLANNING_TOOLS = frozenset({"todo_write"})

    def __init__(self, repeat_limit: int = 3, todo_churn_limit: int = 3):
        self._history: List[Tuple[str, str]] = []  # (tool_name, args_hash)
        self._repeat_limit = repeat_limit
        # 连续 todo_write（中间没有任何"实干"工具）的计数。这个计数刻意独立于
        # _history：循环里每次 todo_write 成功都会调用 reset() 清空 _history，
        # 若 churn 计数也跟着清零，模型就能 todo_write→探索→todo_write→… 无限
        # 规划而永不交付（DeepSeek eval 里实测连刷 12~20 次 todo_write 撞满轮次
        # 却没写出 answer.md）。所以它只被"实干"工具清零，不被 reset() 清零。
        self._todo_churn_limit = todo_churn_limit
        self._planning_streak = 0
        self._last_churn_nudge = 0

    # ------------------------------------------------------------------
    # 记录
    # ------------------------------------------------------------------

    def record(self, tool_name: str, args: Dict[str, Any]) -> None:
        """记录一次工具调用。"""
        args_hash = hashlib.md5(
            json.dumps(args or {}, sort_keys=True, ensure_ascii=False, default=str).encode()
        ).hexdigest()[:12]
        self._history.append((tool_name, args_hash))

        # 维护"规划连击"：连续 todo_write +1，一旦做了实干工具就清零。
        # 探索类工具（read/search 等）既不加也不减——"光规划+光看不动手"也算 churn。
        if tool_name in self._PLANNING_TOOLS:
            self._planning_streak += 1
        elif tool_name in self._PRODUCTIVE_TOOLS:
            self._planning_streak = 0
            self._last_churn_nudge = 0

    @property
    def call_count(self) -> int:
        return len(self._history)

    # ------------------------------------------------------------------
    # 循环检测
    # ------------------------------------------------------------------

    def detect_loop(self) -> Optional[str]:
        """检测是否陷入循环。返回建议提示或 None。

        检测两种模式：
        1. 连续 N 次完全相同的调用（tool + args 完全一致）
        2. A-B-A-B 交替循环
        """
        if len(self._history) < self._repeat_limit:
            return None

        # --- 检测连续重复 ---
        last_n = self._history[-self._repeat_limit:]
        if len(set(last_n)) == 1:
            name = last_n[0][0]
            return (
                f"You've called {name} with the same arguments "
                f"{self._repeat_limit} times consecutively. "
                "Stop and revise the plan with todo_write: mark the current blocker, "
                "then try a different tool or path."
            )

        # --- 检测 A-B-A-B 交替 ---
        if len(self._history) >= 4:
            last_4 = self._history[-4:]
            if last_4[0] == last_4[2] and last_4[1] == last_4[3]:
                name_a = last_4[0][0]
                name_b = last_4[1][0]
                return (
                    f"You're alternating between {name_a} and {name_b} "
                    "without making progress. Use todo_write to record the blocker, "
                    "then switch strategy instead of repeating the same loop."
                )

        return None

    def detect_todo_churn(self) -> Optional[str]:
        """检测"反复写待办却不动手"的规划空转。返回纠偏提示或 None。

        与 detect_loop 不同：detect_loop 要求**参数完全相同**的连续调用，而 churn
        里每次 todo_write 的清单都略有演化（hash 不同），所以会从 detect_loop 漏过去。
        这里只看"连续 todo_write、中间没有任何实干工具"的次数。

        为了不每轮重复刷屏：只在 streak 首次达到阈值、以及之后每再增长一次时返回一次。
        """
        if self._planning_streak < self._todo_churn_limit:
            return None
        if self._planning_streak <= self._last_churn_nudge:
            return None
        self._last_churn_nudge = self._planning_streak
        return (
            f"你已经连续 {self._planning_streak} 次更新待办清单（todo_write）却没有任何"
            "实际推进（没有写文件、编辑或执行命令）。立即停止规划：用 write_file / 编辑 / "
            "execute_bash_command 推进当前待办项并产出交付物（例如把答案写入 answer.md）。"
            "不要再写待办。"
        )

    def is_consecutive_repeat(self) -> bool:
        """检查最近一次调用是否与前一次完全相同（用于快速判断）。"""
        if len(self._history) < 2:
            return False
        return self._history[-1] == self._history[-2]

    def consecutive_repeat_count(self) -> int:
        """返回当前连续重复次数。"""
        if not self._history:
            return 0
        current = self._history[-1]
        count = 0
        for entry in reversed(self._history):
            if entry == current:
                count += 1
            else:
                break
        return count

    # ------------------------------------------------------------------
    # 效率建议
    # ------------------------------------------------------------------

    def get_efficiency_hint(self) -> Optional[str]:
        """基于调用历史给出效率建议。"""
        if len(self._history) < 5:
            return None

        recent_tools = [h[0] for h in self._history[-5:]]

        # 连续多次 read_file 但没用 grep_search
        if recent_tools.count("read_file") >= 4:
            all_recent_tools = {h[0] for h in self._history[-8:]}
            if "grep_search" not in all_recent_tools and "glob_search" not in all_recent_tools:
                return (
                    "Hint: You're reading many files sequentially. "
                    "Consider using grep_search to narrow down first."
                )

        # 连续多次 execute_bash_command 但都是短命令
        if recent_tools.count("execute_bash_command") >= 4:
            return (
                "Hint: You're running many shell commands. "
                "Consider combining them with && into fewer calls."
            )

        # 连续多次 write_file / robust_replace_in_file（可能在逐行编辑）
        edit_tools = {"write_file", "robust_replace_in_file", "apply_patch"}
        edit_count = sum(1 for t in recent_tools if t in edit_tools)
        if edit_count >= 4:
            return (
                "Hint: You're making many small edits. "
                "Consider batching changes into fewer, larger edits."
            )

        return None

    # ------------------------------------------------------------------
    # 摘要
    # ------------------------------------------------------------------

    def summary(self) -> Dict[str, Any]:
        """返回调用统计摘要。"""
        if not self._history:
            return {"total": 0, "unique_tools": 0, "tools": {}}
        tool_counts: Dict[str, int] = {}
        for name, _ in self._history:
            tool_counts[name] = tool_counts.get(name, 0) + 1
        return {
            "total": len(self._history),
            "unique_tools": len(tool_counts),
            "tools": tool_counts,
        }

    def reset(self) -> None:
        """清空调用历史（用于 todo_write 成功后给循环检测一个干净起点）。

        刻意**不**清 _planning_streak / _last_churn_nudge：todo_write 成功本身就会
        触发本方法，若连 churn 计数一起清掉，反复写待办的空转就永远检测不到。
        """
        self._history.clear()

from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, Mapping


EDIT_TOOLS = {
    "write_file",
    "append_to_file",
    "robust_replace_in_file",
    "apply_patch",
    "editCode",
    "edit_code_ast",
    "edit_notebook",
    "rename_file_update_refs",
    "delete_file",
    "delete_directory",
}


@dataclass
class EvalMetrics:
    success: bool = False
    turns: int = 0
    tool_calls: int = 0
    tool_counts: Dict[str, int] = field(default_factory=dict)
    tool_errors: int = 0
    edit_attempts: int = 0
    edit_successes: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    total_tokens: int = 0
    cache_hit_ratio: float = 0.0
    wall_time: float = 0.0
    died_at_max_turns: bool = False

    @property
    def edit_first_try_success(self) -> bool:
        return self.edit_attempts == 1 and self.edit_successes == 1

    def to_dict(self) -> Dict[str, Any]:
        data = asdict(self)
        data["edit_first_try_success"] = self.edit_first_try_success
        return data


class MetricsCollector:
    def __init__(self) -> None:
        self.metrics = EvalMetrics()
        self._started = time.monotonic()

    def observe(self, event: Any) -> None:
        event_type = str(getattr(event, "type", "") or "")
        if event_type == "tool_call":
            name = str(getattr(event, "tool_name", "") or "")
            self.metrics.tool_calls += 1
            if name:
                self.metrics.tool_counts[name] = self.metrics.tool_counts.get(name, 0) + 1
                if name in EDIT_TOOLS:
                    self.metrics.edit_attempts += 1
        elif event_type == "tool_result":
            name = str(getattr(event, "tool_name", "") or "")
            result = str(getattr(event, "result", "") or "")
            if _looks_like_error(result):
                self.metrics.tool_errors += 1
            elif name in EDIT_TOOLS:
                self.metrics.edit_successes += 1
        elif event_type == "done":
            self.metrics.turns = int(getattr(event, "total_turns", 0) or 0)
            self.metrics.tool_calls = int(getattr(event, "total_tool_calls", self.metrics.tool_calls) or 0)
            self.metrics.tokens_in = int(getattr(event, "prompt_tokens", 0) or 0)
            self.metrics.tokens_out = int(getattr(event, "completion_tokens", 0) or 0)
            self.metrics.total_tokens = int(getattr(event, "total_tokens", 0) or 0)
            hit = int(getattr(event, "prompt_cache_hit_tokens", 0) or 0)
            miss = int(getattr(event, "prompt_cache_miss_tokens", 0) or 0)
            total = hit + miss
            self.metrics.cache_hit_ratio = round(hit / total, 4) if total else 0.0
        elif event_type == "error":
            code = str(getattr(event, "code", "") or "")
            if code == "RUNTIME_MAX_TURNS":
                self.metrics.died_at_max_turns = True

    def finish(self, *, success: bool) -> EvalMetrics:
        self.metrics.success = bool(success)
        self.metrics.wall_time = round(time.monotonic() - self._started, 3)
        return self.metrics


def aggregate_metrics(results: Iterable[Mapping[str, Any]]) -> Dict[str, Any]:
    rows = [dict(item) for item in results]
    count = len(rows)
    if not rows:
        return {"runs": 0, "success_rate": 0.0}
    numeric = (
        "turns",
        "tool_calls",
        "tool_errors",
        "edit_attempts",
        "edit_successes",
        "tokens_in",
        "tokens_out",
        "total_tokens",
        "cache_hit_ratio",
        "wall_time",
        "died_at_max_turns",
    )
    summary: Dict[str, Any] = {
        "runs": count,
        "success_rate": round(sum(1 for row in rows if row.get("success")) / count, 4),
    }
    for key in numeric:
        summary[f"avg_{key}"] = round(sum(float(row.get(key) or 0) for row in rows) / count, 3)
    return summary


def _looks_like_error(result: str) -> bool:
    text = str(result or "").lstrip()
    return text.startswith(("❌", "Error", "错误", "[Permission denied]", "[Cancelled]"))

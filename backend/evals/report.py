from __future__ import annotations

import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Mapping

from .metrics import aggregate_metrics


RESULTS_DIR = Path("evals-results")


def git_sha() -> str:
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if proc.returncode == 0:
            return proc.stdout.strip() or "no-git"
    except Exception:
        pass
    return "no-git"


def timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def write_reports(result: Mapping[str, Any], *, output_dir: str | Path = RESULTS_DIR) -> Dict[str, str]:
    target_dir = Path(output_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{result.get('started_at', timestamp())}-{result.get('git_sha', git_sha())}"
    json_path = target_dir / f"{stem}.json"
    md_path = target_dir / f"{stem}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(markdown_report(result), encoding="utf-8")
    return {"json": str(json_path), "markdown": str(md_path)}


def markdown_report(result: Mapping[str, Any]) -> str:
    summary = result.get("summary") if isinstance(result.get("summary"), Mapping) else {}
    rows = result.get("results") if isinstance(result.get("results"), list) else []
    lines = [
        f"# Eval Report: {result.get('suite', 'suite')}",
        "",
        f"- started_at: `{result.get('started_at', '')}`",
        f"- model: `{result.get('model', '')}`",
        f"- backend: `{result.get('backend', '')}`",
        f"- git_sha: `{result.get('git_sha', '')}`",
        f"- runs: {summary.get('runs', 0)}",
        f"- success_rate: {summary.get('success_rate', 0)}",
        "",
        "| task | repeat | success | turns | tools | errors | edits | tokens | checker |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---|",
    ]
    for row in rows:
        metrics = row.get("metrics") if isinstance(row.get("metrics"), Mapping) else {}
        lines.append(
            "| {task} | {repeat} | {success} | {turns} | {tools} | {errors} | {edits} | {tokens} | {checker} |".format(
                task=row.get("task_id", ""),
                repeat=row.get("repeat", 0),
                success="yes" if metrics.get("success") else "no",
                turns=metrics.get("turns", 0),
                tools=metrics.get("tool_calls", 0),
                errors=metrics.get("tool_errors", 0),
                edits=metrics.get("edit_attempts", 0),
                tokens=metrics.get("total_tokens", 0),
                checker="ok" if row.get("checker_ok") else "fail",
            )
        )
    return "\n".join(lines) + "\n"


def compare_reports(current: Mapping[str, Any], previous: Mapping[str, Any]) -> str:
    cur_summary = _summary(current)
    prev_summary = _summary(previous)
    keys = ("success_rate", "avg_turns", "avg_tool_calls", "avg_tool_errors", "avg_total_tokens", "avg_wall_time")
    lines = [
        f"# Eval Comparison: {previous.get('suite', '')} -> {current.get('suite', '')}",
        "",
        "| metric | previous | current | delta |",
        "|---|---:|---:|---:|",
    ]
    for key in keys:
        old = float(prev_summary.get(key) or 0)
        new = float(cur_summary.get(key) or 0)
        lines.append(f"| {key} | {old:.3f} | {new:.3f} | {new - old:+.3f} |")
    return "\n".join(lines) + "\n"


def load_result(path: str | Path) -> Dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _summary(result: Mapping[str, Any]) -> Dict[str, Any]:
    summary = result.get("summary")
    if isinstance(summary, Mapping):
        return dict(summary)
    rows = result.get("results") if isinstance(result.get("results"), list) else []
    return aggregate_metrics(
        row.get("metrics", {})
        for row in rows
        if isinstance(row, Mapping) and isinstance(row.get("metrics"), Mapping)
    )

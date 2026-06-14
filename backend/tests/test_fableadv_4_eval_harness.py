from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Generator, List, Optional

from backend.evals.metrics import MetricsCollector, aggregate_metrics
from backend.evals.report import compare_reports, markdown_report
from backend.evals.runner import run_checker, run_suite
from backend.evals.tasks import task_spec
from backend.evals.tasks.task_spec import EvalSuite, EvalTask, load_suite
from backend.runtime.agent_loop import DoneEvent, ToolCallEvent, ToolResultEvent
from backend.runtime.llm_backends import LLMBackend, LLMResponse, ToolCall, Usage


class ScriptedWriteBackend(LLMBackend):
    def __init__(self) -> None:
        self.calls = 0
        self.messages: List[List[Dict[str, Any]]] = []

    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        cancel_event: Any = None,
    ) -> LLMResponse:
        self.calls += 1
        self.messages.append([dict(message) for message in messages])
        usage = Usage(
            prompt_tokens=10 * self.calls,
            completion_tokens=2,
            total_tokens=(10 * self.calls) + 2,
            prompt_cache_hit_tokens=5,
            prompt_cache_miss_tokens=5,
        )
        if self.calls == 1:
            return LLMResponse(
                tool_calls=[
                    ToolCall(
                        "call_write",
                        "write_file",
                        {"file_path": "answer.md", "content": "eval harness scripted answer\n"},
                    )
                ],
                usage=usage,
            )
        return LLMResponse(content="done", usage=usage)

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        cancel_event: Any = None,
    ) -> Generator[str, None, LLMResponse]:
        response = self.chat(messages, tools, temperature=temperature, max_tokens=max_tokens, timeout=timeout)
        if response.content:
            yield response.content
        return response


def test_smoke_suite_loads_twelve_seed_tasks() -> None:
    suite = load_suite("smoke")

    assert suite.name == "smoke"
    assert len(suite.tasks) == 12
    assert [task.id for task in suite.tasks] == [
        "fix-off-by-one",
        "fix-import-cycle",
        "add-function",
        "rename-config-key",
        "answer-arch",
        "find-dead-code",
        "run-and-fix-test",
        "ts-add-type",
        "create-script",
        "long-plan",
        "web-static-summary",
        "web-spa-browser",
    ]


def test_capability_suite_preserves_probe_metadata() -> None:
    suite = load_suite("capability")

    assert suite.metadata["kind"] == "capability-probes"
    probe_ids = {str(probe["id"]) for probe in suite.metadata["probes"]}
    assert len(suite.metadata["probes"]) == 6
    assert "skills-debug-auto-load" in probe_ids
    assert "skills-frontend-self-verify" in probe_ids
    assert all("capability" in task.tags for task in suite.tasks)


def test_run_checker_reports_success_and_failure(tmp_path: Path) -> None:
    ok = tmp_path / "ok.py"
    bad = tmp_path / "bad.py"
    ok.write_text("import sys\nsys.exit(0)\n", encoding="utf-8")
    bad.write_text("import sys\nprint('failed')\nsys.exit(7)\n", encoding="utf-8")

    assert run_checker(tmp_path, EvalTask("ok", "", ".", "ok.py"))["ok"] is True
    failed = run_checker(tmp_path, EvalTask("bad", "", ".", "bad.py"))

    assert failed["ok"] is False
    assert failed["returncode"] == 7
    assert "failed" in failed["stdout"]


def test_metrics_collector_counts_tools_errors_edits_and_cache() -> None:
    collector = MetricsCollector()

    collector.observe(ToolCallEvent(tool_name="write_file", call_id="1"))
    collector.observe(ToolResultEvent(tool_name="write_file", result="wrote", call_id="1"))
    collector.observe(ToolCallEvent(tool_name="read_file", call_id="2"))
    collector.observe(ToolResultEvent(tool_name="read_file", result="Error: missing", call_id="2"))
    collector.observe(
        DoneEvent(
            total_turns=2,
            total_tool_calls=2,
            prompt_tokens=75,
            completion_tokens=25,
            total_tokens=100,
            prompt_cache_hit_tokens=60,
            prompt_cache_miss_tokens=40,
        )
    )
    metrics = collector.finish(success=True)

    assert metrics.success is True
    assert metrics.turns == 2
    assert metrics.tool_calls == 2
    assert metrics.tool_errors == 1
    assert metrics.edit_attempts == 1
    assert metrics.edit_successes == 1
    assert metrics.edit_first_try_success is True
    assert metrics.cache_hit_ratio == 0.6


def test_aggregate_and_report_comparison() -> None:
    previous = {
        "suite": "smoke",
        "summary": aggregate_metrics(
            [
                {"success": True, "turns": 4, "tool_calls": 2, "total_tokens": 100, "wall_time": 1.0},
                {"success": False, "turns": 8, "tool_calls": 4, "total_tokens": 200, "wall_time": 3.0},
            ]
        ),
        "results": [],
    }
    current = {
        "suite": "smoke",
        "started_at": "20260612T000000Z",
        "backend": "fake",
        "model": "fake-eval",
        "git_sha": "no-git",
        "summary": aggregate_metrics(
            [
                {"success": True, "turns": 2, "tool_calls": 1, "total_tokens": 80, "wall_time": 1.0},
                {"success": True, "turns": 4, "tool_calls": 2, "total_tokens": 120, "wall_time": 1.5},
            ]
        ),
        "results": [
            {
                "task_id": "demo",
                "repeat": 1,
                "checker_ok": True,
                "metrics": {"success": True, "turns": 2, "tool_calls": 1, "tool_errors": 0, "edit_attempts": 1, "total_tokens": 80},
            }
        ],
    }

    report = markdown_report(current)
    comparison = compare_reports(current, previous)

    assert "| demo | 1 | yes |" in report
    assert "success_rate" in comparison
    assert "+0.500" in comparison


def test_run_suite_with_scripted_backend_and_temp_fixture(tmp_path: Path, monkeypatch: Any) -> None:
    fixtures = tmp_path / "fixtures"
    fixture = fixtures / "easy"
    fixture.mkdir(parents=True)
    (fixture / "checks").mkdir()
    (fixture / "checks" / "check_answer.py").write_text(
        "from pathlib import Path\n"
        "text = Path('answer.md').read_text(encoding='utf-8')\n"
        "assert 'scripted answer' in text\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(task_spec, "FIXTURES_ROOT", fixtures)

    suite = EvalSuite(
        name="unit",
        description="unit suite",
        tasks=[EvalTask("write-answer", "write answer.md", "easy", "checks/check_answer.py", max_turns=5)],
    )
    result = run_suite(
        suite,
        backend_name="fake",
        model="scripted",
        repeat=1,
        output_dir=tmp_path / "reports",
        injected_backend=ScriptedWriteBackend(),
    )

    assert result["summary"]["success_rate"] == 1.0
    assert result["results"][0]["checker_ok"] is True
    assert result["results"][0]["metrics"]["edit_attempts"] == 1
    assert Path(result["reports"]["json"]).is_file()
    assert Path(result["reports"]["markdown"]).is_file()

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict, List, Optional

from backend.evals.metrics import MetricsCollector, aggregate_metrics
from backend.evals.report import compare_reports, git_sha, load_result, markdown_report, timestamp, write_reports
from backend.evals.tasks.task_spec import EvalSuite, EvalTask, checker_path, fixture_path, load_suite
from backend.bridges.model_capability import detect_from_model_name
from backend.bridges.provider_registry import resolve_provider_for_config
from backend.core.engine.prompt_runtime import compile_prompt_runtime
from backend.runtime.agent_loop import AgentConfig, DoneEvent, ErrorEvent, run_stream
from backend.runtime.llm_backends import LLMBackend
from backend.runtime.provider_conformance import run_provider_conformance_probe
from backend.runtime.tool_registry import get_registry


def run_suite(
    suite: EvalSuite,
    *,
    backend_name: str = "openai",
    model: str = "",
    base_url: str = "",
    api_key: str = "",
    repeat: int = 3,
    task_filter: str = "",
    output_dir: str | Path = "evals-results",
    injected_backend: Optional[LLMBackend] = None,
    system_prompt_mode: str = "prod",
) -> Dict[str, Any]:
    selected = [task for task in suite.tasks if not task_filter or task.id == task_filter]
    if task_filter and not selected:
        raise ValueError(f"Task {task_filter!r} not found in suite {suite.name!r}")

    started = timestamp()
    rows: List[Dict[str, Any]] = []
    for task in selected:
        for index in range(max(1, repeat)):
            rows.append(
                run_task(
                    task,
                    backend_name=backend_name,
                    model=model,
                    base_url=base_url,
                    api_key=api_key,
                    repeat_index=index + 1,
                    injected_backend=injected_backend,
                    system_prompt_mode=system_prompt_mode,
                )
            )

    metrics_rows = [row["metrics"] for row in rows]
    provider_conformance = _maybe_run_provider_conformance_probe(
        suite,
        backend_name=backend_name,
        model=model,
        base_url=base_url,
        api_key=api_key,
        injected_backend=injected_backend,
    )
    result: Dict[str, Any] = {
        "schema": "metis.eval_result.v1",
        "suite": suite.name,
        "description": suite.description,
        "metadata": suite.metadata,
        "started_at": started,
        "git_sha": git_sha(),
        "backend": backend_name,
        "model": model,
        "repeat": repeat,
        "task_filter": task_filter,
        "system_prompt_mode": system_prompt_mode,
        "summary": aggregate_metrics(metrics_rows),
        "results": rows,
    }
    if provider_conformance is not None:
        result["provider_conformance"] = provider_conformance
    result["reports"] = write_reports(result, output_dir=output_dir)
    return result


def run_task(
    task: EvalTask,
    *,
    backend_name: str = "openai",
    model: str = "",
    base_url: str = "",
    api_key: str = "",
    repeat_index: int = 1,
    injected_backend: Optional[LLMBackend] = None,
    system_prompt_mode: str = "prod",
) -> Dict[str, Any]:
    workspace = Path(tempfile.mkdtemp(prefix=f"metis-eval-{task.id}-"))
    collector = MetricsCollector()
    events: List[Dict[str, Any]] = []
    run_error = ""
    checker = {"ok": False, "returncode": -1, "stdout": "", "stderr": ""}

    try:
        _copy_fixture(fixture_path(task), workspace)
        config = _build_eval_config(
            workspace,
            backend_name=backend_name,
            model=model,
            base_url=base_url,
            api_key=api_key,
            max_turns=task.max_turns,
            system_prompt_mode=system_prompt_mode,
        )
        registry = get_registry(include_desktop=False, include_mcp=False, include_experts=False)
        for event in run_stream(
            [{"role": "user", "content": task.prompt}],
            config,
            registry=registry,
            backend=injected_backend,
        ):
            collector.observe(event)
            events.append(_event_summary(event))
            if isinstance(event, ErrorEvent):
                run_error = event.message or event.title or event.code
        checker = run_checker(workspace, task)
    except Exception as exc:  # noqa: BLE001 — one bad task must not abort the suite
        run_error = run_error or f"{type(exc).__name__}: {exc}"
    finally:
        metrics = collector.finish(success=bool(checker.get("ok")))

    return {
        "task_id": task.id,
        "repeat": repeat_index,
        "fixture": task.fixture,
        "workspace": str(workspace),
        "checker_ok": bool(checker.get("ok")),
        "checker": checker,
        "run_error": run_error,
        "metrics": metrics.to_dict(),
        "events": events,
    }


def run_checker(workspace: Path, task: EvalTask, *, timeout: float = 30.0) -> Dict[str, Any]:
    checker = checker_path(workspace, task)
    env = dict(os.environ)
    env["PYTHONPATH"] = str(workspace) + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    proc = subprocess.run(
        [sys.executable, str(checker)],
        cwd=str(workspace),
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
    )
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout[-4000:],
        "stderr": proc.stderr[-4000:],
    }


def _maybe_run_provider_conformance_probe(
    suite: EvalSuite,
    *,
    backend_name: str,
    model: str,
    base_url: str,
    api_key: str,
    injected_backend: Optional[LLMBackend],
) -> Optional[Dict[str, Any]]:
    if suite.metadata.get("kind") != "capability-probes" or injected_backend is not None:
        return None
    try:
        profile = resolve_provider_for_config(backend_name, base_url=base_url, model=model)
    except Exception:
        return None
    if not profile.openai_compatible:
        return None
    resolved_base_url = str(base_url or profile.base_url or "").strip().rstrip("/")
    resolved_model = str(model or profile.default_model or "").strip()
    if not resolved_base_url or not resolved_model or not str(api_key or "").strip():
        return None
    return run_provider_conformance_probe(
        provider_id=str(profile.provider_id),
        base_url=resolved_base_url,
        api_key=api_key,
        model=resolved_model,
    )


def _build_eval_config(
    workspace: Path,
    *,
    backend_name: str,
    model: str,
    base_url: str,
    api_key: str,
    max_turns: int,
    system_prompt_mode: str,
) -> AgentConfig:
    if system_prompt_mode == "bare":
        return AgentConfig(
            llm_backend=backend_name,
            llm_base_url=base_url,
            llm_api_key=api_key,
            llm_model=model,
            max_turns=max_turns,
            workspace_root=str(workspace),
            execution_mode="auto",
        )
    if system_prompt_mode != "prod":
        raise ValueError("system_prompt_mode must be 'prod' or 'bare'")
    prompt = _production_system_prompt(workspace, model=model)
    return AgentConfig(
        llm_backend=backend_name,
        llm_base_url=base_url,
        llm_api_key=api_key,
        llm_model=model,
        max_turns=max_turns,
        workspace_root=str(workspace),
        execution_mode="auto",
        system_prompt=prompt,
    )


def _production_system_prompt(workspace: Path, *, model: str = "") -> str:
    prompt_root = Path(__file__).resolve().parents[1] / "core" / "prompts"
    parts: List[str] = []
    for name in ("MAIN_PROMPT.txt", "METIS_DEFAULT.md"):
        path = prompt_root / name
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8"))
    if not parts:
        parts.append(
            "You are Metis, an AI assistant with coding and desktop automation "
            "capabilities. Use tools when they help answer the user."
        )
    snapshot = compile_prompt_runtime(
        "\n\n---\n\n".join(part.strip() for part in parts if part.strip()),
        model_tier=detect_from_model_name(model).tier,
        workspace_root=str(workspace),
    )
    return snapshot.final_system_prompt


def _copy_fixture(src: Path, dst: Path) -> None:
    ignore = shutil.ignore_patterns("__pycache__", ".pytest_cache", "node_modules", "dist", ".git")
    for item in src.iterdir():
        target = dst / item.name
        if item.is_dir():
            shutil.copytree(item, target, ignore=ignore)
        else:
            shutil.copy2(item, target)


def _event_summary(event: Any) -> Dict[str, Any]:
    data: Dict[str, Any] = {"type": str(getattr(event, "type", "") or "")}
    if hasattr(event, "tool_name"):
        data["tool"] = getattr(event, "tool_name", "")
    if hasattr(event, "call_id"):
        data["call_id"] = getattr(event, "call_id", "")
    if isinstance(event, DoneEvent):
        data["turns"] = event.total_turns
        data["tool_calls"] = event.total_tool_calls
        data["total_tokens"] = event.total_tokens
    if isinstance(event, ErrorEvent):
        data["code"] = event.code
        data["message"] = event.message
    return data


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Run Metis eval suites.")
    parser.add_argument("--suite", default="smoke", help="Suite name or JSON path.")
    parser.add_argument("--task", default="", help="Optional task id to run.")
    parser.add_argument("--backend", default=os.environ.get("METIS_EVAL_BACKEND", "openai"))
    parser.add_argument("--model", default=os.environ.get("METIS_EVAL_MODEL", ""))
    parser.add_argument("--base-url", default=os.environ.get("METIS_EVAL_BASE_URL", ""))
    parser.add_argument("--api-key", default=os.environ.get("METIS_EVAL_API_KEY", ""))
    parser.add_argument("--repeat", type=int, default=3)
    parser.add_argument("--output-dir", default="evals-results")
    parser.add_argument("--system-prompt-mode", choices=["prod", "bare"], default="prod")
    parser.add_argument("--compare", default="", help="Previous result JSON to compare against.")
    parser.add_argument("--print-markdown", action="store_true")
    args = parser.parse_args(argv)

    suite = load_suite(args.suite)
    result = run_suite(
        suite,
        backend_name=args.backend,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        repeat=args.repeat,
        task_filter=args.task,
        output_dir=args.output_dir,
        system_prompt_mode=args.system_prompt_mode,
    )
    if args.print_markdown:
        print(markdown_report(result))
    else:
        print(json.dumps({"summary": result["summary"], "reports": result["reports"]}, ensure_ascii=False, indent=2))
    if args.compare:
        print(compare_reports(result, load_result(args.compare)))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

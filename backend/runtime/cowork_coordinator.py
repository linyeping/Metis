from __future__ import annotations

import concurrent.futures
import json
import os
import queue
import re
import subprocess
import time
import uuid
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional

from backend.runtime.agent_loop import (
    AgentConfig,
    ContentDeltaEvent,
    ContentEvent,
    DoneEvent,
    ErrorEvent,
    PermissionRequestEvent,
    RuntimeStatusEvent,
    TextDeltaEvent,
    ToolCallEvent,
    ToolResultEvent,
    _create_backend,
)
from backend.runtime.agent_loop import (
    run as run_agent_loop,
)
from backend.runtime.artifact_registry import ArtifactFilters, list_artifacts, register_artifact
from backend.runtime.cancellation import OperationCancelled
from backend.runtime.execution_profile import LOCAL_DIRECT, LOCAL_VM, LOCAL_WORKTREE
from backend.runtime.local_vm_runner import LocalVmCommand, run_local_vm_command
from backend.runtime.worktree_manager import WorktreeRecord, create_worktree, diff_worktree

COWORK_COORDINATOR_SCHEMA = "metis.cowork_coordinator.v1"
COWORK_PLAN_SCHEMA = "metis.cowork_plan.v2"
COWORK_PLAN_VERSION = 2
COWORK_LEGACY_PLAN_SCHEMA = "metis.cowork_plan.v1"
COWORK_SUMMARY_SCHEMA = "metis.cowork_summary.v1"
COWORK_EXECUTION_SCHEMA = "metis.cowork_execution.v1"
COWORK_SUBRUN_EVENT_SCHEMA = "metis.cowork_subrun_event.v1"
COWORK_SUBRUN_EVENT_VERSION = 1
COWORK_SUBRUN_EVIDENCE_SCHEMA = "metis.cowork_subrun_evidence.v1"
COWORK_SUBRUN_EVIDENCE_VERSION = 1
COWORK_PLANNER_SCHEMA = "metis.cowork_planner.v1"
COWORK_START_DECISION_SCHEMA = "metis.cowork_start_decision.v1"
COWORK_USER_ANSWER_SCHEMA = "metis.cowork_user_answer.v1"
COWORK_SCHEDULER_SCHEMA = "metis.cowork_scheduler.v1"
COWORK_VM_TASK_RUNNER_SCHEMA = "metis.cowork_vm_task_runner.v1"
COWORK_VM_TASK_SCHEMA = "metis.cowork_vm_task.v1"

COWORK_READ_ONLY_TOOLS = [
    "read_file",
    "read_multiple_files",
    "list_directory",
    "glob_search",
    "grep_search",
    "semantic_search",
]
COWORK_CODE_TOOLS = [
    "read_file",
    "read_multiple_files",
    "list_directory",
    "glob_search",
    "grep_search",
    "semantic_search",
    "robust_replace_in_file",
    "write_file",
    "append_to_file",
    "apply_patch",
    "execute_bash_command",
    "run_tests",
    "todo_write",
]
COWORK_ARTIFACT_TOOLS = [
    "pdf_info",
    "pdf_extract_text",
    "pdf_render_pages",
    "pdf_screenshot_page",
    "pdf_merge_split",
    "pdf_create",
    "docx_create",
    "docx_edit",
    "docx_to_pdf",
    "docx_render_pages",
    "docx_inspect_layout",
    "xlsx_create",
    "xlsx_inspect",
    "pptx_create",
    "pptx_inspect",
    "office_report_from_code_run",
]
DOCUMENT_ARTIFACT_EXTENSIONS = {".md", ".markdown", ".txt", ".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".tsv"}


@dataclass(frozen=True)
class CoworkSubrunPlan:
    subrun_id: str
    title: str
    objective: str
    inputs: List[str]
    expected_artifacts: List[str]
    acceptance_criteria: List[str]
    dependencies: List[str]
    prompt: str
    execution_profile: str = "local_worktree"
    status: str = "planned"
    run_id: str = ""
    worktree_id: str = ""
    artifacts: List[Dict[str, Any]] = field(default_factory=list)
    diff: Dict[str, Any] = field(default_factory=dict)
    evidence: Dict[str, Any] = field(default_factory=dict)
    vm_tasks: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "subrun_id": self.subrun_id,
            "title": self.title,
            "objective": self.objective,
            "inputs": list(self.inputs),
            "expected_artifacts": list(self.expected_artifacts),
            "acceptance_criteria": list(self.acceptance_criteria),
            "dependencies": list(self.dependencies),
            "prompt": self.prompt,
            "execution_profile": self.execution_profile,
            "status": self.status,
            "run_id": self.run_id,
            "worktree_id": self.worktree_id,
            "artifacts": list(self.artifacts),
            "diff": dict(self.diff),
            "evidence": dict(self.evidence),
            "vm_tasks": list(self.vm_tasks),
        }


def build_cowork_plan(
    goal: str,
    *,
    run_id: str = "",
    session_id: str = "",
    max_subruns: int = 3,
    execution_profile: str = LOCAL_DIRECT,
    base_config: Optional[AgentConfig] = None,
    workspace_root: str = "",
) -> Dict[str, Any]:
    goal_text = str(goal or "").strip()
    limit = _bounded_subrun_limit(max_subruns)
    default_profile = _subrun_execution_profile(execution_profile)
    planner_error = ""
    if base_config is not None:
        try:
            raw_text = _call_bounded_planner_model(
                goal=goal_text,
                max_subruns=limit,
                execution_profile=execution_profile,
                workspace_root=workspace_root,
                base_config=base_config,
            )
            subruns = _normalize_planner_subruns(
                _parse_planner_json(raw_text),
                goal=goal_text,
                max_subruns=limit,
                default_profile=default_profile,
                requested_execution_profile=execution_profile,
            )
            planner = _planner_metadata(
                mode="llm_bounded",
                max_subruns=limit,
                base_config=base_config,
            )
        except Exception as exc:
            planner_error = f"{type(exc).__name__}: {exc}"
            subruns = _deterministic_subruns(
                goal_text,
                max_subruns=limit,
                default_profile=default_profile,
            )
            planner = _planner_metadata(
                mode="deterministic_fallback",
                max_subruns=limit,
                base_config=base_config,
                error=planner_error,
            )
    else:
        subruns = _deterministic_subruns(
            goal_text,
            max_subruns=limit,
            default_profile=default_profile,
        )
        planner = _planner_metadata(mode="deterministic_fallback", max_subruns=limit, error="base_config unavailable")
    return {
        "schema": COWORK_PLAN_SCHEMA,
        "version": COWORK_PLAN_VERSION,
        "coordinator_schema": COWORK_COORDINATOR_SCHEMA,
        "run_id": run_id,
        "session_id": session_id,
        "goal": goal_text,
        "status": "planned",
        "created_at": time.time(),
        "subruns": subruns,
        "planner": planner,
        "merge_policy": {
            "diffs": "review_then_promote",
            "artifacts": "register_all",
            "conflicts": "main_run_decides",
        },
    }


def summarize_cowork_results(
    *,
    plan: Dict[str, Any],
    workspace_root: str,
    run_id: str = "",
    session_id: str = "",
) -> Dict[str, Any]:
    """Persist a stable summary artifact for a coordinator plan/subrun result set."""
    root = Path(workspace_root or ".").expanduser().resolve()
    out_dir = root / ".metis" / "cowork"
    out_dir.mkdir(parents=True, exist_ok=True)
    summary_id = f"cowork_{uuid.uuid4().hex[:12]}"
    subruns = plan.get("subruns") if isinstance(plan.get("subruns"), list) else []
    payload = {
        "schema": COWORK_SUMMARY_SCHEMA,
        "summary_id": summary_id,
        "run_id": run_id or str(plan.get("run_id") or ""),
        "session_id": session_id or str(plan.get("session_id") or ""),
        "goal": str(plan.get("goal") or ""),
        "created_at": time.time(),
        "subrun_count": len(subruns),
        "subruns": subruns,
        "artifacts": _collect_subrun_artifacts(subruns),
        "diffs": _collect_subrun_diffs(subruns),
    }
    payload["user_answer"] = _cowork_user_answer_payload(payload)
    path = out_dir / f"{summary_id}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    artifact = register_artifact(
        kind="report",
        title="Cowork coordinator summary",
        path=str(path),
        mime="application/json",
        run_id=payload["run_id"],
        session_id=payload["session_id"],
        metadata={"summary_id": summary_id, "source": "cowork_coordinator"},
        workspace_root=str(root),
    )
    return {**payload, "artifact": artifact}


def decide_cowork_start(
    goal: str,
    *,
    workspace_root: str = "",
    base_config: Optional[AgentConfig] = None,
) -> Dict[str, Any]:
    """Classify a Cowork request before creating plans, worktrees, or subruns."""
    goal_text = _clean_goal_text(goal)
    if _cowork_goal_needs_clarification(goal_text):
        return {
            "schema": COWORK_START_DECISION_SCHEMA,
            "mode": "clarify",
            "reason": "goal_is_underspecified",
            "message": "Cowork needs a clearer target before starting subruns.",
            "question": _cowork_clarification_question(goal_text),
            "confidence": 0.9,
        }
    if _cowork_goal_requires_project_analysis(goal_text):
        return {
            "schema": COWORK_START_DECISION_SCHEMA,
            "mode": "cowork_plan",
            "reason": "project_analysis_or_workspace_action_required",
            "message": "Cowork will inspect the workspace with subruns.",
            "confidence": 0.82,
        }
    if _is_direct_answer_goal(goal_text):
        return {
            "schema": COWORK_START_DECISION_SCHEMA,
            "mode": "direct_answer",
            "reason": "answerable_without_project_subruns",
            "message": "Cowork can answer directly without creating subruns.",
            "confidence": 0.78,
        }
    return {
        "schema": COWORK_START_DECISION_SCHEMA,
        "mode": "cowork_plan",
        "reason": "specific_task_requires_cowork_execution",
        "message": "Cowork will create a bounded plan.",
        "confidence": 0.68,
    }


def _cowork_direct_answer_text(goal: str, *, base_config: Optional[AgentConfig], workspace_root: str) -> str:
    goal_text = _clean_goal_text(goal)
    if base_config is not None:
        try:
            answer = _call_direct_answer_model(goal=goal_text, workspace_root=workspace_root, base_config=base_config)
            cleaned = _extract_cowork_answer_section(answer, allow_body_without_heading=True)
            if cleaned:
                return cleaned
        except Exception:
            pass
    return _fallback_direct_answer(goal_text)


def _call_direct_answer_model(*, goal: str, workspace_root: str, base_config: AgentConfig) -> str:
    config = replace(
        base_config,
        enabled_tools=[],
        temperature=min(max(float(base_config.temperature or 0.2), 0.0), 0.4),
        max_tokens=min(max(256, int(base_config.max_tokens or 1024)), 1200),
        timeout=max(1.0, min(float(base_config.timeout or 20.0), 20.0)),
        max_turns=1,
        system_prompt="",
        surface_mode="cowork",
        execution_mode="answer",
        workspace_root=workspace_root or base_config.workspace_root,
        source_workspace_root=workspace_root or base_config.source_workspace_root,
    )
    backend = _create_backend(config)
    response = backend.chat(
        [
            {
                "role": "system",
                "content": (
                    "You are Metis Cowork's startup gate. The request does not need workspace subruns. "
                    "Answer the user directly in natural language. Do not write a report, task log, diff, evidence list, "
                    "or markdown wrapper. If the user asks in Chinese, answer in Chinese. Keep it concise and useful."
                ),
            },
            {
                "role": "user",
                "content": goal,
            },
        ],
        tools=None,
        temperature=config.temperature,
        max_tokens=config.max_tokens,
        timeout=config.timeout,
    )
    return str(response.content or "")


def _fallback_direct_answer(goal: str) -> str:
    text = _clean_goal_text(goal)
    if "意义" in text or "为什么" in text or "为何" in text:
        return "这个任务的意义在于把模糊目标转成清晰判断：先确认为什么要做、做完能带来什么价值，再决定是否需要进一步拆解执行。"
    if re.search(r"[\u4e00-\u9fff]", text):
        return "这个问题可以直接回答，不需要启动 Cowork 子任务。我的判断是：先把问题本身说清楚，再决定是否需要项目分析、产物或代码修改。"
    return "This can be answered directly without starting Cowork subruns. The useful next step is to clarify the goal, expected value, and whether project analysis or file changes are actually needed."


def _planner_metadata(
    *,
    mode: str,
    max_subruns: int,
    base_config: Optional[AgentConfig] = None,
    error: str = "",
) -> Dict[str, Any]:
    payload: Dict[str, Any] = {
        "schema": COWORK_PLANNER_SCHEMA,
        "mode": mode,
        "bounded": True,
        "max_subruns": _bounded_subrun_limit(max_subruns),
        "required_subrun_fields": [
            "subrun_id",
            "title",
            "objective",
            "inputs",
            "expected_artifacts",
            "acceptance_criteria",
            "execution_profile",
            "dependencies",
        ],
        "optional_subrun_fields": [
            "vm_tasks",
        ],
        "final_answer_contract": {
            "main_chat": "user-facing natural answer only",
            "details": "reports, diffs, evidence, and subrun markdown stay in artifacts or activity details",
        },
    }
    if base_config is not None:
        payload.update(
            {
                "backend": str(base_config.llm_backend or ""),
                "model": str(base_config.llm_model or ""),
            }
        )
    if error:
        payload["fallback_reason"] = _truncate(str(error), 500)
    return payload


def _call_bounded_planner_model(
    *,
    goal: str,
    max_subruns: int,
    execution_profile: str,
    workspace_root: str,
    base_config: AgentConfig,
) -> str:
    config = replace(
        base_config,
        enabled_tools=[],
        temperature=0.0,
        max_tokens=min(max(512, int(base_config.max_tokens or 2048)), 2048),
        timeout=max(1.0, min(float(base_config.timeout or 20.0), 20.0)),
        max_turns=1,
        system_prompt="",
        surface_mode="cowork",
        execution_mode="plan",
        workspace_root=workspace_root or base_config.workspace_root,
        source_workspace_root=workspace_root or base_config.source_workspace_root,
    )
    backend = _create_backend(config)
    response = backend.chat(
        [
            {
                "role": "system",
                "content": _planner_system_prompt(),
            },
            {
                "role": "user",
                "content": _planner_user_prompt(
                    goal=goal,
                    max_subruns=max_subruns,
                    execution_profile=execution_profile,
                    workspace_root=workspace_root,
                ),
            },
        ],
        tools=None,
        temperature=0.0,
        max_tokens=config.max_tokens,
        timeout=config.timeout,
    )
    return str(response.content or "")


def _planner_system_prompt() -> str:
    return (
        "You are the bounded planner for Metis Cowork. Produce only valid JSON. "
        "Do not use markdown. Do not execute tools. Keep the plan local-first, reviewable, and small."
    )


def _planner_user_prompt(
    *,
    goal: str,
    max_subruns: int,
    execution_profile: str,
    workspace_root: str,
) -> str:
    allowed_profiles = ", ".join(_allowed_subrun_profiles(execution_profile))
    return (
        "Create a bounded Cowork plan for this user goal.\n\n"
        f"Goal:\n{goal}\n\n"
        f"Workspace root: {workspace_root or '(current workspace)'}\n"
        f"Maximum subruns: {max_subruns}\n"
        f"Allowed execution_profile values: {allowed_profiles}\n\n"
        "Return JSON with exactly this shape:\n"
        "{\n"
        '  "subruns": [\n'
        "    {\n"
        '      "title": "short task title",\n'
        '      "objective": "specific outcome for this subrun",\n'
        '      "inputs": ["needed source, file, context, or prior output"],\n'
        '      "expected_artifacts": ["natural final answer, diff, report, document, stdout evidence, etc."],\n'
        '      "acceptance_criteria": ["concrete check that proves completion"],\n'
        '      "execution_profile": "local_worktree or local_vm",\n'
        '      "dependencies": ["1-based prior subrun numbers only, e.g. 1"],\n'
        '      "vm_tasks": [\n'
        "        {\n"
        '          "command": "pytest -q or npm test or python script.py",\n'
        '          "cwd": "",\n'
        '          "timeout": 120,\n'
        '          "allow_network": false,\n'
        '          "collect_artifacts": false,\n'
        '          "artifact_patterns": ["output/**"],\n'
        '          "expected_stdout_contains": ""\n'
        "        }\n"
        "      ]\n"
        "    }\n"
        "  ]\n"
        "}\n\n"
        "Rules:\n"
        "- Use 1 to the maximum subruns only.\n"
        "- Dependencies may reference only earlier subrun numbers; use [] when independent.\n"
        "- For a simple question or conceptual request, use one answer-focused subrun.\n"
        "- Do not create a report/document/diff unless the user explicitly asks for that artifact or the task truly changes files.\n"
        "- Use local_vm only for test/build/data/command-heavy validation when it is allowed.\n"
        "- When execution_profile is local_vm, include vm_tasks with concrete commands to run inside MetisRuntime.\n"
        "- Every subrun must produce either a natural final answer, diff evidence, artifact evidence, stdout/test evidence, or a report.\n"
        "- The coordinator will show only the final natural answer in chat; report/diff/evidence belongs in artifacts or activity details.\n"
        "- Acceptance criteria must be observable and specific."
    )


def _parse_planner_json(text: str) -> Dict[str, Any]:
    raw = str(text or "").strip()
    if not raw:
        raise ValueError("planner returned empty response")
    candidates = [raw]
    if "```" in raw:
        chunks = raw.split("```")
        candidates.extend(chunk.strip().removeprefix("json").strip() for chunk in chunks if "{" in chunk and "}" in chunk)
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        candidates.append(raw[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("planner response was not valid JSON object")


def _normalize_planner_subruns(
    payload: Dict[str, Any],
    *,
    goal: str,
    max_subruns: int,
    default_profile: str,
    requested_execution_profile: str,
) -> List[Dict[str, Any]]:
    raw_subruns = payload.get("subruns")
    if not isinstance(raw_subruns, list):
        raw_subruns = payload.get("tasks")
    if not isinstance(raw_subruns, list):
        raise ValueError("planner JSON missing subruns list")

    limit = _bounded_subrun_limit(max_subruns)
    rows: List[Dict[str, Any]] = []
    raw_dependencies: List[Any] = []
    for index, raw in enumerate(raw_subruns[:limit], 1):
        if not isinstance(raw, dict):
            continue
        title = _clean_plan_text(raw.get("title") or raw.get("name") or raw.get("task") or f"Subrun {index}", 90)
        objective = _clean_plan_text(raw.get("objective") or raw.get("goal") or title, 500)
        inputs = _bounded_string_list(raw.get("inputs"), max_items=6, max_chars=220)
        expected_artifacts = _bounded_string_list(
            raw.get("expected_artifacts") or raw.get("artifacts") or raw.get("deliverables"),
            max_items=6,
            max_chars=220,
        )
        acceptance_criteria = _bounded_string_list(
            raw.get("acceptance_criteria") or raw.get("acceptance") or raw.get("checks"),
            max_items=6,
            max_chars=260,
        )
        profile = _normalize_planner_execution_profile(
            raw.get("execution_profile") or raw.get("executionProfile"),
            default_profile=default_profile,
            requested_execution_profile=requested_execution_profile,
        )
        vm_tasks = _normalize_vm_tasks(
            raw.get("vm_tasks")
            or raw.get("vmTasks")
            or raw.get("commands")
            or raw.get("local_vm_tasks")
            or raw.get("localVmTasks")
            or raw.get("command")
        )
        if profile == LOCAL_VM and not vm_tasks:
            vm_tasks = _default_vm_tasks(goal, title, profile)
        inputs = inputs or _default_subrun_inputs(goal, index)
        expected_artifacts = expected_artifacts or _default_expected_artifacts(goal, title)
        acceptance_criteria = acceptance_criteria or _default_acceptance_criteria(title, goal=goal)
        row = CoworkSubrunPlan(
            subrun_id=f"subrun_{uuid.uuid4().hex[:10]}",
            title=title,
            objective=objective,
            inputs=inputs,
            expected_artifacts=expected_artifacts,
            acceptance_criteria=acceptance_criteria,
            dependencies=[],
            prompt="",
            execution_profile=profile,
            vm_tasks=vm_tasks,
        ).to_dict()
        raw_dependencies.append(raw.get("dependencies") or raw.get("depends_on") or raw.get("dependsOn") or [])
        rows.append(row)

    if not rows:
        raise ValueError("planner JSON contained no usable subruns")
    _apply_normalized_dependencies(rows, raw_dependencies)
    for row in rows:
        row["prompt"] = _subrun_prompt(
            goal,
            str(row.get("title") or ""),
            objective=str(row.get("objective") or ""),
            inputs=[str(item) for item in row.get("inputs", []) if item],
            expected_artifacts=[str(item) for item in row.get("expected_artifacts", []) if item],
            acceptance_criteria=[str(item) for item in row.get("acceptance_criteria", []) if item],
            dependencies=[str(item) for item in row.get("dependencies", []) if item],
        )
    return rows


def _deterministic_subruns(goal: str, *, max_subruns: int, default_profile: str) -> List[Dict[str, Any]]:
    tasks = _task_candidates(goal, max_subruns=max_subruns)
    chain_by_default = len([line for line in str(goal or "").splitlines() if line.strip(" -\t")]) < 2
    rows: List[Dict[str, Any]] = []
    for index, title in enumerate(tasks, 1):
        profile = _deterministic_execution_profile(title, default_profile=default_profile)
        dependencies = _deterministic_dependencies(title, rows, chain_by_default=chain_by_default, index=index)
        objective = _deterministic_objective(goal, title)
        inputs = _default_subrun_inputs(goal, index)
        expected_artifacts = _default_expected_artifacts(goal, title)
        acceptance_criteria = _default_acceptance_criteria(title, goal=goal)
        vm_tasks = _default_vm_tasks(goal, title, profile)
        rows.append(
            CoworkSubrunPlan(
                subrun_id=f"subrun_{uuid.uuid4().hex[:10]}",
                title=title,
                objective=objective,
                inputs=inputs,
                expected_artifacts=expected_artifacts,
                acceptance_criteria=acceptance_criteria,
                dependencies=dependencies,
                prompt=_subrun_prompt(
                    goal,
                    title,
                    objective=objective,
                    inputs=inputs,
                    expected_artifacts=expected_artifacts,
                    acceptance_criteria=acceptance_criteria,
                    dependencies=dependencies,
                ),
                execution_profile=profile,
                vm_tasks=vm_tasks,
            ).to_dict()
        )
    return rows


def iter_local_cowork_events(
    goal: str,
    *,
    workspace_root: str,
    source_workspace_root: str = "",
    run_id: str = "",
    session_id: str = "",
    execution_profile: str = LOCAL_DIRECT,
    max_subruns: int = 3,
    max_parallel_subruns: int = 3,
    cancelled: Callable[[], bool] | None = None,
    cancel_event: Any = None,
    base_config: Optional[AgentConfig] = None,
    resume_state: Optional[Dict[str, Any]] = None,
) -> Iterator[Dict[str, Any]]:
    """Execute the local Cowork path and yield desktop stream events."""
    source_root = Path(source_workspace_root or workspace_root or ".").expanduser().resolve()
    default_subrun_profile = _subrun_execution_profile(execution_profile)
    resume_payload = resume_state if isinstance(resume_state, dict) else {}
    resumed = bool(resume_payload)
    plan = _plan_from_resume_state(resume_payload, run_id=run_id, session_id=session_id) if resumed else None
    if plan is None:
        decision = decide_cowork_start(goal, workspace_root=str(source_root), base_config=base_config)
        if decision["mode"] in {"direct_answer", "clarify"}:
            phase = "answering" if decision["mode"] == "direct_answer" else "clarifying"
            yield _runtime_status(
                phase,
                str(decision.get("message") or ""),
                details={
                    "schema": COWORK_START_DECISION_SCHEMA,
                    "decision": decision,
                },
            )
            _raise_if_cancelled(cancelled)
            if decision["mode"] == "direct_answer":
                text = _cowork_direct_answer_text(goal, base_config=base_config, workspace_root=str(source_root))
            else:
                text = str(decision.get("question") or _cowork_clarification_question(goal))
            yield {"type": "content", "kind": "content", "payload": {"text": text}}
            yield {
                "type": "done",
                "kind": "done",
                "payload": {
                    "turns": 1,
                    "tool_calls": 0,
                    "context_ledger": {
                        "cowork_start_decision": decision["mode"],
                        "cowork_start_reason": str(decision.get("reason") or ""),
                    },
                },
            }
            return
        plan = build_cowork_plan(
            goal,
            run_id=run_id,
            session_id=session_id,
            max_subruns=max_subruns,
            execution_profile=execution_profile,
            base_config=base_config,
            workspace_root=str(source_root),
        )

    yield _runtime_status(
        "resuming" if resumed else "planning",
        "Cowork run resumed from scheduler state." if resumed else "Cowork plan created.",
        details={
            "schema": COWORK_EXECUTION_SCHEMA,
            "plan": plan,
            "default_execution_profile": default_subrun_profile,
            "allowed_execution_profiles": _allowed_subrun_profiles(execution_profile),
            "resume": _resume_details(resume_payload),
            "scheduler": {
                "schema": COWORK_SCHEDULER_SCHEMA,
                "mode": "dag_parallel",
                "max_parallel_subruns": _bounded_parallelism(max_parallel_subruns, len(plan.get("subruns") or [])),
            },
        },
    )

    subruns = plan.get("subruns") if isinstance(plan.get("subruns"), list) else []
    _raise_if_cancelled(cancelled)
    for index, subrun in enumerate(subruns, 1):
        if not isinstance(subrun, dict):
            continue
        planned_profile = _normalize_planner_execution_profile(
            subrun.get("execution_profile"),
            default_profile=default_subrun_profile,
            requested_execution_profile=execution_profile,
        )
        subrun["execution_profile"] = planned_profile
        yield _subrun_event(
            "subrun_planned",
            subrun=subrun,
            index=index,
            total=len(subruns),
            progress=0,
            status="planned",
            stage="planned",
            result={"execution_profile": planned_profile},
        )

    state_path = _write_cowork_scheduler_state(
        plan=plan,
        workspace_root=source_root,
        run_id=run_id,
        session_id=session_id,
        status="planned",
    )
    yield _runtime_status(
        "scheduling",
        "Cowork DAG scheduler resumed." if resumed else "Cowork DAG scheduler started.",
        details={
            "schema": COWORK_SCHEDULER_SCHEMA,
            "mode": "dag_parallel",
            "max_parallel_subruns": _bounded_parallelism(max_parallel_subruns, len(subruns)),
            "state_path": state_path,
            "resume": _resume_details(resume_payload),
        },
    )
    yield from _run_cowork_dag_scheduler(
        plan=plan,
        source_root=source_root,
        run_id=run_id,
        session_id=session_id,
        requested_execution_profile=execution_profile,
        default_subrun_profile=default_subrun_profile,
        max_parallel_subruns=max_parallel_subruns,
        cancelled=cancelled,
        cancel_event=cancel_event,
        base_config=base_config,
        resumed=resumed,
    )

    _raise_if_cancelled(cancelled)
    yield _runtime_status(
        "summarizing",
        "Cowork subruns complete; collecting artifacts and diffs.",
        details={"schema": COWORK_EXECUTION_SCHEMA, "subrun_count": len(subruns)},
    )
    summary = summarize_cowork_results(
        plan=plan,
        workspace_root=str(source_root),
        run_id=run_id,
        session_id=session_id,
    )
    yield {
        "type": "artifact_created",
        "kind": "artifact_created",
        "payload": {
            "artifact": summary.get("artifact", {}),
            "source": "cowork_coordinator",
            "summary_id": summary.get("summary_id", ""),
        },
    }
    text = _cowork_summary_text(summary)
    yield {"type": "content", "kind": "content", "payload": {"text": text}}
    yield {
        "type": "done",
        "kind": "done",
        "payload": {
            "turns": 1,
            "tool_calls": len(subruns),
            "context_ledger": {
                "cowork_summary_id": summary.get("summary_id", ""),
                "artifact_id": (summary.get("artifact") or {}).get("artifact_id", ""),
            },
        },
    }


def _run_cowork_dag_scheduler(
    *,
    plan: Dict[str, Any],
    source_root: Path,
    run_id: str,
    session_id: str,
    requested_execution_profile: str,
    default_subrun_profile: str,
    max_parallel_subruns: int,
    cancelled: Callable[[], bool] | None,
    base_config: Optional[AgentConfig],
    cancel_event: Any = None,
    resumed: bool = False,
) -> Iterator[Dict[str, Any]]:
    subruns = [subrun for subrun in plan.get("subruns", []) if isinstance(subrun, dict)]
    if not subruns:
        return

    max_parallel = _bounded_parallelism(max_parallel_subruns, len(subruns))
    indexed = {str(subrun.get("subrun_id") or f"subrun_{index}"): (index, subrun) for index, subrun in enumerate(subruns, 1)}
    status_by_id = {
        subrun_id: _scheduler_initial_status(subrun, resumed=resumed)
        for subrun_id, (_index, subrun) in indexed.items()
    }
    pending = {subrun_id for subrun_id, status in status_by_id.items() if status not in {"succeeded", "failed"}}
    running: Dict[concurrent.futures.Future[str], str] = {}
    emitted: "queue.Queue[Dict[str, Any]]" = queue.Queue()

    def emit(event: Dict[str, Any]) -> None:
        emitted.put(event)

    def drain_events() -> Iterator[Dict[str, Any]]:
        while True:
            try:
                yield emitted.get_nowait()
            except queue.Empty:
                break

    def schedule_ready(executor: concurrent.futures.ThreadPoolExecutor) -> None:
        for subrun_id in list(indexed.keys()):
            if len(running) >= max_parallel:
                return
            if subrun_id not in pending:
                continue
            index, subrun = indexed[subrun_id]
            deps = _subrun_dependencies(subrun, indexed)
            dep_statuses = [status_by_id.get(dep, "planned") for dep in deps]
            if any(status in {"failed", "canceled"} for status in dep_statuses):
                pending.remove(subrun_id)
                status_by_id[subrun_id] = "failed"
                _emit_dependency_failed(
                    emit,
                    subrun=subrun,
                    index=index,
                    total=len(subruns),
                    dependencies=deps,
                    status_by_id=status_by_id,
                )
                continue
            if any(status != "succeeded" for status in dep_statuses):
                continue
            pending.remove(subrun_id)
            status_by_id[subrun_id] = "running"
            future = executor.submit(
                _execute_cowork_subrun,
                plan=plan,
                subrun=subrun,
                index=index,
                total=len(subruns),
                source_root=source_root,
                run_id=run_id,
                session_id=session_id,
                requested_execution_profile=requested_execution_profile,
                default_subrun_profile=default_subrun_profile,
                cancelled=cancelled,
                cancel_event=cancel_event,
                base_config=base_config,
                emit=emit,
            )
            running[future] = subrun_id

    with concurrent.futures.ThreadPoolExecutor(max_workers=max_parallel, thread_name_prefix="metis-cowork") as executor:
        if resumed:
            for subrun_id, status in status_by_id.items():
                if status not in {"succeeded", "failed"}:
                    continue
                index, subrun = indexed[subrun_id]
                yield _resumed_terminal_subrun_event(
                    subrun=subrun,
                    index=index,
                    total=len(subruns),
                    status=status,
                )
        while pending or running or not emitted.empty():
            _raise_if_cancelled(cancelled)
            schedule_ready(executor)
            yield from drain_events()

            completed = [future for future in running if future.done()]
            for future in completed:
                subrun_id = running.pop(future)
                try:
                    status = future.result()
                except OperationCancelled:
                    status = "canceled"
                except Exception as exc:
                    index, subrun = indexed[subrun_id]
                    subrun["status"] = "failed"
                    subrun["error"] = f"{type(exc).__name__}: {exc}"
                    evidence = _collect_subrun_evidence(
                        subrun=subrun,
                        diff={},
                        artifacts=[],
                        agent_result={},
                        vm_result={},
                        failure_reasons=[_failure_reason("SUBRUN_EXCEPTION", subrun["error"], source="cowork_scheduler")],
                    )
                    subrun["evidence"] = evidence
                    emit(
                        _subrun_event(
                            "subrun_failed",
                            subrun=subrun,
                            index=index,
                            total=len(subruns),
                            progress=100,
                            status="failed",
                            stage="failed",
                            result={"execution_profile": subrun.get("execution_profile") or LOCAL_WORKTREE, "error": subrun["error"], "evidence": evidence},
                        )
                    )
                    status = "failed"
                status_by_id[subrun_id] = status if status in {"succeeded", "failed", "canceled"} else "failed"
                _write_cowork_scheduler_state(
                    plan=plan,
                    workspace_root=source_root,
                    run_id=run_id,
                    session_id=session_id,
                    status="running" if pending or running else "finished",
                )
            yield from drain_events()

            if pending and not running:
                schedule_ready(executor)
                yield from drain_events()
                if running:
                    continue
                unresolved = list(pending)
                for subrun_id in unresolved:
                    pending.remove(subrun_id)
                    status_by_id[subrun_id] = "failed"
                    index, subrun = indexed[subrun_id]
                    _emit_dependency_failed(
                        emit,
                        subrun=subrun,
                        index=index,
                        total=len(subruns),
                        dependencies=_subrun_dependencies(subrun, indexed),
                        status_by_id=status_by_id,
                        code="SUBRUN_DEPENDENCY_UNRESOLVED",
                    )
                yield from drain_events()

            if running and emitted.empty():
                time.sleep(0.03)


def _execute_cowork_subrun(
    *,
    plan: Dict[str, Any],
    subrun: Dict[str, Any],
    index: int,
    total: int,
    source_root: Path,
    run_id: str,
    session_id: str,
    requested_execution_profile: str,
    default_subrun_profile: str,
    cancelled: Callable[[], bool] | None,
    base_config: Optional[AgentConfig],
    emit: Callable[[Dict[str, Any]], None],
    cancel_event: Any = None,
) -> str:
    subrun_profile = _normalize_planner_execution_profile(
        subrun.get("execution_profile"),
        default_profile=default_subrun_profile,
        requested_execution_profile=requested_execution_profile,
    )
    subrun["execution_profile"] = subrun_profile
    task_id = str(subrun.get("subrun_id") or f"subrun_{index}")
    emit(
        _subrun_event(
            "subrun_running",
            subrun=subrun,
            index=index,
            total=total,
            progress=0,
            status="running",
            stage="started",
            result={"execution_profile": subrun_profile},
        )
    )
    try:
        _raise_if_cancelled(cancelled)
        record = create_worktree(
            str(source_root),
            run_id=f"{run_id}-{task_id}" if run_id else task_id,
            session_id=session_id,
            label=f"cowork-{index}",
        )
        subrun.update(
            {
                "status": "running",
                "worktree_id": record.worktree_id,
                "worktree": record.to_dict(),
                "worktree_workspace_root": record.worktree_workspace_root,
            }
        )
        emit(
            _subrun_event(
                "subrun_running",
                subrun=subrun,
                index=index,
                total=total,
                progress=25,
                status="running",
                stage="worktree_created",
                result={
                    "worktree_id": record.worktree_id,
                    "worktree_workspace_root": record.worktree_workspace_root,
                },
            )
        )

        agent_result: Dict[str, Any] = {}
        if base_config is not None:
            _raise_if_cancelled(cancelled)
            enabled_tools = _subrun_enabled_tools(goal=str(plan.get("goal") or ""), subrun=subrun)
            emit(
                _subrun_event(
                    "subrun_running",
                    subrun=subrun,
                    index=index,
                    total=total,
                    progress=40,
                    status="running",
                    stage="agent_running",
                    result={
                        "agent": {
                            "status": "running",
                            "workspace_root": record.worktree_workspace_root,
                            "enabled_tools": enabled_tools,
                        }
                    },
                )
            )
            agent_result = _run_subrun_agent(
                goal=str(plan.get("goal") or ""),
                subrun=subrun,
                record=record,
                source_root=source_root,
                run_id=run_id,
                session_id=session_id,
                base_config=base_config,
                execution_profile=subrun_profile,
                enabled_tools=enabled_tools,
                cancelled=cancelled,
            )
            subrun["agent"] = agent_result
            emit(
                _subrun_event(
                    "subrun_running",
                    subrun=subrun,
                    index=index,
                    total=total,
                    progress=70,
                    status="running",
                    stage="agent_finished",
                    result={"agent": agent_result},
                )
            )

        vm_result: Dict[str, Any] = {}
        if subrun_profile == LOCAL_VM and (not agent_result or agent_result.get("ok")):
            tasks = _subrun_vm_tasks(subrun)
            if tasks:
                _raise_if_cancelled(cancelled)
                emit(
                    _subrun_event(
                        "subrun_running",
                        subrun=subrun,
                        index=index,
                        total=total,
                        progress=80,
                        status="running",
                        stage="local_vm_task_running",
                        result={
                            "runner": "local_vm",
                            "backend": "metis_wsl",
                            "vm_tasks": tasks,
                        },
                    )
                )
                vm_result = _run_subrun_local_vm(subrun, record, tasks=tasks, cancelled=cancelled, cancel_event=cancel_event)
                subrun["local_vm"] = vm_result

        _raise_if_cancelled(cancelled)
        document_artifacts = _register_subrun_document_artifacts(
            record=record,
            run_id=run_id,
            session_id=session_id,
            subrun_id=task_id,
        )
        agent_artifacts = agent_result.get("registered_artifacts") if isinstance(agent_result.get("registered_artifacts"), list) else []
        real_artifacts = _dedupe_artifacts([*agent_artifacts, *document_artifacts])
        diff = _safe_diff(source_root, record)
        failure_reasons = _subrun_failure_reasons(agent_result=agent_result, vm_result=vm_result, diff=diff)
        evidence = _collect_subrun_evidence(
            subrun=subrun,
            diff=diff,
            artifacts=real_artifacts,
            agent_result=agent_result,
            vm_result=vm_result,
            failure_reasons=failure_reasons,
        )
        failed = (
            (bool(agent_result) and not bool(agent_result.get("ok")))
            or (bool(vm_result) and not bool(vm_result.get("ok")))
            or _has_fatal_failure(failure_reasons)
        )
        if not _subrun_has_success_evidence(evidence) and not evidence.get("failure_reasons"):
            failure_reasons.append(
                _failure_reason(
                    "SUBRUN_MISSING_EVIDENCE",
                    "Subrun produced no final answer, diff, artifact, stdout/test evidence, or failure reason.",
                    source="cowork_coordinator",
                )
            )
            evidence = _collect_subrun_evidence(
                subrun=subrun,
                diff=diff,
                artifacts=real_artifacts,
                agent_result=agent_result,
                vm_result=vm_result,
                failure_reasons=failure_reasons,
            )
            failed = True
        subrun["artifacts"] = real_artifacts
        subrun["diff"] = diff
        subrun["evidence"] = evidence
        subrun["status"] = "failed" if failed else "succeeded"
        artifact = _write_subrun_artifact(
            subrun=subrun,
            record=record,
            run_id=run_id,
            session_id=session_id,
            goal=str(plan.get("goal") or ""),
            vm_result=vm_result,
        )
        subrun["artifacts"] = _dedupe_artifacts([*real_artifacts, artifact])
        result = {
            "execution_profile": subrun_profile,
            "worktree_id": record.worktree_id,
            "worktree_workspace_root": record.worktree_workspace_root,
            "worktree": record.to_dict(),
            "artifacts": subrun["artifacts"],
            "diff": diff,
            "evidence": evidence,
        }
        if agent_result:
            result["agent"] = agent_result
        if vm_result:
            result["local_vm"] = vm_result
        terminal_status = "failed" if failed else "succeeded"
        emit(
            _subrun_event(
                "subrun_failed" if failed else "subrun_succeeded",
                subrun=subrun,
                index=index,
                total=total,
                progress=100,
                status=terminal_status,
                stage="finished",
                result=result,
            )
        )
        return terminal_status
    except OperationCancelled:
        subrun["status"] = "canceled"
        evidence = _collect_subrun_evidence(
            subrun=subrun,
            diff={},
            artifacts=[],
            agent_result={},
            vm_result={},
            failure_reasons=[
                _failure_reason(
                    "SUBRUN_CANCELED",
                    "Cowork subrun was canceled before it could finish.",
                    source="cowork_coordinator",
                )
            ],
        )
        subrun["evidence"] = evidence
        emit(
            _subrun_event(
                "subrun_canceled",
                subrun=subrun,
                index=index,
                total=total,
                progress=100,
                status="canceled",
                stage="canceled",
                result={"execution_profile": subrun_profile, "evidence": evidence},
            )
        )
        return "canceled"
    except Exception as exc:
        subrun["status"] = "failed"
        subrun["error"] = f"{type(exc).__name__}: {exc}"
        evidence = _collect_subrun_evidence(
            subrun=subrun,
            diff={},
            artifacts=[],
            agent_result={},
            vm_result={},
            failure_reasons=[
                _failure_reason(
                    "SUBRUN_EXCEPTION",
                    subrun["error"],
                    source="cowork_coordinator",
                )
            ],
        )
        subrun["evidence"] = evidence
        emit(
            _subrun_event(
                "subrun_failed",
                subrun=subrun,
                index=index,
                total=total,
                progress=100,
                status="failed",
                stage="failed",
                result={
                    "execution_profile": subrun_profile,
                    "error": subrun["error"],
                    "evidence": evidence,
                },
            )
        )
        return "failed"


def _emit_dependency_failed(
    emit: Callable[[Dict[str, Any]], None],
    *,
    subrun: Dict[str, Any],
    index: int,
    total: int,
    dependencies: List[str],
    status_by_id: Dict[str, str],
    code: str = "SUBRUN_DEPENDENCY_FAILED",
) -> None:
    dep_status = {dep: status_by_id.get(dep, "unknown") for dep in dependencies}
    message = f"Subrun dependencies are not successful: {dep_status}"
    subrun["status"] = "failed"
    evidence = _collect_subrun_evidence(
        subrun=subrun,
        diff={},
        artifacts=[],
        agent_result={},
        vm_result={},
        failure_reasons=[_failure_reason(code, message, source="cowork_scheduler")],
    )
    subrun["evidence"] = evidence
    emit(
        _subrun_event(
            "subrun_failed",
            subrun=subrun,
            index=index,
            total=total,
            progress=100,
            status="failed",
            stage="dependency_failed",
            result={"execution_profile": subrun.get("execution_profile") or LOCAL_WORKTREE, "dependencies": dependencies, "dependency_status": dep_status, "evidence": evidence},
        )
    )


def _resumed_terminal_subrun_event(
    *,
    subrun: Dict[str, Any],
    index: int,
    total: int,
    status: str,
) -> Dict[str, Any]:
    succeeded = status == "succeeded"
    result = {
        "execution_profile": str(subrun.get("execution_profile") or LOCAL_WORKTREE),
        "resumed": True,
        "resume_action": "reused_terminal_result",
        "artifacts": subrun.get("artifacts") if isinstance(subrun.get("artifacts"), list) else [],
        "diff": subrun.get("diff") if isinstance(subrun.get("diff"), dict) else {},
        "evidence": subrun.get("evidence") if isinstance(subrun.get("evidence"), dict) else {},
    }
    if isinstance(subrun.get("worktree"), dict):
        result["worktree"] = subrun["worktree"]
    if subrun.get("worktree_id"):
        result["worktree_id"] = str(subrun.get("worktree_id") or "")
    if subrun.get("worktree_workspace_root"):
        result["worktree_workspace_root"] = str(subrun.get("worktree_workspace_root") or "")
    return _subrun_event(
        "subrun_succeeded" if succeeded else "subrun_failed",
        subrun=subrun,
        index=index,
        total=total,
        progress=100,
        status=status,
        stage="resume_reused",
        result=result,
    )


def _scheduler_initial_status(subrun: Dict[str, Any], *, resumed: bool) -> str:
    status = _terminal_subrun_status(subrun.get("status"))
    if resumed and status in {"succeeded", "failed"}:
        return status
    return "planned"


def _terminal_subrun_status(value: Any) -> str:
    status = str(value or "").strip().lower().replace("-", "_")
    if status in {"succeeded", "success", "done", "complete", "completed", "finished", "promoted"}:
        return "succeeded"
    if status in {"failed", "failure", "error"}:
        return "failed"
    return ""


def _subrun_dependencies(subrun: Dict[str, Any], indexed: Dict[str, tuple[int, Dict[str, Any]]]) -> List[str]:
    raw = subrun.get("dependencies") if isinstance(subrun.get("dependencies"), list) else []
    deps: List[str] = []
    for item in raw:
        dep = str(item or "").strip()
        if dep and dep in indexed and dep not in deps:
            deps.append(dep)
    return deps


def _bounded_parallelism(value: int, subrun_count: int) -> int:
    try:
        raw = int(value or os.environ.get("METIS_COWORK_MAX_PARALLEL_SUBRUNS") or 3)
    except (TypeError, ValueError):
        raw = 3
    return max(1, min(raw, max(1, min(int(subrun_count or 1), 6))))


def _write_cowork_scheduler_state(
    *,
    plan: Dict[str, Any],
    workspace_root: Path,
    run_id: str,
    session_id: str,
    status: str,
) -> str:
    try:
        root = Path(workspace_root or ".").expanduser().resolve()
        out_dir = root / ".metis" / "cowork"
        out_dir.mkdir(parents=True, exist_ok=True)
        key = _safe_filename_fragment(run_id or session_id or str(plan.get("created_at") or "local"))
        path = out_dir / f"scheduler-{key}.json"
        subruns = plan.get("subruns") if isinstance(plan.get("subruns"), list) else []
        payload = {
            "schema": COWORK_SCHEDULER_SCHEMA,
            "version": 1,
            "run_id": run_id or str(plan.get("run_id") or ""),
            "session_id": session_id or str(plan.get("session_id") or ""),
            "status": str(status or ""),
            "updated_at": time.time(),
            "mode": "dag_parallel",
            "subruns": [_scheduler_subrun_state(item) for item in subruns if isinstance(item, dict)],
            "plan": plan,
            "counts": _scheduler_counts(subruns),
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
        return str(path)
    except Exception:
        return ""


def load_cowork_scheduler_state(
    *,
    workspace_root: str,
    run_id: str = "",
    session_id: str = "",
) -> Dict[str, Any]:
    root = Path(workspace_root or ".").expanduser().resolve()
    candidates: List[Path] = []
    if run_id:
        candidates.append(root / ".metis" / "cowork" / f"scheduler-{_safe_filename_fragment(run_id)}.json")
    state_dir = root / ".metis" / "cowork"
    if state_dir.is_dir():
        candidates.extend(sorted(state_dir.glob("scheduler-*.json"), key=lambda item: item.stat().st_mtime, reverse=True))
    seen: set[str] = set()
    for path in candidates:
        key = str(path)
        if key in seen or not path.is_file():
            continue
        seen.add(key)
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not isinstance(payload, dict) or payload.get("schema") != COWORK_SCHEDULER_SCHEMA:
            continue
        payload_run_id = str(payload.get("run_id") or "")
        payload_session_id = str(payload.get("session_id") or "")
        if run_id and payload_run_id != run_id:
            continue
        if session_id and payload_session_id and payload_session_id != session_id:
            continue
        payload["state_path"] = str(path)
        return payload
    return {}


def has_cowork_scheduler_state(*, workspace_root: str, run_id: str = "", session_id: str = "") -> bool:
    return bool(load_cowork_scheduler_state(workspace_root=workspace_root, run_id=run_id, session_id=session_id))


def _plan_from_resume_state(
    state: Dict[str, Any],
    *,
    run_id: str,
    session_id: str,
) -> Dict[str, Any]:
    if not isinstance(state, dict):
        return {}
    raw_plan = state.get("plan") if isinstance(state.get("plan"), dict) else {}
    if raw_plan:
        plan = json.loads(json.dumps(raw_plan, ensure_ascii=False))
    else:
        subruns = state.get("subruns") if isinstance(state.get("subruns"), list) else []
        if not subruns:
            return {}
        plan = {
            "schema": COWORK_PLAN_SCHEMA,
            "version": COWORK_PLAN_VERSION,
            "coordinator_schema": COWORK_COORDINATOR_SCHEMA,
            "goal": str(state.get("goal") or ""),
            "status": "planned",
            "created_at": time.time(),
            "subruns": [_subrun_plan_from_scheduler_state(item) for item in subruns if isinstance(item, dict)],
            "planner": {"schema": COWORK_PLANNER_SCHEMA, "mode": "resume_state_fallback", "bounded": True},
            "merge_policy": {
                "diffs": "review_then_promote",
                "artifacts": "register_all",
                "conflicts": "main_run_decides",
            },
        }
    plan["run_id"] = run_id
    plan["session_id"] = session_id
    plan["status"] = "planned"
    plan["resume"] = _resume_details(state)
    plan["subruns"] = [
        _resume_subrun_plan(item)
        for item in (plan.get("subruns") if isinstance(plan.get("subruns"), list) else [])
        if isinstance(item, dict)
    ]
    return plan


def _subrun_plan_from_scheduler_state(item: Dict[str, Any]) -> Dict[str, Any]:
    subrun_id = str(item.get("subrun_id") or item.get("task_id") or f"subrun_{uuid.uuid4().hex[:10]}")
    title = str(item.get("title") or subrun_id)
    return CoworkSubrunPlan(
        subrun_id=subrun_id,
        title=title,
        objective=str(item.get("objective") or title),
        inputs=[str(value) for value in item.get("inputs", []) if value] if isinstance(item.get("inputs"), list) else ["Resume scheduler state"],
        expected_artifacts=[str(value) for value in item.get("expected_artifacts", []) if value] if isinstance(item.get("expected_artifacts"), list) else ["Resume result"],
        acceptance_criteria=[str(value) for value in item.get("acceptance_criteria", []) if value] if isinstance(item.get("acceptance_criteria"), list) else ["Subrun reaches terminal status"],
        dependencies=[str(value) for value in item.get("dependencies", []) if value] if isinstance(item.get("dependencies"), list) else [],
        prompt=str(item.get("prompt") or title),
        execution_profile=str(item.get("execution_profile") or LOCAL_WORKTREE),
        status=str(item.get("status") or "planned"),
        run_id=str(item.get("run_id") or ""),
        worktree_id=str(item.get("worktree_id") or ""),
        artifacts=item.get("artifacts") if isinstance(item.get("artifacts"), list) else [],
        diff=item.get("diff") if isinstance(item.get("diff"), dict) else {},
        evidence=item.get("evidence") if isinstance(item.get("evidence"), dict) else {},
        vm_tasks=item.get("vm_tasks") if isinstance(item.get("vm_tasks"), list) else [],
    ).to_dict()


def _resume_subrun_plan(subrun: Dict[str, Any]) -> Dict[str, Any]:
    row = dict(subrun)
    row["resume_original_status"] = str(row.get("status") or "")
    if _terminal_subrun_status(row.get("status")) in {"succeeded", "failed"}:
        return row
    row["status"] = "planned"
    for key in ("worktree", "worktree_id", "worktree_workspace_root", "agent", "local_vm", "error"):
        row.pop(key, None)
    row["artifacts"] = []
    row["diff"] = {}
    row["evidence"] = {}
    return row


def _resume_details(state: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(state, dict) or not state:
        return {}
    subruns = state.get("subruns") if isinstance(state.get("subruns"), list) else []
    counts = _scheduler_counts(subruns)
    return {
        "enabled": True,
        "source_run_id": str(state.get("run_id") or ""),
        "source_session_id": str(state.get("session_id") or ""),
        "state_path": str(state.get("state_path") or ""),
        "state_status": str(state.get("status") or ""),
        "counts": counts,
        "policy": "reuse_succeeded_and_failed_rerun_unfinished",
    }


def _scheduler_counts(subruns: List[Any]) -> Dict[str, int]:
    counts = {"total": 0, "succeeded": 0, "failed": 0, "unfinished": 0}
    for item in subruns:
        if not isinstance(item, dict):
            continue
        counts["total"] += 1
        terminal = _terminal_subrun_status(item.get("status"))
        if terminal == "succeeded":
            counts["succeeded"] += 1
        elif terminal == "failed":
            counts["failed"] += 1
        else:
            counts["unfinished"] += 1
    return counts


def _scheduler_subrun_state(subrun: Dict[str, Any]) -> Dict[str, Any]:
    evidence = subrun.get("evidence") if isinstance(subrun.get("evidence"), dict) else {}
    counts = evidence.get("counts") if isinstance(evidence.get("counts"), dict) else {}
    row = {
        "subrun_id": str(subrun.get("subrun_id") or ""),
        "title": str(subrun.get("title") or ""),
        "status": str(subrun.get("status") or "planned"),
        "execution_profile": str(subrun.get("execution_profile") or LOCAL_WORKTREE),
        "objective": str(subrun.get("objective") or ""),
        "inputs": [str(item) for item in subrun.get("inputs", []) if item] if isinstance(subrun.get("inputs"), list) else [],
        "expected_artifacts": [str(item) for item in subrun.get("expected_artifacts", []) if item] if isinstance(subrun.get("expected_artifacts"), list) else [],
        "acceptance_criteria": [str(item) for item in subrun.get("acceptance_criteria", []) if item] if isinstance(subrun.get("acceptance_criteria"), list) else [],
        "dependencies": [str(item) for item in subrun.get("dependencies", []) if item] if isinstance(subrun.get("dependencies"), list) else [],
        "prompt": str(subrun.get("prompt") or ""),
        "worktree_id": str(subrun.get("worktree_id") or ""),
        "worktree_workspace_root": str(subrun.get("worktree_workspace_root") or ""),
        "evidence_counts": counts,
    }
    for key in ("worktree", "artifacts", "diff", "evidence", "vm_tasks", "agent", "local_vm"):
        if isinstance(subrun.get(key), (dict, list)):
            row[key] = subrun[key]
    return row


def _safe_filename_fragment(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in str(value or "").strip())
    return safe[:120] or "local"


def _task_candidates(goal: str, *, max_subruns: int) -> List[str]:
    limit = _bounded_subrun_limit(max_subruns)
    if _is_direct_answer_goal(goal) and not _cowork_goal_requires_project_analysis(goal):
        return [_direct_answer_task_title(goal)]
    raw_lines = [line.strip() for line in goal.splitlines() if line.strip()]
    numbered = [_strip_numbered_task_marker(line) for line in raw_lines]
    numbered = [line for line in numbered if line]
    if numbered:
        return [_compact_title(line, index) for index, line in enumerate(numbered[:limit], 1)]

    lines = [_strip_bullet_task_marker(line) for line in raw_lines]
    lines = [line for line in lines if line and not _looks_like_cowork_meta_line(line)]
    if len(lines) >= 2:
        return [_compact_title(line, index) for index, line in enumerate(lines[:limit], 1)]
    return [
        "Inspect current implementation",
        "Draft isolated change plan",
        "Validate and summarize diffs",
    ][:limit]


def _strip_numbered_task_marker(line: str) -> str:
    text = str(line or "").strip()
    match = re.match(r"^\s*\d+\s*[\.\)、:：]\s*(.+?)\s*$", text)
    return match.group(1).strip() if match else ""


def _strip_bullet_task_marker(line: str) -> str:
    text = str(line or "").strip()
    match = re.match(r"^\s*[-*]\s+(.+?)\s*$", text)
    if match:
        return match.group(1).strip()
    return text.strip(" -\t")


def _looks_like_cowork_meta_line(line: str) -> bool:
    text = " ".join(str(line or "").split()).strip().lower()
    if not text:
        return True
    if text.endswith(("：", ":")):
        return True
    return any(
        marker in text
        for marker in [
            "必须拆成",
            "每个 subrun",
            "如果任务被取消",
            "max subruns",
            "requirements",
            "要求",
        ]
    )


def _compact_title(text: str, index: int) -> str:
    title = " ".join(text.split())
    if len(title) > 80:
        title = title[:77].rstrip() + "..."
    return title or f"Subtask {index}"


def _direct_answer_task_title(goal: str) -> str:
    if re.search(r"[\u4e00-\u9fff]", str(goal or "")):
        return "直接回答用户问题"
    return "Answer the user directly"


def _subrun_prompt(
    goal: str,
    title: str,
    *,
    objective: str,
    inputs: List[str],
    expected_artifacts: List[str],
    acceptance_criteria: List[str],
    dependencies: List[str],
) -> str:
    direct_answer = _expects_direct_answer(goal=goal, title=title, expected_artifacts=expected_artifacts)
    if direct_answer:
        return (
            f"Parent cowork goal:\n{goal}\n\n"
            f"Subrun title:\n{title}\n\n"
            f"Objective:\n{objective}\n\n"
            f"Inputs:\n{_bullet_lines(inputs)}\n\n"
            f"Expected output:\n{_bullet_lines(expected_artifacts)}\n\n"
            f"Acceptance criteria:\n{_bullet_lines(acceptance_criteria)}\n\n"
            f"Dependencies:\n{_bullet_lines(dependencies) if dependencies else '- none'}\n\n"
            "Answer the user directly in natural language. Do not create a report, document, diff, or file unless "
            "the user explicitly asked for one. Finish with a section titled '最终回答' that contains only the "
            "user-facing answer; keep reasoning notes, evidence, and implementation details out of that section."
        )
    return (
        f"Parent cowork goal:\n{goal}\n\n"
        f"Subrun title:\n{title}\n\n"
        f"Objective:\n{objective}\n\n"
        f"Inputs:\n{_bullet_lines(inputs)}\n\n"
        f"Expected artifacts/evidence:\n{_bullet_lines(expected_artifacts)}\n\n"
        f"Acceptance criteria:\n{_bullet_lines(acceptance_criteria)}\n\n"
        f"Dependencies:\n{_bullet_lines(dependencies) if dependencies else '- none'}\n\n"
        "Work in an isolated local worktree. Return the artifacts produced, changed files, "
        "validation evidence, and a concise diff summary. Do not promote changes to the source workspace. "
        "If there is a conclusion for the user, finish with a '最终回答' section that contains only that conclusion; "
        "do not paste reports, diffs, or evidence into the final answer."
    )


def _bounded_subrun_limit(value: int) -> int:
    try:
        raw = int(value or 3)
    except (TypeError, ValueError):
        raw = 3
    return max(1, min(raw, 6))


def _allowed_subrun_profiles(requested_execution_profile: str) -> List[str]:
    return [LOCAL_WORKTREE, LOCAL_VM]


def _normalize_planner_execution_profile(
    value: Any,
    *,
    default_profile: str,
    requested_execution_profile: str,
) -> str:
    profile = str(value or "").strip().lower().replace("-", "_")
    allowed = set(_allowed_subrun_profiles(requested_execution_profile))
    if profile in allowed:
        return profile
    if default_profile in allowed:
        return default_profile
    return LOCAL_WORKTREE


def _clean_plan_text(value: Any, limit: int) -> str:
    text = " ".join(str(value or "").replace("\x00", "").split())
    if not text:
        return ""
    return text[:limit].rstrip()


def _bounded_string_list(value: Any, *, max_items: int, max_chars: int) -> List[str]:
    if isinstance(value, str):
        raw_items: List[Any] = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    out: List[str] = []
    for item in raw_items:
        text = _clean_plan_text(item, max_chars)
        if text and text not in out:
            out.append(text)
        if len(out) >= max_items:
            break
    return out


def _apply_normalized_dependencies(rows: List[Dict[str, Any]], raw_dependencies: List[Any]) -> None:
    for index, row in enumerate(rows):
        previous_rows = rows[:index]
        title_to_id = {
            _clean_plan_text(previous.get("title"), 90).lower(): str(previous.get("subrun_id") or "")
            for previous in previous_rows
        }
        deps = _raw_dependency_items(raw_dependencies[index] if index < len(raw_dependencies) else [])
        normalized: List[str] = []
        for dep in deps:
            target = ""
            if isinstance(dep, int):
                dep_index = dep
            else:
                dep_text = str(dep or "").strip()
                dep_index = int(dep_text) if dep_text.isdigit() else 0
                if not dep_index:
                    target = title_to_id.get(_clean_plan_text(dep_text, 90).lower(), "")
                    if not target:
                        target = next(
                            (
                                str(candidate.get("subrun_id") or "")
                                for candidate in previous_rows
                                if str(candidate.get("subrun_id") or "") == dep_text
                            ),
                            "",
                        )
            if not target and 1 <= dep_index <= index:
                target = str(rows[dep_index - 1].get("subrun_id") or "")
            if target and target not in normalized:
                normalized.append(target)
        row["dependencies"] = normalized


def _raw_dependency_items(value: Any) -> List[Any]:
    if isinstance(value, list):
        return value[:10]
    if isinstance(value, (str, int)):
        return [value]
    return []


def _normalize_vm_tasks(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, str):
        raw_items: List[Any] = [{"command": value}]
    elif isinstance(value, dict):
        raw_items = [value]
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = []
    tasks: List[Dict[str, Any]] = []
    for item in raw_items:
        if isinstance(item, str):
            raw: Dict[str, Any] = {"command": item}
        elif isinstance(item, dict):
            raw = item
        else:
            continue
        command = _clean_vm_command(raw.get("command") or raw.get("cmd") or raw.get("shell"))
        if not command:
            continue
        index = len(tasks) + 1
        artifact_patterns = _bounded_string_list(
            raw.get("artifact_patterns") or raw.get("artifactPatterns") or raw.get("artifacts"),
            max_items=20,
            max_chars=300,
        )
        collect_artifacts = _truthy(raw.get("collect_artifacts") or raw.get("collectArtifacts")) or bool(artifact_patterns)
        task = {
            "schema": COWORK_VM_TASK_SCHEMA,
            "version": 1,
            "task_id": _safe_filename_fragment(raw.get("task_id") or raw.get("taskId") or raw.get("id") or f"vm_task_{index}"),
            "title": _clean_plan_text(raw.get("title") or raw.get("name") or f"VM task {index}", 120),
            "command": command,
            "cwd": _clean_vm_cwd(raw.get("cwd") or raw.get("working_directory") or raw.get("workingDirectory") or "."),
            "timeout": _coerce_vm_timeout(raw.get("timeout") or raw.get("timeout_seconds") or raw.get("timeoutSeconds")),
            "allow_network": _truthy(raw.get("allow_network") or raw.get("allowNetwork")),
            "collect_artifacts": collect_artifacts,
            "artifact_patterns": artifact_patterns,
            "require_artifacts": _truthy(raw.get("require_artifacts") or raw.get("requireArtifacts")),
            "expected_stdout_contains": _clean_plan_text(raw.get("expected_stdout_contains") or raw.get("expectedStdoutContains"), 500),
        }
        tasks.append(task)
        if len(tasks) >= 6:
            break
    return tasks


def _default_vm_tasks(goal: str, title: str, profile: str) -> List[Dict[str, Any]]:
    if profile != LOCAL_VM:
        return []
    text = f"{goal} {title}".lower()
    instruction_task = _default_local_vm_instruction_task(_matching_goal_instruction(goal, title) or title)
    if instruction_task:
        return _normalize_vm_tasks(instruction_task)
    if any(token in text for token in ["build", "compile", "构建", "编译"]):
        return _normalize_vm_tasks(
            {
                "title": "Run workspace build",
                "command": _default_build_command(),
                "timeout": 300,
                "collect_artifacts": False,
            }
        )
    if any(token in text for token in ["test", "pytest", "unittest", "validate", "validation", "check", "验证", "测试", "检查"]):
        return _normalize_vm_tasks(
            {
                "title": "Run workspace validation",
                "command": _default_validation_command(),
                "timeout": 300,
                "collect_artifacts": False,
            }
        )
    if any(token in text for token in ["data", "dataset", "csv", "json", "数据"]):
        return _normalize_vm_tasks(
            {
                "title": "Inspect workspace data files",
                "command": _default_workspace_scan_command("data"),
                "timeout": 120,
                "collect_artifacts": False,
            }
        )
    return []


def _matching_goal_instruction(goal: str, title: str) -> str:
    key = str(title or "").split("：", 1)[0].split(":", 1)[0].replace("...", "").strip().lower()
    key = re.sub(r"^\d+\s*[\.\)、:：]\s*", "", key).strip()
    if not key:
        return ""
    for line in str(goal or "").splitlines():
        clean = line.strip()
        if clean and key in clean.lower():
            return clean
    return ""


def _default_local_vm_instruction_task(title: str) -> Dict[str, Any] | None:
    text = str(title or "")
    if not _mentions_local_vm(_profile_hint_text(text)):
        return None

    markers = _uppercase_markers(text)
    path = _first_workspace_report_path(text) or "reports/metis-local-vm-task.md"
    sleep_seconds = _first_sleep_seconds(text)

    started = next((marker for marker in markers if marker.endswith("_STARTED")), "METIS_VM_STARTED")
    done = next((marker for marker in markers if marker.endswith("_DONE")), "METIS_VM_DONE")
    artifact_marker = next((marker for marker in markers if marker.endswith("_OK")), done)
    normalized_path = path.replace("\\", "/")
    normalized_parent = str(Path(normalized_path).parent).replace("\\", "/")

    commands = [
        f"mkdir -p {_shell_quote(normalized_parent)}",
        f"printf '%s\\n' {_shell_quote(started)}",
    ]
    if sleep_seconds:
        commands.append(f"sleep {sleep_seconds}")
    commands.extend(
        [
            f"printf '%s\\n' {_shell_quote(artifact_marker)} > {_shell_quote(normalized_path)}",
            f"printf '%s\\n' {_shell_quote(done)}",
        ]
    )
    return {
        "title": "Run local_vm instruction",
        "command": " && ".join(commands),
        "timeout": max(120, min(sleep_seconds + 90, 900)) if sleep_seconds else 120,
        "collect_artifacts": True,
        "artifact_patterns": [normalized_path],
        "require_artifacts": True,
        "expected_stdout_contains": done,
    }


def _uppercase_markers(value: str) -> List[str]:
    markers: List[str] = []
    for match in re.findall(r"(?<![A-Za-z0-9_])([A-Z][A-Z0-9_]{2,})(?![A-Za-z0-9_])", str(value or "")):
        if match not in markers:
            markers.append(match)
    return markers


def _first_workspace_report_path(value: str) -> str:
    match = re.search(r"(reports/[^\s,，。；;\"']+)", str(value or ""), flags=re.IGNORECASE)
    return match.group(1).strip() if match else ""


def _first_sleep_seconds(value: str) -> int:
    match = re.search(r"\bsleep\s+(\d{1,4})\b", str(value or ""), flags=re.IGNORECASE)
    if not match:
        return 0
    return max(0, min(int(match.group(1)), 600))


def _shell_quote(value: str) -> str:
    return "'" + str(value or "").replace("'", "'\"'\"'") + "'"


def _deterministic_execution_profile(title: str, *, default_profile: str) -> str:
    text = _profile_hint_text(title)
    if _mentions_local_vm(text):
        return LOCAL_VM
    if _mentions_local_worktree(text):
        return LOCAL_WORKTREE
    return default_profile if default_profile in {LOCAL_WORKTREE, LOCAL_VM} else LOCAL_WORKTREE


def _profile_hint_text(value: str) -> str:
    return str(value or "").lower().replace("-", "_").replace(" ", "_")


def _mentions_local_vm(text: str) -> bool:
    normalized = _profile_hint_text(text)
    return any(
        token in normalized
        for token in [
            "使用_local_vm",
            "用_local_vm",
            "走_local_vm",
            "通过_local_vm",
            "local_vm_执行",
            "execution_profile:local_vm",
            "execution_profile=local_vm",
            "profile:local_vm",
            "profile=local_vm",
            "metisruntime",
            "metis_runtime",
            "wsl",
            "虚拟机",
        ]
    )


def _mentions_local_worktree(text: str) -> bool:
    return any(token in text for token in ["local_worktree", "worktree", "工作树"])


def _deterministic_dependencies(
    title: str,
    rows: List[Dict[str, Any]],
    *,
    chain_by_default: bool,
    index: int,
) -> List[str]:
    if not rows:
        return []
    text = str(title or "").lower()
    if any(token in text for token in ["dependent", "summary", "aggregate", "merge", "combine", "依赖", "汇总", "总结"]):
        return [str(row.get("subrun_id") or "") for row in rows if row.get("subrun_id")]
    if chain_by_default and index > 1:
        return [str(rows[-1].get("subrun_id") or "")]
    return []


def _subrun_vm_tasks(subrun: Dict[str, Any]) -> List[Dict[str, Any]]:
    tasks = _normalize_vm_tasks(subrun.get("vm_tasks") or subrun.get("vmTasks") or subrun.get("commands"))
    if tasks:
        return tasks
    profile = str(subrun.get("execution_profile") or "").strip().lower().replace("-", "_")
    return _default_vm_tasks(
        " ".join(
            [
                str(subrun.get("objective") or ""),
                str(subrun.get("title") or ""),
                " ".join(str(item) for item in subrun.get("acceptance_criteria", []) if item)
                if isinstance(subrun.get("acceptance_criteria"), list)
                else "",
            ]
        ),
        str(subrun.get("title") or ""),
        profile,
    )


def _clean_vm_command(value: Any) -> str:
    text = str(value or "").replace("\x00", "").strip()
    return text[:8000].rstrip()


def _clean_vm_cwd(value: Any) -> str:
    raw = str(value or ".").replace("\x00", "").strip().replace("\\", "/") or "."
    if raw in {"", ".", "./"}:
        return "."
    candidate = Path(raw)
    if candidate.is_absolute():
        return "."
    parts = [part for part in raw.split("/") if part not in {"", "."}]
    if any(part == ".." for part in parts):
        return "."
    return "/".join(parts)[:300] or "."


def _coerce_vm_timeout(value: Any) -> int:
    try:
        raw = int(value if value is not None else 120)
    except (TypeError, ValueError):
        raw = 120
    return max(1, min(raw, 1800))


def _truthy(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    text = str(value or "").strip().lower()
    return text in {"1", "true", "yes", "y", "on"}


def _default_validation_command() -> str:
    return (
        "set -e\n"
        "if [ -f package.json ] && command -v npm >/dev/null 2>&1; then\n"
        "  npm test || npm run build --if-present\n"
        "elif [ -d tests ] || [ -f pytest.ini ] || [ -f pyproject.toml ] || [ -f setup.cfg ]; then\n"
        "  python3 -m pytest -q\n"
        "else\n"
        f"{_default_workspace_scan_command('validation')}\n"
        "fi"
    )


def _default_build_command() -> str:
    return (
        "set -e\n"
        "if [ -f package.json ] && command -v npm >/dev/null 2>&1; then\n"
        "  npm run build --if-present\n"
        "elif [ -f Makefile ] || [ -f makefile ]; then\n"
        "  make\n"
        "else\n"
        f"{_default_workspace_scan_command('build')}\n"
        "fi"
    )


def _default_workspace_scan_command(label: str) -> str:
    safe_label = _safe_filename_fragment(label or "scan")
    return (
        "python3 - <<'PY'\n"
        "import json\n"
        "from pathlib import Path\n"
        f"label = {safe_label!r}\n"
        "root = Path('.')\n"
        "ignored = {'.git', 'node_modules', '.venv', 'venv', '__pycache__'}\n"
        "files = []\n"
        "for path in root.rglob('*'):\n"
        "    if len(files) >= 80:\n"
        "        break\n"
        "    if not path.is_file() or any(part in ignored for part in path.parts):\n"
        "        continue\n"
        "    files.append(str(path).replace('\\\\', '/'))\n"
        "print('METIS_VM_WORKSPACE_SCAN ' + json.dumps({'label': label, 'file_count_sample': len(files), 'files': files[:20]}, sort_keys=True))\n"
        "PY"
    )


def _default_subrun_inputs(goal: str, index: int) -> List[str]:
    if _is_direct_answer_goal(goal) and not _cowork_goal_requires_project_analysis(goal):
        return ["Parent Cowork goal"]
    items = ["Parent Cowork goal", "Current source workspace"]
    if index > 1:
        items.append("Earlier subrun summaries and artifacts")
    if _is_artifact_text(goal):
        items.append("Requested report/document requirements")
    return items


def _default_expected_artifacts(goal: str, title: str) -> List[str]:
    if _is_direct_answer_goal(goal) and not _cowork_goal_requires_project_analysis(goal):
        return ["Natural final answer"]
    if _is_artifact_text(f"{goal} {title}"):
        return ["Registered document/report artifact", "Rendered or inspected artifact evidence", "Concise artifact summary"]
    if "test" in f"{goal} {title}".lower() or "验证" in f"{goal} {title}":
        return ["stdout/test evidence", "diff summary if files change", "validation risk notes"]
    return ["worktree diff or investigation notes", "concise result summary", "validation evidence"]


def _default_acceptance_criteria(title: str, *, goal: str = "") -> List[str]:
    if _is_direct_answer_goal(goal or title) and not _cowork_goal_requires_project_analysis(goal or title):
        return [
            "Final answer directly addresses the user's question in natural language.",
            "No report, document, diff, or file is created unless explicitly requested.",
        ]
    return [
        f"{title} has a clear result summary.",
        "Changed files, artifacts, or stdout evidence are attached to the subrun result.",
        "Known risks or unresolved follow-ups are explicitly stated.",
    ]


def _deterministic_objective(goal: str, title: str) -> str:
    return _clean_plan_text(f"{title} for the parent goal: {goal}", 500) or title


def _is_artifact_text(text: str) -> bool:
    return any(token in str(text or "").lower() for token in ["report", "报告", "docx", "document", "pdf", "xlsx", "spreadsheet", "pptx", "presentation", "office"])


def _clean_goal_text(value: str) -> str:
    return " ".join(str(value or "").replace("\x00", "").split()).strip()


def _cowork_goal_needs_clarification(goal: str) -> bool:
    text = _clean_goal_text(goal)
    if not text:
        return True
    lower = text.lower()
    exact = {
        "做一下",
        "处理一下",
        "优化一下",
        "改一下",
        "看看",
        "看一下",
        "继续",
        "下一步",
        "这个任务",
        "任务",
        "帮我做",
        "do it",
        "continue",
        "next",
        "fix it",
        "make it better",
        "handle this",
    }
    if lower in exact:
        return True
    if _is_direct_answer_goal(text):
        return False
    vague_actions = ["做", "处理", "优化", "改", "弄", "搞", "继续", "do", "fix", "handle", "improve", "continue"]
    vague_targets = ["这个", "这个任务", "它", "这里", "this", "it", "that"]
    has_vague_action = any(token in lower for token in vague_actions)
    has_vague_target = any(token in lower for token in vague_targets)
    if has_vague_action and has_vague_target and len(text) <= 28:
        return True
    if has_vague_action and not _cowork_goal_requires_project_analysis(text) and len(text) <= 12:
        return True
    return False


def _cowork_goal_requires_project_analysis(goal: str) -> bool:
    text = _clean_goal_text(goal)
    if not text:
        return False
    lower = text.lower()
    project_markers = [
        "this project",
        "current project",
        "repo",
        "repository",
        "codebase",
        "workspace",
        "current implementation",
        "current code",
        "this file",
        "this function",
        "test failure",
        "build failure",
        "error log",
        "stack trace",
        "当前项目",
        "这个项目",
        "本项目",
        "当前仓库",
        "这个仓库",
        "代码库",
        "当前实现",
        "当前代码",
        "这个文件",
        "这个函数",
        "这段代码",
        "报错",
        "错误日志",
        "测试失败",
        "构建失败",
    ]
    action_markers = [
        "fix",
        "implement",
        "change",
        "edit",
        "refactor",
        "debug",
        "test",
        "validate",
        "build",
        "inspect current",
        "analyze current",
        "修复",
        "实现",
        "修改",
        "重构",
        "调试",
        "测试",
        "验证",
        "构建",
        "检查当前",
        "分析当前",
    ]
    if any(marker in lower for marker in project_markers):
        return True
    if _looks_like_general_definition_question(text):
        return False
    return any(marker in lower for marker in action_markers)


def _cowork_clarification_question(goal: str) -> str:
    if re.search(r"[\u4e00-\u9fff]", str(goal or "")):
        return "你想让我具体处理什么？请补充目标对象和期望产出：是直接解释、分析项目，还是修改/验证某个文件或功能？"
    return "What exactly should I do? Please add the target and expected outcome: a direct explanation, project analysis, or a file/code change with validation."


def _looks_like_general_definition_question(goal: str) -> bool:
    text = _clean_goal_text(goal)
    lower = text.lower()
    if re.search(r"\b(what is|what are|why is|why are|how does|how do)\b", lower):
        return True
    return any(marker in text for marker in ["什么是", "是什么", "为什么", "为何", "怎么理解", "如何理解", "意义"])


def _expects_direct_answer(*, goal: str, title: str, expected_artifacts: List[str]) -> bool:
    text = " ".join([goal, title, " ".join(str(item) for item in expected_artifacts if item)])
    return _is_direct_answer_goal(goal) or _contains_direct_answer_marker(text)


def _subrun_expects_direct_answer(subrun: Dict[str, Any]) -> bool:
    expected = " ".join(str(item) for item in subrun.get("expected_artifacts", []) if item) if isinstance(subrun.get("expected_artifacts"), list) else ""
    text = " ".join(
        [
            str(subrun.get("title") or ""),
            str(subrun.get("objective") or ""),
            str(subrun.get("prompt") or ""),
            expected,
        ]
    )
    return _contains_direct_answer_marker(text)


def _contains_direct_answer_marker(text: str) -> bool:
    normalized = str(text or "").lower()
    return any(
        marker in normalized
        for marker in [
            "natural final answer",
            "final answer",
            "direct answer",
            "user-facing answer",
            "answer the user",
            "最终回答",
            "直接回答",
            "自然回答",
            "用户可读回答",
        ]
    )


def _is_direct_answer_goal(text: str) -> bool:
    source = " ".join(str(text or "").split()).strip()
    if not source:
        return False
    lower = source.lower()
    if _is_artifact_text(source):
        return False
    if len([line for line in str(text or "").splitlines() if line.strip()]) > 2:
        return False
    action_tokens = [
        "fix",
        "implement",
        "change",
        "edit",
        "create",
        "build",
        "test",
        "validate",
        "commit",
        "push",
        "diff",
        "artifact",
        "修复",
        "实现",
        "修改",
        "创建",
        "生成",
        "构建",
        "测试",
        "验证",
        "提交",
        "推送",
        "产物",
    ]
    if any(token in lower for token in action_tokens) and not _looks_like_general_definition_question(source):
        return False
    question_like = bool(re.search(r"[?？]\s*$", source)) or bool(
        re.search(r"\b(what|why|how|when|where|whether|should|can|could|is|are|do|does)\b", lower)
    )
    chinese_question_markers = [
        "是什么",
        "为什么",
        "为何",
        "意义",
        "怎么理解",
        "如何理解",
        "怎么看",
        "是否",
        "可以吗",
        "好吗",
        "对吗",
        "吗",
    ]
    return question_like or any(marker in source for marker in chinese_question_markers)


def _bullet_lines(items: List[str]) -> str:
    clean = [_clean_plan_text(item, 300) for item in items if _clean_plan_text(item, 300)]
    if not clean:
        return "- none"
    return "\n".join(f"- {item}" for item in clean)


def _subrun_execution_profile(value: str) -> str:
    profile = str(value or "").strip().lower().replace("-", "_")
    if profile == LOCAL_VM:
        return LOCAL_VM
    if profile in {LOCAL_WORKTREE, LOCAL_DIRECT, ""}:
        return LOCAL_WORKTREE
    return LOCAL_WORKTREE


def _runtime_status(phase: str, message: str, *, details: Dict[str, Any] | None = None) -> Dict[str, Any]:
    return {
        "type": "runtime_status",
        "kind": "runtime_status",
        "payload": {
            "phase": phase,
            "message": message,
            "recoverable": True,
            "details": details or {},
        },
    }


def _subrun_event(
    kind: str,
    *,
    subrun: Dict[str, Any],
    index: int,
    total: int,
    progress: int,
    status: str,
    stage: str = "",
    result: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    subrun_id = str(subrun.get("subrun_id") or subrun.get("task_id") or f"subrun_{index}")
    title = str(subrun.get("title") or subrun.get("name") or subrun_id)
    payload: Dict[str, Any] = {
        "schema": COWORK_SUBRUN_EVENT_SCHEMA,
        "version": COWORK_SUBRUN_EVENT_VERSION,
        "subrun_id": subrun_id,
        "task_id": subrun_id,
        "name": title,
        "title": title,
        "objective": str(subrun.get("objective") or ""),
        "inputs": [str(item) for item in subrun.get("inputs", []) if item] if isinstance(subrun.get("inputs"), list) else [],
        "expected_artifacts": [str(item) for item in subrun.get("expected_artifacts", []) if item] if isinstance(subrun.get("expected_artifacts"), list) else [],
        "acceptance_criteria": [str(item) for item in subrun.get("acceptance_criteria", []) if item] if isinstance(subrun.get("acceptance_criteria"), list) else [],
        "dependencies": [str(item) for item in subrun.get("dependencies", []) if item] if isinstance(subrun.get("dependencies"), list) else [],
        "prompt": str(subrun.get("prompt") or ""),
        "index": max(1, int(index or 1)),
        "total": max(1, int(total or 1)),
        "progress": max(0, min(int(progress or 0), 100)),
        "status": status,
        "stage": stage or status,
        "execution_profile": str(subrun.get("execution_profile") or LOCAL_WORKTREE),
        "worktree_id": str(subrun.get("worktree_id") or ""),
        "worktree_workspace_root": str(subrun.get("worktree_workspace_root") or ""),
    }
    if isinstance(subrun.get("worktree"), dict):
        payload["worktree"] = subrun["worktree"]
    if subrun.get("error"):
        payload["error"] = str(subrun.get("error") or "")
    if isinstance(subrun.get("evidence"), dict):
        payload["evidence"] = subrun["evidence"]
    if isinstance(subrun.get("vm_tasks"), list):
        payload["vm_tasks"] = subrun["vm_tasks"]
    if result is not None:
        payload["result"] = result
    return {"type": kind, "kind": kind, "payload": payload}


def _run_subrun_agent(
    *,
    goal: str,
    subrun: Dict[str, Any],
    record: WorktreeRecord,
    source_root: Path,
    run_id: str,
    session_id: str,
    base_config: AgentConfig,
    execution_profile: str,
    enabled_tools: List[str],
    cancelled: Callable[[], bool] | None = None,
) -> Dict[str, Any]:
    subrun_id = str(subrun.get("subrun_id") or record.worktree_id)
    title = str(subrun.get("title") or subrun_id)
    prompt = str(subrun.get("prompt") or title)
    finish_instruction = (
        "Finish with a section titled '最终回答' containing only the natural answer for the user. "
        "Do not create or paste reports, diffs, evidence lists, or file-change summaries unless explicitly requested."
        if _subrun_expects_direct_answer(subrun)
        else "Finish with a concise summary of files changed, artifacts produced, validation performed, and unresolved risks. "
        "If there is a user-facing conclusion, put only that conclusion in a section titled '最终回答'."
    )
    config = replace(
        base_config,
        system_prompt=_cowork_subrun_system_prompt(base_config.system_prompt),
        enabled_tools=enabled_tools,
        execution_mode="auto",
        workspace_root=record.worktree_workspace_root,
        source_workspace_root=str(source_root),
        worktree_id=record.worktree_id,
        surface_mode="cowork",
        execution_profile=execution_profile,
        max_turns=max(1, min(int(base_config.max_turns or 4), 8)),
        permission_checker=None,
        tool_boundary_overrides=None,
        routing_task_type=_subrun_task_type(goal, subrun),
        routing_preferred_tools=enabled_tools,
    )
    messages = [
        {
            "role": "user",
            "content": (
                f"Parent Cowork goal:\n{goal}\n\n"
                f"Subrun id: {subrun_id}\n"
                f"Subrun title: {title}\n\n"
                f"Subrun task:\n{prompt}\n\n"
                "Work only inside this subrun worktree. Make focused changes or produce requested artifacts. "
                f"Do not promote changes to the source workspace. {finish_instruction}"
            ),
        }
    ]
    final_text_parts: List[str] = []
    tool_rows: List[Dict[str, Any]] = []
    statuses: List[Dict[str, Any]] = []
    errors: List[str] = []
    registered_artifacts: List[Dict[str, Any]] = []
    artifact_registration_errors: List[Dict[str, str]] = []
    done_payload: Dict[str, Any] = {}
    permission_denials = 0
    generator = run_agent_loop(messages, config)
    send_value: Optional[bool] = None
    try:
        while True:
            _raise_if_cancelled(cancelled)
            try:
                event = generator.send(send_value)
            except StopIteration:
                break
            send_value = None
            if isinstance(event, (ContentEvent, ContentDeltaEvent, TextDeltaEvent)):
                text = str(getattr(event, "text", "") or "")
                if text:
                    final_text_parts.append(text)
            elif isinstance(event, ToolCallEvent):
                tool_rows.append(
                    {
                        "tool_name": event.tool_name,
                        "call_id": event.call_id,
                        "arguments_preview": _truncate(_json_preview(event.arguments), 1200),
                        "status": "running",
                    }
                )
            elif isinstance(event, ToolResultEvent):
                _merge_tool_result(tool_rows, event)
                created, registration_errors = _register_tool_result_artifacts(
                    event=event,
                    record=record,
                    run_id=run_id,
                    session_id=session_id,
                    subrun_id=subrun_id,
                )
                registered_artifacts.extend(created)
                artifact_registration_errors.extend(registration_errors)
            elif isinstance(event, RuntimeStatusEvent):
                statuses.append(
                    {
                        "phase": event.phase,
                        "message": event.message,
                        "tool_name": event.tool_name,
                        "call_id": event.call_id,
                    }
                )
            elif isinstance(event, PermissionRequestEvent):
                permission_denials += 1
                send_value = False
            elif isinstance(event, ErrorEvent):
                errors.append(event.message or event.details or event.code)
            elif isinstance(event, DoneEvent):
                done_payload = {
                    "turns": event.total_turns,
                    "tool_calls": event.total_tool_calls,
                    "usage": {
                        "prompt_tokens": event.prompt_tokens,
                        "completion_tokens": event.completion_tokens,
                        "total_tokens": event.total_tokens,
                    },
                    "context_ledger": event.context_ledger,
                }
    finally:
        close = getattr(generator, "close", None)
        if callable(close):
            close()

    final_text = _truncate("\n".join(part for part in final_text_parts if part).strip(), 6000)
    ok = not errors
    return {
        "schema": "metis.cowork_subrun_agent.v1",
        "ok": ok,
        "subrun_id": subrun_id,
        "workspace_root": record.worktree_workspace_root,
        "source_workspace_root": str(source_root),
        "execution_profile": execution_profile,
        "enabled_tools": enabled_tools,
        "final_text": final_text,
        "tools": tool_rows,
        "statuses": statuses[-20:],
        "errors": errors,
        "registered_artifacts": registered_artifacts,
        "artifact_registration_errors": artifact_registration_errors,
        "permission_denials": permission_denials,
        **done_payload,
    }


def _cowork_subrun_system_prompt(base: str) -> str:
    guard = (
        "Cowork subrun protocol:\n"
        "- You are one isolated subrun under a local Cowork coordinator.\n"
        "- The current workspace_root is your private managed git worktree.\n"
        "- Do not edit, delete, or promote files in the source workspace directly.\n"
        "- Keep changes focused on the assigned subtask.\n"
        "- If producing reports/documents, save them under output/ or .metis/cowork/ inside the worktree.\n"
        "- The main chat receives only the coordinator's final natural answer.\n"
        "- Do not paste reports, diffs, or evidence into a final answer section; those details belong in artifacts or activity details.\n"
        "- For answer-focused subruns, finish with '最终回答' and only the user-facing answer.\n"
        "- For code/artifact subruns, summarize changed files, artifacts, tests, and unresolved risks outside any final answer section."
    )
    text = str(base or "").strip()
    return f"{text}\n\n---\n\n{guard}" if text else guard


def _subrun_enabled_tools(goal: str, subrun: Dict[str, Any]) -> List[str]:
    if _subrun_expects_direct_answer(subrun) and not _is_artifact_subrun(goal, subrun):
        return list(COWORK_READ_ONLY_TOOLS)
    tools = list(COWORK_CODE_TOOLS)
    if _is_artifact_subrun(goal, subrun):
        tools.extend(name for name in COWORK_ARTIFACT_TOOLS if name not in tools)
    return tools


def _is_artifact_subrun(goal: str, subrun: Dict[str, Any]) -> bool:
    text = " ".join(
        [
            str(goal or ""),
            str(subrun.get("title") or ""),
            str(subrun.get("prompt") or ""),
            str(subrun.get("objective") or ""),
            " ".join(str(item) for item in subrun.get("expected_artifacts", []) if item)
            if isinstance(subrun.get("expected_artifacts"), list)
            else "",
        ]
    ).lower()
    return _is_artifact_text(text)


def _subrun_task_type(goal: str, subrun: Dict[str, Any]) -> str:
    return "artifact_workflow" if _is_artifact_subrun(goal, subrun) else "code"


def _merge_tool_result(tool_rows: List[Dict[str, Any]], event: ToolResultEvent) -> None:
    for row in reversed(tool_rows):
        if row.get("call_id") == event.call_id:
            row["status"] = "done"
            row["result_preview"] = _truncate(str(event.result or ""), 1600)
            return
    tool_rows.append(
        {
            "tool_name": event.tool_name,
            "call_id": event.call_id,
            "status": "done",
            "result_preview": _truncate(str(event.result or ""), 1600),
        }
    )


def _register_tool_result_artifacts(
    *,
    event: ToolResultEvent,
    record: WorktreeRecord,
    run_id: str,
    session_id: str,
    subrun_id: str,
) -> tuple[List[Dict[str, Any]], List[Dict[str, str]]]:
    if event.tool_name not in COWORK_ARTIFACT_TOOLS:
        return [], []
    payload = _parse_tool_json_result(event.result)
    if not isinstance(payload, dict) or not payload.get("ok"):
        return [], []
    root = Path(record.worktree_workspace_root).expanduser().resolve()
    paths = _artifact_paths_from_tool_payload(payload)
    created: List[Dict[str, Any]] = []
    errors: List[Dict[str, str]] = []
    seen: set[str] = set()
    for path_text in paths:
        path = Path(path_text).expanduser()
        if not path.is_absolute():
            path = root / path
        resolved = path.resolve(strict=False)
        key = str(resolved)
        if key in seen or not resolved.exists() or not resolved.is_file():
            continue
        seen.add(key)
        try:
            rel = resolved.relative_to(root).as_posix()
        except ValueError:
            rel = resolved.name
        try:
            artifact = register_artifact(
                kind=_artifact_kind_for_path(resolved),
                title=_artifact_title_for_path(resolved, event.tool_name, payload),
                path=str(resolved),
                run_id=run_id,
                session_id=session_id,
                source_tool_call_id=event.call_id,
                metadata={
                    "source": "cowork_tool_result",
                    "tool_name": event.tool_name,
                    "subrun_id": subrun_id,
                    "worktree_id": record.worktree_id,
                    "relative_path": rel,
                    "tool_payload_schema": str(payload.get("schema") or ""),
                },
                workspace_root=str(root),
            )
            created.append(artifact)
        except Exception as exc:
            errors.append({"path": key, "error": f"{type(exc).__name__}: {exc}"})
    return created, errors


def _parse_tool_json_result(value: str) -> Dict[str, Any]:
    text = str(value or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _artifact_paths_from_tool_payload(payload: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    for key in ("output_path", "pdf_path", "image", "path"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            paths.append(value.strip())
    images = payload.get("images")
    if isinstance(images, list):
        paths.extend(str(item).strip() for item in images if str(item or "").strip())
    artifacts = payload.get("artifacts")
    if isinstance(artifacts, list):
        for item in artifacts:
            if isinstance(item, dict) and str(item.get("path") or "").strip():
                paths.append(str(item.get("path") or "").strip())
    activity = payload.get("artifact_activity") if isinstance(payload.get("artifact_activity"), dict) else {}
    output = activity.get("output_path") if isinstance(activity, dict) else ""
    if isinstance(output, str) and output.strip():
        paths.append(output.strip())
    activity_artifacts = activity.get("artifacts") if isinstance(activity, dict) else []
    if isinstance(activity_artifacts, list):
        for item in activity_artifacts:
            if isinstance(item, dict) and str(item.get("path") or "").strip():
                paths.append(str(item.get("path") or "").strip())
    out: List[str] = []
    seen: set[str] = set()
    for path in paths:
        if path not in seen:
            out.append(path)
            seen.add(path)
    return out


def _artifact_kind_for_path(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in {".md", ".markdown", ".txt", ".pdf", ".docx", ".xlsx", ".pptx", ".csv", ".tsv"}:
        return "document" if ext not in {".md", ".markdown"} else "report"
    if ext in {".diff", ".patch"}:
        return "diff"
    return "workspace_file"


def _artifact_title_for_path(path: Path, tool_name: str, payload: Dict[str, Any]) -> str:
    title = str(payload.get("title") or "").strip()
    if title:
        return title[:200]
    return f"{tool_name} - {path.name}"


def _json_preview(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True)
    except Exception:
        return str(value)


def _run_subrun_local_vm(
    subrun: Dict[str, Any],
    record: WorktreeRecord,
    *,
    tasks: List[Dict[str, Any]],
    cancelled: Callable[[], bool] | None = None,
    cancel_event: Any = None,
) -> Dict[str, Any]:
    normalized_tasks = _normalize_vm_tasks(tasks)
    subrun_id = str(subrun.get("subrun_id") or record.worktree_id)
    if not normalized_tasks:
        return {
            "schema": COWORK_VM_TASK_RUNNER_SCHEMA,
            "ok": False,
            "runner": "local_vm",
            "backend": "metis_wsl",
            "subrun_id": subrun_id,
            "worktree_id": record.worktree_id,
            "status": "failed",
            "code": "LOCAL_VM_TASKS_REQUIRED",
            "error": "local_vm subrun requires at least one vm_tasks command.",
            "tasks": [],
        }

    started = time.time()
    task_results: List[Dict[str, Any]] = []
    stdout_parts: List[str] = []
    stderr_parts: List[str] = []
    changed_files: List[Dict[str, Any]] = []
    artifacts: List[Dict[str, Any]] = []
    patch_paths: List[str] = []
    first_error = ""
    first_nonzero: Any = None
    timed_out = False

    for index, task in enumerate(normalized_tasks, 1):
        _raise_if_cancelled(cancelled)
        result = run_local_vm_command(
            LocalVmCommand(
                command=str(task.get("command") or ""),
                workspace_root=record.worktree_workspace_root,
                cwd=str(task.get("cwd") or "."),
                timeout=int(task.get("timeout") or 120),
                allow_network=bool(task.get("allow_network")),
                collect_artifacts=bool(task.get("collect_artifacts")),
                artifact_patterns=task.get("artifact_patterns") if isinstance(task.get("artifact_patterns"), list) else None,
                require_artifacts=bool(task.get("require_artifacts")),
                expected_stdout_contains=str(task.get("expected_stdout_contains") or ""),
                export_patch=True,
                export_diagnostics="on_failure",
                cancel_event=cancel_event,
            )
        )
        compact = _compact_local_vm_result(result)
        compact.update(
            {
                "schema": COWORK_VM_TASK_SCHEMA,
                "task_id": str(task.get("task_id") or f"vm_task_{index}"),
                "title": str(task.get("title") or f"VM task {index}"),
                "command": _truncate(str(task.get("command") or ""), 1000),
                "cwd": str(task.get("cwd") or "."),
                "timeout": int(task.get("timeout") or 120),
                "allow_network": bool(task.get("allow_network")),
                "collect_artifacts": bool(task.get("collect_artifacts")),
                "artifact_patterns": task.get("artifact_patterns") if isinstance(task.get("artifact_patterns"), list) else [],
                "expected_stdout_contains": str(task.get("expected_stdout_contains") or ""),
            }
        )
        task_results.append(compact)

        stdout = str(compact.get("stdout") or "")
        stderr = str(compact.get("stderr") or "")
        if stdout:
            stdout_parts.append(f"[{compact['task_id']}] {stdout}")
        if stderr:
            stderr_parts.append(f"[{compact['task_id']}] {stderr}")
        if isinstance(compact.get("changed_files"), list):
            changed_files.extend(item for item in compact["changed_files"] if isinstance(item, dict))
        if isinstance(compact.get("artifacts"), list):
            artifacts.extend(item for item in compact["artifacts"] if isinstance(item, dict))
        patch_path = str(compact.get("patch_path") or "")
        if patch_path:
            patch_paths.append(patch_path)
        timed_out = timed_out or bool(compact.get("timed_out"))
        returncode = compact.get("returncode")
        if first_nonzero is None and returncode not in {None, 0}:
            first_nonzero = returncode
        if not bool(compact.get("ok")):
            first_error = (
                str(compact.get("error") or "").strip()
                or str(compact.get("stderr") or "").strip()
                or f"VM task {compact['task_id']} failed with returncode {returncode}."
            )
            break

    ok = bool(task_results) and all(bool(item.get("ok")) for item in task_results)
    return {
        "schema": COWORK_VM_TASK_RUNNER_SCHEMA,
        "ok": ok,
        "runner": "local_vm",
        "backend": "metis_wsl",
        "subrun_id": subrun_id,
        "worktree_id": record.worktree_id,
        "workspace_root": record.worktree_workspace_root,
        "status": "done" if ok else "failed",
        "started_at": started,
        "finished_at": time.time(),
        "task_count": len(normalized_tasks),
        "completed_task_count": len(task_results),
        "tasks": task_results,
        "returncode": 0 if ok else first_nonzero,
        "timed_out": timed_out,
        "stdout": _truncate("\n".join(stdout_parts), 6000),
        "stderr": _truncate("\n".join(stderr_parts), 6000),
        "changed_files": _dedupe_vm_rows(changed_files),
        "artifacts": _dedupe_vm_rows(artifacts),
        "patch_path": patch_paths[-1] if patch_paths else "",
        "patch_paths": patch_paths,
        "error": _truncate(first_error, 2000) if first_error else "",
    }


def _compact_local_vm_result(result: Dict[str, Any]) -> Dict[str, Any]:
    job = result.get("job") if isinstance(result.get("job"), dict) else {}
    payload: Dict[str, Any] = {
        "ok": bool(result.get("ok")),
        "runner": str(result.get("runner") or "local_vm"),
        "backend": str(result.get("backend") or "metis_wsl"),
        "run_id": str(result.get("run_id") or ""),
        "canceled": bool(result.get("canceled")),
    }
    for key in ("code", "error"):
        if result.get(key):
            payload[key] = str(result.get(key) or "")
    if job:
        payload.update(
            {
                "job_id": str(job.get("job_id") or ""),
                "status": str(job.get("status") or ""),
                "command": _truncate(str(job.get("command") or ""), 1000),
                "returncode": job.get("returncode"),
                "timed_out": bool(job.get("timed_out")),
                "canceled": bool(job.get("canceled")),
                "cancel_detail": str(job.get("cancel_detail") or ""),
                "stdout": _truncate(str(job.get("stdout") or ""), 2000),
                "stderr": _truncate(str(job.get("stderr") or ""), 2000),
                "artifacts_dir": str(job.get("artifacts_dir") or ""),
                "patch_path": str(job.get("patch_path") or ""),
                "changed_files": job.get("changed_files") if isinstance(job.get("changed_files"), list) else [],
                "artifacts": _compact_artifacts(job.get("artifacts")),
                "verifier": job.get("verifier") if isinstance(job.get("verifier"), dict) else {},
            }
        )
    return payload


def _dedupe_vm_rows(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = str(item.get("path") or item.get("relative_path") or item.get("status") or item)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
        if len(out) >= 100:
            break
    return out


def _register_subrun_document_artifacts(
    *,
    record: WorktreeRecord,
    run_id: str,
    session_id: str,
    subrun_id: str,
) -> List[Dict[str, Any]]:
    root = Path(record.worktree_workspace_root).expanduser().resolve()
    if not root.is_dir():
        return []
    changed = _changed_document_relative_paths(root)
    if not changed:
        return []
    existing_paths = {
        str(row.get("path") or "")
        for row in list_artifacts(ArtifactFilters(run_id=run_id, session_id=session_id, limit=500))
        if isinstance(row, dict)
    }
    artifacts: List[Dict[str, Any]] = []
    for path in _document_artifact_candidates(root, changed):
        path_text = str(path)
        if path_text in existing_paths:
            continue
        rel = path.relative_to(root).as_posix()
        try:
            artifact = register_artifact(
                kind="document" if path.suffix.lower() != ".md" else "report",
                title=f"Cowork artifact - {rel}",
                path=path_text,
                run_id=run_id,
                session_id=session_id,
                metadata={
                    "source": "cowork_subrun_document_scan",
                    "subrun_id": subrun_id,
                    "worktree_id": record.worktree_id,
                    "relative_path": rel,
                },
                workspace_root=str(root),
            )
            artifacts.append(artifact)
            existing_paths.add(path_text)
        except Exception:
            continue
    return artifacts


def _changed_document_relative_paths(root: Path) -> set[str]:
    try:
        proc = subprocess.run(
            ["git", "status", "--short", "--untracked-files=all"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
        )
    except Exception:
        return set()
    if proc.returncode != 0:
        return set()
    paths: set[str] = set()
    for line in str(proc.stdout or "").splitlines():
        if len(line) < 4:
            continue
        path = line[3:].strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1].strip()
        path = path.strip('"').replace("\\", "/")
        if Path(path).suffix.lower() in DOCUMENT_ARTIFACT_EXTENSIONS:
            paths.add(path)
    return paths


def _document_artifact_candidates(root: Path, changed: set[str]) -> List[Path]:
    candidates: List[Path] = []
    ignored_parts = {".git", "node_modules", ".venv", "venv", "__pycache__"}
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        rel_parts = set(path.relative_to(root).parts)
        if rel_parts & ignored_parts:
            continue
        if path.suffix.lower() not in DOCUMENT_ARTIFACT_EXTENSIONS:
            continue
        rel = path.relative_to(root).as_posix()
        if rel not in changed:
            continue
        if path.name in {"METIS.md", "MIRO.md"}:
            continue
        try:
            if path.stat().st_size > 50 * 1024 * 1024:
                continue
        except OSError:
            continue
        candidates.append(path)
    candidates.sort(key=lambda item: str(item.relative_to(root)).lower())
    return candidates[:50]


def _collect_subrun_evidence(
    *,
    subrun: Dict[str, Any],
    diff: Dict[str, Any],
    artifacts: List[Dict[str, Any]],
    agent_result: Dict[str, Any],
    vm_result: Dict[str, Any],
    failure_reasons: List[Dict[str, Any]],
) -> Dict[str, Any]:
    diff_evidence = _diff_evidence(diff)
    artifact_evidence = _evidence_artifacts(artifacts)
    artifact_evidence.extend(_local_vm_artifact_evidence(vm_result))
    stdout_test = _stdout_test_evidence(agent_result=agent_result, vm_result=vm_result)
    answer_evidence = _answer_evidence(subrun=subrun, agent_result=agent_result)
    clean_failure_reasons = _clean_failure_reasons(failure_reasons)
    success_evidence = bool(diff_evidence.get("has_changes")) or bool(artifact_evidence) or bool(stdout_test) or bool(answer_evidence)
    has_evidence = success_evidence or bool(clean_failure_reasons)
    return {
        "schema": COWORK_SUBRUN_EVIDENCE_SCHEMA,
        "version": COWORK_SUBRUN_EVIDENCE_VERSION,
        "subrun_id": str(subrun.get("subrun_id") or subrun.get("task_id") or ""),
        "has_evidence": has_evidence,
        "success_evidence": success_evidence,
        "missing_evidence": not has_evidence,
        "missing_success_evidence": not success_evidence,
        "counts": {
            "diff": 1 if diff_evidence.get("has_changes") else 0,
            "artifacts": len(artifact_evidence),
            "stdout_test": len(stdout_test),
            "answer": len(answer_evidence),
            "failure_reasons": len(clean_failure_reasons),
        },
        "diff": diff_evidence,
        "artifacts": artifact_evidence,
        "stdout_test": stdout_test,
        "answer": answer_evidence,
        "failure_reasons": clean_failure_reasons,
    }


def _subrun_has_success_evidence(evidence: Dict[str, Any]) -> bool:
    return bool(evidence.get("success_evidence"))


def _subrun_failure_reasons(
    *,
    agent_result: Dict[str, Any],
    vm_result: Dict[str, Any],
    diff: Dict[str, Any],
) -> List[Dict[str, Any]]:
    reasons: List[Dict[str, Any]] = []
    if agent_result and not bool(agent_result.get("ok")):
        errors = agent_result.get("errors") if isinstance(agent_result.get("errors"), list) else []
        if errors:
            for error in errors[:10]:
                reasons.append(_failure_reason("AGENT_FAILED", str(error or "Agent subrun failed."), source="agent"))
        else:
            reasons.append(_failure_reason("AGENT_FAILED", "Agent subrun failed without error detail.", source="agent"))
    if vm_result and not bool(vm_result.get("ok")):
        code = "LOCAL_VM_CANCELED" if bool(vm_result.get("canceled")) else "LOCAL_VM_TIMED_OUT" if bool(vm_result.get("timed_out")) else "LOCAL_VM_FAILED"
        message = (
            str(vm_result.get("error") or "").strip()
            or str(vm_result.get("cancel_detail") or "").strip()
            or str(vm_result.get("stderr") or "").strip()
            or f"Local VM command failed with returncode {vm_result.get('returncode')}."
        )
        reasons.append(_failure_reason(code, message, source="local_vm"))
    if diff and diff.get("ok") is False:
        reasons.append(
            _failure_reason(
                "DIFF_UNAVAILABLE",
                str(diff.get("error") or "Worktree diff could not be collected."),
                source="worktree_diff",
            )
        )
    return reasons


def _failure_reason(code: str, message: str, *, source: str = "", fatal: bool = True) -> Dict[str, Any]:
    return {
        "code": str(code or "SUBRUN_FAILED"),
        "message": _truncate(str(message or "Subrun failed."), 2000),
        "source": str(source or "cowork_coordinator"),
        "fatal": bool(fatal),
    }


def _has_fatal_failure(reasons: List[Dict[str, Any]]) -> bool:
    return any(isinstance(reason, dict) and bool(reason.get("fatal", True)) for reason in reasons)


def _clean_failure_reasons(reasons: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    clean: List[Dict[str, Any]] = []
    for item in reasons:
        if not isinstance(item, dict):
            continue
        code = str(item.get("code") or "").strip()
        message = str(item.get("message") or "").strip()
        if not code and not message:
            continue
        clean.append(
            {
                "code": code or "SUBRUN_FAILED",
                "message": _truncate(message or code or "Subrun failed.", 2000),
                "source": str(item.get("source") or "cowork_coordinator"),
                "fatal": bool(item.get("fatal", True)),
            }
        )
    return clean[:20]


def _diff_evidence(diff: Dict[str, Any]) -> Dict[str, Any]:
    if not isinstance(diff, dict):
        diff = {}
    status = str(diff.get("status") or "")
    stat = str(diff.get("stat") or "")
    patch_preview = str(diff.get("patch_preview") or diff.get("patch") or "")
    return {
        "ok": bool(diff.get("ok", False)),
        "has_changes": bool(status.strip() or stat.strip() or patch_preview.strip()),
        "worktree_id": str(diff.get("worktree_id") or ""),
        "base_ref": str(diff.get("base_ref") or ""),
        "status": _truncate(status, 2000),
        "stat": _truncate(stat, 2000),
        "patch_preview": _truncate(patch_preview, 4000),
        "changed_files": _changed_files_from_status(status),
        "truncated": bool(diff.get("truncated")),
        "error": str(diff.get("error") or ""),
    }


def _changed_files_from_status(status: str) -> List[Dict[str, str]]:
    files: List[Dict[str, str]] = []
    for line in str(status or "").splitlines():
        if not line.strip():
            continue
        marker = line[:2].strip() or line[:1].strip() or "?"
        path = line[3:].strip() if len(line) >= 4 else line.strip()
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1].strip()
        path = path.strip('"').replace("\\", "/")
        if path:
            files.append({"path": path, "status": marker})
        if len(files) >= 100:
            break
    return files


def _evidence_artifacts(artifacts: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            continue
        metadata = artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {}
        if str(metadata.get("source") or "") == "cowork_subrun":
            continue
        row = {
            "source": str(metadata.get("source") or "artifact_registry"),
            "artifact_id": str(artifact.get("artifact_id") or ""),
            "kind": str(artifact.get("kind") or ""),
            "title": str(artifact.get("title") or ""),
            "path": str(artifact.get("path") or ""),
            "url": str(artifact.get("url") or ""),
            "mime": str(artifact.get("mime") or ""),
            "source_tool_call_id": str(artifact.get("source_tool_call_id") or ""),
        }
        if row["artifact_id"] or row["path"] or row["url"]:
            rows.append(row)
        if len(rows) >= 100:
            break
    return rows


def _local_vm_artifact_evidence(vm_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not isinstance(vm_result, dict):
        return []
    rows: List[Dict[str, Any]] = []
    raw_artifacts = vm_result.get("artifacts") if isinstance(vm_result.get("artifacts"), list) else []
    for item in raw_artifacts:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        relative_path = str(item.get("relative_path") or "")
        if not path and not relative_path:
            continue
        rows.append(
            {
                "source": "local_vm",
                "kind": "workspace_file",
                "title": relative_path or Path(path).name,
                "path": path,
                "relative_path": relative_path,
                "size": item.get("size", 0),
            }
        )
    patch_path = str(vm_result.get("patch_path") or "")
    if patch_path:
        rows.append({"source": "local_vm", "kind": "diff", "title": Path(patch_path).name, "path": patch_path})
    return rows[:100]


def _stdout_test_evidence(*, agent_result: Dict[str, Any], vm_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    if isinstance(agent_result, dict):
        tools = agent_result.get("tools") if isinstance(agent_result.get("tools"), list) else []
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            tool_name = str(tool.get("tool_name") or "")
            if tool_name not in {"execute_bash_command", "run_tests"} and "test" not in tool_name.lower():
                continue
            preview = str(tool.get("result_preview") or "")
            if not preview and not tool.get("status"):
                continue
            rows.append(
                {
                    "source": "agent_tool",
                    "tool_name": tool_name,
                    "call_id": str(tool.get("call_id") or ""),
                    "status": str(tool.get("status") or ""),
                    "preview": _truncate(preview, 2000),
                }
            )
    if isinstance(vm_result, dict) and vm_result:
        stdout = str(vm_result.get("stdout") or "")
        stderr = str(vm_result.get("stderr") or "")
        has_command_evidence = bool(stdout.strip() or stderr.strip() or vm_result.get("returncode") is not None)
        if has_command_evidence:
            rows.append(
                {
                    "source": "local_vm",
                    "runner": str(vm_result.get("runner") or ""),
                    "backend": str(vm_result.get("backend") or ""),
                    "status": str(vm_result.get("status") or ""),
                    "returncode": vm_result.get("returncode"),
                    "stdout": _truncate(stdout, 2000),
                    "stderr": _truncate(stderr, 2000),
                    "changed_files_count": len(vm_result.get("changed_files") if isinstance(vm_result.get("changed_files"), list) else []),
                    "artifact_count": len(vm_result.get("artifacts") if isinstance(vm_result.get("artifacts"), list) else []),
                }
            )
    return rows[:100]


def _answer_evidence(*, subrun: Dict[str, Any], agent_result: Dict[str, Any]) -> List[Dict[str, Any]]:
    if not _subrun_expects_direct_answer(subrun) or not isinstance(agent_result, dict):
        return []
    answer = _extract_cowork_answer_section(str(agent_result.get("final_text") or ""), allow_body_without_heading=True)
    if not answer:
        return []
    return [
        {
            "source": "agent_final_answer",
            "kind": "natural_answer",
            "chars": len(answer),
            "preview": _truncate(_plain_answer_preview(answer), 500),
        }
    ]


def _write_subrun_artifact(
    *,
    subrun: Dict[str, Any],
    record: WorktreeRecord,
    run_id: str,
    session_id: str,
    goal: str,
    vm_result: Dict[str, Any],
) -> Dict[str, Any]:
    root = Path(record.worktree_workspace_root).expanduser().resolve()
    out_dir = root / ".metis" / "cowork"
    out_dir.mkdir(parents=True, exist_ok=True)
    subrun_id = str(subrun.get("subrun_id") or record.worktree_id)
    path = out_dir / f"{subrun_id}.json"
    payload = {
        "schema": "metis.cowork_subrun_report.v1",
        "subrun_id": subrun_id,
        "run_id": run_id,
        "session_id": session_id,
        "goal": goal,
        "title": str(subrun.get("title") or ""),
        "objective": str(subrun.get("objective") or ""),
        "inputs": [str(item) for item in subrun.get("inputs", []) if item] if isinstance(subrun.get("inputs"), list) else [],
        "expected_artifacts": [str(item) for item in subrun.get("expected_artifacts", []) if item] if isinstance(subrun.get("expected_artifacts"), list) else [],
        "acceptance_criteria": [str(item) for item in subrun.get("acceptance_criteria", []) if item] if isinstance(subrun.get("acceptance_criteria"), list) else [],
        "dependencies": [str(item) for item in subrun.get("dependencies", []) if item] if isinstance(subrun.get("dependencies"), list) else [],
        "prompt": str(subrun.get("prompt") or ""),
        "execution_profile": str(subrun.get("execution_profile") or ""),
        "status": str(subrun.get("status") or ""),
        "worktree": record.to_dict(),
        "artifacts": subrun.get("artifacts") if isinstance(subrun.get("artifacts"), list) else [],
        "diff": subrun.get("diff") if isinstance(subrun.get("diff"), dict) else {},
        "evidence": subrun.get("evidence") if isinstance(subrun.get("evidence"), dict) else {},
        "agent": subrun.get("agent") if isinstance(subrun.get("agent"), dict) else {},
        "local_vm": vm_result,
        "created_at": time.time(),
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n")
    return register_artifact(
        kind="report",
        title=f"Cowork subrun - {payload['title'] or subrun_id}",
        path=str(path),
        mime="application/json",
        run_id=run_id,
        session_id=session_id,
        metadata={"source": "cowork_subrun", "subrun_id": subrun_id, "worktree_id": record.worktree_id},
        workspace_root=str(root),
    )


def _safe_diff(source_root: Path, record: WorktreeRecord) -> Dict[str, Any]:
    try:
        return _compact_diff(diff_worktree(str(source_root), record.worktree_id))
    except Exception as exc:
        return {
            "schema": "metis.worktree_diff_summary.v1",
            "ok": False,
            "worktree_id": record.worktree_id,
            "error": f"{type(exc).__name__}: {exc}",
        }


def _compact_diff(diff: Dict[str, Any]) -> Dict[str, Any]:
    worktree = diff.get("worktree") if isinstance(diff.get("worktree"), dict) else {}
    patch = str(diff.get("patch") or "")
    return {
        "schema": "metis.worktree_diff_summary.v1",
        "ok": True,
        "worktree_id": str(worktree.get("worktree_id") or ""),
        "worktree_workspace_root": str(worktree.get("worktree_workspace_root") or ""),
        "base_ref": str(diff.get("base_ref") or ""),
        "status": _truncate(str(diff.get("status") or ""), 2000),
        "stat": _truncate(str(diff.get("stat") or ""), 2000),
        "patch_preview": _truncate(patch, 4000),
        "truncated": bool(diff.get("truncated")) or len(patch) > 4000,
    }


def _compact_artifacts(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        return []
    out: List[Dict[str, Any]] = []
    for item in value[:50]:
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "path": str(item.get("path") or ""),
                "relative_path": str(item.get("relative_path") or item.get("relativePath") or ""),
                "size": item.get("size", 0),
            }
        )
    return out


def _dedupe_artifacts(items: List[Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        key = str(item.get("artifact_id") or item.get("path") or item.get("url") or "")
        if not key:
            key = json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def _cowork_summary_text(summary: Dict[str, Any]) -> str:
    subruns = summary.get("subruns") if isinstance(summary.get("subruns"), list) else []
    done = sum(1 for item in subruns if isinstance(item, dict) and item.get("status") in {"done", "succeeded", "promoted"})
    failed = sum(1 for item in subruns if isinstance(item, dict) and item.get("status") == "failed")
    user_answer = summary.get("user_answer") if isinstance(summary.get("user_answer"), dict) else {}
    answer = str(user_answer.get("text") or "").strip() or _cowork_answer_text(summary, subruns)
    if answer:
        if failed:
            return f"{answer}\n\n注意：Cowork 有 {failed} 个子任务失败，细节已放在右栏活动和汇总 artifact。"
        return answer
    if done:
        return "Cowork 已完成，但没有形成可直接展示的自然回答；报告、diff 和 evidence 已放在右栏活动和汇总 artifact。"
    return "Cowork 没有得到可用回答；错误、diff 和 evidence 已放在右栏活动和汇总 artifact。"


def _cowork_user_answer_payload(summary: Dict[str, Any]) -> Dict[str, Any]:
    subruns = summary.get("subruns") if isinstance(summary.get("subruns"), list) else []
    text = _cowork_answer_text(summary, subruns)
    return {
        "schema": COWORK_USER_ANSWER_SCHEMA,
        "text": text,
        "style": "natural_answer",
        "detail_policy": "reports_diffs_evidence_in_artifacts_or_activity_details",
        "available": bool(text),
    }


def _cowork_answer_text(summary: Dict[str, Any], subruns: List[Any]) -> str:
    candidates: List[str] = []
    for subrun in [item for item in subruns if _subrun_terminal_success(item)]:
        final_text = _subrun_final_text(subrun)
        section = _extract_cowork_answer_section(final_text, allow_body_without_heading=_subrun_expects_direct_answer(subrun))
        if section:
            candidates.append(section)
        for text in _cowork_subrun_markdown_texts(subrun):
            section = _extract_cowork_answer_section(text, allow_body_without_heading=False)
            if section:
                candidates.append(section)
    return _merge_user_answer_candidates(candidates)


def _merge_user_answer_candidates(candidates: List[str]) -> str:
    clean: List[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        text = _sanitize_user_answer(candidate)
        key = " ".join(text.lower().split())
        if not text or key in seen:
            continue
        seen.add(key)
        clean.append(text)
        if len(clean) >= 3:
            break
    if not clean:
        return ""
    if len(clean) == 1:
        return _truncate(clean[0], 2200)
    return _truncate("\n\n".join(clean), 2600)


def _subrun_terminal_success(item: Any) -> bool:
    return isinstance(item, dict) and str(item.get("status") or "").lower() in {"done", "succeeded", "promoted"}


def _subrun_final_text(subrun: Dict[str, Any]) -> str:
    agent = subrun.get("agent") if isinstance(subrun.get("agent"), dict) else {}
    return str(agent.get("final_text") or subrun.get("final_text") or "")


def _cowork_subrun_markdown_texts(subrun: Dict[str, Any]) -> List[str]:
    root_text = str(subrun.get("worktree_workspace_root") or (subrun.get("worktree") or {}).get("worktree_workspace_root") or "")
    root = Path(root_text).expanduser() if root_text else None
    candidates: List[Path] = []
    for path_text in _cowork_markdown_paths_from_subrun(subrun):
        path = Path(path_text)
        if not path.is_absolute() and root is not None:
            path = root / path
        candidates.append(path)

    texts: List[str] = []
    seen: set[str] = set()
    for path in candidates:
        try:
            resolved = path.expanduser().resolve()
            key = str(resolved).lower()
            if key in seen or not resolved.is_file() or resolved.suffix.lower() not in {".md", ".markdown"}:
                continue
            if resolved.stat().st_size > 128_000:
                continue
            seen.add(key)
            texts.append(resolved.read_text(encoding="utf-8", errors="replace"))
        except Exception:
            continue
    return texts


def _cowork_markdown_paths_from_subrun(subrun: Dict[str, Any]) -> List[str]:
    paths: List[str] = []
    diff = subrun.get("diff") if isinstance(subrun.get("diff"), dict) else {}
    for item in diff.get("changed_files") if isinstance(diff.get("changed_files"), list) else []:
        if not isinstance(item, dict):
            continue
        paths.extend([str(item.get("path") or ""), str(item.get("relative_path") or item.get("relativePath") or "")])
    paths.extend(_cowork_markdown_paths_from_git_status(str(diff.get("status") or "")))
    paths.extend(_cowork_markdown_paths_from_patch_preview(str(diff.get("patch_preview") or "")))
    for item in subrun.get("artifacts") if isinstance(subrun.get("artifacts"), list) else []:
        if not isinstance(item, dict):
            continue
        paths.extend([str(item.get("path") or ""), str(item.get("relative_path") or item.get("relativePath") or "")])
    out: List[str] = []
    for path in paths:
        clean = path.strip()
        if not clean:
            continue
        suffix = Path(clean).suffix.lower()
        if suffix in {".md", ".markdown"} and Path(clean).name != ".agent_todos.json":
            out.append(clean)
    return out


def _cowork_markdown_paths_from_git_status(status: str) -> List[str]:
    paths: List[str] = []
    for line in str(status or "").splitlines():
        text = line.strip()
        if not text:
            continue
        match = re.match(r"^(?:[ MADRCU?!]{1,2}|[MADRCU?!]{1,2})\s+(.+?)\s*$", text)
        if match:
            paths.append(match.group(1).strip())
    return paths


def _cowork_markdown_paths_from_patch_preview(patch: str) -> List[str]:
    paths: List[str] = []
    for match in re.finditer(r"(?m)^(?:---|\+\+\+)\s+(?:a|b)/(.+?)\s*$", str(patch or "")):
        path = match.group(1).strip()
        if path and path != "/dev/null":
            paths.append(path)
    return paths


def _extract_cowork_answer_section(text: str, *, allow_body_without_heading: bool = True) -> str:
    source = str(text or "").replace("\r\n", "\n").strip()
    if not source:
        return ""
    headings = list(re.finditer(r"(?m)^(#{1,6})\s*(.+?)\s*$", source))
    targets = [
        "最终回答",
        "直接回答",
        "结论摘要",
        "核心结论",
        "结论",
        "final answer",
        "answer",
        "summary",
    ]
    for target in targets:
        for index, match in enumerate(headings):
            title = match.group(2).strip().lower()
            if target not in title:
                continue
            end = headings[index + 1].start() if index + 1 < len(headings) else len(source)
            section = source[match.end():end].strip()
            cleaned = _strip_cowork_status_noise(section)
            if cleaned:
                return _truncate(_sanitize_user_answer(cleaned), 2200)
    if headings:
        return ""
    if not allow_body_without_heading:
        return ""
    cleaned = _strip_cowork_status_noise(source)
    if cleaned and not _looks_like_cowork_completion_only(cleaned) and not _looks_like_detail_report(cleaned):
        return _truncate(_sanitize_user_answer(cleaned), 1400)
    return ""


def _strip_cowork_status_noise(text: str) -> str:
    lines = [line.rstrip() for line in str(text or "").strip().splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    noise_prefixes = (
        "已完成子任务",
        "已完成该子任务",
        "## 产物",
        "## 变更摘要",
        "## 验证情况",
        "## 未解决风险",
        "Changed files",
        "Files changed",
        "Validation",
        "Artifacts",
        "Evidence",
    )
    if lines and any(lines[0].strip().startswith(prefix) for prefix in noise_prefixes):
        return ""
    if len(lines) == 1 and _looks_like_cowork_completion_only(lines[0]):
        return ""
    return "\n".join(lines).strip()


def _looks_like_cowork_completion_only(text: str) -> bool:
    normalized = " ".join(str(text or "").lower().split())
    if not normalized:
        return True
    return normalized in {"done", "finished", "completed", "success", "succeeded", "完成", "已完成"} or normalized.startswith("cowork local run complete") or normalized.startswith("cowork 已完成")


def _sanitize_user_answer(text: str) -> str:
    source = str(text or "").replace("\r\n", "\n").strip()
    if not source:
        return ""
    source = re.sub(r"(?m)^#{1,6}\s*(最终回答|直接回答|final answer|answer)\s*$", "", source, flags=re.IGNORECASE).strip()
    sections = list(re.finditer(r"(?m)^(#{1,6})\s*(.+?)\s*$", source))
    if not sections:
        return source
    kept: List[str] = []
    cursor = 0
    for index, match in enumerate(sections):
        if match.start() > cursor:
            kept.append(source[cursor:match.start()].strip())
        title = match.group(2).strip()
        end = sections[index + 1].start() if index + 1 < len(sections) else len(source)
        body = source[match.end():end].strip()
        cursor = end
        if _is_detail_heading(title):
            continue
        kept.append(f"{match.group(0).strip()}\n\n{body}".strip())
    if cursor < len(source):
        kept.append(source[cursor:].strip())
    return "\n\n".join(part for part in kept if part).strip()


def _plain_answer_preview(text: str) -> str:
    return re.sub(r"\s+", " ", _sanitize_user_answer(text)).strip()


def _looks_like_detail_report(text: str) -> bool:
    source = str(text or "")
    headings = re.findall(r"(?m)^#{1,6}\s*(.+?)\s*$", source)
    if any(_is_detail_heading(title) for title in headings):
        return True
    lower = source.lower()
    markers = [
        "diff",
        "evidence",
        "artifacts",
        "changed files",
        "files changed",
        "validation",
        "tests",
        "变更摘要",
        "验证情况",
        "产物",
        "证据",
    ]
    return sum(1 for marker in markers if marker in lower) >= 2


def _is_detail_heading(title: str) -> bool:
    lower = str(title or "").strip().lower()
    return any(
        marker in lower
        for marker in [
            "diff",
            "evidence",
            "artifact",
            "changed file",
            "file change",
            "validation",
            "test",
            "risk",
            "report",
            "产物",
            "证据",
            "变更",
            "验证",
            "测试",
            "风险",
            "报告",
            "文件",
        ]
    )


def _raise_if_cancelled(cancelled: Callable[[], bool] | None) -> None:
    if callable(cancelled) and cancelled():
        raise OperationCancelled("Cowork run cancelled")


def _truncate(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + "..."


def _collect_subrun_artifacts(subruns: List[Any]) -> List[Dict[str, Any]]:
    artifacts: List[Dict[str, Any]] = []
    for item in subruns:
        if not isinstance(item, dict):
            continue
        raw = item.get("artifacts")
        if isinstance(raw, list):
            artifacts.extend([artifact for artifact in raw if isinstance(artifact, dict)])
    return artifacts


def _collect_subrun_diffs(subruns: List[Any]) -> List[Dict[str, Any]]:
    diffs: List[Dict[str, Any]] = []
    for item in subruns:
        if not isinstance(item, dict):
            continue
        diff = item.get("diff")
        if isinstance(diff, dict) and diff:
            diffs.append(diff)
    return diffs


__all__ = [
    "COWORK_COORDINATOR_SCHEMA",
    "COWORK_EXECUTION_SCHEMA",
    "COWORK_LEGACY_PLAN_SCHEMA",
    "COWORK_PLAN_SCHEMA",
    "COWORK_PLAN_VERSION",
    "COWORK_PLANNER_SCHEMA",
    "COWORK_SUBRUN_EVIDENCE_SCHEMA",
    "COWORK_SUBRUN_EVIDENCE_VERSION",
    "COWORK_SUBRUN_EVENT_SCHEMA",
    "COWORK_SUBRUN_EVENT_VERSION",
    "COWORK_START_DECISION_SCHEMA",
    "COWORK_SUMMARY_SCHEMA",
    "COWORK_USER_ANSWER_SCHEMA",
    "build_cowork_plan",
    "decide_cowork_start",
    "has_cowork_scheduler_state",
    "iter_local_cowork_events",
    "load_cowork_scheduler_state",
    "summarize_cowork_results",
]

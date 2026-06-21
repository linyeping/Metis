from __future__ import annotations

import json
import logging
import queue
import hashlib
import os
import threading
import time
import concurrent.futures
from contextlib import nullcontext
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Dict, Generator, List, Optional, Tuple

from .error_catalog import classify_llm_error, is_non_retryable_llm_error
from .context_budget import context_ledger
from .context_eviction import evict_tool_results, maybe_auto_compact_messages
from .edit_guard import EditGuard
from .hook_lifecycle_bus import emit_hook_lifecycle
from .image_utils import build_image_content_block, extract_image_paths
from .llm_backends import LLMBackend, LLMResponse, ToolCall, Usage, get_backend
from .llm_backends._common import sanitize_for_log
from .loop_discipline import (
    LOOP_DISCIPLINE_PROMPT,
    VerificationTracker,
    compact_todo_block,
    workspace_todo_block,
)
from .model_router import (
    ROUTE_MARKER,
    TaskRoute,
    build_task_route,
    desktop_control_tools,
    document_workflow_tools,
    prioritized_tools_for_route,
    render_route_hint,
    router_enabled,
    should_block_desktop_control,
    should_block_document_tools,
)
from .permission_control import evaluate_permission
from .result_compactor import ResultCompactor
from .tool_call_tracker import ToolCallTracker
from .tool_errors import teaching_error_text
from .tool_profiles import normalize_tool_profile
from .tool_registry import ToolRegistry, get_registry
from .tool_visibility import sanitize_tool_result
from backend.bridges.model_capability import detect_from_model_name
from backend.bridges.provider_registry import resolve_provider_for_config, requires_reasoning_passback_enabled
from backend.runtime.tool_tiers import INTERNAL_TOOLS, expose_internal_tools, tools_for_tier
from .cancellation import (
    OperationCancelled,
    cancellation_context,
    is_cancel_requested,
    raise_if_cancelled,
    wait_or_cancel,
)

logger = logging.getLogger(__name__)
MAX_WORKING_CONTEXT_CHARS = int(os.environ.get("METIS_MAX_WORKING_CONTEXT_CHARS", "1500000"))
TOOL_EXECUTION_TIMEOUT = float(os.environ.get("METIS_TOOL_EXECUTION_TIMEOUT", "300"))
CONTENT_DELTA_FLUSH_INTERVAL = float(os.environ.get("METIS_CONTENT_DELTA_FLUSH_INTERVAL", "0.08"))
CONTENT_DELTA_FLUSH_CHARS = int(os.environ.get("METIS_CONTENT_DELTA_FLUSH_CHARS", "30"))
REPEATED_TOOL_CALL_LIMIT = 3
TOOL_CALL_RECOVERY_PROMPT = (
    "Your previous response indicated a tool/function call, but the call could not be parsed or had no valid arguments. "
    "Repair it now by returning exactly one native tool/function call using one of the available tools. "
    "Use a strict JSON object for arguments, keep the same intent, do not answer in prose, and do not invent tool names. "
    "If no tool is actually needed, return a concise final answer instead."
)
MAX_TOOL_CALL_REPAIR_ATTEMPTS = int(os.environ.get("METIS_TOOL_CALL_REPAIR_ATTEMPTS", "2"))
# FABLEADV-19: truncation continuation. When output is cut off by max_tokens, the
# turn was not "done" — guide the model to continue / chunk instead of ending.
MAX_TRUNCATION_CONTINUATIONS = int(os.environ.get("METIS_MAX_CONTINUATIONS", "5"))
CONTINUATION_PROMPT = (
    "你上一条回复因长度限制被截断了（未写完）。请从被截断处**继续输出剩余内容**，"
    "不要重复已经输出的部分，也不要重新开始。"
)
TRUNCATED_TOOLCALL_PROMPT = (
    "你上一次的工具调用参数因长度限制被截断，无法执行。请改用分块方式：先用 write_file "
    "写入文件的前一部分（或骨架），再用 append_to_file 追加剩余内容；或精简单次写入量。"
)
_REASONING_ORIGIN_PROVIDER_KEY = "_metis_reasoning_provider_id"
_REASONING_ORIGIN_MODEL_KEY = "_metis_reasoning_model"
_REASONING_ORIGIN_BASE_URL_KEY = "_metis_reasoning_base_url"


@dataclass
class Event:
    type: str
    data: Any = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class ContentEvent(Event):
    type: str = "content"
    text: str = ""


@dataclass
class ContentDeltaEvent(Event):
    type: str = "content_delta"
    text: str = ""


@dataclass
class TextDeltaEvent(Event):
    type: str = "text_delta"
    text: str = ""


@dataclass
class ThinkingEvent(Event):
    type: str = "thinking"
    text: str = ""


@dataclass
class ToolCallEvent(Event):
    type: str = "tool_call"
    tool_name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    call_id: str = ""


@dataclass
class ToolResultEvent(Event):
    type: str = "tool_result"
    tool_name: str = ""
    result: str = ""
    call_id: str = ""


@dataclass
class TodoUpdateEvent(Event):
    type: str = "todo_update"
    todos: List[Dict[str, Any]] = field(default_factory=list)
    summary: str = ""
    call_id: str = ""


@dataclass
class PermissionRequestEvent(Event):
    type: str = "permission_request"
    tool_name: str = ""
    arguments: Dict[str, Any] = field(default_factory=dict)
    call_id: str = ""
    request_id: str = ""


@dataclass
class ErrorEvent(Event):
    type: str = "error"
    message: str = ""
    recoverable: bool = True
    code: str = "LLM_ERROR"
    title: str = ""
    hint: str = ""
    status: int = 0
    details: str = ""


@dataclass
class DoneEvent(Event):
    type: str = "done"
    total_turns: int = 0
    total_tool_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    prompt_cache_hit_tokens: int = 0
    prompt_cache_miss_tokens: int = 0
    context_ledger: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CompactEvent(Event):
    type: str = "compact"
    before_count: int = 0
    after_count: int = 0
    summary_preview: str = ""


@dataclass
class RuntimeStatusEvent(Event):
    type: str = "runtime_status"
    phase: str = ""
    message: str = ""
    turn: int = 0
    tool_calls: int = 0
    tool_name: str = ""
    call_id: str = ""
    recoverable: bool = True
    details: Dict[str, Any] = field(default_factory=dict)


@dataclass
class AgentConfig:
    llm_backend: str = "openai"
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    # 推理强度开关（opt-in，默认空=关，零行为变化）。取值 off/low/medium/high/max；
    # 仅对 DeepSeek v4 推理模型注入 reasoning_effort + thinking，其它供应商/模型忽略。
    reasoning_effort: str = ""
    temperature: float = 0.3
    # FABLEADV-19: 4096 was too small for writing whole files (e.g. a 569-line
    # CSS exceeds it → truncated tool_call → "No response" task failure). Modern
    # models (DeepSeek v4, GPT-5.x) support far larger output.
    max_tokens: int = 8192
    max_turns: int = 64
    max_consecutive_errors: int = 3
    timeout: float = 120.0
    system_prompt: str = ""
    enabled_tools: List[str] = field(default_factory=list)
    execution_mode: str = "auto"
    workspace_root: str = ""
    session_id: str = ""
    permission_checker: Optional[Callable[[str, Dict[str, Any]], Optional[str]]] = None
    tool_boundary_overrides: Optional[Callable[[str, Dict[str, Any]], Dict[str, bool]]] = None
    routing_task_type: str = ""
    routing_model_role: str = ""
    routing_reason: str = ""
    routing_tool_guidance: str = ""
    routing_preferred_tools: List[str] = field(default_factory=list)
    model_fallbacks: List[str] = field(default_factory=list)
    requested_model: str = ""


# FABLEADV-25: 环境探测结果记忆化。原实现每个 run 现探测、带 20ms 超时——
# 冷启动超时返回空、热启动返回完整摘要，**结果时而空时而满**。它被插进系统前缀，
# 于是跨 run 前缀字节漂移 → 打破 DeepSeek 上下文缓存（命中率掉）。
# 一旦探测成功就缓存，之后所有 run 复用同一份，前缀稳定、缓存最大化（且更快）。
_ENV_CONTEXT_CACHE: Optional[str] = None


def _build_environment_context() -> str:
    """Return a small runtime availability summary for coding decisions (memoized)."""
    global _ENV_CONTEXT_CACHE
    if _ENV_CONTEXT_CACHE is not None:
        return _ENV_CONTEXT_CACHE

    result_queue: "queue.Queue[str]" = queue.Queue(maxsize=1)

    def worker() -> None:
        global _ENV_CONTEXT_CACHE
        text = _detect_environment_context()
        # 即使首个 run 已超时返回空，worker 完成后写入缓存，后续 run 即取稳定值。
        _ENV_CONTEXT_CACHE = text
        try:
            result_queue.put_nowait(text)
        except queue.Full:
            pass

    thread = threading.Thread(target=worker, daemon=True, name="metis-env-context")
    thread.start()
    try:
        timeout = float(os.environ.get("METIS_ENV_CONTEXT_TIMEOUT", "0.02"))
    except ValueError:
        timeout = 0.02
    thread.join(max(0.0, timeout))
    if _ENV_CONTEXT_CACHE is not None:
        return _ENV_CONTEXT_CACHE
    try:
        return result_queue.get_nowait()
    except queue.Empty:
        logger.debug("environment context detection skipped after %.3fs", timeout)
        return ""  # 尚未缓存；下一个 run 会取到 worker 写入的稳定值


def _detect_environment_context() -> str:
    try:
        from shutil import which

        from backend.tools.coding.execution.runtime_manager import KNOWN_RUNTIMES

        quick_names = {"Python", "Node.js", "Git"}
        quick_runtimes = [runtime for runtime in KNOWN_RUNTIMES if runtime.name in quick_names]
        available = [
            runtime.name
            for runtime in quick_runtimes
            if any(which(cli_name) for cli_name in runtime.cli_names)
        ]
        missing = [
            runtime.name
            for runtime in quick_runtimes
            if runtime.name not in set(available)
        ]
        lines: list[str] = []
        if available:
            lines.append(f"Available runtimes: {', '.join(available)}.")
        if missing:
            lines.append(
                "Missing runtimes: "
                + ", ".join(missing)
                + ". Use check_dev_environment or install_dev_runtime before running commands that need them."
            )
        text = "\n".join(lines)
    except Exception:
        text = ""
    return text


def _system_prompt_with_environment_context(system_prompt: str) -> str:
    prompt = str(system_prompt or "").rstrip()
    if "[Loop Discipline]" not in prompt:
        prompt = prompt + "\n\n" + LOOP_DISCIPLINE_PROMPT
    return prompt.strip()


def _prepare_working_messages(messages: List[Dict[str, Any]], config: AgentConfig) -> List[Dict[str, Any]]:
    working_messages = list(messages)
    prompt = _system_prompt_with_environment_context(config.system_prompt)
    if prompt and (
        not working_messages or working_messages[0].get("role") != "system"
    ):
        working_messages.insert(0, {"role": "system", "content": prompt})
    elif prompt and working_messages and working_messages[0].get("role") == "system":
        content = str(working_messages[0].get("content") or "")
        if "[Loop Discipline]" not in content:
            working_messages[0] = {**working_messages[0], "content": _system_prompt_with_environment_context(content)}
    env_context = _build_environment_context()
    if env_context:
        _insert_environment_context_message(working_messages, env_context)
    route_hint = _route_hint_message(config)
    if route_hint:
        working_messages = _without_route_hint(working_messages)
        working_messages.append({"role": "user", "content": route_hint})
    return working_messages


def _insert_environment_context_message(messages: List[Dict[str, Any]], env_context: str) -> None:
    if not env_context:
        return
    marker = "## Development Environment"
    for message in messages:
        if message.get("role") == "system" and marker in str(message.get("content") or ""):
            return
    insert_at = 0
    while insert_at < len(messages) and messages[insert_at].get("role") == "system":
        insert_at += 1
    messages.insert(insert_at, {"role": "system", "content": f"{marker}\n{env_context}"})


def _messages_for_llm_request(messages: List[Dict[str, Any]], config: AgentConfig) -> List[Dict[str, Any]]:
    replay_reasoning = requires_reasoning_passback_enabled(
        config.llm_backend,
        base_url=config.llm_base_url,
        model=config.llm_model,
    )
    current_origin = _reasoning_origin_for_config(config)
    request_messages: List[Dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict):
            request_messages.append(message)
            continue
        # FABLEADV-16: transcript-only tool records (persisted so the UI can
        # rebuild tool cards after reload/compaction) must never be sent to the
        # model — they are not valid OpenAI tool messages and would break the
        # request. The live run keeps its own working tool context.
        if message.get("metis_kind") == "tool":
            continue
        cleaned = {
            key: value
            for key, value in message.items()
            if key
            not in {
                _REASONING_ORIGIN_PROVIDER_KEY,
                _REASONING_ORIGIN_MODEL_KEY,
                _REASONING_ORIGIN_BASE_URL_KEY,
                "metis_kind",
                "metis_tool",
            }
        }
        if "reasoning_content" not in cleaned:
            request_messages.append(cleaned)
            continue
        should_keep = (
            replay_reasoning
            and cleaned.get("role") == "assistant"
            and _reasoning_origin_matches(message, current_origin)
        )
        if not should_keep:
            cleaned.pop("reasoning_content", None)
        request_messages.append(cleaned)
    return request_messages


def _reasoning_origin_for_config(config: AgentConfig) -> Dict[str, str]:
    try:
        profile = resolve_provider_for_config(
            config.llm_backend,
            base_url=config.llm_base_url,
            model=config.llm_model,
        )
        provider_id = str(profile.provider_id)
        base_url = str(config.llm_base_url or profile.base_url or "").strip().rstrip("/")
        model = str(config.llm_model or profile.default_model or "").strip()
    except Exception:
        provider_id = str(config.llm_backend or "").strip()
        base_url = str(config.llm_base_url or "").strip().rstrip("/")
        model = str(config.llm_model or "").strip()
    return {
        "provider_id": provider_id.lower(),
        "base_url": base_url.lower(),
        "model": model.lower(),
    }


def _reasoning_origin_matches(message: Dict[str, Any], current_origin: Dict[str, str]) -> bool:
    origin_keys = {
        _REASONING_ORIGIN_PROVIDER_KEY,
        _REASONING_ORIGIN_MODEL_KEY,
        _REASONING_ORIGIN_BASE_URL_KEY,
    }
    if not any(key in message for key in origin_keys):
        return True
    provider_id = str(message.get(_REASONING_ORIGIN_PROVIDER_KEY) or "").strip().lower()
    model = str(message.get(_REASONING_ORIGIN_MODEL_KEY) or "").strip().lower()
    base_url = str(message.get(_REASONING_ORIGIN_BASE_URL_KEY) or "").strip().rstrip("/").lower()
    if provider_id and provider_id != current_origin.get("provider_id", ""):
        return False
    if model and model != current_origin.get("model", ""):
        return False
    if base_url and current_origin.get("base_url") and base_url != current_origin.get("base_url", ""):
        return False
    return True


_TODO_CONTEXT_MARKER = "[Metis dynamic todo state]"
_TURN_BUDGET_MARKER = "[轮次预算]"


def _refresh_todo_context_message(messages: List[Dict[str, Any]], workspace_root: str = "") -> List[Dict[str, Any]]:
    next_messages = [
        message
        for message in messages
        if _TODO_CONTEXT_MARKER not in str(message.get("content") or "")
    ]
    block = workspace_todo_block(workspace_root)
    if block:
        next_messages.append({"role": "system", "content": f"{_TODO_CONTEXT_MARKER}{block}"})
    return next_messages


def _clear_run_todos(workspace_root: str = "") -> None:
    if not workspace_root:
        return
    try:
        from backend.tools.coding.workflow_features.agent_state.state_paths import AGENT_TODO_FILE

        path = os.path.join(workspace_root, AGENT_TODO_FILE)
        if os.path.isfile(path):
            os.remove(path)
    except Exception:
        logger.debug("failed to clear run todo state", exc_info=True)


def _config_with_task_route(config: AgentConfig, messages: List[Dict[str, Any]]) -> AgentConfig:
    if not router_enabled() or config.routing_task_type:
        return config
    if str(config.llm_backend or "").strip().lower() == "fake":
        return config
    try:
        route = build_task_route(
            messages,
            llm_backend=config.llm_backend,
            llm_base_url=config.llm_base_url,
            llm_model=config.llm_model,
        )
    except Exception:
        logger.debug("task routing failed; using current model/tool order", exc_info=True)
        return config
    next_model = route.selected_model or config.llm_model
    logger.info(
        "task route type=%s role=%s model=%s fallback=%s reason=%s",
        route.task_type,
        route.model_role,
        next_model,
        route.fallback_models[:4],
        route.reason,
    )
    return replace(
        config,
        llm_model=next_model,
        requested_model=config.requested_model or config.llm_model,
        routing_task_type=route.task_type,
        routing_model_role=route.model_role,
        routing_reason=route.reason,
        routing_tool_guidance=route.tool_guidance,
        routing_preferred_tools=list(route.preferred_tools),
        model_fallbacks=list(route.fallback_models),
    )


def _route_hint_message(config: AgentConfig) -> str:
    if not config.routing_task_type:
        return ""
    return render_route_hint(
        TaskRoute(
            task_type=config.routing_task_type,
            model_role=config.routing_model_role,
            selected_model=config.llm_model,
            fallback_models=list(config.model_fallbacks or []),
            preferred_tools=list(config.routing_preferred_tools or []),
            reason=config.routing_reason,
            tool_guidance=config.routing_tool_guidance,
        )
    )


def _without_route_hint(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        message
        for message in messages
        if ROUTE_MARKER not in str((message or {}).get("content") or "")
    ]


def _routing_status_message(config: AgentConfig) -> str:
    role = config.routing_model_role or "current"
    task_type = config.routing_task_type or "task"
    return f"已按 {task_type} 任务选择 {role} 路由：{config.llm_model}"


def _model_fallback_status_message(current: AgentConfig, fallback: AgentConfig) -> str:
    return f"模型 {current.llm_model or 'current'} 调用失败，已自动切换到 {fallback.llm_model} 重试。"


def _model_debug_details(
    config: AgentConfig,
    backend: Optional[LLMBackend] = None,
    response: Optional[LLMResponse] = None,
    *,
    fallback_from: str = "",
    fallback_to: str = "",
    error: BaseException | None = None,
) -> Dict[str, Any]:
    raw = response.raw if response is not None and isinstance(response.raw, dict) else {}
    raw_model = str(raw.get("model") or "").strip()
    if not raw_model and isinstance(raw.get("chunks"), list):
        for chunk in reversed(raw.get("chunks") or []):
            if isinstance(chunk, dict) and str(chunk.get("model") or "").strip():
                raw_model = str(chunk.get("model")).strip()
                break
    return {
        "user_selected_model": config.requested_model or config.llm_model,
        "router_selected_model": config.llm_model,
        "request_model": str(getattr(backend, "model", "") or config.llm_model),
        "served_model": raw_model or str(getattr(backend, "detected_model", "") or ""),
        "backend": config.llm_backend,
        "base_url_host": _base_url_host(config.llm_base_url),
        "routing_task_type": config.routing_task_type,
        "routing_model_role": config.routing_model_role,
        "fallback_models": list(config.model_fallbacks or []),
        "fallback_from": fallback_from,
        "fallback_to": fallback_to,
        "error": f"{type(error).__name__}: {sanitize_for_log(error)}" if error else "",
    }


def _base_url_host(url: str) -> str:
    value = str(url or "").strip()
    if not value:
        return ""
    try:
        from urllib.parse import urlparse

        parsed = urlparse(value if "://" in value else f"https://{value}")
        return parsed.netloc or parsed.path.split("/", 1)[0]
    except Exception:
        return value.split("/", 1)[0]


def _next_model_fallback_config(config: AgentConfig, exc: BaseException) -> Optional[AgentConfig]:
    if not config.model_fallbacks:
        return None
    info = classify_llm_error(exc, recoverable=True)
    if info.code in {"LLM_AUTH_FAILED", "LLM_FORBIDDEN", "LLM_API_KEY_MISSING"}:
        return None
    current = str(config.llm_model or "").strip().lower()
    for model in config.model_fallbacks:
        candidate = str(model or "").strip()
        if not candidate or candidate.lower() == current:
            continue
        remaining = [
            item
            for item in config.model_fallbacks
            if str(item or "").strip().lower() not in {current, candidate.lower()}
        ]
        return replace(config, llm_model=candidate, model_fallbacks=remaining)
    return None


def _append_turn_budget_hint(
    messages: List[Dict[str, Any]],
    *,
    max_turns: int,
    completed_turns: int,
) -> List[Dict[str, Any]]:
    remaining = max_turns - completed_turns
    if remaining <= 0 or remaining > 3:
        return messages
    marker = f"{_TURN_BUDGET_MARKER} remaining={remaining}"
    if any(marker in str(message.get("content") or "") for message in messages):
        return messages
    return messages + [
        {
            "role": "system",
            "content": (
                f"{marker}\n"
                f"你还剩 {remaining} 轮。停止探索，立即用现有信息完成任务交付"
                "（写出文件/给出答案）。宁可基于不完整信息交付，也不要空手耗尽轮次。"
            ),
        }
    ]


def _auto_compact_ratio() -> float:
    raw = os.environ.get("METIS_AUTO_COMPACT_RATIO", "0.7").strip()
    try:
        return float(raw)
    except ValueError:
        logger.warning("invalid METIS_AUTO_COMPACT_RATIO=%r; using 0.7", raw)
        return 0.7


def _maybe_auto_compact_context(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    config: AgentConfig,
    *,
    turn: int,
    tool_calls: int,
) -> Tuple[List[Dict[str, Any]], List[Event]]:
    result = maybe_auto_compact_messages(
        messages,
        tools=tools,
        model=config.llm_model,
        ratio_threshold=_auto_compact_ratio(),
    )
    if not result.compacted:
        return result.messages, []
    logger.info(
        "runtime auto compact messages_before=%s messages_after=%s ratio=%s",
        result.before_count,
        result.after_count,
        result.context_ratio,
    )
    return result.messages, [
        _runtime_status_event(
            "compact_started",
            "Compacting runtime context",
            turn=turn,
            tool_calls=tool_calls,
        ),
        CompactEvent(
            before_count=result.before_count,
            after_count=result.after_count,
            summary_preview=result.summary_preview,
        ),
        _runtime_status_event(
            "compact_done",
            "Runtime context compacted",
            turn=turn,
            tool_calls=tool_calls,
        ),
    ]


def _evict_tool_results_for_budget(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    config: AgentConfig,
) -> List[Dict[str, Any]]:
    ledger = context_ledger(messages, tools, model=config.llm_model)
    ratio = float(ledger.get("context_ratio") or 0.0)
    if ratio < 0.5:
        return messages
    evicted_messages, evicted = evict_tool_results(messages, context_ratio=ratio)
    if evicted:
        logger.info("runtime tool result eviction evicted=%s ratio=%s", evicted, ratio)
    return evicted_messages


def _log_context_ledger(
    messages: List[Dict[str, Any]],
    tools: List[Dict[str, Any]],
    config: AgentConfig,
    *,
    phase: str,
    turn: int,
    usage: Optional[Usage] = None,
) -> Dict[str, Any]:
    payload = context_ledger(messages, tools, usage=usage, model=config.llm_model)
    payload["phase"] = phase
    payload["turn"] = turn
    logger.info("context_ledger %s", json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return payload


def _emit_agent_lifecycle(
    kind: str,
    config: Optional[AgentConfig],
    *,
    ok: Optional[bool] = None,
    status: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> None:
    try:
        emit_hook_lifecycle(
            kind,
            workspace_root=config.workspace_root if config else "",
            ok=ok,
            status=status,
            metadata=metadata or {},
        )
    except Exception:
        logger.debug("failed to emit hook lifecycle event %s", kind, exc_info=True)


def _done_event(
    turn_count: int,
    tool_call_count: int,
    usage: Usage,
    *,
    ledger: Optional[Dict[str, Any]] = None,
    messages: Optional[List[Dict[str, Any]]] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    config: Optional[AgentConfig] = None,
) -> DoneEvent:
    context_payload = dict(ledger or {})
    if not context_payload and messages is not None and config is not None:
        try:
            context_payload = context_ledger(messages, tools or [], usage=usage, model=config.llm_model)
        except Exception:
            logger.debug("failed to build done context ledger", exc_info=True)
            context_payload = {}
    _emit_agent_lifecycle(
        "agent.stop",
        config,
        ok=True,
        status="stopped",
        metadata={
            "turns": turn_count,
            "tool_calls": tool_call_count,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
        },
    )
    return DoneEvent(
        total_turns=turn_count,
        total_tool_calls=tool_call_count,
        prompt_tokens=usage.prompt_tokens,
        completion_tokens=usage.completion_tokens,
        total_tokens=usage.total_tokens,
        prompt_cache_hit_tokens=usage.prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=usage.prompt_cache_miss_tokens,
        context_ledger=context_payload,
    )


def _save_runtime_checkpoint(
    config: AgentConfig,
    messages: List[Dict[str, Any]],
    *,
    turn_count: int,
    tool_call_count: int,
    reason: str,
) -> None:
    session_id = str(getattr(config, "session_id", "") or "").strip()
    if not session_id:
        return
    try:
        from .checkpoint_store import save_checkpoint

        state = {
            "kind": "agent_loop",
            "reason": reason,
            "messages": json.loads(json.dumps(messages, ensure_ascii=False, default=str)),
            "runtime": {
                "turn": turn_count,
                "tool_calls": tool_call_count,
                "model": config.llm_model,
            },
        }
        save_checkpoint(session_id, state)
    except Exception:
        logger.debug("failed to save runtime checkpoint", exc_info=True)


_SAFE_TOOLS: set[str] = {
    "read_file",
    "read_multiple_files",
    "list_directory",
    "read_terminal_state",
    "search_in_file",
    "search_in_codebase",
    "find_files",
    "ast_search_code",
    "generate_repo_map",
    "web_search",
    "web_research",
    "web_fetch",
    "desktop_screenshot",
    "desktop_inventory",
    "metis_rootfs_asset_status",
    "metis_rootfs_source_status",
    "metis_rootfs_builder_status",
    "metis_rootfs_image_builder_status",
    "metis_vm_bundle_status",
    "metis_wsl_runtime_status",
    "metis_sandbox_status",
    "metis_runtime_status",
}
_PARALLEL_READONLY_TOOLS: set[str] = {
    "read_file",
    "read_multiple_files",
    "list_directory",
    "grep_search",
    "glob_search",
    "search_in_files",
    "generate_repo_map",
    "web_search",
    "web_research",
    "web_fetch",
}
_EDIT_TOOLS: set[str] = {
    "write_file",
    "append_to_file",
    "robust_replace_in_file",
    "edit_code_ast",
    "apply_patch",
    "rename_file_update_refs",
    "delete_file",
    "delete_directory",
    "create_directory",
    "metis_rootfs_asset_download",
    "metis_rootfs_build",
    "metis_rootfs_image_build",
    "metis_rootfs_asset_register",
    "metis_runtime_bundle_package",
    "metis_runtime_bundle_package_v2",
    "metis_runtime_bundle_prepare",
    "metis_vm_direct_assets_prepare",
    "metis_vm_direct_runner_prepare",
    "metis_vm_direct_runner_smoke",
    "metis_vm_hcs_starter_prepare",
    "metis_vm_hcs_starter_start",
    "metis_vm_guest_handshake_prepare",
    "metis_vm_guest_handshake_verify",
    "metis_vm_rootfs_boot_verifier_prepare",
    "metis_vm_rootfs_boot_verify",
    "metis_vm_pack_adopt_reference",
    "metis_vm_pack_scaffold",
    "metis_wsl_runtime_import",
    "metis_runtime_create",
    "metis_runtime_run",
    "metis_runtime_collect_artifacts",
    "metis_runtime_export_patch",
    "metis_runtime_export_diagnostics",
}


def _error_done_event(
    turn_count: int,
    tool_call_count: int,
    usage: Usage,
    *,
    ledger: Optional[Dict[str, Any]] = None,
    messages: Optional[List[Dict[str, Any]]] = None,
    tools: Optional[List[Dict[str, Any]]] = None,
    config: Optional[AgentConfig] = None,
) -> DoneEvent:
    return _done_event(
        turn_count,
        tool_call_count,
        usage,
        ledger=ledger,
        messages=messages,
        tools=tools,
        config=config,
    )


def _llm_error_event(
    exc: BaseException,
    *,
    message: str,
    recoverable: bool,
) -> ErrorEvent:
    info = classify_llm_error(exc, message=message, recoverable=recoverable)
    return ErrorEvent(
        message=info.message,
        recoverable=info.recoverable,
        code=info.code,
        title=info.title,
        hint=info.hint,
        status=info.status,
        details=info.details,
    )


def _cancelled_error_event() -> ErrorEvent:
    return ErrorEvent(
        code="RUN_CANCELLED",
        title="运行已取消",
        message="本次后台运行已取消。",
        hint="可以重新发送，或从会话历史继续。",
        recoverable=False,
    )


def _runtime_status_event(
    phase: str,
    message: str = "",
    *,
    turn: int = 0,
    tool_calls: int = 0,
    tool_name: str = "",
    call_id: str = "",
    recoverable: bool = True,
    details: Optional[Dict[str, Any]] = None,
) -> RuntimeStatusEvent:
    return RuntimeStatusEvent(
        phase=phase,
        message=message,
        turn=turn,
        tool_calls=tool_calls,
        tool_name=tool_name,
        call_id=call_id,
        recoverable=recoverable,
        details=details or {},
    )


def run(
    messages: List[Dict[str, Any]],
    config: AgentConfig,
    registry: Optional[ToolRegistry] = None,
    backend: Optional[LLMBackend] = None,
) -> Generator[Event, Optional[bool], None]:
    """Run the ReAct loop and yield events for UI or CLI consumers."""
    registry = registry or get_registry()
    config = _config_with_task_route(config, messages)
    backend = backend or _create_backend(config)

    activated_deferred: set[str] = set()
    tools = _tool_schemas_for_config(registry, config, activated=activated_deferred)

    _clear_run_todos(config.workspace_root)
    working_messages = _prepare_working_messages(messages, config)
    _deferred_msg = _deferred_catalog_message(registry, activated_deferred)
    if _deferred_msg:
        working_messages.append(_deferred_msg)

    turn_count = 0
    tool_call_count = 0
    consecutive_errors = 0
    cumulative_usage = Usage()
    last_context_ledger: Dict[str, Any] = {}
    tool_repair_attempts = 0
    continuation_count = 0
    continuation_buffer = ""
    seen_files: Dict[str, str] = {}
    compactor = ResultCompactor()
    edit_guard = EditGuard(config.workspace_root)
    verification_tracker = VerificationTracker()
    verification_nudged = False

    logger.info(
        "agent run started messages=%s tools=%s mode=%s backend=%s model=%s",
        len(messages),
        len(tools),
        config.execution_mode,
        config.llm_backend,
        config.llm_model,
    )
    _emit_agent_lifecycle(
        "agent.start",
        config,
        ok=None,
        status="starting",
        metadata={
            "message_count": len(messages),
            "tool_count": len(tools),
            "execution_mode": config.execution_mode,
            "backend": config.llm_backend,
            "model": config.llm_model,
        },
    )
    if config.routing_task_type:
        yield _runtime_status_event(
            "model_routing",
            _routing_status_message(config),
            recoverable=True,
            details=_model_debug_details(config, backend),
        )
    yield _runtime_status_event("starting", "Agent runtime started")

    while turn_count < config.max_turns:
        working_messages = _refresh_todo_context_message(working_messages, config.workspace_root)
        working_messages = _append_turn_budget_hint(
            working_messages,
            max_turns=config.max_turns,
            completed_turns=turn_count,
        )
        working_messages, compact_events = _maybe_auto_compact_context(
            working_messages,
            tools,
            config,
            turn=turn_count,
            tool_calls=tool_call_count,
        )
        for event in compact_events:
            yield event
        if _enforce_working_context(working_messages):
            yield _runtime_status_event(
                "failed",
                "Agent working context exceeded hard limit",
                turn=turn_count,
                tool_calls=tool_call_count,
                recoverable=False,
            )
            yield _context_limit_error_event()
            yield _error_done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
            return
        turn_count += 1
        yield _runtime_status_event(
            "llm_request",
            "Calling LLM",
            turn=turn_count,
            tool_calls=tool_call_count,
            details=_model_debug_details(config, backend),
        )
        try:
            request_messages = _messages_for_llm_request(working_messages, config)
            last_context_ledger = _log_context_ledger(
                request_messages,
                tools,
                config,
                phase="before_request",
                turn=turn_count,
            )
            response = backend.chat(
                request_messages,
                tools=tools or None,
                temperature=config.temperature,
                max_tokens=config.max_tokens,
                timeout=config.timeout,
            )
            yield _runtime_status_event(
                "llm_response",
                "模型响应已返回",
                turn=turn_count,
                tool_calls=tool_call_count,
                details=_model_debug_details(config, backend, response),
            )
            consecutive_errors = 0
        except Exception as exc:
            consecutive_errors += 1
            fallback_config = _next_model_fallback_config(config, exc)
            if fallback_config is not None:
                yield _runtime_status_event(
                    "model_fallback",
                    _model_fallback_status_message(config, fallback_config),
                    turn=turn_count,
                    tool_calls=tool_call_count,
                    recoverable=True,
                    details=_model_debug_details(
                        config,
                        backend,
                        fallback_from=config.llm_model,
                        fallback_to=fallback_config.llm_model,
                        error=exc,
                    ),
                )
                config = fallback_config
                backend = _create_backend(config)
                tools = _tool_schemas_for_config(registry, config, activated=activated_deferred)
                working_messages = _without_route_hint(working_messages)
                route_hint = _route_hint_message(config)
                if route_hint:
                    working_messages.append({"role": "user", "content": route_hint})
                consecutive_errors = 0
                turn_count -= 1
                continue
            message = (
                f"LLM call failed (attempt {consecutive_errors}): "
                f"{type(exc).__name__}: {sanitize_for_log(exc)}"
            )
            logger.warning(
                "llm call failed attempt=%s recoverable=%s error=%s",
                consecutive_errors,
                consecutive_errors < config.max_consecutive_errors,
                sanitize_for_log(exc),
            )
            if (
                is_non_retryable_llm_error(exc)
                or consecutive_errors >= config.max_consecutive_errors
            ):
                yield _runtime_status_event(
                    "failed",
                    "LLM call failed",
                    turn=turn_count,
                    tool_calls=tool_call_count,
                    recoverable=False,
                )
                yield _llm_error_event(exc, message=message, recoverable=False)
                yield _error_done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
                return
            yield _llm_error_event(exc, message=message, recoverable=True)
            yield _runtime_status_event(
                "retrying",
                "Retrying LLM call",
                turn=turn_count,
                tool_calls=tool_call_count,
                recoverable=True,
            )
            time.sleep(min(2**consecutive_errors, 30))
            turn_count -= 1
            continue

        cumulative_usage = _combine_usage(cumulative_usage, response.usage)
        last_context_ledger = _log_context_ledger(
            request_messages,
            tools,
            config,
            phase="after_response",
            turn=turn_count,
            usage=response.usage,
        )
        if response.thinking:
            yield ThinkingEvent(text=response.thinking)

        if response.stop_reason == "tool_use" and not response.tool_calls and tool_repair_attempts < MAX_TOOL_CALL_REPAIR_ATTEMPTS:
            tool_repair_attempts += 1
            yield _runtime_status_event(
                "tool_call_repair",
                "Repairing malformed tool call",
                turn=turn_count,
                tool_calls=tool_call_count,
                recoverable=True,
            )
            working_messages.append(_format_assistant_message(response, config))
            working_messages.append({"role": "user", "content": TOOL_CALL_RECOVERY_PROMPT})
            turn_count -= 1
            continue

        # FABLEADV-19: output truncated by max_tokens with no complete tool_call to
        # run → the turn was cut off mid-output, NOT finished. Continue/guide the
        # model instead of ending as "(No response from LLM)".
        if response.stop_reason == "max_tokens" and not response.tool_calls:
            if continuation_count < MAX_TRUNCATION_CONTINUATIONS:
                continuation_count += 1
                partial = response.content or ""
                if partial.strip():
                    continuation_buffer += partial
                    working_messages.append(_format_assistant_message(response, config))
                    working_messages.append({"role": "user", "content": CONTINUATION_PROMPT})
                else:
                    working_messages.append({"role": "user", "content": TRUNCATED_TOOLCALL_PROMPT})
                turn_count -= 1
                continue
            final_text = (continuation_buffer + (response.content or "")) or "(No response from LLM)"
            yield ContentEvent(text=final_text + "\n\n[输出多次超长被截断，请把任务拆小或缩小单次写入范围]")
            yield _runtime_status_event("completed", "Agent runtime completed", turn=turn_count, tool_calls=tool_call_count)
            yield _done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
            return

        if response.tool_calls:
            if response.content:
                yield ContentEvent(text=response.content)

            results: List[Tuple[ToolCall, str]] = []
            parallel_results = _execute_parallel_readonly_if_safe(
                response.tool_calls,
                registry,
                config,
                edit_guard=edit_guard,
            )
            if parallel_results is not None:
                for tool_call in response.tool_calls:
                    yield ToolCallEvent(
                        tool_name=tool_call.name,
                        arguments=tool_call.arguments,
                        call_id=tool_call.id,
                    )
                    yield _runtime_status_event(
                        "tool_running",
                        f"Running tool {tool_call.name}",
                        turn=turn_count,
                        tool_calls=tool_call_count,
                        tool_name=tool_call.name,
                        call_id=tool_call.id,
                    )
                for tool_call, result in parallel_results:
                    logger.info("tool finished name=%s call_id=%s", tool_call.name, tool_call.id)
                    tool_call_count += 1
                    _record_tool_progress(registry, tool_call, result, verification_tracker)
                    results.append((tool_call, result))
                    yield ToolResultEvent(
                        tool_name=tool_call.name,
                        result=_public_tool_result(tool_call, result),
                        call_id=tool_call.id,
                    )
                    yield _runtime_status_event(
                        "tool_done",
                        f"Tool {tool_call.name} finished",
                        turn=turn_count,
                        tool_calls=tool_call_count,
                        tool_name=tool_call.name,
                        call_id=tool_call.id,
                    )
            else:
                for tool_call in response.tool_calls:
                    yield ToolCallEvent(
                        tool_name=tool_call.name,
                        arguments=tool_call.arguments,
                        call_id=tool_call.id,
                    )
                    yield _runtime_status_event(
                        "tool_running",
                        f"Running tool {tool_call.name}",
                        turn=turn_count,
                        tool_calls=tool_call_count,
                        tool_name=tool_call.name,
                        call_id=tool_call.id,
                    )

                    if config.execution_mode == "plan":
                        result = (
                            f"[Plan mode] Tool '{tool_call.name}' would be called with "
                            "the arguments shown above. Describe what you expect this "
                            "tool would return and continue planning the next steps."
                        )
                        tool_call_count += 1
                        results.append((tool_call, result))
                        yield ToolResultEvent(
                            tool_name=tool_call.name,
                            result=_public_tool_result(tool_call, result),
                            call_id=tool_call.id,
                        )
                        yield _runtime_status_event(
                            "tool_done",
                            f"Tool {tool_call.name} finished",
                            turn=turn_count,
                            tool_calls=tool_call_count,
                            tool_name=tool_call.name,
                            call_id=tool_call.id,
                        )
                        continue

                    permission_action = _permission_action(
                        config.execution_mode,
                        tool_call.name,
                        tool_call.arguments,
                        config.permission_checker,
                        registry=registry,
                    )
                    if permission_action == "deny":
                        result = (
                            "[Permission denied] A workspace permission rule blocked "
                            f"execution of '{tool_call.name}'."
                        )
                        tool_call_count += 1
                        results.append((tool_call, result))
                        yield ToolResultEvent(
                            tool_name=tool_call.name,
                            result=_public_tool_result(tool_call, result),
                            call_id=tool_call.id,
                        )
                        yield _runtime_status_event(
                            "tool_done",
                            f"Tool {tool_call.name} finished",
                            turn=turn_count,
                            tool_calls=tool_call_count,
                            tool_name=tool_call.name,
                            call_id=tool_call.id,
                        )
                        continue

                    if permission_action == "ask":
                        import uuid as _uuid

                        request_id = str(_uuid.uuid4())
                        approved = yield PermissionRequestEvent(
                            tool_name=tool_call.name,
                            arguments=tool_call.arguments,
                            call_id=tool_call.id,
                            request_id=request_id,
                        )
                        if approved is False:
                            result = (
                                f"[Permission denied] User declined execution of "
                                f"'{tool_call.name}'."
                            )
                            tool_call_count += 1
                            results.append((tool_call, result))
                            yield ToolResultEvent(
                                tool_name=tool_call.name,
                                result=_public_tool_result(tool_call, result),
                                call_id=tool_call.id,
                            )
                            yield _runtime_status_event(
                                "tool_done",
                                f"Tool {tool_call.name} finished",
                                turn=turn_count,
                                tool_calls=tool_call_count,
                                tool_name=tool_call.name,
                                call_id=tool_call.id,
                            )
                            continue

                    result = _execute_tool_with_hooks(
                        registry,
                        tool_call,
                        config.tool_boundary_overrides,
                        workspace_root=config.workspace_root,
                        edit_guard=edit_guard,
                    )
                    logger.info("tool finished name=%s call_id=%s", tool_call.name, tool_call.id)
                    tool_call_count += 1
                    _record_tool_progress(registry, tool_call, result, verification_tracker)
                    results.append((tool_call, result))
                    yield ToolResultEvent(
                        tool_name=tool_call.name,
                        result=_public_tool_result(tool_call, result),
                        call_id=tool_call.id,
                    )
                    todo_event = _todo_update_event_if_any(registry, tool_call, result)
                    if todo_event is not None:
                        yield todo_event
                    yield _runtime_status_event(
                        "tool_done",
                        f"Tool {tool_call.name} finished",
                        turn=turn_count,
                        tool_calls=tool_call_count,
                        tool_name=tool_call.name,
                        call_id=tool_call.id,
                    )
            working_messages.append(_format_assistant_message(response, config))
            for tool_call, result in results:
                working_messages.append(_format_tool_result(tool_call, result, seen_files, compactor))
            if backend.supports_vision:
                image_message = _build_vision_message(results)
                if image_message:
                    working_messages.append(image_message)
            if _apply_deferred_activation(results, registry, activated_deferred):
                tools = _tool_schemas_for_config(registry, config, activated=activated_deferred)
            _audit_tool_results(results, config, turn_count)
            working_messages = _evict_tool_results_for_budget(working_messages, tools, config)
            _save_runtime_checkpoint(
                config,
                _messages_for_llm_request(working_messages, config),
                turn_count=turn_count,
                tool_call_count=tool_call_count,
                reason="tool_loop",
            )
            continue

        # No tool calls — pure content response
        if response.content:
            if verification_tracker.needs_nudge() and not verification_nudged:
                verification_nudged = True
                working_messages.append(_format_assistant_message(response, config))
                working_messages.append({"role": "user", "content": verification_tracker.nudge_text()})
                turn_count -= 1
                continue
            yield ContentEvent(text=continuation_buffer + response.content)
            continuation_buffer = ""
            yield _runtime_status_event(
                "completed",
                "Agent runtime completed",
                turn=turn_count,
                tool_calls=tool_call_count,
            )
            yield _done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
            logger.info("agent run completed turns=%s tool_calls=%s", turn_count, tool_call_count)
            return

        yield ContentEvent(text="(No response from LLM)")
        yield _runtime_status_event(
            "completed",
            "Agent runtime completed",
            turn=turn_count,
            tool_calls=tool_call_count,
        )
        yield _done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
        logger.info("agent run completed turns=%s tool_calls=%s", turn_count, tool_call_count)
        return

    logger.warning("agent run reached max turns turns=%s tool_calls=%s", turn_count, tool_call_count)
    yield _runtime_status_event(
        "failed",
        "Agent runtime reached max turns",
        turn=turn_count,
        tool_calls=tool_call_count,
        recoverable=False,
    )
    yield ErrorEvent(
        code="RUNTIME_MAX_TURNS",
        title="达到执行轮次上限",
        message=f"已达到本次任务的最大执行轮次（{config.max_turns}）。",
        hint="请缩小任务范围、提高最大轮次，或把复杂任务拆成几步继续。",
        recoverable=False,
    )
    yield _done_event(
        turn_count,
        tool_call_count,
        cumulative_usage,
        ledger=last_context_ledger,
        messages=_messages_for_llm_request(working_messages, config),
        tools=tools,
        config=config,
    )


def run_stream(
    messages: List[Dict[str, Any]],
    config: AgentConfig,
    registry: Optional[ToolRegistry] = None,
    backend: Optional[LLMBackend] = None,
    cancel_event: Optional[threading.Event] = None,
) -> Generator[Event, Optional[bool], None]:
    """Streaming variant of run(). Yields text chunks before final content events."""
    registry = registry or get_registry()
    config = _config_with_task_route(config, messages)
    backend = backend or _create_backend(config)

    activated_deferred: set[str] = set()
    tools = _tool_schemas_for_config(registry, config, activated=activated_deferred)

    _clear_run_todos(config.workspace_root)
    working_messages = _prepare_working_messages(messages, config)
    _deferred_msg = _deferred_catalog_message(registry, activated_deferred)
    if _deferred_msg:
        working_messages.append(_deferred_msg)

    turn_count = 0
    tool_call_count = 0
    consecutive_errors = 0
    cumulative_usage = Usage()
    last_context_ledger: Dict[str, Any] = {}
    last_tool_signature = ""
    repeated_tool_count = 0
    tool_repair_attempts = 0
    continuation_count = 0
    continuation_buffer = ""
    seen_files: Dict[str, str] = {}
    compactor = ResultCompactor()
    edit_guard = EditGuard(config.workspace_root)
    verification_tracker = VerificationTracker()
    verification_nudged = False
    tracker = ToolCallTracker(repeat_limit=REPEATED_TOOL_CALL_LIMIT)

    logger.info(
        "agent stream started messages=%s tools=%s mode=%s backend=%s model=%s",
        len(messages),
        len(tools),
        config.execution_mode,
        config.llm_backend,
        config.llm_model,
    )
    _emit_agent_lifecycle(
        "agent.start",
        config,
        ok=None,
        status="starting",
        metadata={
            "message_count": len(messages),
            "tool_count": len(tools),
            "execution_mode": config.execution_mode,
            "backend": config.llm_backend,
            "model": config.llm_model,
            "streaming": True,
        },
    )
    if config.routing_task_type:
        yield _runtime_status_event(
            "model_routing",
            _routing_status_message(config),
            recoverable=True,
            details=_model_debug_details(config, backend),
        )
    yield _runtime_status_event("starting", "Agent runtime started")

    while turn_count < config.max_turns:
        if is_cancel_requested(cancel_event):
            logger.info("agent stream canceled before turn")
            yield _runtime_status_event("canceled", "Agent runtime canceled", recoverable=False)
            yield _cancelled_error_event()
            yield _error_done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
            return
        working_messages = _refresh_todo_context_message(working_messages, config.workspace_root)
        working_messages = _append_turn_budget_hint(
            working_messages,
            max_turns=config.max_turns,
            completed_turns=turn_count,
        )
        working_messages, compact_events = _maybe_auto_compact_context(
            working_messages,
            tools,
            config,
            turn=turn_count,
            tool_calls=tool_call_count,
        )
        for event in compact_events:
            yield event
        if _enforce_working_context(working_messages):
            yield _runtime_status_event(
                "failed",
                "Agent working context exceeded hard limit",
                turn=turn_count,
                tool_calls=tool_call_count,
                recoverable=False,
            )
            yield _context_limit_error_event()
            yield _error_done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
            return
        turn_count += 1
        yield _runtime_status_event(
            "llm_request",
            "Calling LLM stream",
            turn=turn_count,
            tool_calls=tool_call_count,
            details=_model_debug_details(config, backend),
        )
        try:
            stream_kwargs: Dict[str, Any] = {
                "tools": tools or None,
                "temperature": config.temperature,
                "max_tokens": config.max_tokens,
                "timeout": config.timeout,
            }
            if cancel_event is not None:
                stream_kwargs["cancel_event"] = cancel_event
            request_messages = _messages_for_llm_request(working_messages, config)
            last_context_ledger = _log_context_ledger(
                request_messages,
                tools,
                config,
                phase="before_request",
                turn=turn_count,
            )
            stream = backend.chat_stream(
                request_messages,
                **stream_kwargs,
            )
        except OperationCancelled:
            logger.info("llm stream setup canceled turn=%s", turn_count)
            yield _runtime_status_event(
                "canceled",
                "LLM stream setup canceled",
                turn=turn_count,
                tool_calls=tool_call_count,
                recoverable=False,
            )
            yield _cancelled_error_event()
            yield _error_done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
            return
        except Exception as exc:
            consecutive_errors += 1
            fallback_config = _next_model_fallback_config(config, exc)
            if fallback_config is not None:
                yield _runtime_status_event(
                    "model_fallback",
                    _model_fallback_status_message(config, fallback_config),
                    turn=turn_count,
                    tool_calls=tool_call_count,
                    recoverable=True,
                    details=_model_debug_details(
                        config,
                        backend,
                        fallback_from=config.llm_model,
                        fallback_to=fallback_config.llm_model,
                        error=exc,
                    ),
                )
                config = fallback_config
                backend = _create_backend(config)
                tools = _tool_schemas_for_config(registry, config, activated=activated_deferred)
                working_messages = _without_route_hint(working_messages)
                route_hint = _route_hint_message(config)
                if route_hint:
                    working_messages.append({"role": "user", "content": route_hint})
                consecutive_errors = 0
                turn_count -= 1
                continue
            message = (
                f"LLM call failed (attempt {consecutive_errors}): "
                f"{type(exc).__name__}: {sanitize_for_log(exc)}"
            )
            logger.warning(
                "llm stream setup failed attempt=%s recoverable=%s error=%s",
                consecutive_errors,
                consecutive_errors < config.max_consecutive_errors,
                sanitize_for_log(exc),
            )
            if (
                is_non_retryable_llm_error(exc)
                or consecutive_errors >= config.max_consecutive_errors
            ):
                yield _runtime_status_event(
                    "failed",
                    "LLM stream setup failed",
                    turn=turn_count,
                    tool_calls=tool_call_count,
                    recoverable=False,
                )
                yield _llm_error_event(exc, message=message, recoverable=False)
                yield _error_done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
                return
            yield _llm_error_event(exc, message=message, recoverable=True)
            yield _runtime_status_event(
                "retrying",
                "Retrying LLM stream setup",
                turn=turn_count,
                tool_calls=tool_call_count,
                recoverable=True,
            )
            try:
                wait_or_cancel(cancel_event, min(2**consecutive_errors, 30))
            except OperationCancelled:
                yield _runtime_status_event(
                    "canceled",
                    "LLM retry canceled",
                    turn=turn_count,
                    tool_calls=tool_call_count,
                    recoverable=False,
                )
                yield _cancelled_error_event()
                yield _error_done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
                return
            turn_count -= 1
            continue

        accumulated_text = ""
        delta_buffer = ""
        last_delta_flush = time.monotonic()
        yield _runtime_status_event(
            "streaming",
            "Receiving LLM stream",
            turn=turn_count,
            tool_calls=tool_call_count,
        )
        try:
            while True:
                raise_if_cancelled(cancel_event)
                chunk = next(stream)
                if chunk:
                    accumulated_text += chunk
                    delta_buffer += chunk
                    now = time.monotonic()
                    if (
                        len(delta_buffer) >= CONTENT_DELTA_FLUSH_CHARS
                        or now - last_delta_flush >= CONTENT_DELTA_FLUSH_INTERVAL
                    ):
                        yield ContentDeltaEvent(text=delta_buffer)
                        delta_buffer = ""
                        last_delta_flush = now
        except StopIteration as stop:
            response = stop.value
        except OperationCancelled:
            close = getattr(stream, "close", None)
            if callable(close):
                close()
            logger.info("llm stream canceled turn=%s", turn_count)
            yield _runtime_status_event(
                "canceled",
                "LLM stream canceled",
                turn=turn_count,
                tool_calls=tool_call_count,
                recoverable=False,
            )
            yield _cancelled_error_event()
            yield _error_done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
            return
        except Exception as exc:
            consecutive_errors += 1
            message = f"Stream error: {type(exc).__name__}: {sanitize_for_log(exc)}"
            fallback_config = _next_model_fallback_config(config, exc)
            if not accumulated_text and fallback_config is not None:
                yield _runtime_status_event(
                    "model_fallback",
                    _model_fallback_status_message(config, fallback_config),
                    turn=turn_count,
                    tool_calls=tool_call_count,
                    recoverable=True,
                    details=_model_debug_details(
                        config,
                        backend,
                        fallback_from=config.llm_model,
                        fallback_to=fallback_config.llm_model,
                        error=exc,
                    ),
                )
                config = fallback_config
                backend = _create_backend(config)
                tools = _tool_schemas_for_config(registry, config, activated=activated_deferred)
                working_messages = _without_route_hint(working_messages)
                route_hint = _route_hint_message(config)
                if route_hint:
                    working_messages.append({"role": "user", "content": route_hint})
                consecutive_errors = 0
                turn_count -= 1
                continue
            logger.warning(
                "llm stream read failed attempt=%s recoverable=%s error=%s",
                consecutive_errors,
                consecutive_errors < config.max_consecutive_errors,
                sanitize_for_log(exc),
            )
            if (
                is_non_retryable_llm_error(exc)
                or consecutive_errors >= config.max_consecutive_errors
            ):
                yield _runtime_status_event(
                    "failed",
                    "LLM stream failed",
                    turn=turn_count,
                    tool_calls=tool_call_count,
                    recoverable=False,
                )
                yield _llm_error_event(exc, message=message, recoverable=False)
                yield _error_done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
                return
            yield _llm_error_event(exc, message=message, recoverable=True)
            yield _runtime_status_event(
                "retrying",
                "Retrying LLM stream",
                turn=turn_count,
                tool_calls=tool_call_count,
                recoverable=True,
            )
            try:
                wait_or_cancel(cancel_event, min(2**consecutive_errors, 30))
            except OperationCancelled:
                yield _runtime_status_event(
                    "canceled",
                    "LLM retry canceled",
                    turn=turn_count,
                    tool_calls=tool_call_count,
                    recoverable=False,
                )
                yield _cancelled_error_event()
                yield _error_done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
                return
            turn_count -= 1
            continue

        if delta_buffer:
            yield ContentDeltaEvent(text=delta_buffer)

        consecutive_errors = 0
        if response is None:
            response = LLMResponse(content=accumulated_text)
        elif accumulated_text and not response.content:
            response = replace(response, content=accumulated_text)
        yield _runtime_status_event(
            "llm_response",
            "模型响应已返回",
            turn=turn_count,
            tool_calls=tool_call_count,
            details=_model_debug_details(config, backend, response),
        )

        cumulative_usage = _combine_usage(cumulative_usage, response.usage)
        last_context_ledger = _log_context_ledger(
            request_messages,
            tools,
            config,
            phase="after_response",
            turn=turn_count,
            usage=response.usage,
        )
        if response.thinking:
            yield ThinkingEvent(text=response.thinking)

        if response.stop_reason == "tool_use" and not response.tool_calls and tool_repair_attempts < MAX_TOOL_CALL_REPAIR_ATTEMPTS:
            tool_repair_attempts += 1
            yield _runtime_status_event(
                "tool_call_repair",
                "Repairing malformed tool call",
                turn=turn_count,
                tool_calls=tool_call_count,
                recoverable=True,
            )
            working_messages.append(_format_assistant_message(response, config))
            working_messages.append({"role": "user", "content": TOOL_CALL_RECOVERY_PROMPT})
            turn_count -= 1
            continue

        # FABLEADV-19: output truncated by max_tokens with no complete tool_call to
        # run → the turn was cut off mid-output, NOT finished. Continue/guide the
        # model instead of ending as "(No response from LLM)".
        if response.stop_reason == "max_tokens" and not response.tool_calls:
            if continuation_count < MAX_TRUNCATION_CONTINUATIONS:
                continuation_count += 1
                partial = response.content or ""
                if partial.strip():
                    continuation_buffer += partial
                    working_messages.append(_format_assistant_message(response, config))
                    working_messages.append({"role": "user", "content": CONTINUATION_PROMPT})
                else:
                    working_messages.append({"role": "user", "content": TRUNCATED_TOOLCALL_PROMPT})
                turn_count -= 1
                continue
            final_text = (continuation_buffer + (response.content or "")) or "(No response from LLM)"
            yield ContentEvent(text=final_text + "\n\n[输出多次超长被截断，请把任务拆小或缩小单次写入范围]")
            yield _runtime_status_event("completed", "Agent runtime completed", turn=turn_count, tool_calls=tool_call_count)
            yield _done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
            return

        if response.tool_calls:
            if response.content:
                yield ContentEvent(text=response.content)

            results: List[Tuple[ToolCall, str]] = []
            parallel_results = None if is_cancel_requested(cancel_event) else _execute_parallel_readonly_if_safe(
                response.tool_calls,
                registry,
                config,
                cancel_event=cancel_event,
                edit_guard=edit_guard,
            )
            if parallel_results is not None:
                for tool_call in response.tool_calls:
                    tracker.record(tool_call.name, tool_call.arguments or {})
                    yield ToolCallEvent(
                        tool_name=tool_call.name,
                        arguments=tool_call.arguments,
                        call_id=tool_call.id,
                    )
                    yield _runtime_status_event(
                        "tool_running",
                        f"Running tool {tool_call.name}",
                        turn=turn_count,
                        tool_calls=tool_call_count,
                        tool_name=tool_call.name,
                        call_id=tool_call.id,
                    )
                for tool_call, result in parallel_results:
                    if is_cancel_requested(cancel_event):
                        yield _runtime_status_event(
                            "canceled",
                            f"Tool {tool_call.name} canceled",
                            turn=turn_count,
                            tool_calls=tool_call_count,
                            tool_name=tool_call.name,
                            call_id=tool_call.id,
                            recoverable=False,
                        )
                        yield _cancelled_error_event()
                        yield _error_done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
                        return
                    tool_call_count += 1
                    logger.info("tool finished name=%s call_id=%s", tool_call.name, tool_call.id)
                    _record_tool_progress(registry, tool_call, result, verification_tracker)
                    results.append((tool_call, result))
                    yield ToolResultEvent(
                        tool_name=tool_call.name,
                        result=_public_tool_result(tool_call, result),
                        call_id=tool_call.id,
                    )
                    yield _runtime_status_event(
                        "tool_done",
                        f"Tool {tool_call.name} finished",
                        turn=turn_count,
                        tool_calls=tool_call_count,
                        tool_name=tool_call.name,
                        call_id=tool_call.id,
                    )

            for tool_call in ([] if parallel_results is not None else response.tool_calls):
                # --- 记录到 tracker ---
                tracker.record(tool_call.name, tool_call.arguments or {})

                # --- 循环检测（连续重复 + A-B 交替） ---
                signature = _tool_call_signature(tool_call)
                if signature == last_tool_signature:
                    repeated_tool_count += 1
                else:
                    last_tool_signature = signature
                    repeated_tool_count = 1
                loop_hint = tracker.detect_loop()
                if repeated_tool_count >= REPEATED_TOOL_CALL_LIMIT or loop_hint:
                    yield _runtime_status_event(
                        "failed",
                        "Repeated tool call loop detected",
                        turn=turn_count,
                        tool_calls=tool_call_count,
                        tool_name=tool_call.name,
                        call_id=tool_call.id,
                        recoverable=False,
                    )
                    yield _repeated_tool_error_event(tool_call, hint=loop_hint)
                    yield _error_done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
                    return
                if is_cancel_requested(cancel_event):
                    yield _runtime_status_event(
                        "canceled",
                        "Tool execution canceled",
                        turn=turn_count,
                        tool_calls=tool_call_count,
                        tool_name=tool_call.name,
                        call_id=tool_call.id,
                        recoverable=False,
                    )
                    yield _cancelled_error_event()
                    yield _error_done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
                    return
                yield ToolCallEvent(
                    tool_name=tool_call.name,
                    arguments=tool_call.arguments,
                    call_id=tool_call.id,
                )
                yield _runtime_status_event(
                    "tool_running",
                    f"Running tool {tool_call.name}",
                    turn=turn_count,
                    tool_calls=tool_call_count,
                    tool_name=tool_call.name,
                    call_id=tool_call.id,
                )

                if config.execution_mode == "plan":
                    result = (
                        f"[Plan mode] Tool '{tool_call.name}' would be called with "
                        "the arguments shown above. Describe what you expect this "
                        "tool would return and continue planning the next steps."
                    )
                    tool_call_count += 1
                    results.append((tool_call, result))
                    yield ToolResultEvent(
                        tool_name=tool_call.name,
                        result=_public_tool_result(tool_call, result),
                        call_id=tool_call.id,
                    )
                    yield _runtime_status_event(
                        "tool_done",
                        f"Tool {tool_call.name} finished",
                        turn=turn_count,
                        tool_calls=tool_call_count,
                        tool_name=tool_call.name,
                        call_id=tool_call.id,
                    )
                    continue

                permission_action = _permission_action(
                    config.execution_mode,
                    tool_call.name,
                    tool_call.arguments,
                    config.permission_checker,
                    registry=registry,
                )
                if permission_action == "deny":
                    result = (
                        "[Permission denied] A workspace permission rule blocked "
                        f"execution of '{tool_call.name}'."
                    )
                    tool_call_count += 1
                    results.append((tool_call, result))
                    yield ToolResultEvent(
                        tool_name=tool_call.name,
                        result=_public_tool_result(tool_call, result),
                        call_id=tool_call.id,
                    )
                    yield _runtime_status_event(
                        "tool_done",
                        f"Tool {tool_call.name} finished",
                        turn=turn_count,
                        tool_calls=tool_call_count,
                        tool_name=tool_call.name,
                        call_id=tool_call.id,
                    )
                    continue

                if permission_action == "ask":
                    import uuid as _uuid

                    request_id = str(_uuid.uuid4())
                    approved = yield PermissionRequestEvent(
                        tool_name=tool_call.name,
                        arguments=tool_call.arguments,
                        call_id=tool_call.id,
                        request_id=request_id,
                    )
                    if approved is False:
                        result = (
                            f"[Permission denied] User declined execution of "
                            f"'{tool_call.name}'."
                        )
                        tool_call_count += 1
                        results.append((tool_call, result))
                        yield ToolResultEvent(
                            tool_name=tool_call.name,
                            result=_public_tool_result(tool_call, result),
                            call_id=tool_call.id,
                        )
                        yield _runtime_status_event(
                            "tool_done",
                            f"Tool {tool_call.name} finished",
                            turn=turn_count,
                            tool_calls=tool_call_count,
                            tool_name=tool_call.name,
                            call_id=tool_call.id,
                        )
                        continue

                result = _execute_tool_with_hooks(
                    registry,
                    tool_call,
                    config.tool_boundary_overrides,
                    cancel_event=cancel_event,
                    workspace_root=config.workspace_root,
                    edit_guard=edit_guard,
                )
                if is_cancel_requested(cancel_event):
                    logger.info("tool canceled name=%s call_id=%s", tool_call.name, tool_call.id)
                    yield _runtime_status_event(
                        "canceled",
                        f"Tool {tool_call.name} canceled",
                        turn=turn_count,
                        tool_calls=tool_call_count,
                        tool_name=tool_call.name,
                        call_id=tool_call.id,
                        recoverable=False,
                    )
                    yield _cancelled_error_event()
                    yield _error_done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
                    return
                tool_call_count += 1
                logger.info("tool finished name=%s call_id=%s", tool_call.name, tool_call.id)
                _record_tool_progress(registry, tool_call, result, verification_tracker)
                results.append((tool_call, result))
                yield ToolResultEvent(
                    tool_name=tool_call.name,
                    result=_public_tool_result(tool_call, result),
                    call_id=tool_call.id,
                )
                todo_event = _todo_update_event_if_any(registry, tool_call, result)
                if todo_event is not None:
                    tracker.reset()
                    last_tool_signature = ""
                    repeated_tool_count = 0
                    yield todo_event
                yield _runtime_status_event(
                    "tool_done",
                    f"Tool {tool_call.name} finished",
                    turn=turn_count,
                    tool_calls=tool_call_count,
                    tool_name=tool_call.name,
                    call_id=tool_call.id,
                )

            working_messages.append(_format_assistant_message(response, config))
            for tool_call, result in results:
                working_messages.append(_format_tool_result(tool_call, result, seen_files, compactor))

            # --- 效率建议注入 ---
            efficiency_hint = tracker.get_efficiency_hint()
            if efficiency_hint:
                working_messages.append({
                    "role": "user",
                    "content": f"[System hint] {efficiency_hint}",
                })

            if backend.supports_vision:
                image_message = _build_vision_message(results)
                if image_message:
                    working_messages.append(image_message)
            if _apply_deferred_activation(results, registry, activated_deferred):
                tools = _tool_schemas_for_config(registry, config, activated=activated_deferred)
            _audit_tool_results(results, config, turn_count)
            working_messages = _evict_tool_results_for_budget(working_messages, tools, config)
            _save_runtime_checkpoint(
                config,
                _messages_for_llm_request(working_messages, config),
                turn_count=turn_count,
                tool_call_count=tool_call_count,
                reason="tool_loop",
            )
            if _enforce_working_context(working_messages):
                yield _runtime_status_event(
                    "failed",
                    "Agent working context exceeded hard limit",
                    turn=turn_count,
                    tool_calls=tool_call_count,
                    recoverable=False,
                )
                yield _context_limit_error_event()
                yield _error_done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
                return
            continue

        if response.content:
            if verification_tracker.needs_nudge() and not verification_nudged:
                verification_nudged = True
                working_messages.append(_format_assistant_message(response, config))
                working_messages.append({"role": "user", "content": verification_tracker.nudge_text()})
                turn_count -= 1
                continue
            yield ContentEvent(text=continuation_buffer + response.content)
            continuation_buffer = ""
            yield _runtime_status_event(
                "completed",
                "Agent runtime completed",
                turn=turn_count,
                tool_calls=tool_call_count,
            )
            yield _done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
            logger.info("agent stream completed turns=%s tool_calls=%s", turn_count, tool_call_count)
            return

        yield ContentEvent(text=(continuation_buffer + accumulated_text) or "(No response from LLM)")
        yield _runtime_status_event(
            "completed",
            "Agent runtime completed",
            turn=turn_count,
            tool_calls=tool_call_count,
        )
        yield _done_event(turn_count, tool_call_count, cumulative_usage, ledger=last_context_ledger, config=config)
        logger.info("agent stream completed turns=%s tool_calls=%s", turn_count, tool_call_count)
        return

    logger.warning("agent stream reached max turns turns=%s tool_calls=%s", turn_count, tool_call_count)
    yield _runtime_status_event(
        "failed",
        "Agent runtime reached max turns",
        turn=turn_count,
        tool_calls=tool_call_count,
        recoverable=False,
    )
    yield ErrorEvent(
        code="RUNTIME_MAX_TURNS",
        title="达到执行轮次上限",
        message=f"已达到本次任务的最大执行轮次（{config.max_turns}）。",
        hint="请缩小任务范围、提高最大轮次，或把复杂任务拆成几步继续。",
        recoverable=False,
    )
    yield _done_event(
        turn_count,
        tool_call_count,
        cumulative_usage,
        ledger=last_context_ledger,
        messages=_messages_for_llm_request(working_messages, config),
        tools=tools,
        config=config,
    )


def run_sync(
    user_message: str,
    config: AgentConfig,
    system_prompt: str = "",
    history: Optional[List[Dict[str, Any]]] = None,
) -> str:
    """Run the agent loop synchronously and return the final text response."""
    messages = list(history or [])
    messages.append({"role": "user", "content": user_message})

    if system_prompt:
        config = replace(config, system_prompt=system_prompt)

    final_text = ""
    for event in run(messages, config):
        if isinstance(event, ContentEvent):
            final_text = event.text
        elif isinstance(event, ErrorEvent) and not event.recoverable:
            return f"Error: {event.message}"
    return final_text


def _create_backend(config: AgentConfig) -> LLMBackend:
    backend_type = config.llm_backend
    if backend_type in {"openai", "deepseek", "openai-compatible", "openai_compat", "custom", "custom-openai"}:
        backend = get_backend(
            backend_type,
            base_url=config.llm_base_url,
            api_key=config.llm_api_key,
            model=config.llm_model,
        )
        # opt-in 推理强度：构造后注入，避免穿过 build_backend_kwargs 的参数白名单。
        effort = str(getattr(config, "reasoning_effort", "") or "").strip()
        if effort and hasattr(backend, "reasoning_effort"):
            backend.reasoning_effort = effort
        return backend
    if backend_type == "anthropic":
        kwargs: Dict[str, Any] = {"api_key": config.llm_api_key}
        if config.llm_model:
            kwargs["model"] = config.llm_model
        if config.llm_base_url:
            kwargs["base_url"] = config.llm_base_url
        return get_backend("anthropic", **kwargs)
    if backend_type == "gemini":
        kwargs = {"api_key": config.llm_api_key}
        if config.llm_model:
            kwargs["model"] = config.llm_model
        if config.llm_base_url:
            kwargs["base_url_template"] = config.llm_base_url
        return get_backend("gemini", **kwargs)
    if backend_type == "fake":
        return get_backend("fake", model=config.llm_model or "fake-model")
    raise ValueError(f"Unknown LLM backend: {backend_type}")


def _combine_usage(current: Usage, next_usage: Usage) -> Usage:
    return Usage(
        prompt_tokens=current.prompt_tokens + next_usage.prompt_tokens,
        completion_tokens=current.completion_tokens + next_usage.completion_tokens,
        total_tokens=current.total_tokens + next_usage.total_tokens,
        prompt_cache_hit_tokens=current.prompt_cache_hit_tokens + next_usage.prompt_cache_hit_tokens,
        prompt_cache_miss_tokens=current.prompt_cache_miss_tokens + next_usage.prompt_cache_miss_tokens,
    )


def _tool_name_from_openai_schema(schema: Dict[str, Any]) -> str:
    return ((schema.get("function") or {}).get("name") or "")


def _tool_schemas_for_config(
    registry: ToolRegistry,
    config: AgentConfig,
    activated: Optional[set[str]] = None,
) -> List[Dict[str, Any]]:
    activated = activated or set()
    if config.enabled_tools:
        enabled = set(config.enabled_tools)
        # 显式启用的工具视为已激活，确保 deferred 工具被显式启用时仍出现。
        all_tools = registry.get_all_schemas(format="openai", activated=enabled | activated)
        tools = [tool for tool in all_tools if _tool_name_from_openai_schema(tool) in enabled]
        profile = "explicit"
    else:
        requested_profile = os.environ.get("METIS_TOOL_PROFILE", "lean")
        profile = normalize_tool_profile(requested_profile)
        if profile != str(requested_profile or "").strip().lower() and str(requested_profile or "").strip():
            logger.warning("invalid METIS_TOOL_PROFILE=%r; using lean profile", requested_profile)
        tools = registry.get_schemas_for_profile(profile, format="openai", activated=activated)

    tools = _include_preferred_tool_schemas(registry, tools, config.routing_preferred_tools)
    tools = _filter_desktop_control_tools_for_route(tools, config)
    tools = _filter_document_tools_for_route(tools, config)

    capabilities = detect_from_model_name(config.llm_model)
    forced_tier = os.environ.get("METIS_TOOL_TIER", "").strip()
    tier = capabilities.tier
    if forced_tier:
        try:
            tier = int(forced_tier)
        except ValueError:
            logger.warning("invalid METIS_TOOL_TIER=%r; using detected tier=%s", forced_tier, tier)

    allowed_tools = tools_for_tier(tier)
    if allowed_tools is not None:
        before_count = len(tools)
        tools = [
            tool
            for tool in tools
            if _tool_allowed_for_tier(registry, _tool_name_from_openai_schema(tool), allowed_tools)
        ]
        logger.info(
            "tool tier filtering tier=%s model=%s tools_before=%s tools_after=%s",
            tier,
            config.llm_model,
            before_count,
            len(tools),
        )

    # Hide maintainer-only sandbox build/verify tools (and the low-level runtime
    # primitives that metis_runtime_job already composes) from end-user agents on
    # every tier — including tier 1's "all tools". Explicit enabled_tools and the
    # METIS_EXPOSE_INTERNAL_TOOLS override still win.
    if not config.enabled_tools and not expose_internal_tools():
        kept = [
            tool
            for tool in tools
            if _tool_name_from_openai_schema(tool) not in INTERNAL_TOOLS
        ]
        if len(kept) != len(tools):
            logger.info(
                "internal tool hiding model=%s tools_before=%s tools_after=%s",
                config.llm_model,
                len(tools),
                len(kept),
            )
        tools = kept

    # FABLEADV-23: deferred 模式下，若仍有未激活的可检索工具，注入 search_tools 元工具。
    from .tool_registry import deferred_tools_enabled

    if deferred_tools_enabled():
        names = {_tool_name_from_openai_schema(tool) for tool in tools}
        has_searchable = bool(registry.deferred_catalog(activated=activated))
        if has_searchable and "search_tools" not in names:
            schema = registry.openai_schema_for("search_tools")
            if schema:
                tools.append(schema)

    tools = prioritized_tools_for_route(tools, config.routing_preferred_tools)

    logger.info(
        "model capability detected tier=%s family=%s model=%s method=%s profile=%s tools=%s",
        tier,
        capabilities.detected_family,
        capabilities.detected_model,
        capabilities.detection_method,
        profile,
        len(tools),
    )
    return tools


def _filter_desktop_control_tools_for_route(
    tools: List[Dict[str, Any]],
    config: AgentConfig,
) -> List[Dict[str, Any]]:
    if config.enabled_tools or not should_block_desktop_control(config.routing_task_type):
        return tools
    blocked = desktop_control_tools()
    filtered = [
        tool
        for tool in tools
        if _tool_name_from_openai_schema(tool) not in blocked
    ]
    if len(filtered) != len(tools):
        logger.info(
            "computer use router guard blocked desktop control tools route=%s removed=%s",
            config.routing_task_type,
            sorted({_tool_name_from_openai_schema(tool) for tool in tools} & blocked),
        )
    return filtered


def _filter_document_tools_for_route(
    tools: List[Dict[str, Any]],
    config: AgentConfig,
) -> List[Dict[str, Any]]:
    if config.enabled_tools or not should_block_document_tools(config.routing_task_type):
        return tools
    blocked = document_workflow_tools()
    # Keep any document tool the route explicitly preferred (a real doc task).
    preferred = {str(name or "").strip() for name in (config.routing_preferred_tools or [])}
    filtered = [
        tool
        for tool in tools
        if _tool_name_from_openai_schema(tool) not in blocked
        or _tool_name_from_openai_schema(tool) in preferred
    ]
    if len(filtered) != len(tools):
        logger.info(
            "document tool route guard removed=%s route=%s",
            sorted({_tool_name_from_openai_schema(tool) for tool in tools} & blocked),
            config.routing_task_type,
        )
    return filtered


def _include_preferred_tool_schemas(
    registry: ToolRegistry,
    tools: List[Dict[str, Any]],
    preferred_tools: List[str],
) -> List[Dict[str, Any]]:
    if not preferred_tools:
        return tools
    names = {_tool_name_from_openai_schema(tool) for tool in tools}
    out = list(tools)
    for name in preferred_tools:
        canonical = registry.resolve_name(str(name or "").strip())
        if not canonical or canonical in names:
            continue
        schema = registry.openai_schema_for(canonical)
        if not schema:
            continue
        out.append(schema)
        names.add(canonical)
    return out


def _deferred_catalog_message(
    registry: ToolRegistry, activated: set[str]
) -> Optional[Dict[str, Any]]:
    """FABLEADV-23: 注入紧凑的"可按需加载工具"目录，让模型知道可用 search_tools 检索。"""
    from .tool_registry import deferred_tools_enabled

    if not deferred_tools_enabled():
        return None
    catalog = registry.deferred_catalog(activated=activated)
    if not catalog:
        return None
    lines = [
        "[System hint] 可按需加载的工具（未默认载入，需要时用 search_tools(query) 检索并加载后再调用）："
    ]
    for name, desc in catalog[:60]:
        lines.append(f"- {name}: {desc}" if desc else f"- {name}")
    if len(catalog) > 60:
        lines.append(f"... 另有 {len(catalog) - 60} 个，用 search_tools 检索")
    return {"role": "user", "content": "\n".join(lines)}


def _audit_tool_results(
    results: List[Tuple[ToolCall, str]],
    config: AgentConfig,
    turn_count: int,
) -> None:
    """FABLEADV-24: 把本轮工具动作写入全量审计（失败不影响主流程）。"""
    try:
        from .action_audit import record_actions

        record_actions(results, workspace_root=config.workspace_root, turn=turn_count)
    except Exception:
        pass


def _apply_deferred_activation(
    results: List[Tuple[ToolCall, str]],
    registry: ToolRegistry,
    activated: set[str],
) -> bool:
    """扫描本轮 tool_calls 里的 search_tools，将命中的 deferred 工具并入 activated。
    返回 activated 是否发生变化（变化则上层需重算 tools）。"""
    from .tool_registry import deferred_tools_enabled

    if not deferred_tools_enabled():
        return False
    changed = False
    for tool_call, _result in results:
        if tool_call.name != "search_tools":
            continue
        query = ""
        args = tool_call.arguments
        if isinstance(args, dict):
            query = str(args.get("query") or "")
        names, _text = registry.search_deferred(query, activated=activated)
        for name in names:
            if name not in activated:
                activated.add(name)
                changed = True
    return changed


def _tool_allowed_for_tier(registry: ToolRegistry, tool_name: str, allowed_tools: set[str]) -> bool:
    if tool_name in allowed_tools:
        return True
    definition = registry.get(tool_name)
    if definition is None:
        return False
    return str(definition.source or "").strip().lower() not in {"", "builtin"}


def _execute_parallel_readonly_if_safe(
    tool_calls: List[ToolCall],
    registry: ToolRegistry,
    config: AgentConfig,
    *,
    cancel_event: Optional[threading.Event] = None,
    edit_guard: Optional[EditGuard] = None,
) -> Optional[List[Tuple[ToolCall, str]]]:
    if len(tool_calls) < 2 or config.execution_mode == "plan":
        return None
    for tool_call in tool_calls:
        canonical = registry.resolve_name(tool_call.name)
        if canonical not in _PARALLEL_READONLY_TOOLS:
            return None
        if _permission_action(
            config.execution_mode,
            tool_call.name,
            tool_call.arguments,
            config.permission_checker,
            registry=registry,
        ) != "allow":
            return None

    indexed_calls = list(enumerate(tool_calls))

    def run_one(indexed: Tuple[int, ToolCall]) -> Tuple[int, ToolCall, str]:
        index, item = indexed
        result = _execute_tool_with_hooks(
            registry,
            item,
            config.tool_boundary_overrides,
            cancel_event=cancel_event,
            workspace_root=config.workspace_root,
            edit_guard=edit_guard,
        )
        return index, item, result

    max_workers = min(8, len(tool_calls))
    rows: List[Tuple[int, ToolCall, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [pool.submit(run_one, indexed) for indexed in indexed_calls]
        for future in concurrent.futures.as_completed(futures):
            rows.append(future.result())
    return [(tool_call, result) for _index, tool_call, result in sorted(rows, key=lambda item: item[0])]


def _needs_permission(
    mode: str,
    tool_name: str,
    arguments: Optional[Dict[str, Any]] = None,
    rule_checker: Optional[Callable[[str, Dict[str, Any]], Optional[str]]] = None,
) -> bool:
    """Return True if a tool call requires approval in the selected mode."""
    if rule_checker and arguments is not None:
        decision = rule_checker(tool_name, arguments)
        if decision == "allow":
            return False
        if decision in ("ask", "deny"):
            return True
    if mode in ("auto", "bypass"):
        return False
    if mode == "plan":
        return False
    if mode == "ask":
        return True
    if mode == "edit":
        return tool_name not in _SAFE_TOOLS and tool_name not in _EDIT_TOOLS
    return False


def _permission_action(
    mode: str,
    tool_name: str,
    arguments: Dict[str, Any],
    rule_checker: Optional[Callable[[str, Dict[str, Any]], Optional[str]]] = None,
    registry: Optional[ToolRegistry] = None,
) -> str:
    """Return allow, ask, or deny for a tool call."""
    if rule_checker:
        decision = rule_checker(tool_name, arguments)
        if decision in ("allow", "ask", "deny"):
            return decision
    if registry is not None and hasattr(registry, "tool_requires_approval"):
        try:
            return evaluate_permission(
                mode=mode,
                tool_name=tool_name,
                arguments=arguments,
                registry_requires_approval=registry.tool_requires_approval(tool_name, mode, arguments),
            ).action
        except Exception:
            pass
    return evaluate_permission(
        mode=mode,
        tool_name=tool_name,
        arguments=arguments,
        registry_requires_approval=_needs_permission(mode, tool_name),
    ).action


def _execute_tool_with_hooks(
    registry: ToolRegistry,
    tool_call: ToolCall,
    boundary_overrides: Optional[Callable[[str, Dict[str, Any]], Dict[str, bool]]] = None,
    cancel_event: Optional[threading.Event] = None,
    workspace_root: str = "",
    edit_guard: Optional[EditGuard] = None,
) -> str:
    """Execute a tool in an abort-aware isolation thread."""
    effective_cancel_event = cancel_event or threading.Event()
    raise_if_cancelled(effective_cancel_event)

    result_queue: "queue.Queue[Tuple[str, Any]]" = queue.Queue(maxsize=1)

    def worker() -> None:
        try:
            result_queue.put(
                (
                    "result",
                    _execute_tool_with_hooks_sync(
                        registry,
                        tool_call,
                        boundary_overrides,
                        cancel_event=effective_cancel_event,
                        workspace_root=workspace_root,
                        edit_guard=edit_guard,
                    ),
                )
            )
        except OperationCancelled as exc:
            result_queue.put(("cancelled", exc))
        except BaseException as exc:  # pragma: no cover - defensive isolation boundary
            result_queue.put(("error", exc))

    thread = threading.Thread(
        target=worker,
        daemon=True,
        name=f"metis-tool-{tool_call.name[:24]}",
    )
    thread.start()
    deadline = time.monotonic() + TOOL_EXECUTION_TIMEOUT
    while True:
        try:
            kind, payload = result_queue.get(timeout=0.05)
        except queue.Empty:
            if is_cancel_requested(effective_cancel_event):
                return (
                    f"[Cancelled] Tool '{tool_call.name}' was canceled before it finished. "
                    "If the tool spawned an external process, Metis requested termination."
                )
            if time.monotonic() >= deadline:
                effective_cancel_event.set()
                return _append_error_recovery_hint(
                    teaching_error_text(
                        tool_call.name,
                        tool_call.arguments,
                        f"TimeoutError: tool exceeded {int(TOOL_EXECUTION_TIMEOUT)} seconds and was canceled.",
                        workspace_root=workspace_root,
                    )
                )
            continue
        if kind == "result":
            return str(payload)
        if kind == "cancelled":
            raise OperationCancelled("Tool execution cancelled")
        exc = payload
        return _append_error_recovery_hint(
            teaching_error_text(
                tool_call.name,
                tool_call.arguments,
                f"{type(exc).__name__}: {sanitize_for_log(exc)}",
                workspace_root=workspace_root,
            )
        )


def _execute_tool_with_hooks_sync(
    registry: ToolRegistry,
    tool_call: ToolCall,
    boundary_overrides: Optional[Callable[[str, Dict[str, Any]], Dict[str, bool]]] = None,
    *,
    cancel_event: Optional[threading.Event] = None,
    workspace_root: str = "",
    edit_guard: Optional[EditGuard] = None,
) -> str:
    """Execute a tool, run configured post-hooks, and add recovery hints."""
    raise_if_cancelled(cancel_event)
    context = nullcontext()
    if boundary_overrides:
        try:
            overrides = {
                str(key): bool(value)
                for key, value in boundary_overrides(tool_call.name, tool_call.arguments).items()
                if value
            }
        except Exception as exc:
            return _append_error_recovery_hint(
                f"Error applying tool boundary overrides: {type(exc).__name__}: {sanitize_for_log(exc)}"
            )
        if overrides:
            try:
                from backend.tools.coding.foundation.core_mechanisms.execution_boundary_context import boundary_override

                context = boundary_override(**overrides)
            except Exception as exc:
                return _append_error_recovery_hint(
                    f"Error applying tool boundary overrides: {type(exc).__name__}: {sanitize_for_log(exc)}"
                )

    workspace_context = nullcontext()
    if workspace_root:
        try:
            from backend.tools.coding.foundation.core_mechanisms.execution_boundary_context import workspace_root_override

            workspace_context = workspace_root_override(workspace_root)
        except Exception as exc:
            return _append_error_recovery_hint(
                f"Error applying workspace root override: {type(exc).__name__}: {sanitize_for_log(exc)}"
            )

    with context, workspace_context, cancellation_context(cancel_event):
        raise_if_cancelled(cancel_event)
        canonical_tool_name = registry.resolve_name(tool_call.name)
        if edit_guard is not None:
            guard_error = edit_guard.before_execute(canonical_tool_name, tool_call.arguments)
            if guard_error:
                return _append_error_recovery_hint(guard_error)
            edit_snapshot = edit_guard.capture_before(canonical_tool_name, tool_call.arguments)
        else:
            edit_snapshot = None
        result = registry.execute(
            tool_call.name,
            tool_call.arguments,
            cancel_event=cancel_event,
            workspace_root=workspace_root or None,
        )
        raise_if_cancelled(cancel_event)
        if edit_guard is not None:
            result = edit_guard.after_execute(
                canonical_tool_name,
                tool_call.arguments,
                result,
                edit_snapshot,
            )
    raise_if_cancelled(cancel_event)
    return _append_error_recovery_hint(result)


def _append_error_recovery_hint(result: str) -> str:
    head = result[:80].lower()
    if result.startswith(("❌", "Error", "错误")) or "error" in head:
        return (
            f"{result}\n\n"
            "[Hint: The tool returned an error. Analyze the error message, "
            "determine the cause, and try a corrected approach. "
            "Do NOT repeat the same call with identical arguments.]"
        )
    return result


def _enforce_working_context(messages: List[Dict[str, Any]]) -> bool:
    """Keep the working context under the char cap WITHOUT killing the run.

    When over the cap, hard-truncate oversized tool results and historical
    tool-call arguments (e.g. a huge inline script the agent sent) in place,
    then re-measure. Returns True only if it is STILL over after truncation
    (i.e. genuinely unrecoverable) — the caller fails only in that case.
    """
    if _working_context_chars(messages) <= MAX_WORKING_CONTEXT_CHARS:
        return False
    for message in messages:
        content = message.get("content")
        if isinstance(content, str) and len(content) > _MAX_RESULT_CHARS:
            message["content"] = _head_tail_truncate(content)
        for tool_call in message.get("tool_calls") or []:
            fn = tool_call.get("function") or {}
            args = fn.get("arguments")
            if isinstance(args, str) and len(args) > _MAX_RESULT_CHARS:
                fn["arguments"] = args[:_MAX_RESULT_CHARS] + " /* …arguments truncated… */"
    still_over = _working_context_chars(messages) > MAX_WORKING_CONTEXT_CHARS
    if not still_over:
        logger.info("runtime emergency context truncation applied (recovered under cap)")
    return still_over


def _working_context_chars(messages: List[Dict[str, Any]]) -> int:
    total = 0
    for message in messages:
        total += _content_context_chars(message.get("content"))
        if message.get("tool_calls"):
            total += len(json.dumps(message.get("tool_calls"), ensure_ascii=False, default=str))
        total += len(str(message.get("name") or ""))
    return total


def _content_context_chars(content: Any) -> int:
    if isinstance(content, list):
        total = 0
        for block in content:
            if isinstance(block, dict):
                block_type = str(block.get("type") or "").lower()
                if "image" in block_type or isinstance(block.get("image_url"), dict):
                    total += 6400
                    continue
                if isinstance(block.get("text"), str):
                    total += len(block["text"])
                    continue
            total += len(str(block or ""))
        return total
    return len(str(content or ""))


def _context_limit_error_event() -> ErrorEvent:
    return ErrorEvent(
        recoverable=False,
        code="RUNTIME_CONTEXT_LIMIT",
        title="工作上下文过大",
        message=(
            "工具结果和消息历史已超过本次运行的安全上限，Metis 已停止继续追加上下文，"
            "以避免内存膨胀。请压缩上下文后重试。"
        ),
        hint="建议先执行上下文压缩，或拆分任务、减少大文件/大输出进入对话。",
    )


def _tool_call_signature(tool_call: ToolCall) -> str:
    args_json = json.dumps(tool_call.arguments or {}, sort_keys=True, ensure_ascii=False, default=str)
    digest = hashlib.sha256(args_json.encode("utf-8", errors="replace")).hexdigest()
    return f"{tool_call.name}:{digest}"


def _record_tool_progress(
    registry: ToolRegistry,
    tool_call: ToolCall,
    result: str,
    verification_tracker: VerificationTracker,
) -> None:
    canonical = registry.resolve_name(tool_call.name)
    if canonical in {"run_tests", "verify_compilation", "execute_bash_command"}:
        verification_tracker.record(canonical, tool_call.arguments or {})
        return
    if _tool_result_looks_successful(result):
        verification_tracker.record(canonical, tool_call.arguments or {})


def _tool_result_looks_successful(result: str) -> bool:
    text = str(result or "").lstrip()
    if text.startswith(("❌", "Error", "错误", "[Permission denied]", "[Cancelled]")):
        return False
    return "Traceback (most recent call last)" not in text


def _todo_update_event_if_any(
    registry: ToolRegistry,
    tool_call: ToolCall,
    result: str,
) -> Optional[TodoUpdateEvent]:
    if registry.resolve_name(tool_call.name) != "todo_write":
        return None
    if not _tool_result_looks_successful(result):
        return None
    todos = tool_call.arguments.get("todos") if isinstance(tool_call.arguments, dict) else None
    if not isinstance(todos, list):
        return None
    clean_todos = [dict(item) for item in todos if isinstance(item, dict)]
    return TodoUpdateEvent(
        todos=clean_todos,
        summary=compact_todo_block(clean_todos),
        call_id=tool_call.id,
    )


def _repeated_tool_error_event(tool_call: ToolCall, hint: Optional[str] = None) -> ErrorEvent:
    if hint:
        message = hint
    else:
        message = (
            f"工具 {tool_call.name} 连续以相同参数调用 "
            f"{REPEATED_TOOL_CALL_LIMIT} 次，已停止本次运行。"
        )
    return ErrorEvent(
        recoverable=False,
        code="RUNTIME_REPEATED_TOOL_CALL",
        title="检测到重复工具调用",
        message=message,
        hint=(
            "停下来重新审视计划：先用 todo_write 标记当前项的障碍，"
            "再换一个工具或路径（例如 grep 找不到就试 glob_search、generate_repo_map 或 read_file）。"
        ),
    )


def _format_assistant_message(response: LLMResponse, config: Optional[AgentConfig] = None) -> Dict[str, Any]:
    message: Dict[str, Any] = {
        "role": "assistant",
        "content": response.content or None,
    }
    if response.reasoning_content:
        message["reasoning_content"] = response.reasoning_content
        if config is not None:
            origin = _reasoning_origin_for_config(config)
            message[_REASONING_ORIGIN_PROVIDER_KEY] = origin["provider_id"]
            message[_REASONING_ORIGIN_MODEL_KEY] = origin["model"]
            message[_REASONING_ORIGIN_BASE_URL_KEY] = origin["base_url"]
    if response.tool_calls:
        message["tool_calls"] = [
            {
                "id": tool_call.id,
                "type": "function",
                "function": {
                    "name": tool_call.name,
                    "arguments": json.dumps(tool_call.arguments, ensure_ascii=False),
                },
            }
            for tool_call in response.tool_calls
        ]
    return message


def _format_tool_result(
    tool_call: ToolCall,
    result: str,
    seen_files: Optional[Dict[str, str]] = None,
    compactor: Optional[ResultCompactor] = None,
) -> Dict[str, Any]:
    result = _public_tool_result(tool_call, result)
    if compactor is not None:
        content = compactor.compact(tool_call.name, result)
    else:
        content = _smart_tool_result(result, tool_call.name, seen_files)
    return {
        "role": "tool",
        "tool_call_id": tool_call.id,
        "name": tool_call.name,
        "content": content,
    }


def _public_tool_result(tool_call: ToolCall, result: str) -> str:
    return sanitize_tool_result(tool_call.name, result).public_result


# ---------------------------------------------------------------------------
# Tool result optimization: per-tool strategies + file dedup + head/tail
# ---------------------------------------------------------------------------

_FILE_READ_TOOLS = {"read_file", "read_multiple_files"}
_WRITE_TOOLS = {
    "write_file",
    "create_file",
    "append_to_file",
    "robust_replace_in_file",
    "edit_code_ast",
    "apply_patch",
    "rename_file_update_refs",
    "delete_file",
    "metis_rootfs_asset_download",
    "metis_rootfs_build",
    "metis_rootfs_image_build",
    "metis_rootfs_asset_register",
    "metis_runtime_bundle_package",
    "metis_runtime_bundle_package_v2",
    "metis_runtime_bundle_prepare",
    "metis_vm_direct_assets_prepare",
    "metis_vm_direct_runner_prepare",
    "metis_vm_direct_runner_smoke",
    "metis_vm_hcs_starter_prepare",
    "metis_vm_hcs_starter_start",
    "metis_vm_guest_handshake_prepare",
    "metis_vm_guest_handshake_verify",
    "metis_vm_rootfs_boot_verifier_prepare",
    "metis_vm_rootfs_boot_verify",
    "metis_vm_pack_adopt_reference",
    "metis_vm_pack_scaffold",
    "metis_wsl_runtime_import",
}
_SEARCH_TOOLS = {"search_in_file", "search_in_codebase", "find_files"}
_SHELL_TOOLS = {"execute_bash_command", "execute_command"}
_LIST_TOOLS = {"list_directory"}
_BROWSER_TOOLS = {"browser_navigate", "browser_get_text", "browser_extract_text",
                  "browser_read_page", "web_search", "web_research"}

_MAX_RESULT_CHARS = 24_000  # ~6K tokens


def _smart_tool_result(
    result: str,
    tool_name: str,
    seen_files: Optional[Dict[str, str]] = None,
) -> str:
    name_lower = tool_name.lower()

    # Write/edit tools: compact confirmation
    if name_lower in _WRITE_TOOLS or any(kw in name_lower for kw in ("write", "save", "create_file")):
        return _compact_write_result(result)

    # File reads: dedup identical content
    if name_lower in _FILE_READ_TOOLS and seen_files is not None:
        result = _dedup_file_content(result, seen_files)

    # Shell output: keep exit code + tail
    if name_lower in _SHELL_TOOLS:
        result = _compact_shell_result(result)

    # Search results: limit match count
    if name_lower in _SEARCH_TOOLS:
        result = _compact_search_result(result)

    # Directory listing: compact format
    if name_lower in _LIST_TOOLS:
        result = _compact_list_result(result)

    # Browser page text: hard cap
    if name_lower in _BROWSER_TOOLS:
        if len(result) > 6_000:
            result = result[:5_500] + f"\n\n[... page text truncated, {len(result) - 5500} chars omitted ...]"

    # Final head+tail fallback for anything still too large
    return _head_tail_truncate(result)


def _head_tail_truncate(result: str) -> str:
    if len(result) <= _MAX_RESULT_CHARS:
        return result
    HEAD, TAIL = 12_000, 8_000
    mid_lines = result[HEAD:-TAIL].count("\n")
    mid_chars = len(result) - HEAD - TAIL
    return (
        result[:HEAD]
        + f"\n\n[... truncated {mid_lines} lines / {mid_chars} chars."
        " Re-read the source file if you need full content. ...]\n\n"
        + result[-TAIL:]
    )


def _compact_write_result(result: str) -> str:
    if len(result) <= 500:
        return result
    first_line = result.split("\n", 1)[0]
    return first_line[:300] + f"\n[Write confirmed. Full echo omitted ({len(result)} chars).]"


def _dedup_file_content(result: str, seen_files: Dict[str, str]) -> str:
    content_hash = hashlib.sha256(result.encode("utf-8", errors="replace")).hexdigest()[:16]
    # Try to extract file path from typical tool output patterns
    path = ""
    for line in result.split("\n", 5):
        stripped = line.strip()
        if stripped.startswith("=== ") and stripped.endswith(" ==="):
            path = stripped[4:-4].strip()
            break
        if stripped.startswith("File: ") or stripped.startswith("Path: "):
            path = stripped.split(": ", 1)[1].strip()
            break
    if not path:
        path = f"__unnamed_{content_hash}"
    prev_hash = seen_files.get(path)
    if prev_hash == content_hash:
        return f"[File already in context: {path} (unchanged, {len(result)} chars). No need to re-read.]"
    seen_files[path] = content_hash
    return result


def _compact_shell_result(result: str) -> str:
    if len(result) <= 4_000:
        return result
    lines = result.split("\n")
    # Keep first 10 lines + last 40 lines + exit code line if present
    head = lines[:10]
    tail = lines[-40:]
    omitted = len(lines) - 50
    return "\n".join(head) + f"\n\n[... {omitted} lines omitted ...]\n\n" + "\n".join(tail)


def _compact_search_result(result: str) -> str:
    if len(result) <= 8_000:
        return result
    lines = result.split("\n")
    if len(lines) <= 80:
        return _head_tail_truncate(result)
    kept = lines[:60]
    return "\n".join(kept) + f"\n\n[... {len(lines) - 60} more matches omitted. Narrow your search if needed. ...]"


def _compact_list_result(result: str) -> str:
    if len(result) <= 3_000:
        return result
    lines = result.split("\n")
    if len(lines) <= 100:
        return result
    kept = lines[:80]
    return "\n".join(kept) + f"\n\n[... {len(lines) - 80} more entries omitted ...]"


def _build_vision_message(
    results: List[Tuple[ToolCall, str]],
) -> Optional[Dict[str, Any]]:
    """Build a multimodal user message from image paths in tool results."""
    blocks: List[Dict[str, Any]] = []
    for _tool_call, result_text in results:
        for path in extract_image_paths(result_text):
            block = build_image_content_block(path)
            if block:
                blocks.append(block)

    if not blocks:
        return None

    blocks.insert(
        0,
        {
            "type": "text",
            "text": (
                "[Attached: visual output from the tool calls above. "
                "Describe what you see if relevant to the user's request.]"
            ),
        },
    )
    return {"role": "user", "content": blocks}

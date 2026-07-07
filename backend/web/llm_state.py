from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import shutil
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import requests

from backend.bridges.model_capability import detect_from_model_name, tier_compact_thresholds
from backend.core.engine.prompt_runtime import compile_prompt_runtime
from backend.core.paths import legacy_miro_path, metis_path
from backend.runtime.agent_loop import AgentConfig
from backend.runtime.provider_conformance import run_provider_conformance_probe

try:
    from backend.bridges.provider_registry import (
        list_provider_payloads,
        normalize_openai_api_base_url,
        normalize_provider_model,
        resolve_provider_for_config,
        validate_provider_config,
    )
    from backend.runtime.llm_backends._common import (
        _force_direct_connection,
        _proxies_for_url,
        _should_retry_without_env_proxy,
        sanitize_for_log,
    )
    from backend.runtime.llm_backends.ollama_helper import (
        check_ollama_running,
        list_ollama_models,
        normalize_ollama_base_url,
    )
except ImportError:  # pragma: no cover - supports running from inside miro/
    from backend.bridges.provider_registry import (
        list_provider_payloads,
        normalize_openai_api_base_url,
        normalize_provider_model,
        resolve_provider_for_config,
        validate_provider_config,
    )
    from backend.runtime.llm_backends._common import (
        _force_direct_connection,
        _proxies_for_url,
        _should_retry_without_env_proxy,
        sanitize_for_log,
    )
    from backend.runtime.llm_backends.ollama_helper import (
        check_ollama_running,
        list_ollama_models,
        normalize_ollama_base_url,
    )


_MODEL_CONTEXT_LIMITS: Dict[str, int] = {
    "deepseek-v4-flash": 1000000,
    "deepseek-v4-pro": 1000000,
    "deepseek-chat": 128000,
    "deepseek-coder": 128000,
    "deepseek-reasoner": 64000,
    "gpt-4o": 128000,
    "gpt-4o-mini": 128000,
    "gpt-4-turbo": 128000,
    "gpt-4.1": 1047576,
    "gpt-4.1-mini": 1047576,
    "o3": 200000,
    "o3-mini": 200000,
    "o4-mini": 200000,
    "claude-sonnet-4-20250514": 200000,
    "claude-opus-4-20250514": 200000,
    "claude-3-5-sonnet": 200000,
    "gpt-5.5": 1000000,
    "gpt-5.4": 1000000,
    "gpt-5.4-mini": 1000000,
    "codex-auto-review": 1000000,
    "kimi-k2.6": 262144,
    "glm-5.1": 200000,
    "qwen3-coder-plus": 1000000,
    "qwen3-max": 262144,
    "qwen2.5:7b": 32768,
}
_DEFAULT_CONTEXT_LIMIT = 128000
_COMPACT_STAGE_1_THRESHOLD = 0.60
_COMPACT_STAGE_2_THRESHOLD = 0.80
_COMPACT_STAGE_3_THRESHOLD = 0.92
_PROVIDER_SETTINGS_LOCK = threading.RLock()
_PROVIDER_PROBE_CACHE_LOCK = threading.RLock()
_PROVIDER_MODEL_CACHE_TTL_SECONDS = 300.0
_PROVIDER_MODEL_CACHE: Dict[Tuple[str, str, str], Tuple[float, Dict[str, Any]]] = {}
_PROXY_ENV_RUNTIME_KEYS = (
    "METIS_PROXY_MODE",
    "METIS_PROXY_SCHEME",
    "METIS_PROXY_HOST",
    "METIS_PROXY_PORT",
    "METIS_PROXY_BYPASS",
    "METIS_LLM_PROXY",
)
logger = logging.getLogger(__name__)


def env(new_key: str, old_key: str = "", default: str = "") -> str:
    if os.environ.get(new_key):
        return os.environ[new_key]
    if old_key and os.environ.get(old_key):
        return os.environ[old_key]
    return default


def env_any(keys: List[str], default: str = "") -> str:
    for key in keys:
        if os.environ.get(key):
            return os.environ[key]
    return default


def _env_file_values() -> Dict[str, str]:
    """Read root/workspace .env values without mutating the process environment."""
    values: Dict[str, str] = {}
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths = [os.path.join(root, ".env")]
    cwd_env = os.path.join(os.getcwd(), ".env")
    if os.path.normcase(os.path.abspath(cwd_env)) != os.path.normcase(os.path.abspath(paths[0])):
        paths.append(cwd_env)

    for path in paths:
        if not os.path.isfile(path):
            continue
        try:
            with open(path, "r", encoding="utf-8") as handle:
                lines = handle.readlines()
        except OSError:
            continue
        file_values: Dict[str, str] = {}
        for raw_line in lines:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()
            if not key or key.startswith("#"):
                continue
            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in {"'", '"'}
            ):
                value = value[1:-1]
            if value:
                file_values[key] = value
        _normalize_env_file_aliases(file_values)
        values.update(file_values)
    return values


def _normalize_env_file_aliases(values: Dict[str, str]) -> None:
    """Map provider-specific .env names onto the runtime names inside one file."""
    backend_hint = (
        values.get("METIS_LLM_BACKEND")
        or values.get("MIRO_LLM_BACKEND")
        or values.get("LLM_BACKEND")
        or ""
    ).strip().lower()
    if not values.get("METIS_LLM_API_KEY") and not values.get("MIRO_LLM_API_KEY"):
        api_key = values.get("DEEPSEEK_API_KEY") if backend_hint == "deepseek" else values.get("DEEPSEEK_API_KEY") or values.get("OPENAI_API_KEY")
        if api_key:
            values["METIS_LLM_API_KEY"] = api_key
    if not values.get("METIS_LLM_BASE_URL") and not values.get("MIRO_LLM_BASE_URL"):
        base_url = values.get("DEEPSEEK_BASE_URL") or values.get("DEEPSEEK_API_URL")
        if not base_url and values.get("OPENAI_BASE_URL"):
            base_url = values["OPENAI_BASE_URL"]
        if base_url:
            values["METIS_LLM_BASE_URL"] = base_url
    if not values.get("METIS_LLM_MODEL") and not values.get("MIRO_LLM_MODEL"):
        model = values.get("DEEPSEEK_CHAT_MODEL")
        if not model and backend_hint and backend_hint != "deepseek":
            model = values.get("OPENAI_MODEL")
        if model:
            values["METIS_LLM_MODEL"] = model


def _configured(keys: List[str], file_values: Dict[str, str], default: str = "") -> str:
    for key in keys:
        if os.environ.get(key):
            return os.environ[key]
    for key in keys:
        if file_values.get(key):
            return file_values[key]
    return default


def _runtime_keys(kind: str, backend: str) -> List[str]:
    backend = (backend or "openai").strip().lower()
    keys = {
        "base_url": ["METIS_LLM_BASE_URL", "MIRO_LLM_BASE_URL"],
        "api_key": ["METIS_LLM_API_KEY", "MIRO_LLM_API_KEY"],
        "model": ["METIS_LLM_MODEL", "MIRO_LLM_MODEL"],
    }[kind]
    if backend == "deepseek":
        if kind == "base_url":
            return keys + ["DEEPSEEK_BASE_URL", "DEEPSEEK_API_URL"]
        if kind == "api_key":
            return keys + ["DEEPSEEK_API_KEY"]
        return keys + ["DEEPSEEK_CHAT_MODEL"]
    if backend in {"openai", "openai-compatible", "openai_compat", "custom", "custom-openai"}:
        if kind == "base_url":
            return keys + ["DEEPSEEK_BASE_URL", "DEEPSEEK_API_URL", "OPENAI_BASE_URL"]
        if kind == "api_key":
            return keys + ["DEEPSEEK_API_KEY", "OPENAI_API_KEY"]
        return keys + ["DEEPSEEK_CHAT_MODEL", "OPENAI_MODEL"]
    if backend == "doubao":
        if kind == "base_url":
            return keys + ["DOUBAO_BASE_URL", "ARK_BASE_URL", "VOLCENGINE_BASE_URL"]
        if kind == "api_key":
            return keys + ["DOUBAO_API_KEY", "ARK_API_KEY", "VOLCENGINE_API_KEY"]
        return keys + ["DOUBAO_MODEL", "ARK_MODEL"]
    if backend == "ollama":
        if kind == "base_url":
            return keys + ["OLLAMA_BASE_URL"]
        if kind == "api_key":
            return keys + ["OLLAMA_API_KEY"]
        return keys + ["OLLAMA_MODEL"]
    if backend == "anthropic":
        if kind == "api_key":
            return keys + ["ANTHROPIC_API_KEY"]
        return keys + ["ANTHROPIC_MODEL"] if kind == "model" else keys
    if backend == "gemini":
        if kind == "api_key":
            return keys + ["GEMINI_API_KEY"]
        return keys + ["GEMINI_MODEL"] if kind == "model" else keys
    return keys


def _strip_config_whitespace(value: str) -> str:
    return "".join(
        char
        for char in str(value or "")
        if not char.isspace() and char not in "\u200b\u200c\u200d\ufeff"
    )


def _runtime_value(kind: str, backend: str, file_values: Dict[str, str], default: str = "") -> str:
    value = _configured(_runtime_keys(kind, backend), file_values, default)
    if kind in {"base_url", "api_key"}:
        return _strip_config_whitespace(value)
    return value


def env_disabled(new_key: str, old_key: str = "") -> bool:
    return env(new_key, old_key, "").strip().lower() in {"1", "true", "yes", "on"}


def env_bool(new_key: str, old_key: str = "", default: bool = False) -> bool:
    value = env(new_key, old_key, "1" if default else "0").strip().lower()
    return value in {"1", "true", "yes", "on"}


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def default_base_url(backend: str) -> str:
    backend = (backend or "openai").strip().lower()
    if backend == "deepseek":
        return "https://api.deepseek.com"
    if backend == "kimi":
        return "https://api.moonshot.cn/v1"
    if backend == "zhipu-glm":
        return "https://open.bigmodel.cn/api/coding/paas/v4"
    if backend == "bailian":
        return "https://dashscope.aliyuncs.com/compatible-mode/v1"
    if backend == "doubao":
        return "https://ark.cn-beijing.volces.com/api/v3"
    if backend == "ollama":
        return "http://localhost:11434/v1"
    if backend in {"openai-compatible", "openai_compat", "custom", "custom-openai"}:
        return ""
    if backend == "anthropic":
        return "https://api.anthropic.com/v1/messages"
    if backend == "gemini":
        return ""
    # Preserve old Metis behavior: the legacy "openai" backend defaults to DeepSeek.
    return "https://api.deepseek.com"


def default_model(backend: str) -> str:
    backend = (backend or "openai").strip().lower()
    if backend == "deepseek":
        return "deepseek-v4-flash"
    if backend == "kimi":
        return "kimi-k2.6"
    if backend == "zhipu-glm":
        return "glm-5.1"
    if backend == "bailian":
        return "qwen3-coder-plus"
    if backend == "doubao":
        return ""
    if backend == "ollama":
        return "qwen2.5:7b"
    if backend in {"openai-compatible", "openai_compat", "custom", "custom-openai"}:
        return ""
    if backend == "anthropic":
        return "claude-sonnet-4-20250514"
    if backend == "gemini":
        return "gemini-2.0-flash"
    # Preserve old Metis behavior: the legacy "openai" backend defaults to DeepSeek.
    return "deepseek-v4-flash"


def normalize_base_url(backend: str, base_url: str) -> str:
    return _strip_config_whitespace(str(base_url or "")).rstrip("/")


def _resolved_provider_runtime_values(
    provider_id_or_alias: str,
    *,
    base_url: str,
    model: str,
) -> Dict[str, Any]:
    profile = resolve_provider_for_config(
        provider_id_or_alias,
        base_url=str(base_url or ""),
        model=str(model or ""),
    )
    backend = str(profile.provider_id)
    resolved_base_url = normalize_base_url(backend, str(base_url or profile.base_url or default_base_url(backend)))
    if profile.openai_compatible and resolved_base_url:
        resolved_base_url = normalize_openai_api_base_url(resolved_base_url)
    resolved_model, model_warning = normalize_provider_model(profile, str(model or profile.default_model or default_model(backend)))
    return {
        "profile": profile,
        "backend": backend,
        "base_url": resolved_base_url,
        "model": resolved_model,
        "model_warning": model_warning or "",
    }


def config_path(*, legacy: bool = False) -> str:
    return str(legacy_miro_path("config.json") if legacy else metis_path("config.json"))


def _is_masked_api_key(value: str) -> bool:
    value = _strip_config_whitespace(str(value or ""))
    return value == "***" or "****" in value


def _mask_api_key(value: str) -> str:
    value = _strip_config_whitespace(str(value or ""))
    if not value:
        return ""
    suffix = value[-4:] if len(value) >= 4 else value
    if value.startswith("sk-"):
        return f"sk-****{suffix}"
    prefix = value[:3] if len(value) > 7 else ""
    return f"{prefix}****{suffix}"


def load_persistent_config() -> None:
    """Load persistent Metis config, falling back to legacy Miro config."""
    path = config_path()
    if not os.path.isfile(path):
        legacy_path = config_path(legacy=True)
        path = legacy_path if os.path.isfile(legacy_path) else path
    if not os.path.isfile(path):
        return
    try:
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
    except json.JSONDecodeError:
        backup = f"{path}.corrupt.{int(time.time())}"
        try:
            shutil.copy2(path, backup)
            logger.warning("Config corrupted, backed up to %s. Using defaults.", sanitize_for_log(backup))
        except OSError as exc:
            logger.warning("Config corrupted and backup failed: %s", sanitize_for_log(exc))
        return
    except OSError as exc:
        logger.warning("Config load failed: %s", sanitize_for_log(exc))
        return

    mapping = {
        "backend": "METIS_LLM_BACKEND",
        "base_url": "METIS_LLM_BASE_URL",
        "api_key": "METIS_LLM_API_KEY",
        "model": "METIS_LLM_MODEL",
        "temperature": "METIS_TEMPERATURE",
        "reasoning_effort": "METIS_REASONING_EFFORT",
        "max_tokens": "METIS_MAX_TOKENS",
        "auto_memory": "METIS_AUTO_MEMORY",
        "auto_skills": "METIS_AUTO_SKILLS",
        "proxy_mode": "METIS_PROXY_MODE",
        "proxy_scheme": "METIS_PROXY_SCHEME",
        "proxy_host": "METIS_PROXY_HOST",
        "proxy_port": "METIS_PROXY_PORT",
        "proxy_bypass": "METIS_PROXY_BYPASS",
        "terminal_shell": "METIS_TERMINAL_SHELL",
        "python_path": "METIS_PYTHON",
    }
    for key, env_var in mapping.items():
        value = data.get(key) if isinstance(data, dict) else None
        if value not in (None, ""):
            os.environ[env_var] = str(value)
    if isinstance(data, dict):
        _apply_proxy_runtime(data)


def build_agent_config(
    *,
    system_prompt: str,
    user_memory_text: str = "",
    execution_mode: str,
    permission_checker: Optional[Callable[[str, Dict[str, Any]], Optional[str]]] = None,
    tool_boundary_overrides: Optional[Callable[[str, Dict[str, Any]], Dict[str, bool]]] = None,
    workspace_root: str = "",
) -> AgentConfig:
    load_persistent_config()
    file_values = _env_file_values()
    backend = _configured(["METIS_LLM_BACKEND", "MIRO_LLM_BACKEND"], file_values, "openai")
    base_url = normalize_base_url(
        backend,
        _runtime_value("base_url", backend, file_values, default_base_url(backend)),
    )
    raw_model = _runtime_value("model", backend, file_values, default_model(backend))
    resolved = _resolved_provider_runtime_values(backend, base_url=base_url, model=raw_model)
    api_key = _runtime_value("api_key", backend, file_values, "")
    if not api_key and resolved["backend"] != backend:
        api_key = _runtime_value("api_key", resolved["backend"], file_values, "")
    model_capabilities = detect_from_model_name(resolved["model"])
    # 缓存纪律：凡"会随干活而变"的层都不进 system 前缀，否则在活跃会话中逐轮漂移，从漂移点起
    # 打断 DeepSeek 上下文缓存（连带后续整段历史全不命中）。
    #   - agent_state/open_files/terminal：每轮易变
    #   - repo_map/workspace_memory：随文件/记忆改动而变
    # 这些信息本就可由模型经工具按需获取（符合工具化设计），todo 也已在末尾消息单独刷新。
    # 前缀只保留真正稳定的层（base、固定规则、cwd、skills 索引、Project Profile、用户 METIS.md），以最大化缓存命中。
    prompt_snapshot = compile_prompt_runtime(
        system_prompt,
        user_memory_text=user_memory_text,
        model_tier=model_capabilities.tier,
        model_context_window=model_capabilities.effective_context,
        workspace_root=workspace_root or "",
        include_agent_state_hint=False,
        include_open_files_hint=False,
        include_terminal_hint=False,
        include_repo_map_hint=False,
        include_workspace_memory_hint=False,
    )
    return AgentConfig(
        llm_backend=resolved["backend"],
        llm_base_url=resolved["base_url"],
        llm_api_key=api_key,
        llm_model=resolved["model"],
        reasoning_effort=env("METIS_REASONING_EFFORT", "MIRO_REASONING_EFFORT", ""),
        temperature=float(env("METIS_TEMPERATURE", "MIRO_TEMPERATURE", "0.3")),
        max_tokens=int(env("METIS_MAX_TOKENS", "MIRO_MAX_TOKENS", "4096")),
        max_turns=int(env("METIS_MAX_TURNS", "MIRO_MAX_TURNS", "64")),
        timeout=float(env("METIS_LLM_TIMEOUT", "MIRO_LLM_TIMEOUT", "120")),
        system_prompt=prompt_snapshot.final_system_prompt,
        execution_mode=execution_mode,
        workspace_root=workspace_root,
        permission_checker=permission_checker,
        tool_boundary_overrides=tool_boundary_overrides,
    )


def get_runtime_settings() -> Dict[str, Any]:
    with _PROVIDER_SETTINGS_LOCK:
        load_persistent_config()
        file_values = _env_file_values()
        backend = _configured(["METIS_LLM_BACKEND", "MIRO_LLM_BACKEND"], file_values, "openai")
        api_key = _runtime_value("api_key", backend, file_values, "")
        base_url = normalize_base_url(
            backend,
            _runtime_value("base_url", backend, file_values, default_base_url(backend)),
        )
        raw_model = _runtime_value("model", backend, file_values, default_model(backend))
        resolved = _resolved_provider_runtime_values(backend, base_url=base_url, model=raw_model)
        if not api_key and resolved["backend"] != backend:
            api_key = _runtime_value("api_key", resolved["backend"], file_values, "")
        validation = validate_provider_config(
            resolved["backend"],
            base_url=resolved["base_url"],
            model=resolved["model"],
            api_key=api_key,
        )
        model = validation.get("model") or resolved["model"]
        return {
            "backend": resolved["backend"],
            "provider_id": validation.get("provider_id", resolved["backend"]),
            "provider": validation.get("provider"),
            "base_url": resolved["base_url"],
            "api_key": _mask_api_key(api_key),
            "has_api_key": bool(api_key),
            "model": model,
            "temperature": float(env("METIS_TEMPERATURE", "MIRO_TEMPERATURE", "0.3")),
            "reasoning_effort": env("METIS_REASONING_EFFORT", "MIRO_REASONING_EFFORT", "off"),
            "max_tokens": int(env("METIS_MAX_TOKENS", "MIRO_MAX_TOKENS", "4096")),
            "auto_memory": env_bool("METIS_AUTO_MEMORY", "MIRO_AUTO_MEMORY", True),
            "auto_skills": env_bool("METIS_AUTO_SKILLS", "MIRO_AUTO_SKILLS", True),
            "proxy_mode": _normalized_proxy_settings({})["proxy_mode"],
            "proxy_scheme": _normalized_proxy_settings({})["proxy_scheme"],
            "proxy_host": _normalized_proxy_settings({})["proxy_host"],
            "proxy_port": _normalized_proxy_settings({})["proxy_port"],
            "proxy_bypass": _normalized_proxy_settings({})["proxy_bypass"],
            "terminal_shell": env("METIS_TERMINAL_SHELL", "MIRO_TERMINAL_SHELL", "powershell"),
            "python_path": env("METIS_PYTHON", "MIRO_PYTHON", ""),
            "provider_validation": validation,
        }


def update_runtime_settings(data: Dict[str, Any]) -> List[str]:
    with _PROVIDER_SETTINGS_LOCK:
        data = _normalized_runtime_settings_update(data)
        if _has_proxy_settings(data):
            data.update(_normalized_proxy_settings(data))
        mapping = {
            "backend": "METIS_LLM_BACKEND",
            "base_url": "METIS_LLM_BASE_URL",
            "api_key": "METIS_LLM_API_KEY",
            "model": "METIS_LLM_MODEL",
            "temperature": "METIS_TEMPERATURE",
            "reasoning_effort": "METIS_REASONING_EFFORT",
            "max_tokens": "METIS_MAX_TOKENS",
            "auto_memory": "METIS_AUTO_MEMORY",
            "auto_skills": "METIS_AUTO_SKILLS",
            "terminal_shell": "METIS_TERMINAL_SHELL",
            "python_path": "METIS_PYTHON",
        }
        proxy_mapping = {
            "proxy_mode": "METIS_PROXY_MODE",
            "proxy_scheme": "METIS_PROXY_SCHEME",
            "proxy_host": "METIS_PROXY_HOST",
            "proxy_port": "METIS_PROXY_PORT",
            "proxy_bypass": "METIS_PROXY_BYPASS",
        }
        updated = []
        for key, env_var in mapping.items():
            value = data.get(key)
            if value in (None, ""):
                continue
            text = _strip_config_whitespace(str(value)) if key in {"base_url", "api_key"} else str(value).strip()
            if key == "api_key" and _is_masked_api_key(text):
                continue
            os.environ[env_var] = text
            updated.append(key)
        for key, env_var in proxy_mapping.items():
            if key not in data:
                continue
            value = data.get(key)
            os.environ[env_var] = "" if value is None else str(value).strip()
            updated.append(key)
        if any(key in data for key in proxy_mapping):
            _apply_proxy_runtime(data)
        if updated:
            persist_runtime_settings(data)
        return updated


def _normalized_runtime_settings_update(data: Dict[str, Any]) -> Dict[str, Any]:
    normalized = dict(data)
    if not any(key in normalized for key in ("provider", "provider_id", "backend", "base_url", "model")):
        return normalized

    load_persistent_config()
    file_values = _env_file_values()
    current_backend = _configured(["METIS_LLM_BACKEND", "MIRO_LLM_BACKEND"], file_values, "openai")
    backend = str(normalized.get("provider") or normalized.get("provider_id") or normalized.get("backend") or current_backend).strip()
    base_url = normalize_base_url(
        backend,
        str(
            normalized.get("base_url")
            or _runtime_value("base_url", backend, file_values, default_base_url(backend))
        ),
    )
    model = str(
        normalized.get("model")
        or _runtime_value("model", backend, file_values, default_model(backend))
    ).strip()
    resolved = _resolved_provider_runtime_values(backend, base_url=base_url, model=model)
    normalized["backend"] = resolved["backend"]
    normalized["provider_id"] = resolved["backend"]
    normalized["base_url"] = resolved["base_url"]
    normalized["model"] = resolved["model"]
    return normalized


def persist_runtime_settings(data: Dict[str, Any]) -> None:
    with _PROVIDER_SETTINGS_LOCK:
        data = _normalized_runtime_settings_update(data)
        if _has_proxy_settings(data):
            data.update(_normalized_proxy_settings(data))
        load_persistent_config()
        file_values = _env_file_values()
        backend = str(
            data.get("backend")
            or _configured(["METIS_LLM_BACKEND", "MIRO_LLM_BACKEND"], file_values, "openai")
        ).strip()
        existing_key = _runtime_value("api_key", backend, file_values, "")
        incoming_key = _strip_config_whitespace(str(data.get("api_key") or ""))
        base_url = normalize_base_url(
            backend,
            str(
                data.get("base_url")
                or _runtime_value("base_url", backend, file_values, default_base_url(backend))
            ),
        )
        config_data = {
            "backend": backend,
            "provider_id": backend,
            "base_url": base_url,
            "api_key": incoming_key if incoming_key and not _is_masked_api_key(incoming_key) else existing_key,
            "model": str(
                data.get("model")
                or _runtime_value("model", backend, file_values, default_model(backend))
            ).strip(),
            "temperature": str(data.get("temperature") or env("METIS_TEMPERATURE", "MIRO_TEMPERATURE", "0.3")).strip(),
            "reasoning_effort": str(data.get("reasoning_effort") or env("METIS_REASONING_EFFORT", "MIRO_REASONING_EFFORT", "off")).strip(),
            "max_tokens": str(data.get("max_tokens") or env("METIS_MAX_TOKENS", "MIRO_MAX_TOKENS", "4096")).strip(),
            "auto_memory": data.get("auto_memory")
            if data.get("auto_memory") is not None
            else env_bool("METIS_AUTO_MEMORY", "MIRO_AUTO_MEMORY", True),
            "auto_skills": data.get("auto_skills")
            if data.get("auto_skills") is not None
            else env_bool("METIS_AUTO_SKILLS", "MIRO_AUTO_SKILLS", True),
            "proxy_mode": _normalized_proxy_settings(data)["proxy_mode"],
            "proxy_scheme": _normalized_proxy_settings(data)["proxy_scheme"],
            "proxy_host": _normalized_proxy_settings(data)["proxy_host"],
            "proxy_port": _normalized_proxy_settings(data)["proxy_port"],
            "proxy_bypass": _normalized_proxy_settings(data)["proxy_bypass"],
            "terminal_shell": str(data.get("terminal_shell") or env("METIS_TERMINAL_SHELL", "MIRO_TERMINAL_SHELL", "powershell")).strip(),
            "python_path": str(data.get("python_path") or env("METIS_PYTHON", "MIRO_PYTHON", "")).strip(),
        }
        _atomic_write_json(config_path(), config_data)


def _apply_proxy_runtime(data: Dict[str, Any]) -> None:
    data = _normalized_proxy_settings(data)
    mode = str(data.get("proxy_mode") or os.environ.get("METIS_PROXY_MODE") or "system").strip().lower()
    os.environ["METIS_PROXY_MODE"] = mode
    if mode == "off":
        os.environ.pop("METIS_LLM_PROXY", None)
        return
    if mode != "custom":
        os.environ.pop("METIS_LLM_PROXY", None)
        return
    scheme = str(data.get("proxy_scheme") or "http").strip() or "http"
    host = str(data.get("proxy_host") or "").strip()
    port = str(data.get("proxy_port") or "").strip()
    if not host or not port:
        os.environ.pop("METIS_LLM_PROXY", None)
        return
    os.environ["METIS_LLM_PROXY"] = f"{scheme}://{host}:{port}"


def _has_proxy_settings(data: Dict[str, Any]) -> bool:
    return any(
        key in data
        for key in (
            "proxy_mode",
            "proxyMode",
            "proxy_scheme",
            "proxyScheme",
            "proxy_host",
            "proxyHost",
            "proxy_port",
            "proxyPort",
            "proxy_bypass",
            "proxyBypass",
        )
    )


def _normalized_proxy_settings(data: Dict[str, Any]) -> Dict[str, str]:
    raw_mode = str(data.get("proxy_mode") or data.get("proxyMode") or env("METIS_PROXY_MODE", "MIRO_PROXY_MODE", "system")).strip().lower()
    mode_aliases = {
        "auto": "system",
        "env": "system",
        "environment": "system",
        "windows": "system",
        "manual": "custom",
        "proxy": "custom",
        "direct": "off",
        "none": "off",
        "disabled": "off",
        "disable": "off",
    }
    mode = mode_aliases.get(raw_mode, raw_mode)
    if mode not in {"system", "custom", "off"}:
        mode = "system"
    scheme = str(data.get("proxy_scheme") or data.get("proxyScheme") or env("METIS_PROXY_SCHEME", "MIRO_PROXY_SCHEME", "http")).strip().lower()
    if scheme not in {"http", "https", "socks5", "socks5h"}:
        scheme = "http"
    host = str(data.get("proxy_host") or data.get("proxyHost") or env("METIS_PROXY_HOST", "MIRO_PROXY_HOST", "127.0.0.1")).strip()
    port = str(data.get("proxy_port") or data.get("proxyPort") or env("METIS_PROXY_PORT", "MIRO_PROXY_PORT", "7890")).strip()
    if port and not port.isdigit():
        port = ""
    bypass = str(
        data.get("proxy_bypass")
        or data.get("proxyBypass")
        or env("METIS_PROXY_BYPASS", "MIRO_PROXY_BYPASS", "localhost,127.0.0.1,::1")
    ).strip()
    return {
        "proxy_mode": mode,
        "proxy_scheme": scheme,
        "proxy_host": host,
        "proxy_port": port,
        "proxy_bypass": bypass,
    }


def _capture_proxy_env() -> Dict[str, Optional[str]]:
    return {key: os.environ.get(key) for key in _PROXY_ENV_RUNTIME_KEYS}


def _restore_proxy_env(snapshot: Dict[str, Optional[str]]) -> None:
    for key in _PROXY_ENV_RUNTIME_KEYS:
        value = snapshot.get(key)
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value


def _proxy_effective_payload(url: str) -> Dict[str, Any]:
    trust_env = not _force_direct_connection(url)
    proxies = _proxies_for_url(url) if trust_env else None
    proxy_url = ""
    if isinstance(proxies, dict):
        proxy_url = str(proxies.get("https") or proxies.get("http") or "")
    return {
        "mode": os.environ.get("METIS_PROXY_MODE", "system"),
        "trust_env": trust_env,
        "proxy_url": proxy_url,
        "bypassed": not trust_env or not bool(proxy_url),
    }


def check_network_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    """Run a real provider probe with transient proxy settings."""
    normalized = _normalized_runtime_settings_update(data)
    proxy = _normalized_proxy_settings(data)
    snapshot = _capture_proxy_env()
    started = time.monotonic()
    try:
        load_persistent_config()
        os.environ["METIS_PROXY_MODE"] = proxy["proxy_mode"]
        os.environ["METIS_PROXY_SCHEME"] = proxy["proxy_scheme"]
        os.environ["METIS_PROXY_HOST"] = proxy["proxy_host"]
        os.environ["METIS_PROXY_PORT"] = proxy["proxy_port"]
        os.environ["METIS_PROXY_BYPASS"] = proxy["proxy_bypass"]
        _apply_proxy_runtime(proxy)

        provider = str(normalized.get("provider") or normalized.get("provider_id") or normalized.get("backend") or "openai").strip()
        profile = resolve_provider_for_config(
            provider,
            base_url=str(normalized.get("base_url") or ""),
            model=str(normalized.get("model") or ""),
        )
        backend = str(profile.provider_id)
        file_values = _env_file_values()
        incoming_key = _strip_config_whitespace(str(normalized.get("api_key") or ""))
        if _is_masked_api_key(incoming_key):
            incoming_key = ""
        fallback_key = _runtime_value("api_key", backend, file_values, "")
        if not fallback_key and backend != provider:
            fallback_key = _runtime_value("api_key", provider, file_values, "")
        base_url = normalize_base_url(
            backend,
            str(normalized.get("base_url") or _runtime_value("base_url", backend, file_values, default_base_url(backend))),
        )
        model = str(normalized.get("model") or _runtime_value("model", backend, file_values, default_model(backend))).strip()
        api_base_url = normalize_openai_api_base_url(base_url) if profile.openai_compatible and base_url else base_url
        context = {
            "profile": profile,
            "provider_id": backend,
            "provider_name": profile.display_name,
            "base_url": base_url,
            "api_base_url": api_base_url,
            "model": model,
            "api_key": incoming_key or fallback_key,
        }
        validation = verify_provider_settings(
            {
                "backend": backend,
                "provider_id": backend,
                "base_url": base_url,
                "model": model,
                "api_key": context["api_key"],
                "deep_probe": False,
            }
        )
        os.environ["METIS_PROXY_MODE"] = proxy["proxy_mode"]
        os.environ["METIS_PROXY_SCHEME"] = proxy["proxy_scheme"]
        os.environ["METIS_PROXY_HOST"] = proxy["proxy_host"]
        os.environ["METIS_PROXY_PORT"] = proxy["proxy_port"]
        os.environ["METIS_PROXY_BYPASS"] = proxy["proxy_bypass"]
        _apply_proxy_runtime(proxy)
        # Keep the settings-page network check interactive. The heavier provider
        # conformance probe is available through the provider registry probe flow.
        models_result: Dict[str, Any]
        if not context["api_key"]:
            models_result = _provider_probe_result(
                "models",
                status="error",
                ok=False,
                context=context,
                message="缺少 API Key，无法做真实网络探测。",
                hint="填写并保存 API Key，或在本页输入未保存 Key 后再测试。",
                models=[],
                models_url="",
            )
        elif profile.openai_compatible:
            models_url = (_models_url_candidates(context) or [""])[0]
            try:
                payload = _provider_get_first_json_uncached(_models_url_candidates(context), context["api_key"], timeout=8.0)
                raw_models = payload.get("data") if isinstance(payload, dict) else []
                if not isinstance(raw_models, list):
                    raw_models = payload.get("models") if isinstance(payload, dict) else []
                models = [_model_catalog_item(item) for item in raw_models if isinstance(item, dict)]
                models = [item for item in models if item["id"]]
                models_result = _provider_probe_result(
                    "models",
                    status="ok",
                    ok=True,
                    context=context,
                    message=f"模型目录可访问，返回 {len(models)} 个模型。",
                    hint="网络、Base URL 与 API Key 至少通过了 /models 探测。",
                    models=models[:50],
                    models_url=models_url,
                )
            except Exception as exc:
                models_result = _provider_probe_result(
                    "models",
                    status="error",
                    ok=False,
                    context=context,
                    message=_safe_error_message(exc),
                    hint="模型目录探测失败。请检查代理模式、Base URL、API Key 和中转站路径。",
                    models=[],
                    models_url=models_url,
                )
        else:
            models_result = _provider_probe_result(
                "models",
                status="unsupported",
                ok=bool(validation.get("ok")),
                context=context,
                message="当前 provider 不提供 OpenAI-compatible /models 探测。",
                hint="以 provider 深度探测结果为准。",
                models=[],
                models_url="",
            )
        conformance = validation.get("conformance") if isinstance(validation.get("conformance"), dict) else {}
        ok = bool(validation.get("ok")) and (bool(models_result.get("ok")) or bool(conformance.get("ok")))
        elapsed_ms = int((time.monotonic() - started) * 1000)
        return {
            "ok": ok,
            "status": "ok" if ok else "error",
            "message": "网络探测通过。" if ok else "网络探测未通过。",
            "hint": "" if ok else str(models_result.get("hint") or validation.get("hint") or ""),
            "elapsed_ms": elapsed_ms,
            "provider": {
                "provider_id": backend,
                "display_name": profile.display_name,
                "base_url": base_url,
                "api_base_url": api_base_url,
                "model": model,
                "has_api_key": bool(context["api_key"]),
            },
            "proxy": proxy,
            "effective_proxy": _proxy_effective_payload(str(validation.get("chat_url") or api_base_url or base_url)),
            "validation": validation,
            "models": models_result,
        }
    finally:
        _restore_proxy_env(snapshot)


def _replace_with_retry(temp_path: str, path: str, *, attempts: int = 5) -> None:
    """FABLEADV-18: os.replace fails on Windows when the target is transiently
    locked (antivirus scan, search indexer, cloud sync). Retry with backoff so a
    momentary lock doesn't lose the user's data."""
    import time

    delay = 0.05
    for attempt in range(attempts):
        try:
            os.replace(temp_path, path)
            return
        except PermissionError:
            if attempt == attempts - 1:
                raise
            time.sleep(delay)
            delay = min(delay * 2, 0.5)


def _atomic_write_json(path: str, data: Dict[str, Any]) -> None:
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    temp_path = os.path.join(
        directory,
        f".{os.path.basename(path)}.{uuid.uuid4().hex}.tmp",
    )
    try:
        with open(temp_path, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        _replace_with_retry(temp_path, path)
    except OSError as exc:
        logger.error("Config write failed: %s", sanitize_for_log(exc))
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        raise
    except Exception:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            pass
        raise


def first_run_status_payload() -> Dict[str, Any]:
    load_persistent_config()
    file_values = _env_file_values()
    backend = _configured(["METIS_LLM_BACKEND", "MIRO_LLM_BACKEND"], file_values, "openai")
    has_key = bool(_runtime_value("api_key", backend, file_values, ""))
    primary_path = config_path()
    legacy_path = config_path(legacy=True)
    has_config = os.path.isfile(primary_path) or os.path.isfile(legacy_path)
    return {
        "first_run": not has_key and not has_config,
        "has_api_key": has_key,
        "has_config": has_config,
        "config_path": primary_path if os.path.isfile(primary_path) else None,
        "legacy_config_path": legacy_path if os.path.isfile(legacy_path) else None,
    }


def get_provider_status() -> Dict[str, Any]:
    settings = get_runtime_settings()
    return {
        "providers": list_provider_payloads(),
        "active": settings.get("provider_validation"),
        "settings": {
            "backend": settings.get("backend"),
            "provider_id": settings.get("provider_id"),
            "base_url": settings.get("base_url"),
            "model": settings.get("model"),
            "has_api_key": settings.get("has_api_key"),
        },
    }


def verify_provider_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    load_persistent_config()
    file_values = _env_file_values()
    provider = str(data.get("provider") or data.get("provider_id") or data.get("backend") or "openai").strip()
    profile = resolve_provider_for_config(
        provider,
        base_url=str(data.get("base_url") or ""),
        model=str(data.get("model") or ""),
    )
    backend = str(profile.provider_id)
    api_key = _strip_config_whitespace(str(data.get("api_key") or ""))
    fallback_key = _runtime_value("api_key", backend, file_values, "")
    if not fallback_key and backend != provider:
        fallback_key = _runtime_value("api_key", provider, file_values, "")
    validation = validate_provider_config(
        provider,
        base_url=str(data.get("base_url") or default_base_url(backend)),
        model=str(data.get("model") or default_model(backend)),
        api_key=api_key,
        masked_api_key_fallback=fallback_key,
    )
    if not _truthy(data.get("deep_probe")) or not validation.get("ok"):
        return validation

    conformance = run_provider_conformance_probe(
        provider_id=str(validation.get("provider_id") or backend),
        base_url=str(validation.get("base_url") or data.get("base_url") or default_base_url(backend)),
        api_key=api_key or fallback_key,
        model=str(validation.get("model") or data.get("model") or default_model(backend)),
    )
    validation["conformance"] = conformance
    if conformance.get("ok"):
        validation["title"] = "深度探测完成"
        validation["message"] = "配置检查通过，provider 一致性探测已完成。"
        validation["hint"] = "探测结果已落盘，运行时会优先使用该模型的实测能力。"
    else:
        warnings = list(validation.get("warnings") or [])
        warnings.append("深度探测未完成，运行时将回退到保守 provider profile。")
        validation["warnings"] = warnings
        validation["title"] = "配置通过，深度探测失败"
        validation["message"] = "本地配置有效，但真实小请求探测未完成。"
        validation["hint"] = str(conformance.get("error") or "请检查网络、余额、模型权限或中转站协议兼容性。")
    return validation


def get_provider_models(data: Dict[str, Any]) -> Dict[str, Any]:
    context = _provider_probe_context(data)
    profile = context["profile"]
    remote_only = _truthy(data.get("remote_only") or data.get("remoteOnly"))
    if str(profile.provider_id) == "ollama":
        return _ollama_provider_model_result(context)
    if not profile.openai_compatible:
        if not remote_only:
            preset = _provider_preset_model_result(
                context,
                status="preset",
                ok=True,
                message="当前供应商不支持远程模型目录，已显示本地预设模型。",
                hint="可以选择预设模型，也可以继续手动填写模型名。",
            )
            if preset:
                return preset
        return _provider_probe_result(
            "models",
            status="unsupported",
            ok=False,
            context=context,
            message="当前供应商不支持 OpenAI-compatible /models 目录查询。",
            hint="可以继续手动填写模型名。",
            models=[],
        )
    if not context["api_key"]:
        if not remote_only:
            preset = _provider_preset_model_result(
                context,
                status="preset",
                ok=True,
                message="尚未填写 API Key，已显示本地预设模型。",
                hint="填入 API Key 后可再刷新远程模型目录；未保存的 Key 不会被持久化。",
            )
            if preset:
                return preset
        return _provider_probe_result(
            "models",
            status="error",
            ok=False,
            context=context,
            message="查询模型目录需要 API Key。",
            hint="填入 API Key 后再刷新；不会保存未点击保存的 Key。",
            models=[],
        )

    models_urls = _models_url_candidates(context)
    models_url = models_urls[0] if models_urls else ""
    last_error = ""
    try:
        payload = _provider_get_first_json(models_urls, context["api_key"])
    except Exception as exc:
        last_error = _safe_error_message(exc)
        if not remote_only:
            preset = _provider_preset_model_result(
                context,
                status="fallback",
                ok=True,
                message="远程模型目录不可用，已使用本地预设模型。",
                hint=f"{last_error}；请检查 Base URL、API Key、代理和模型平台分组。",
                models_url=models_url,
            )
            if preset:
                return preset
        return _provider_probe_result(
            "models",
            status="error",
            ok=False,
            context=context,
            message=last_error,
            hint="请检查 Base URL、API Key、代理和模型平台分组。",
            models=[],
            models_url=models_url,
        )

    raw_models = payload.get("data") if isinstance(payload, dict) else []
    if not isinstance(raw_models, list):
        raw_models = payload.get("models") if isinstance(payload, dict) else []
    models = [_model_catalog_item(item) for item in raw_models if isinstance(item, dict)]
    models = [item for item in models if item["id"]]
    if not models:
        if not remote_only:
            preset = _provider_preset_model_result(
                context,
                status="fallback",
                ok=True,
                message="远程模型目录为空，已使用本地预设模型。",
                hint="可以选择预设模型，也可以继续手动填写模型名。",
                models_url=models_url,
            )
            if preset:
                return preset
    return _provider_probe_result(
        "models",
        status="ok",
        ok=True,
        context=context,
        message=f"已读取 {len(models)} 个模型。",
        hint="选择一个模型会写入当前设置；也可以继续手动填写。",
        models=models,
        models_url=models_url,
    )


def get_provider_usage(data: Dict[str, Any]) -> Dict[str, Any]:
    context = _provider_probe_context(data)
    if not context["api_key"]:
        return _provider_probe_result(
            "usage",
            status="error",
            ok=False,
            context=context,
            message="查询额度需要 API Key。",
            hint="填入 API Key 后再刷新；不会保存未点击保存的 Key。",
        )

    usage_url = _usage_url_for_context(context)
    if not usage_url:
        return _provider_probe_result(
            "usage",
            status="unsupported",
            ok=False,
            context=context,
            message="当前供应商没有已知的只读额度接口。",
            hint="模型仍可使用；额度需要到供应商后台查看。",
        )
    try:
        payload = _provider_get_json(usage_url, context["api_key"])
    except Exception as exc:
        return _provider_probe_result(
            "usage",
            status="error",
            ok=False,
            context=context,
            message=_safe_error_message(exc),
            hint="请检查 Base URL、API Key、余额接口权限和代理设置。",
            usage_url=usage_url,
        )

    parsed = _parse_usage_payload(context, payload)
    return _provider_probe_result(
        "usage",
        status=parsed["status"],
        ok=parsed["status"] in {"ok", "warning"},
        context=context,
        message=parsed["message"],
        hint=parsed["hint"],
        usage_url=usage_url,
        **parsed["data"],
    )


def _provider_probe_context(data: Dict[str, Any]) -> Dict[str, Any]:
    load_persistent_config()
    file_values = _env_file_values()
    provider = str(data.get("provider") or data.get("provider_id") or data.get("backend") or "openai").strip()
    profile = resolve_provider_for_config(
        provider,
        base_url=str(data.get("base_url") or ""),
        model=str(data.get("model") or ""),
    )
    backend = str(profile.provider_id)
    incoming_key = _strip_config_whitespace(str(data.get("api_key") or ""))
    if _is_masked_api_key(incoming_key):
        incoming_key = ""
    fallback_key = _runtime_value("api_key", backend, file_values, "")
    if not fallback_key and backend != provider:
        fallback_key = _runtime_value("api_key", provider, file_values, "")
    base_url = normalize_base_url(
        backend,
        str(data.get("base_url") or _runtime_value("base_url", backend, file_values, default_base_url(backend))),
    )
    model = str(data.get("model") or _runtime_value("model", backend, file_values, default_model(backend))).strip()
    api_base_url = normalize_openai_api_base_url(base_url) if profile.openai_compatible and base_url else base_url
    return {
        "profile": profile,
        "provider_id": backend,
        "provider_name": profile.display_name,
        "base_url": base_url,
        "api_base_url": api_base_url,
        "model": model,
        "api_key": incoming_key or fallback_key,
    }


def _provider_probe_result(kind: str, *, status: str, ok: bool, context: Dict[str, Any], **kwargs: Any) -> Dict[str, Any]:
    return {
        "ok": ok,
        "kind": kind,
        "status": status,
        "provider_id": context["provider_id"],
        "display_name": context["provider_name"],
        "base_url": context["base_url"],
        "api_base_url": context["api_base_url"],
        "model": context["model"],
        **kwargs,
    }


def _provider_get_json(url: str, api_key: str, timeout: float = 12.0) -> Dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    trust_env = not _force_direct_connection(url)
    session = requests.Session()
    session.trust_env = trust_env
    try:
        try:
            response = session.get(
                url,
                headers=headers,
                timeout=timeout,
                proxies=_proxies_for_url(url) if trust_env else None,
            )
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as exc:
            if not trust_env or not _should_retry_without_env_proxy(url, exc):
                raise
            direct_session = requests.Session()
            direct_session.trust_env = False
            try:
                response = direct_session.get(url, headers=headers, timeout=timeout)
            finally:
                direct_session.close()
        response.raise_for_status()
        content_type = response.headers.get("content-type", "")
        if "text/html" in content_type.lower():
            raise ValueError("API 端点返回了 HTML 而非 JSON，可能是网络劫持或地址错误。")
        payload = response.json()
        return payload if isinstance(payload, dict) else {"data": payload}
    finally:
        session.close()


def _provider_get_first_json(urls: List[str], api_key: str, timeout: float = 12.0) -> Dict[str, Any]:
    last_exc: Optional[Exception] = None
    for url in urls:
        try:
            return _provider_get_cached_model_json(url, api_key, timeout)
        except Exception as exc:
            last_exc = exc
            continue
    if last_exc:
        raise last_exc
    raise ValueError("模型目录端点为空。")


def _provider_get_first_json_uncached(urls: List[str], api_key: str, timeout: float = 12.0) -> Dict[str, Any]:
    last_exc: Optional[Exception] = None
    for url in urls:
        try:
            return _provider_get_json(url, api_key, timeout)
        except Exception as exc:
            last_exc = exc
            continue
    if last_exc:
        raise last_exc
    raise ValueError("模型目录端点为空。")


def _provider_get_cached_model_json(url: str, api_key: str, timeout: float = 12.0) -> Dict[str, Any]:
    cache_key = _provider_probe_cache_key("models", url, api_key)
    now = time.monotonic()
    with _PROVIDER_PROBE_CACHE_LOCK:
        cached = _PROVIDER_MODEL_CACHE.get(cache_key)
        if cached and cached[0] > now:
            return copy.deepcopy(cached[1])

    payload = _provider_get_json(url, api_key, timeout)
    with _PROVIDER_PROBE_CACHE_LOCK:
        _PROVIDER_MODEL_CACHE[cache_key] = (
            time.monotonic() + _PROVIDER_MODEL_CACHE_TTL_SECONDS,
            copy.deepcopy(payload),
        )
    return payload


def _provider_probe_cache_key(kind: str, url: str, api_key: str) -> Tuple[str, str, str]:
    return (kind, str(url or "").strip(), _api_key_fingerprint(api_key))


def _api_key_fingerprint(api_key: str) -> str:
    value = str(api_key or "").strip()
    if not value:
        return "no-key"
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


def _clear_provider_probe_caches_for_tests() -> None:
    with _PROVIDER_PROBE_CACHE_LOCK:
        _PROVIDER_MODEL_CACHE.clear()


_KNOWN_MODEL_COMPAT_SUFFIXES = (
    "/api/claudecode",
    "/api/anthropic",
    "/apps/anthropic",
    "/api/coding",
    "/claudecode",
    "/anthropic",
    "/step_plan",
    "/coding",
    "/claude",
)


def _models_url_candidates(context: Dict[str, Any]) -> List[str]:
    base_url = str(context.get("base_url") or "").strip().rstrip("/")
    api_base_url = str(context.get("api_base_url") or "").strip().rstrip("/")
    provider_id = str(context.get("provider_id") or "")
    candidates: List[str] = []

    def add(url: str) -> None:
        value = str(url or "").strip().rstrip("/")
        if value and value not in candidates:
            candidates.append(value)

    if provider_id == "deepseek" and base_url:
        add(f"{base_url}/models")
        add(f"{base_url}/v1/models")
    elif api_base_url:
        add(f"{api_base_url}/models")

    if base_url:
        if _ends_with_version_segment(base_url):
            add(f"{base_url}/models")
            if not base_url.lower().endswith("/v1"):
                add(f"{base_url}/v1/models")
        elif not api_base_url:
            add(f"{base_url}/v1/models")

        stripped = _strip_model_compat_suffix(base_url)
        if stripped:
            root = stripped.rstrip("/")
            add(f"{root}/v1/models")
            add(f"{root}/models")

    return candidates


def _strip_model_compat_suffix(base_url: str) -> str:
    lower = base_url.lower().rstrip("/")
    for suffix in _KNOWN_MODEL_COMPAT_SUFFIXES:
        if lower.endswith(suffix):
            return base_url[: len(base_url) - len(suffix)]
    return ""


def _ends_with_version_segment(url: str) -> bool:
    path = urlparse(str(url or "")).path.rstrip("/")
    segment = path.rsplit("/", 1)[-1]
    digits = segment[1:] if segment.startswith("v") else ""
    return bool(digits) and digits.isdigit()


def _provider_preset_model_result(
    context: Dict[str, Any],
    *,
    status: str,
    ok: bool,
    message: str,
    hint: str,
    models_url: str = "",
) -> Optional[Dict[str, Any]]:
    models = _provider_preset_models(context)
    if not models:
        return None
    return _provider_probe_result(
        "models",
        status=status,
        ok=ok,
        context=context,
        message=message,
        hint=hint,
        models=models,
        models_url=models_url,
    )


def _provider_preset_models(context: Dict[str, Any]) -> List[Dict[str, Any]]:
    profile = context["profile"]
    model_ids: List[str] = []
    for model_id in (profile.default_model, *profile.fallback_models):
        value = str(model_id or "").strip()
        if value and value not in model_ids:
            model_ids.append(value)

    models: List[Dict[str, Any]] = []
    for model_id in model_ids:
        models.append(
            _model_catalog_item(
                {
                    "id": model_id,
                    "display_name": _preset_model_display_name(model_id),
                    "owned_by": profile.display_name,
                    "type": "chat",
                    "context_limit": profile.model_context_windows.get(model_id, context_limit(model_id)),
                }
            )
        )
    return models


def _ollama_provider_model_result(context: Dict[str, Any]) -> Dict[str, Any]:
    base_url = str(context.get("base_url") or "http://localhost:11434/v1")
    tags_url = f"{normalize_ollama_base_url(base_url)}/api/tags"
    raw_models = list_ollama_models(base_url)
    models = [
        _model_catalog_item(
            {
                "id": str(item.get("name") or ""),
                "display_name": str(item.get("name") or ""),
                "owned_by": "Ollama",
                "type": "chat",
                "context_limit": context_limit(str(item.get("name") or "")),
            }
        )
        for item in raw_models
        if item.get("name")
    ]
    if models:
        return _provider_probe_result(
            "models",
            status="ok",
            ok=True,
            context=context,
            message=f"已读取 {len(models)} 个本地 Ollama 模型。",
            hint="选择一个模型会写入当前设置；Ollama 不需要 API Key。",
            models=models,
            models_url=tags_url,
        )

    running = check_ollama_running(base_url)
    if running:
        return _provider_probe_result(
            "models",
            status="empty",
            ok=True,
            context=context,
            message="Ollama 正在运行，但尚未发现已安装模型。",
            hint="可以先运行 ollama pull <model>，或手动填写已有模型名。",
            models=[],
            models_url=tags_url,
        )

    preset = _provider_preset_models(context)
    return _provider_probe_result(
        "models",
        status="error",
        ok=False,
        context=context,
        message="未检测到 Ollama 本地服务。",
        hint="启动 Ollama 后刷新；也可以继续手动填写模型名。",
        models=preset,
        models_url=tags_url,
    )


def _preset_model_display_name(model_id: str) -> str:
    labels = {
        "deepseek-v4-flash": "DeepSeek V4 Flash",
        "deepseek-v4-pro": "DeepSeek V4 Pro",
        "kimi-k2.6": "Kimi K2.6",
        "glm-5.1": "GLM-5.1",
        "qwen3-coder-plus": "Qwen3 Coder Plus",
        "qwen3-max": "Qwen3 Max",
        "qwen2.5:7b": "Qwen2.5 7B (Ollama)",
        "gpt-4o-mini": "GPT-4o mini",
        "gpt-4o": "GPT-4o",
        "gpt-4.1-mini": "GPT-4.1 mini",
        "claude-sonnet-4-20250514": "Claude Sonnet 4",
        "claude-3-5-haiku-latest": "Claude 3.5 Haiku",
        "gemini-2.0-flash": "Gemini 2.0 Flash",
        "gemini-1.5-pro": "Gemini 1.5 Pro",
    }
    return labels.get(model_id, model_id)


def _model_catalog_item(item: Dict[str, Any]) -> Dict[str, Any]:
    model_id = str(item.get("id") or item.get("model") or item.get("name") or "").strip()
    model_type = str(item.get("type") or "").strip()
    display_name = str(item.get("display_name") or item.get("displayName") or model_id).strip()
    lowered_model_id = model_id.lower()
    non_chat_by_id = lowered_model_id.startswith(("gpt-image", "dall-e")) or any(
        marker in lowered_model_id
        for marker in ("embedding", "rerank", "moderation", "whisper", "tts")
    )
    chat_capable = (not model_type or model_type.lower() not in {"image", "embedding", "rerank", "audio"}) and not non_chat_by_id
    raw_context_limit = item.get("context_limit", item.get("contextWindow"))
    context_limit_value = raw_context_limit if isinstance(raw_context_limit, int) else context_limit(model_id)
    return {
        "id": model_id,
        "display_name": display_name or model_id,
        "owned_by": str(item.get("owned_by") or item.get("ownedBy") or "").strip(),
        "type": model_type or "chat",
        "created": item.get("created") if isinstance(item.get("created"), int) else 0,
        "context_limit": context_limit_value,
        "chat_capable": chat_capable,
    }


def _usage_url_for_context(context: Dict[str, Any]) -> str:
    provider_id = str(context.get("provider_id") or "")
    base_url = str(context.get("base_url") or "")
    api_base_url = str(context.get("api_base_url") or "")
    host = (urlparse(base_url).netloc or "").lower()
    if provider_id == "deepseek" or "api.deepseek.com" in host:
        return f"{base_url.rstrip('/')}/user/balance"
    if provider_id in {"openai", "openai-compatible", "custom-openai"} and api_base_url:
        return f"{api_base_url.rstrip('/')}/usage"
    return ""


def _parse_usage_payload(context: Dict[str, Any], payload: Dict[str, Any]) -> Dict[str, Any]:
    provider_id = str(context.get("provider_id") or "")
    if provider_id == "deepseek" or "api.deepseek.com" in str(context.get("base_url") or ""):
        return _parse_deepseek_usage(payload)
    return _parse_openai_compatible_usage(payload)


def _parse_deepseek_usage(payload: Dict[str, Any]) -> Dict[str, Any]:
    balances = payload.get("balance_infos") if isinstance(payload.get("balance_infos"), list) else []
    unit = ""
    remaining = 0.0
    for row in balances:
        if not isinstance(row, dict):
            continue
        currency = str(row.get("currency") or "").upper()
        total = _float_value(row.get("total_balance"))
        if currency == "CNY" or not unit:
            unit = currency or unit
            remaining = total
    available = bool(payload.get("is_available"))
    status = "ok" if available and remaining > 10 else "warning" if available and remaining > 0 else "danger"
    return {
        "status": status,
        "message": "DeepSeek 余额可用。" if available else "DeepSeek 余额不可用。",
        "hint": "这里只读取余额，不会发起模型调用。",
        "data": {
            "mode": "balance",
            "is_valid": available,
            "plan_name": "DeepSeek balance",
            "remaining": remaining,
            "balance": remaining,
            "unit": unit or "CNY",
            "today": {},
            "total": {},
            "quota": {},
        },
    }


def _parse_openai_compatible_usage(payload: Dict[str, Any]) -> Dict[str, Any]:
    quota = payload.get("quota") if isinstance(payload.get("quota"), dict) else {}
    subscription = payload.get("subscription") if isinstance(payload.get("subscription"), dict) else {}
    today = _usage_counter(payload.get("usage", {}).get("today") if isinstance(payload.get("usage"), dict) else payload.get("today"))
    total = _usage_counter(payload.get("usage", {}).get("total") if isinstance(payload.get("usage"), dict) else payload.get("total"))
    remaining = _first_float(payload.get("remaining"), quota.get("remaining"), subscription.get("remaining"))
    balance = _first_float(payload.get("balance"), payload.get("remaining"), quota.get("remaining"))
    unit = str(payload.get("unit") or quota.get("unit") or subscription.get("unit") or "").strip() or "USD"
    is_valid = bool(payload.get("isValid", payload.get("is_valid", True)))
    mode = str(payload.get("mode") or quota.get("mode") or "usage").strip()
    plan_name = str(payload.get("planName") or payload.get("plan_name") or subscription.get("planName") or "").strip()
    status = _usage_status(is_valid, remaining, balance, quota)
    return {
        "status": status,
        "message": "额度查询成功。" if status in {"ok", "warning"} else "额度不可用。",
        "hint": "钱包余额模式不伪造百分比，只显示金额和单位。",
        "data": {
            "mode": mode,
            "is_valid": is_valid,
            "plan_name": plan_name,
            "remaining": remaining,
            "balance": balance,
            "unit": unit,
            "today": today,
            "total": total,
            "quota": {
                "limit": _float_value(quota.get("limit")),
                "used": _float_value(quota.get("used")),
                "remaining": _float_value(quota.get("remaining")),
            } if quota else {},
        },
    }


def _usage_counter(value: Any) -> Dict[str, Any]:
    row = value if isinstance(value, dict) else {}
    return {
        "requests": int(_float_value(row.get("requests"))),
        "total_tokens": int(_float_value(row.get("total_tokens") or row.get("totalTokens"))),
        "cost": _float_value(row.get("cost")),
    }


def _usage_status(is_valid: bool, remaining: float, balance: float, quota: Dict[str, Any]) -> str:
    if not is_valid:
        return "danger"
    amount = remaining if remaining > 0 else balance
    if amount <= 0:
        return "danger"
    limit = _float_value(quota.get("limit")) if quota else 0.0
    used = _float_value(quota.get("used")) if quota else 0.0
    if limit > 0:
        ratio = max(0.0, min(1.0, (limit - used) / limit))
        if ratio <= 0.1:
            return "danger"
        if ratio <= 0.25:
            return "warning"
    elif amount < 1:
        return "warning"
    return "ok"


def _first_float(*values: Any) -> float:
    for value in values:
        result = _float_value(value)
        if result:
            return result
    return 0.0


def _float_value(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _safe_error_message(exc: Exception) -> str:
    text = sanitize_for_log(str(exc))
    return text[:500] or exc.__class__.__name__


def save_first_run_config(data: Dict[str, Any]) -> Dict[str, Any]:
    with _PROVIDER_SETTINGS_LOCK:
        data = _normalized_runtime_settings_update(data)
        api_key = _strip_config_whitespace(str(data.get("api_key") or ""))
        if _is_masked_api_key(api_key):
            file_values = _env_file_values()
            backend = str(data.get("backend") or "openai")
            api_key = _runtime_value("api_key", backend, file_values, "")
        if not api_key:
            raise ValueError("API key required")
        backend = str(data.get("backend") or "openai").strip()
        config_data = {
            "backend": backend,
            "provider_id": backend,
            "base_url": normalize_base_url(backend, str(data.get("base_url") or default_base_url(backend))),
            "api_key": api_key,
            "model": str(data.get("model") or default_model(backend)).strip(),
        }
        path = config_path()
        _atomic_write_json(path, config_data)

        os.environ["METIS_LLM_BACKEND"] = config_data["backend"]
        os.environ["METIS_LLM_BASE_URL"] = config_data["base_url"]
        os.environ["METIS_LLM_API_KEY"] = config_data["api_key"]
        os.environ["METIS_LLM_MODEL"] = config_data["model"]
        return {"ok": True, "config_path": path}


def context_limit(model: str = "") -> int:
    model_name = (model or env("METIS_LLM_MODEL", "MIRO_LLM_MODEL", "")).lower()
    for key, limit in _MODEL_CONTEXT_LIMITS.items():
        if key in model_name:
            return limit
    return _DEFAULT_CONTEXT_LIMIT


def compaction_stage(prompt_tokens: int, model: str = "") -> int:
    if prompt_tokens <= 0:
        return 0
    model_name = model or env("METIS_LLM_MODEL", "MIRO_LLM_MODEL", "")
    capabilities = detect_from_model_name(model_name)
    stage_1, stage_2, stage_3 = tier_compact_thresholds(capabilities.tier)
    ratio = prompt_tokens / context_limit(model_name)
    if ratio > stage_3:
        return 3
    if ratio > stage_2:
        return 2
    if ratio > stage_1:
        return 1
    return 0


def should_auto_compact(prompt_tokens: int, model: str = "") -> bool:
    return compaction_stage(prompt_tokens, model) >= 1

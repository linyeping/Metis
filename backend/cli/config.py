from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Any, Dict, Mapping

from backend.core.credential_store import CredentialStoreError, read_api_key
from backend.core.paths import metis_home

from .args import ParsedCliArgs

_SETTING_ENV = {
    "backend": "METIS_LLM_BACKEND",
    "base_url": "METIS_LLM_BASE_URL",
    "api_key": "METIS_LLM_API_KEY",
    "model": "METIS_LLM_MODEL",
    "temperature": "METIS_TEMPERATURE",
    "reasoning_effort": "METIS_REASONING_EFFORT",
    "max_tokens": "METIS_MAX_TOKENS",
    "max_turns": "METIS_MAX_TURNS",
    "timeout": "METIS_LLM_TIMEOUT",
    "proxy_mode": "METIS_PROXY_MODE",
    "proxy_scheme": "METIS_PROXY_SCHEME",
    "proxy_host": "METIS_PROXY_HOST",
    "proxy_port": "METIS_PROXY_PORT",
    "proxy_bypass": "METIS_PROXY_BYPASS",
}

_ENV_OVERRIDE_KEYS = tuple(
    dict.fromkeys(
        [
            *_SETTING_ENV.values(),
            "MIRO_LLM_BACKEND",
            "MIRO_LLM_BASE_URL",
            "MIRO_LLM_API_KEY",
            "MIRO_LLM_MODEL",
            "DEEPSEEK_BASE_URL",
            "DEEPSEEK_API_URL",
            "DEEPSEEK_API_KEY",
            "DEEPSEEK_CHAT_MODEL",
            "OPENAI_BASE_URL",
            "OPENAI_API_KEY",
            "OPENAI_MODEL",
            "ANTHROPIC_API_KEY",
            "ANTHROPIC_MODEL",
            "GEMINI_API_KEY",
            "GEMINI_MODEL",
        ]
    )
)


class CliConfigError(ValueError):
    pass


def build_cli_runtime(args: ParsedCliArgs, *, workspace: Path, session_id: str) -> tuple[Any, Any]:
    """Build AgentConfig and ToolRegistry using CLI > env > workspace > user precedence."""
    settings = merged_settings(args, workspace=workspace)
    _apply_settings_environment(settings)
    if args.no_desktop:
        os.environ["METIS_DISABLE_DESKTOP_TOOLS"] = "1"
    if args.no_mcp:
        os.environ["METIS_DISABLE_MCP"] = "1"

    # Deliberately lazy: `metis --help` and `--version` must not initialize the
    # agent/tool stack or print import-time diagnostics into machine stdout.
    from backend.runtime.tool_registry import get_registry
    from backend.web.llm_state import build_agent_config

    permission_mode = resolve_permission_mode(args, settings)
    config = build_agent_config(
        system_prompt=_load_system_prompt(),
        execution_mode=permission_mode,
        workspace_root=str(workspace),
        load_persistent=False,
    )
    allowed_tools = parse_allowed_tools(args.allowed_tools)
    config = replace(
        config,
        max_turns=args.max_turns or config.max_turns,
        enabled_tools=allowed_tools,
        execution_mode=permission_mode,
        workspace_root=str(workspace),
        source_workspace_root=str(workspace),
        session_id=session_id,
        surface_mode="code",
        requested_model=config.llm_model,
    )
    registry = get_registry(
        include_desktop=not args.no_desktop,
        include_mcp=not args.no_mcp,
    )
    return config, registry


def merged_settings(args: ParsedCliArgs, *, workspace: Path) -> Dict[str, Any]:
    explicit_home = str(os.environ.get("METIS_HOME") or "").strip()
    user_root = Path(explicit_home).expanduser().resolve(strict=False) if explicit_home else metis_home()
    merged: Dict[str, Any] = {}
    for path in (
        user_root / "config.json",
        user_root / "settings.json",
        workspace / ".metis" / "settings.json",
    ):
        merged.update(_read_settings(path))

    # Capture the caller's environment after reading files so it remains above
    # both user and project configuration.  The list is ordered from canonical
    # METIS names to compatibility aliases, so preserve the first value mapped
    # to each setting instead of letting a provider-specific alias overwrite it.
    environment_settings: Dict[str, Any] = {}
    for key in _ENV_OVERRIDE_KEYS:
        value = os.environ.get(key)
        if value not in (None, ""):
            environment_settings.setdefault(_setting_key_for_env(key), value)
    merged.update(environment_settings)

    # Credential Manager is the final user-level fallback. Explicit process
    # environment and both project/user settings retain their documented
    # precedence, while the standalone EXE can share the desktop credential.
    if not merged.get("api_key"):
        try:
            stored_api_key = read_api_key()
        except CredentialStoreError:
            stored_api_key = None
        if stored_api_key:
            merged["api_key"] = stored_api_key

    if args.backend:
        merged["backend"] = args.backend
    if args.base_url:
        merged["base_url"] = args.base_url
    if args.model:
        merged["model"] = args.model
    if args.max_turns is not None:
        merged["max_turns"] = args.max_turns
    if args.permission_mode:
        merged["permission_mode"] = args.permission_mode
    return merged


def resolve_permission_mode(args: ParsedCliArgs, settings: Mapping[str, Any]) -> str:
    explicit = str(args.permission_mode or settings.get("permission_mode") or "").strip()
    if explicit:
        return explicit
    return "ask" if _truthy(os.environ.get("CI")) else "auto_guard"


def parse_allowed_tools(value: str) -> list[str]:
    tools: list[str] = []
    seen: set[str] = set()
    for raw in str(value or "").split(","):
        tool = raw.strip()
        if not tool or tool in seen:
            continue
        seen.add(tool)
        tools.append(tool)
    return tools


def _read_settings(path: Path) -> Dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CliConfigError(f"invalid settings file {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise CliConfigError(f"settings file must contain a JSON object: {path}")
    return {str(key): value for key, value in data.items()}


def _apply_settings_environment(settings: Mapping[str, Any]) -> None:
    for key, env_key in _SETTING_ENV.items():
        value = settings.get(key)
        if value in (None, ""):
            continue
        os.environ[env_key] = str(value).strip()


def _setting_key_for_env(env_key: str) -> str:
    upper = env_key.upper()
    if upper.endswith("LLM_BACKEND"):
        return "backend"
    if "BASE_URL" in upper or upper.endswith("API_URL"):
        return "base_url"
    if "API_KEY" in upper:
        return "api_key"
    if upper.endswith("MODEL"):
        return "model"
    reverse = {value: key for key, value in _SETTING_ENV.items()}
    return reverse.get(env_key, env_key.lower())


def _load_system_prompt() -> str:
    path = Path(__file__).resolve().parents[1] / "core" / "prompts" / "MAIN_PROMPT.txt"
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CliConfigError(f"Metis system prompt is missing: {path}") from exc


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}

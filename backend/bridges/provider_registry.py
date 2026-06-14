"""Provider registry and config normalization for Metis LLM backends."""

from __future__ import annotations

import logging
import os
from typing import Any, Optional
from urllib.parse import urlparse

from .provider_contract import ProviderId, ProviderProfile, ProviderRegistryError
from .provider_profiles import builtin_provider_profiles


_PROFILES: dict[str, ProviderProfile] = {}
_ALIASES: dict[str, str] = {}
_ACTIVE_WORKSPACE_ROOT: str = ""
_BUILTIN_PROVIDER_IDS = frozenset(str(profile.provider_id) for profile in builtin_provider_profiles())


def _rebuild_registry(workspace_root: str = "") -> None:
    """FABLEADV-15: rebuild the provider registry by merging builtin defaults
    with user-defined providers.json (global + project). Later entries override
    earlier ones by provider id. Builtin always remains as the fallback layer."""
    profiles: dict[str, ProviderProfile] = {
        str(profile.provider_id): profile for profile in builtin_provider_profiles()
    }
    try:
        from .provider_user_config import load_user_provider_profiles

        for profile in load_user_provider_profiles(workspace_root):
            profiles[str(profile.provider_id)] = profile
    except Exception:  # never let bad user config break startup
        logging.getLogger(__name__).warning("provider user config merge failed; using builtin defaults", exc_info=True)

    aliases: dict[str, str] = {}
    for profile in profiles.values():
        for alias in profile.aliases:
            aliases[alias.lower()] = str(profile.provider_id)

    _PROFILES.clear()
    _PROFILES.update(profiles)
    _ALIASES.clear()
    _ALIASES.update(aliases)


def reload_provider_registry(workspace_root: str = "") -> int:
    """Re-read providers.json (global + project) and rebuild the registry.
    Returns the number of providers now registered. No restart required."""
    global _ACTIVE_WORKSPACE_ROOT
    _ACTIVE_WORKSPACE_ROOT = str(workspace_root or "")
    _rebuild_registry(_ACTIVE_WORKSPACE_ROOT)
    return len(_PROFILES)


_rebuild_registry()


def list_provider_profiles() -> list[ProviderProfile]:
    return list(_PROFILES.values())


def available_provider_ids() -> tuple[str, ...]:
    return tuple(sorted(_PROFILES))


def is_builtin_provider_id(provider_id_or_alias: str) -> bool:
    key = _normalize_key(provider_id_or_alias)
    if key in _BUILTIN_PROVIDER_IDS:
        return True
    return _ALIASES.get(key, "") in _BUILTIN_PROVIDER_IDS


def resolve_provider_id(provider_id_or_alias: str) -> ProviderId:
    key = _normalize_key(provider_id_or_alias)
    if key in _PROFILES:
        return ProviderId(key)
    if key in _ALIASES:
        return ProviderId(_ALIASES[key])
    choices = ", ".join(available_provider_ids())
    raise ProviderRegistryError(f"Unknown provider: {provider_id_or_alias}. Choose from: {choices}")


def get_provider_profile(provider_id_or_alias: str) -> ProviderProfile:
    return _PROFILES[str(resolve_provider_id(provider_id_or_alias))]


def resolve_provider_for_config(
    provider_id_or_alias: str = "",
    *,
    base_url: str = "",
    model: str = "",
) -> ProviderProfile:
    """Resolve old backend values plus URL/model clues into a provider profile."""
    key = _normalize_key(provider_id_or_alias)
    base = str(base_url or "").strip().lower()
    model_name = str(model or "").strip().lower()

    if key in {"", "openai"}:
        if _looks_like_ollama(base):
            return get_provider_profile("ollama")
        if _looks_like_doubao(base, model_name):
            return get_provider_profile("doubao")
        if _looks_like_deepseek(base, model_name):
            return get_provider_profile("deepseek")
        if key == "openai" and base and "api.openai.com" not in base:
            return get_provider_profile("openai-compatible")
        return get_provider_profile("openai")

    return get_provider_profile(key)


def normalize_chat_completions_url(
    base_url: str,
    path: str = "/chat/completions",
) -> str:
    base = normalize_openai_api_base_url(base_url)
    if not base:
        raise ProviderRegistryError("OpenAI-compatible provider requires a non-empty base_url")

    normalized_path = "/" + str(path or "/chat/completions").strip("/")
    base = base.rstrip("/")
    if base.lower().endswith(normalized_path.lower()):
        return base
    return f"{base}{normalized_path}"


def normalize_openai_api_base_url(base_url: str) -> str:
    """Normalize OpenAI-compatible API roots without breaking DeepSeek root URLs."""
    base = str(base_url or "").strip().rstrip("/")
    if not base:
        return ""

    lower = base.lower()
    for suffix in ("/chat/completions", "/models", "/usage"):
        if lower.endswith(suffix):
            base = base[: -len(suffix)].rstrip("/")
            lower = base.lower()
            break

    parsed = urlparse(base)
    host = (parsed.netloc or "").lower()
    path = (parsed.path or "").rstrip("/").lower()
    if "api.deepseek.com" in host:
        return base
    if _ends_with_version_segment(path) or "/v1/" in path:
        return base
    return f"{base}/v1"


def provider_profile_payload(profile: ProviderProfile) -> dict[str, Any]:
    capabilities = {
        "stream": profile.supports_stream,
        "tools": profile.supports_tools,
        "vision": profile.supports_vision,
        "parallel_tool_calls": profile.parallel_tool_calls,
        "requires_reasoning_passback": profile.requires_reasoning_passback,
    }
    return {
        "provider_id": str(profile.provider_id),
        "display_name": profile.display_name,
        "backend_type": profile.backend_type,
        "aliases": list(profile.aliases),
        "base_url": profile.base_url,
        "chat_completions_path": profile.chat_completions_path,
        "default_model": profile.default_model,
        "fallback_models": list(profile.fallback_models),
        "api_key_required": profile.api_key_required,
        "openai_compatible": profile.openai_compatible,
        "capabilities": capabilities,
        "model_context_windows": dict(profile.model_context_windows),
        "model_notes": dict(profile.model_notes),
        "parallel_tool_calls": profile.parallel_tool_calls,
        "requires_reasoning_passback": profile.requires_reasoning_passback,
    }


def parallel_tool_calls_enabled(
    provider_id_or_alias: str = "",
    *,
    base_url: str = "",
    model: str = "",
) -> bool:
    override = os.environ.get("METIS_PARALLEL_TOOLCALLS", "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False

    profile = resolve_provider_for_config(provider_id_or_alias, base_url=base_url, model=model)
    name = str(model or profile.default_model or "").strip().lower()
    override = _conformance_bool(str(profile.provider_id), name, "parallel_tool_calls")
    if override is not None:
        return override
    if str(profile.provider_id) in {"openai-compatible", "custom-openai"}:
        if name.startswith(("gpt-", "o1", "o3", "o4", "o5", "chatgpt", "codex")):
            return True
        if name.startswith("deepseek"):
            return False
    return bool(profile.parallel_tool_calls)


def requires_reasoning_passback_enabled(
    provider_id_or_alias: str = "",
    *,
    base_url: str = "",
    model: str = "",
) -> bool:
    override = os.environ.get("METIS_REASONING_PASSBACK", "").strip().lower()
    if override in {"1", "true", "yes", "on"}:
        return True
    if override in {"0", "false", "no", "off"}:
        return False

    profile = resolve_provider_for_config(provider_id_or_alias, base_url=base_url, model=model)
    name = str(model or profile.default_model or "").strip().lower()
    conformance = _conformance_bool(str(profile.provider_id), name, "requires_reasoning_passback")
    if conformance is not None:
        return conformance
    return bool(profile.requires_reasoning_passback)


def _conformance_bool(provider_id: str, model: str, field: str) -> Optional[bool]:
    try:
        from backend.runtime.provider_conformance import load_provider_conformance
    except Exception:
        return None
    result = load_provider_conformance(provider_id, model)
    if not result:
        return None
    value = result.get(field)
    return value if isinstance(value, bool) else None


def normalize_provider_model(profile: ProviderProfile, model: str) -> tuple[str, Optional[str]]:
    """Return a provider-safe model plus an optional warning.

    The repair is intentionally conservative: unknown names are allowed, but
    obvious cross-provider IDs such as DeepSeek + gpt-4o are repaired to the
    profile default so stale environment/config values cannot poison runtime.
    """
    value = str(model or "").strip()
    if not value:
        return str(profile.default_model or "").strip(), None
    if not _looks_like_foreign_model(profile, value):
        return value, None
    repaired = str(profile.default_model or "").strip()
    if not repaired:
        return value, None
    return repaired, f"模型 {value} 与 {profile.display_name} 不匹配，已切换为 {repaired}。"


def list_provider_payloads() -> list[dict[str, Any]]:
    return [provider_profile_payload(profile) for profile in list_provider_profiles()]


def validate_provider_config(
    provider_id_or_alias: str = "",
    *,
    base_url: str = "",
    model: str = "",
    api_key: str = "",
    masked_api_key_fallback: str = "",
) -> dict[str, Any]:
    """Validate provider settings locally without making a network request."""
    profile = resolve_provider_for_config(provider_id_or_alias, base_url=base_url, model=model)
    resolved_base_url = str(base_url or profile.base_url or "").strip().rstrip("/")
    raw_model = str(model or profile.default_model or "").strip()
    resolved_model, model_warning = normalize_provider_model(profile, raw_model)
    provided_key = str(api_key or "").strip()
    has_api_key = bool(provided_key and not _is_masked_api_key(provided_key))
    if _is_masked_api_key(provided_key):
        has_api_key = bool(str(masked_api_key_fallback or "").strip())

    warnings: list[str] = []
    if model_warning:
        warnings.append(model_warning)
    chat_url = ""
    if profile.openai_compatible:
        try:
            chat_url = normalize_chat_completions_url(
                resolved_base_url,
                profile.chat_completions_path,
            )
        except ProviderRegistryError as exc:
            return _validation_error(
                profile,
                code="LLM_ENDPOINT_NOT_FOUND",
                title="模型接口地址不正确",
                message=str(exc),
                hint="请填写供应商的根地址，例如 DeepSeek 使用 https://api.deepseek.com。",
                base_url=resolved_base_url,
                model=resolved_model,
                has_api_key=has_api_key,
                chat_url="",
                warnings=warnings,
            )

    if profile.api_key_required and not has_api_key:
        return _validation_error(
            profile,
            code="LLM_API_KEY_MISSING",
            title="未配置 API Key",
            message="当前模型供应商需要 API Key。",
            hint="请在设置或首次引导中填入供应商提供的 API Key；本地检查不会保存空 Key。",
            base_url=resolved_base_url,
            model=resolved_model,
            has_api_key=False,
            chat_url=chat_url,
            warnings=warnings,
        )

    if not resolved_model:
        return _validation_error(
            profile,
            code="LLM_ERROR",
            title="模型未配置",
            message="当前供应商需要模型名。",
            hint="选择一个预设模型，或填写供应商文档中的模型名。",
            base_url=resolved_base_url,
            model=resolved_model,
            has_api_key=has_api_key,
            chat_url=chat_url,
            warnings=warnings,
        )

    note = profile.model_notes.get(resolved_model)
    if note:
        warnings.append(note)

    return {
        "ok": True,
        "code": "PROVIDER_CONFIG_OK",
        "title": "配置检查通过",
        "message": "本次只检查本地配置，没有发起真实模型调用。",
        "hint": "真实网络、余额和模型权限会在发送消息时由流式错误分类继续提示。",
        "recoverable": False,
        "provider": provider_profile_payload(profile),
        "provider_id": str(profile.provider_id),
        "display_name": profile.display_name,
        "backend": profile.backend_type,
        "base_url": resolved_base_url,
        "chat_url": chat_url,
        "model": resolved_model,
        "api_key_required": profile.api_key_required,
        "has_api_key": has_api_key,
        "warnings": warnings,
    }


def build_backend_kwargs(provider_id_or_alias: str, **kwargs: Any) -> tuple[str, dict[str, Any]]:
    profile = get_provider_profile(provider_id_or_alias)
    if profile.backend_type == "fake":
        return "fake", dict(kwargs)

    normalized = dict(kwargs)
    model = str(normalized.get("model") or profile.default_model or "").strip()
    if model:
        normalized["model"] = model

    if profile.openai_compatible:
        base_url = str(normalized.get("base_url") or profile.base_url or "").strip()
        if not base_url:
            raise ProviderRegistryError(
                f"Provider {profile.provider_id} requires base_url for OpenAI-compatible chat completions"
            )
        normalized["base_url"] = base_url.rstrip("/")
        normalized.setdefault("api_key", "")

    if profile.api_key_required and "api_key" not in normalized:
        normalized["api_key"] = ""

    return profile.backend_type, normalized


def _normalize_key(value: str) -> str:
    return str(value or "").strip().lower()


def _looks_like_deepseek(base_url: str, model: str) -> bool:
    return "api.deepseek.com" in base_url or model.startswith("deepseek")


def _looks_like_doubao(base_url: str, model: str) -> bool:
    return "volces.com" in base_url or "volcengine" in base_url or "doubao" in model


def _looks_like_ollama(base_url: str) -> bool:
    return "localhost:11434" in base_url or "127.0.0.1:11434" in base_url


def _looks_like_foreign_model(profile: ProviderProfile, model: str) -> bool:
    provider_id = str(profile.provider_id)
    name = str(model or "").strip().lower()
    if not name:
        return False
    local_models = {
        str(model_id).lower()
        for model_id in (profile.default_model, *profile.fallback_models, *profile.model_context_windows.keys())
        if model_id
    }
    if name in local_models:
        return False

    families = {
        "deepseek": ("deepseek",),
        "openai": ("gpt-", "o1", "o3", "o4", "o5", "chatgpt", "codex"),
        "openai-compatible": (),
        "custom-openai": (),
        "kimi": ("kimi", "moonshot"),
        "zhipu-glm": ("glm", "zhipu"),
        "bailian": ("qwen", "dashscope"),
        "doubao": ("doubao",),
        "ollama": (),
        "anthropic": ("claude",),
        "gemini": ("gemini",),
    }
    own_prefixes = families.get(provider_id, ())
    if own_prefixes and name.startswith(own_prefixes):
        return False
    if provider_id in {"openai-compatible", "custom-openai", "fake"}:
        return False
    other_prefixes = tuple(
        prefix
        for key, prefixes in families.items()
        if key != provider_id and key not in {"openai-compatible", "custom-openai", "ollama"}
        for prefix in prefixes
    )
    return name.startswith(other_prefixes)


def _ends_with_version_segment(path: str) -> bool:
    segment = str(path or "").rstrip("/").rsplit("/", 1)[-1]
    digits = segment[1:] if segment.startswith("v") else ""
    return bool(digits) and digits.isdigit()


def _is_masked_api_key(value: str) -> bool:
    value = str(value or "").strip()
    return value == "***" or "****" in value


def _validation_error(
    profile: ProviderProfile,
    *,
    code: str,
    title: str,
    message: str,
    hint: str,
    base_url: str,
    model: str,
    has_api_key: bool,
    chat_url: str,
    warnings: Optional[list[str]] = None,
) -> dict[str, Any]:
    return {
        "ok": False,
        "code": code,
        "title": title,
        "message": message,
        "error": message,
        "hint": hint,
        "recoverable": code not in {"LLM_API_KEY_MISSING"},
        "provider": provider_profile_payload(profile),
        "provider_id": str(profile.provider_id),
        "display_name": profile.display_name,
        "backend": profile.backend_type,
        "base_url": base_url,
        "chat_url": chat_url,
        "model": model,
        "api_key_required": profile.api_key_required,
        "has_api_key": has_api_key,
        "warnings": warnings or [],
    }

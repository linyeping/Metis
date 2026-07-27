from __future__ import annotations

import importlib
import json
import os
import threading
from dataclasses import dataclass
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any, Mapping

from backend.core.paths import metis_path

try:
    _tomllib = importlib.import_module("tomllib")
except ModuleNotFoundError:  # pragma: no cover - Python 3.9/3.10
    _tomllib = importlib.import_module("tomli")


MODEL_PROFILE_SCHEMA_VERSION = 1
DEFAULT_CONTEXT_WINDOW = 128_000
DEFAULT_MAX_OUTPUT_TOKENS = 32_768
_LOCK = threading.RLock()


@dataclass(frozen=True)
class ResolvedModelProfile:
    model: str
    matched_model: str
    context_window: int
    max_output_tokens: int
    compact_thresholds: tuple[float, float, float]
    source: str
    source_path: str
    is_estimate: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "matched_model": self.matched_model,
            "context_window": self.context_window,
            "max_output_tokens": self.max_output_tokens,
            "compact_thresholds": list(self.compact_thresholds),
            "source": self.source,
            "source_label": model_profile_source_label(self.source),
            "source_path": self.source_path,
            "is_estimate": self.is_estimate,
        }


@dataclass(frozen=True)
class _ProfileRule:
    model: str
    context_window: int
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    compact_thresholds: tuple[float, float, float] | None = None
    is_estimate: bool = False


_CURATED_RULES: tuple[_ProfileRule, ...] = (
    _ProfileRule("deepseek-v4-flash", 1_000_000, 65_536),
    _ProfileRule("deepseek-v4-pro", 1_000_000, 65_536),
    _ProfileRule("deepseek-chat", 128_000, 8_192),
    _ProfileRule("deepseek-coder", 128_000, 8_192),
    _ProfileRule("deepseek-reasoner", 128_000, 64_000),
    _ProfileRule("gpt-4o", 128_000, 16_384),
    _ProfileRule("gpt-4o-mini", 128_000, 16_384),
    _ProfileRule("gpt-4-turbo", 128_000, 4_096),
    _ProfileRule("gpt-4.1", 1_047_576, 32_768),
    _ProfileRule("gpt-4.1-mini", 1_047_576, 32_768),
    _ProfileRule("o3", 200_000, 100_000),
    _ProfileRule("o3-mini", 200_000, 100_000),
    _ProfileRule("o4-mini", 200_000, 100_000),
    _ProfileRule("gpt-5.4", 1_000_000, 128_000),
    _ProfileRule("gpt-5.4-mini", 1_000_000, 128_000),
    _ProfileRule("gpt-5.5", 1_000_000, 128_000),
    _ProfileRule("codex-auto-review", 1_000_000, 128_000),
    _ProfileRule("claude-sonnet-4-20250514", 200_000, 64_000),
    _ProfileRule("claude-opus-4-20250514", 200_000, 64_000),
    _ProfileRule("claude-3-5-sonnet", 200_000, 8_192),
    _ProfileRule("claude-3-5-haiku-latest", 200_000, 8_192),
    _ProfileRule("gemini-2.0-flash", 1_000_000, 8_192),
    _ProfileRule("gemini-1.5-pro", 2_000_000, 8_192),
    _ProfileRule("qwen3-coder-plus", 1_000_000, 32_768),
    _ProfileRule("qwen3-max", 262_144, 32_768),
    _ProfileRule("kimi-k2.6", 262_144, 32_768),
    _ProfileRule("glm-5.1", 200_000, 32_768),
    _ProfileRule("qwen2.5:7b", 32_768, 8_192),
)

_ESTIMATED_FAMILY_RULES: tuple[_ProfileRule, ...] = (
    _ProfileRule("deepseek-v4*", 1_000_000, 65_536, is_estimate=True),
    _ProfileRule("gpt-4.1*", 1_047_576, 32_768, is_estimate=True),
    _ProfileRule("gpt-5.4*", 1_000_000, 128_000, is_estimate=True),
    _ProfileRule("gpt-5.5*", 1_000_000, 128_000, is_estimate=True),
    _ProfileRule("claude-*", 200_000, 64_000, is_estimate=True),
    _ProfileRule("gemini-2*", 1_000_000, 8_192, is_estimate=True),
    _ProfileRule("o3*", 200_000, 100_000, is_estimate=True),
    _ProfileRule("o4*", 200_000, 100_000, is_estimate=True),
)


def models_toml_path() -> Path:
    return metis_path("models.toml")


def compact_thresholds_for_tier(tier: int) -> tuple[float, float, float]:
    if tier == 1:
        return (0.65, 0.82, 0.93)
    if tier == 3:
        return (0.50, 0.70, 0.85)
    return (0.60, 0.80, 0.92)


def resolve_model_profile(model_name: str, *, tier: int = 2) -> ResolvedModelProfile:
    model = str(model_name or "").strip()
    normalized = model.lower()
    thresholds = compact_thresholds_for_tier(tier)
    path = models_toml_path()
    user_rules = load_user_model_profiles()

    exact = user_rules.get(normalized)
    if exact is not None:
        return _resolved(model, exact, "user", str(path), thresholds)
    for key in sorted((key for key in user_rules if _is_pattern(key)), key=len, reverse=True):
        if fnmatchcase(normalized, key):
            return _resolved(model, user_rules[key], "user", str(path), thresholds)

    for rule in _CURATED_RULES:
        if normalized == rule.model:
            return _resolved(model, rule, "builtin", "", thresholds)
    for rule in _ESTIMATED_FAMILY_RULES:
        if fnmatchcase(normalized, rule.model):
            return _resolved(model, rule, "builtin_estimate", "", thresholds)

    fallback = _ProfileRule(
        model=normalized or "*",
        context_window=DEFAULT_CONTEXT_WINDOW,
        max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
        compact_thresholds=thresholds,
        is_estimate=True,
    )
    return _resolved(model, fallback, "default", "", thresholds)


def load_user_model_profiles() -> dict[str, _ProfileRule]:
    path = models_toml_path()
    if not path.is_file():
        return {}
    try:
        parsed = _tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, _tomllib.TOMLDecodeError):
        return {}
    models = parsed.get("models") if isinstance(parsed, Mapping) else None
    if not isinstance(models, Mapping):
        return {}
    result: dict[str, _ProfileRule] = {}
    for raw_model, raw_profile in models.items():
        if not isinstance(raw_profile, Mapping):
            continue
        try:
            rule = _profile_rule(str(raw_model), raw_profile)
        except ValueError:
            continue
        result[rule.model] = rule
    return result


def list_user_model_profiles() -> list[dict[str, Any]]:
    profiles = load_user_model_profiles()
    return [
        {
            "model": rule.model,
            "context_window": rule.context_window,
            "max_output_tokens": rule.max_output_tokens,
            "compact_thresholds": list(rule.compact_thresholds or compact_thresholds_for_tier(2)),
        }
        for rule in sorted(profiles.values(), key=lambda item: item.model)
    ]


def save_user_model_profile(data: Mapping[str, Any]) -> ResolvedModelProfile:
    model = _normalize_model_key(data.get("model"))
    rule = _profile_rule(model, data)
    with _LOCK:
        profiles = load_user_model_profiles()
        profiles[model] = rule
        _write_user_profiles(profiles)
    return resolve_model_profile(model)


def delete_user_model_profile(model_name: str) -> bool:
    model = _normalize_model_key(model_name)
    with _LOCK:
        profiles = load_user_model_profiles()
        removed = profiles.pop(model, None) is not None
        if removed:
            _write_user_profiles(profiles)
    return removed


def model_profiles_payload(model_name: str = "", *, tier: int = 2) -> dict[str, Any]:
    return {
        "schema": "metis.model_profiles.v1",
        "version": MODEL_PROFILE_SCHEMA_VERSION,
        "path": str(models_toml_path()),
        "resolved": resolve_model_profile(model_name, tier=tier).to_dict(),
        "overrides": list_user_model_profiles(),
    }


def model_profile_source_label(source: str) -> str:
    return {
        "user": "用户配置",
        "builtin": "内置模型资料",
        "builtin_estimate": "内置家族估算",
        "default": "未确认默认值",
    }.get(str(source or ""), "未确认默认值")


def _resolved(
    model: str,
    rule: _ProfileRule,
    source: str,
    source_path: str,
    default_thresholds: tuple[float, float, float],
) -> ResolvedModelProfile:
    return ResolvedModelProfile(
        model=model,
        matched_model=rule.model,
        context_window=rule.context_window,
        max_output_tokens=rule.max_output_tokens,
        compact_thresholds=rule.compact_thresholds or default_thresholds,
        source=source,
        source_path=source_path,
        is_estimate=rule.is_estimate or source in {"builtin_estimate", "default"},
    )


def _profile_rule(model: str, data: Mapping[str, Any]) -> _ProfileRule:
    normalized = _normalize_model_key(model)
    context_window = _bounded_int(data.get("context_window", data.get("contextWindow")), "context_window", 4_096, 20_000_000)
    max_output = _bounded_int(
        data.get("max_output_tokens", data.get("maxOutputTokens", DEFAULT_MAX_OUTPUT_TOKENS)),
        "max_output_tokens",
        256,
        context_window,
    )
    raw_thresholds = data.get("compact_thresholds", data.get("compactThresholds"))
    if raw_thresholds is None:
        raw_thresholds = (
            data.get("compact_stage_1", 0.60),
            data.get("compact_stage_2", 0.80),
            data.get("compact_stage_3", 0.92),
        )
    if not isinstance(raw_thresholds, (list, tuple)) or len(raw_thresholds) != 3:
        raise ValueError("compact_thresholds must contain exactly three numbers")
    try:
        threshold_values = tuple(float(value) for value in raw_thresholds)
    except (TypeError, ValueError) as exc:
        raise ValueError("compact_thresholds must contain numbers") from exc
    thresholds = (threshold_values[0], threshold_values[1], threshold_values[2])
    if not (0.10 <= thresholds[0] < thresholds[1] < thresholds[2] <= 0.99):
        raise ValueError("compact_thresholds must increase between 0.10 and 0.99")
    return _ProfileRule(
        model=normalized,
        context_window=context_window,
        max_output_tokens=max_output,
        compact_thresholds=thresholds,
    )


def _normalize_model_key(value: Any) -> str:
    model = str(value or "").strip().lower()
    if not model or len(model) > 160 or any(char in model for char in "\r\n\t"):
        raise ValueError("model is required and must be at most 160 characters")
    return model


def _bounded_int(value: Any, name: str, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if parsed < minimum or parsed > maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _write_user_profiles(profiles: Mapping[str, _ProfileRule]) -> None:
    path = models_toml_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        f"version = {MODEL_PROFILE_SCHEMA_VERSION}",
        "",
        "# User overrides take precedence over Metis built-in model profiles.",
        "# Model keys may be exact ids or shell-style patterns such as \"vendor-model-*\".",
    ]
    for model in sorted(profiles):
        rule = profiles[model]
        thresholds = rule.compact_thresholds or compact_thresholds_for_tier(2)
        lines.extend(
            [
                "",
                f"[models.{json.dumps(model, ensure_ascii=False)}]",
                f"context_window = {rule.context_window}",
                f"max_output_tokens = {rule.max_output_tokens}",
                "compact_thresholds = [" + ", ".join(f"{value:.2f}" for value in thresholds) + "]",
            ]
        )
    content = "\n".join(lines).rstrip() + "\n"
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _is_pattern(value: str) -> bool:
    return "*" in value or "?" in value or "[" in value


__all__ = [
    "DEFAULT_CONTEXT_WINDOW",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "MODEL_PROFILE_SCHEMA_VERSION",
    "ResolvedModelProfile",
    "compact_thresholds_for_tier",
    "delete_user_model_profile",
    "list_user_model_profiles",
    "load_user_model_profiles",
    "model_profiles_payload",
    "models_toml_path",
    "resolve_model_profile",
    "save_user_model_profile",
]

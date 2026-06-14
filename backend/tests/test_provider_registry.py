from __future__ import annotations

import pytest

from backend.bridges.provider_contract import ProviderRegistryError
from backend.bridges.provider_registry import (
    build_backend_kwargs,
    get_provider_profile,
    list_provider_profiles,
    normalize_chat_completions_url,
    resolve_provider_for_config,
    resolve_provider_id,
    validate_provider_config,
)
from backend.runtime.llm_backends import AnthropicBackend, GeminiBackend, OpenAICompatBackend, get_backend


def test_builtin_provider_profiles_are_registered() -> None:
    provider_ids = {str(profile.provider_id) for profile in list_provider_profiles()}
    assert {
        "fake",
        "deepseek",
        "openai",
        "openai-compatible",
        "custom-openai",
        "kimi",
        "zhipu-glm",
        "bailian",
        "doubao",
        "ollama",
        "anthropic",
        "gemini",
    } <= provider_ids


def test_alias_resolution() -> None:
    assert str(resolve_provider_id("ds")) == "deepseek"
    assert str(resolve_provider_id("deepseek-chat")) == "deepseek"
    assert str(resolve_provider_id("openai_compat")) == "openai-compatible"
    assert str(resolve_provider_id("custom-openai")) == "custom-openai"
    assert str(resolve_provider_id("moonshot")) == "kimi"
    assert str(resolve_provider_id("glm")) == "zhipu-glm"
    assert str(resolve_provider_id("qwen")) == "bailian"
    assert str(resolve_provider_id("ark")) == "doubao"
    assert str(resolve_provider_id("local-ollama")) == "ollama"
    assert str(resolve_provider_id("claude")) == "anthropic"
    assert str(resolve_provider_id("google")) == "gemini"


def test_deepseek_profile_defaults() -> None:
    profile = get_provider_profile("deepseek")
    assert profile.base_url == "https://api.deepseek.com"
    assert profile.default_model == "deepseek-v4-flash"
    assert "deepseek-v4-pro" in profile.fallback_models
    assert "deepseek-chat" in profile.fallback_models
    assert "deepseek-reasoner" in profile.fallback_models
    assert profile.model_context_windows["deepseek-v4-flash"] == 1_000_000
    assert profile.openai_compatible is True
    assert profile.supports_vision is False
    assert profile.parallel_tool_calls is True
    assert profile.requires_reasoning_passback is True


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        ("https://api.deepseek.com", "https://api.deepseek.com/chat/completions"),
        ("https://api.deepseek.com/", "https://api.deepseek.com/chat/completions"),
        ("https://api.deepseek.com/v1", "https://api.deepseek.com/v1/chat/completions"),
        (
            "https://api.deepseek.com/chat/completions",
            "https://api.deepseek.com/chat/completions",
        ),
        ("https://api.openai.com/v1", "https://api.openai.com/v1/chat/completions"),
        (
            "https://open.bigmodel.cn/api/coding/paas/v4",
            "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions",
        ),
        ("http://127.0.0.1:11434/v1", "http://127.0.0.1:11434/v1/chat/completions"),
    ],
)
def test_normalize_chat_completions_url(base_url: str, expected: str) -> None:
    assert normalize_chat_completions_url(base_url) == expected


def test_normalize_chat_completions_url_rejects_empty_base_url() -> None:
    with pytest.raises(ProviderRegistryError):
        normalize_chat_completions_url("")


def test_build_backend_kwargs_for_deepseek() -> None:
    backend_type, kwargs = build_backend_kwargs("deepseek", api_key="test-key")
    assert backend_type == "openai"
    assert kwargs["base_url"] == "https://api.deepseek.com"
    assert kwargs["model"] == "deepseek-v4-flash"
    assert kwargs["api_key"] == "test-key"


def test_resolve_provider_for_legacy_openai_deepseek_config() -> None:
    profile = resolve_provider_for_config(
        "openai",
        base_url="https://api.deepseek.com",
        model="deepseek-v4-flash",
    )
    assert str(profile.provider_id) == "deepseek"


def test_resolve_provider_for_legacy_openai_ollama_config() -> None:
    profile = resolve_provider_for_config(
        "openai",
        base_url="http://localhost:11434/v1",
        model="llama3.1:8b",
    )
    assert str(profile.provider_id) == "ollama"


def test_validate_provider_config_is_local_and_normalizes_url() -> None:
    result = validate_provider_config(
        "deepseek",
        base_url="https://api.deepseek.com/",
        model="deepseek-v4-flash",
        api_key="test-key",
    )
    assert result["ok"] is True
    assert result["provider_id"] == "deepseek"
    assert result["chat_url"] == "https://api.deepseek.com/chat/completions"
    assert "没有发起真实模型调用" in result["message"]


def test_validate_provider_config_reports_missing_key() -> None:
    result = validate_provider_config("deepseek", base_url="https://api.deepseek.com", model="deepseek-v4-flash")
    assert result["ok"] is False
    assert result["code"] == "LLM_API_KEY_MISSING"
    assert result["has_api_key"] is False


def test_validate_provider_config_warns_for_legacy_deepseek_models() -> None:
    result = validate_provider_config(
        "deepseek",
        base_url="https://api.deepseek.com",
        model="deepseek-chat",
        api_key="test-key",
    )
    assert result["ok"] is True
    assert result["warnings"]
    assert "2026-07-24" in result["warnings"][0]


def test_validate_provider_config_repairs_obvious_foreign_model() -> None:
    result = validate_provider_config(
        "deepseek",
        base_url="https://api.deepseek.com",
        model="gpt-4o",
        api_key="test-key",
    )
    assert result["ok"] is True
    assert result["provider_id"] == "deepseek"
    assert result["model"] == "deepseek-v4-flash"
    assert any("gpt-4o" in warning and "deepseek-v4-flash" in warning for warning in result["warnings"])


def test_get_backend_deepseek_constructs_openai_compat_offline() -> None:
    backend = get_backend("deepseek", api_key="test-key")
    assert isinstance(backend, OpenAICompatBackend)
    assert backend.base_url == "https://api.deepseek.com"
    assert backend.model == "deepseek-v4-flash"
    assert backend.chat_completions_url == "https://api.deepseek.com/chat/completions"


def test_get_backend_openai_compat_preserves_old_explicit_kwargs() -> None:
    backend = get_backend(
        "openai",
        base_url="https://example.test/v1",
        api_key="test-key",
        model="test-model",
    )
    assert isinstance(backend, OpenAICompatBackend)
    assert backend.base_url == "https://example.test/v1"
    assert backend.model == "test-model"


def test_get_backend_new_openai_compatible_alias() -> None:
    backend = get_backend(
        "openai-compatible",
        base_url="https://example.test/v1",
        api_key="test-key",
        model="test-model",
    )
    assert isinstance(backend, OpenAICompatBackend)
    assert backend.chat_completions_url == "https://example.test/v1/chat/completions"


def test_get_backend_openai_compatible_builtin_profiles() -> None:
    kimi = get_backend("kimi", api_key="test-key")
    glm = get_backend("zhipu-glm", api_key="test-key")
    bailian = get_backend("bailian", api_key="test-key")
    doubao = get_backend("doubao", api_key="test-key", model="doubao-endpoint-id")
    assert isinstance(kimi, OpenAICompatBackend)
    assert kimi.base_url == "https://api.moonshot.cn/v1"
    assert kimi.model == "kimi-k2.6"
    assert isinstance(glm, OpenAICompatBackend)
    assert glm.chat_completions_url == "https://open.bigmodel.cn/api/coding/paas/v4/chat/completions"
    assert glm.model == "glm-5.1"
    assert isinstance(bailian, OpenAICompatBackend)
    assert bailian.model == "qwen3-coder-plus"
    assert isinstance(doubao, OpenAICompatBackend)
    assert doubao.chat_completions_url == "https://ark.cn-beijing.volces.com/api/v3/chat/completions"


def test_ollama_provider_does_not_require_api_key() -> None:
    result = validate_provider_config("ollama", base_url="http://localhost:11434/v1", model="llama3.1:8b")
    assert result["ok"] is True
    assert result["api_key_required"] is False
    assert result["chat_url"] == "http://localhost:11434/v1/chat/completions"

    backend = get_backend("ollama", model="llama3.1:8b")
    assert isinstance(backend, OpenAICompatBackend)
    assert backend.base_url == "http://localhost:11434/v1"
    assert backend.api_key == ""


def test_get_backend_anthropic_and_gemini_still_construct() -> None:
    anthropic = get_backend("anthropic", api_key="test-key")
    gemini = get_backend("gemini", api_key="test-key")
    assert isinstance(anthropic, AnthropicBackend)
    assert isinstance(gemini, GeminiBackend)


def test_get_backend_unknown_provider_lists_registry_choices() -> None:
    with pytest.raises(ValueError) as excinfo:
        get_backend("no-such-provider", api_key="test-key")
    message = str(excinfo.value)
    assert "Unknown provider" in message
    assert "deepseek" in message
    assert "openai-compatible" in message

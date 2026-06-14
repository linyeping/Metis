# -*- coding: utf-8 -*-
"""Metis 项目 API 集中配置（密钥与端点）。

**全项目仅此文件**应出现具体密钥与可改 API 地址（URL）；其它模块只通过本文件的常量或
``resolve_*`` / ``get_*`` 读取，**禁止**在业务代码、``desk_automation.json``、
``miro_config.json`` 等处再写密钥或端点。

同名环境变量仅在本文件内通过 ``_env()`` 读取，用于覆盖常量（可选）。

接入位置概览：
  · ``core/engine/constants.py`` → DeepSeek 文本对话（Flask 主链路）
  · ``Tools/desk_automation/.../screen_reader.py`` / ``vision_loop.py`` → 截图多模态
  · ``Tools/coding/foundation/.../config.py`` → OpenAI 兼容密钥（工具链）
  · ``Tools/coding/network_external/media/generate_image.py`` → DALL·E 等
"""

from __future__ import annotations

import os
from typing import Any

# ---------------------------------------------------------------------------
# 环境变量读取（空串视为未设置）
# ---------------------------------------------------------------------------


def _env(name: str, default: str = "") -> str:
    v = os.environ.get(name)
    if v is None:
        return default
    return str(v).strip()


# =============================================================================
# DeepSeek — 文本 Chat Completions（core/engine 流式主循环，默认）
# =============================================================================

# 在此填写，或设置环境变量 DEEPSEEK_API_KEY
DEEPSEEK_API_KEY: str = ""

DEEPSEEK_API_URL: str = _env(
    "DEEPSEEK_API_URL",
    "https://api.deepseek.com/chat/completions",
)

# 例如 deepseek-chat
DEEPSEEK_CHAT_MODEL: str = _env("DEEPSEEK_CHAT_MODEL", "deepseek-chat")


def resolve_deepseek_api_key() -> str:
    return (DEEPSEEK_API_KEY or _env("DEEPSEEK_API_KEY")).strip()


# =============================================================================
# OpenAI 兼容 — 多模态 / Chat（desk SoM、vision_loop 双图；也可接 DeepSeek 兼容端等）
# =============================================================================

OPENAI_API_KEY: str = ""
# 默认与常见「百炼 OpenAI 兼容模式」一致；纯 OpenAI 官方请改此处或设环境变量 OPENAI_BASE_URL
OPENAI_BASE_URL: str = _env(
    "OPENAI_BASE_URL",
    "https://dashscope.aliyuncs.com/compatible-mode/v1",
)
# 兼容模式下多为 qwen-vl-*；官方 GPT 多模态可改为 gpt-4o 等
OPENAI_VISION_MODEL: str = _env("OPENAI_VISION_MODEL", "qwen-vl-max")


# =============================================================================
# 阿里云 DashScope — 通义千问多模态（Qwen-VL）
# =============================================================================

# 与下方 OpenAI 兼容端共用同一密钥时可只填此处（或环境变量 DASHSCOPE_API_KEY）
DASHSCOPE_API_KEY: str = ""
DASHSCOPE_VISION_MODEL: str = _env("DASHSCOPE_VISION_MODEL", "qwen-vl-max")
# 原生 multimodal-generation 接口（非 OpenAI 兼容路径）
DASHSCOPE_NATIVE_MULTIMODAL_URL: str = _env(
    "DASHSCOPE_NATIVE_MULTIMODAL_URL",
    "https://dashscope.aliyuncs.com/api/v1/services/aigc/multimodal-generation/generation",
)


def resolve_dashscope_api_key() -> str:
    return (DASHSCOPE_API_KEY or _env("DASHSCOPE_API_KEY")).strip()


def resolve_openai_api_key() -> str:
    """OpenAI 兼容密钥；未单独配置 OPENAI_API_KEY 时与 DashScope/百炼密钥共用。"""
    o = (OPENAI_API_KEY or _env("OPENAI_API_KEY")).strip()
    if o:
        return o
    return resolve_dashscope_api_key()


# =============================================================================
# Google Gemini — 多模态
# =============================================================================

GEMINI_API_KEY: str = ""
GEMINI_VISION_MODEL: str = _env("GEMINI_VISION_MODEL", "gemini-2.0-flash")
# 使用 ``{model}`` 占位符；请求时追加 ``?key=``
GEMINI_GENERATE_URL_TEMPLATE: str = _env(
    "GEMINI_GENERATE_URL_TEMPLATE",
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
)


def resolve_gemini_api_key() -> str:
    return (GEMINI_API_KEY or _env("GEMINI_API_KEY")).strip()


# =============================================================================
# Ollama — 本地多模态
# =============================================================================

OLLAMA_HOST: str = _env("OLLAMA_HOST", "http://localhost:11434")
OLLAMA_VISION_MODEL: str = _env("OLLAMA_VISION_MODEL", "llava")


# =============================================================================
# 视觉后端选择（desk_automation：screen_reader / vision_loop）
# =============================================================================
# 可选：auto | openai | dashscope | gemini | ollama
# auto 会按顺序尝试 ollama → dashscope → gemini → openai

# openai = 走 OpenAI 兼容 Chat Completions（含百炼 compatible-mode/v1）
VISION_BACKEND: str = _env("VISION_BACKEND", "openai")


def get_vision_api_dict() -> dict[str, Any]:
    """视觉/Computer Use 调用参数。

    默认使用本文件的静态视觉配置；如果用户已经在设置页配置了聊天
    provider，则尽量桥接同一组 key/model，避免 Chat 与 Computer Use
    使用两套互不相干的配置。
    """
    result: dict[str, Any] = {
        "backend": VISION_BACKEND,
        "backend_type": "",
        "vision_protocol": "legacy",
        "openai_api_key": resolve_openai_api_key(),
        "openai_base_url": OPENAI_BASE_URL,
        "openai_model": OPENAI_VISION_MODEL,
        "anthropic_api_key": _env("ANTHROPIC_API_KEY"),
        "anthropic_model": _env("ANTHROPIC_MODEL", "claude-sonnet-4-20250514"),
        "dashscope_api_key": resolve_dashscope_api_key(),
        "dashscope_model": DASHSCOPE_VISION_MODEL,
        "dashscope_native_multimodal_url": DASHSCOPE_NATIVE_MULTIMODAL_URL,
        "gemini_api_key": resolve_gemini_api_key(),
        "gemini_model": GEMINI_VISION_MODEL,
        "gemini_generate_url_template": GEMINI_GENERATE_URL_TEMPLATE,
        "ollama_host": OLLAMA_HOST,
        "ollama_model": OLLAMA_VISION_MODEL,
    }
    try:
        from backend.bridges.model_capability import detect_from_model_name
        from backend.web.llm_state import (
            _configured,
            _env_file_values,
            _runtime_value,
            _resolved_provider_runtime_values,
            default_base_url,
            default_model,
            normalize_base_url,
        )

        file_values = _env_file_values()
        backend = _configured(["METIS_LLM_BACKEND", "MIRO_LLM_BACKEND"], file_values, "openai")
        base_url = normalize_base_url(backend, _runtime_value("base_url", backend, file_values, default_base_url(backend)))
        raw_model = _runtime_value("model", backend, file_values, default_model(backend))
        resolved = _resolved_provider_runtime_values(backend, base_url=base_url, model=raw_model)
        provider_id = str(resolved["backend"])
        api_key = _runtime_value("api_key", backend, file_values, "")
        if not api_key and provider_id != backend:
            api_key = _runtime_value("api_key", provider_id, file_values, "")
        model = str(resolved["model"] or raw_model)
        caps = detect_from_model_name(model)

        result["backend_type"] = provider_id
        result["vision_protocol"] = caps.vision_protocol
        if provider_id == "anthropic":
            result.update(
                {
                    "backend": "auto",
                    "anthropic_api_key": api_key,
                    "anthropic_model": model,
                }
            )
        elif provider_id == "gemini":
            result.update(
                {
                    "backend": "gemini",
                    "gemini_api_key": api_key or result["gemini_api_key"],
                    "gemini_model": model,
                }
            )
        elif provider_id == "bailian":
            result.update(
                {
                    "backend": "dashscope",
                    "dashscope_api_key": api_key or result["dashscope_api_key"],
                    "dashscope_model": model if "vl" in model.lower() else result["dashscope_model"],
                    "openai_api_key": api_key or result["openai_api_key"],
                    "openai_base_url": str(resolved["base_url"] or result["openai_base_url"]),
                    "openai_model": model,
                }
            )
        elif provider_id in {"openai", "openai-compatible", "custom-openai"}:
            result.update(
                {
                    "backend": "openai",
                    "openai_api_key": api_key or result["openai_api_key"],
                    "openai_base_url": str(resolved["base_url"] or result["openai_base_url"]),
                    "openai_model": model or result["openai_model"],
                }
            )
    except Exception:
        pass
    return result


# =============================================================================
# 图像生成（OpenAI Images API，generate_image 工具）
# =============================================================================

OPENAI_IMAGE_MODEL: str = _env("OPENAI_IMAGE_MODEL", "dall-e-2")
OPENAI_IMAGE_API_BASE: str = _env(
    "OPENAI_IMAGE_API_BASE",
    "https://api.openai.com/v1",
)


def get_openai_image_api_url() -> str:
    return f"{OPENAI_IMAGE_API_BASE.rstrip('/')}/images/generations"

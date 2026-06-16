# -*- coding: utf-8 -*-
"""AI-driven browser automation — reuses the system Chrome / Edge.

Key design: we do NOT download a separate Chromium.  Instead we detect the
user's already-installed Chrome or Edge and launch it via Playwright's
``channel`` parameter.  This means:

  • No 200 MB Chromium download on first use.
  • The user's existing cookies / logins / extensions are available if we
    point at their real profile directory (opt-in, off by default for
    safety — a fresh temp profile is used unless ``use_user_profile=True``).

Falls back gracefully when ``browser-use`` is not ``pip install``-ed.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import inspect
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class BrowserTask:
    goal: str
    start_url: str = ""
    max_steps: int = 20
    timeout: float = 120.0
    headless: bool = True
    allowed_domains: list[str] = field(default_factory=list)
    extract_content: bool = False
    use_user_profile: bool = False  # True = reuse login sessions / cookies


@dataclass
class BrowserResult:
    ok: bool = True
    output: str = ""
    url: str = ""
    steps_used: int = 0
    extracted_content: str = ""
    error: str = ""
    screenshots: list[str] = field(default_factory=list)
    provider_id: str = ""
    model: str = ""
    base_url: str = ""
    browser_channel: str = ""

    def __str__(self) -> str:
        if self.ok:
            parts = [self.output or "Browser task completed."]
            if self.url:
                parts.append(f"URL: {self.url}")
            if self.extracted_content:
                parts.append(f"Extracted content:\n{self.extracted_content}")
            return "\n\n".join(parts)
        return (
            f"Browser task failed: {self.error}\n\n"
            "安装/修复方法:\n"
            "python -m pip install browser-use playwright langchain-openai\n"
            "(不需要 playwright install chromium — 直接使用系统 Chrome/Edge)"
        )


@dataclass
class BrowserLLMConfig:
    provider_id: str = "openai"
    display_name: str = "OpenAI"
    backend_type: str = "openai"
    model: str = "gpt-4o-mini"
    base_url: str = "https://api.openai.com/v1"
    api_key: str = ""
    api_key_source: str = ""
    api_key_required: bool = True
    openai_compatible: bool = True
    warnings: list[str] = field(default_factory=list)


# ── Detect system browser ─────────────────────────────────────────────

def _detect_system_browser() -> tuple[str, str]:
    """Return (channel, exe_path) for the first available system browser.

    Priority: Chrome → Edge → Chromium.
    ``channel`` is the Playwright channel name: "chrome", "msedge", etc.
    """
    if os.name == "nt":
        candidates = [
            ("chrome", [
                os.path.join(os.environ.get("PROGRAMFILES", ""), "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Google", "Chrome", "Application", "chrome.exe"),
                os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
            ]),
            ("msedge", [
                os.path.join(os.environ.get("PROGRAMFILES", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
                os.path.join(os.environ.get("PROGRAMFILES(X86)", ""), "Microsoft", "Edge", "Application", "msedge.exe"),
            ]),
        ]
    else:
        candidates = [
            ("chrome", [shutil.which("google-chrome") or "", shutil.which("chrome") or ""]),
            ("msedge", [shutil.which("microsoft-edge") or ""]),
            ("chromium", [shutil.which("chromium") or "", shutil.which("chromium-browser") or ""]),
        ]

    for channel, paths in candidates:
        for p in paths:
            if p and os.path.isfile(p):
                return channel, p

    return "", ""


def _user_data_dir(channel: str) -> str:
    """Return the default user-data-dir for a Chromium-family browser on this OS."""
    if os.name == "nt":
        local = os.environ.get("LOCALAPPDATA", "")
        if channel == "msedge":
            return os.path.join(local, "Microsoft", "Edge", "User Data")
        return os.path.join(local, "Google", "Chrome", "User Data")
    home = Path.home()
    if channel == "msedge":
        return str(home / ".config" / "microsoft-edge")
    return str(home / ".config" / "google-chrome")


# ── LLM provider bridge ───────────────────────────────────────────────

def _redact_secret(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return "***"
    return f"{text[:3]}****{text[-4:]}"


def _sanitize_error_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    # Avoid leaking bearer/API tokens in tool output.
    text = re.sub(r"sk-[A-Za-z0-9_\-]{12,}", "sk-****", text)
    text = re.sub(r"Bearer\s+[A-Za-z0-9._\-]{12,}", "Bearer ****", text, flags=re.IGNORECASE)
    text = re.sub(r"(api[_-]?key['\"]?\s*[:=]\s*['\"]?)[^'\"\s,;]{8,}", r"\1****", text, flags=re.IGNORECASE)
    return text[:1200]


def _supported_kwargs(factory: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    try:
        signature = inspect.signature(factory)
    except (TypeError, ValueError):
        return kwargs
    params = signature.parameters
    if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in params.values()):
        return kwargs
    return {key: value for key, value in kwargs.items() if key in params}


def _active_provider_config() -> BrowserLLMConfig:
    try:
        from backend.web.llm_state import (
            _configured,
            _env_file_values,
            _runtime_value,
            _resolved_provider_runtime_values,
            default_base_url,
            default_model,
            load_persistent_config,
            normalize_base_url,
        )

        load_persistent_config()
        file_values = _env_file_values()
        backend = _configured(["METIS_LLM_BACKEND", "MIRO_LLM_BACKEND"], file_values, "openai")
        base_url = normalize_base_url(backend, _runtime_value("base_url", backend, file_values, default_base_url(backend)))
        raw_model = _runtime_value("model", backend, file_values, default_model(backend))
        resolved = _resolved_provider_runtime_values(backend, base_url=base_url, model=raw_model)
        profile = resolved.get("profile")
        provider_id = str(resolved["backend"])
        api_key = _runtime_value("api_key", backend, file_values, "")
        api_key_source = "runtime"
        if not api_key and provider_id != backend:
            api_key = _runtime_value("api_key", provider_id, file_values, "")
            api_key_source = f"runtime:{provider_id}"
        api_key_env = str(getattr(profile, "api_key_env", "") or "").strip()
        if not api_key and api_key_env:
            api_key = os.environ.get(api_key_env, "").strip()
            api_key_source = api_key_env if api_key else ""
        return BrowserLLMConfig(
            provider_id=provider_id,
            display_name=str(getattr(profile, "display_name", "") or provider_id),
            backend_type=str(getattr(profile, "backend_type", "") or "openai"),
            base_url=str(resolved["base_url"] or ""),
            model=str(resolved["model"] or raw_model or ""),
            api_key=api_key,
            api_key_source=api_key_source if api_key else "",
            api_key_required=bool(getattr(profile, "api_key_required", True)),
            openai_compatible=bool(getattr(profile, "openai_compatible", False)),
            warnings=[str(resolved.get("model_warning") or "")] if resolved.get("model_warning") else [],
        )
    except Exception as exc:
        return BrowserLLMConfig(
            provider_id="openai",
            display_name="OpenAI",
            backend_type="openai",
            model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini").strip() or "gpt-4o-mini",
            base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").strip() or "https://api.openai.com/v1",
            api_key=os.environ.get("OPENAI_API_KEY", "").strip(),
            api_key_source="OPENAI_API_KEY" if os.environ.get("OPENAI_API_KEY") else "",
            warnings=[f"provider config fallback: {_sanitize_error_text(exc)}"],
        )


def _build_browser_use_llm(config: BrowserLLMConfig | None = None) -> tuple[Any, BrowserLLMConfig]:
    """Build a browser-use native LLM object from Metis' active provider config.

    browser-use 0.13+ expects its own ``BaseChatModel`` classes.  Passing
    LangChain's ``ChatOpenAI`` directly can fail with errors such as
    ``ChatOpenAI object has no attribute provider``.  Use browser-use native
    adapters first and keep a narrow legacy fallback for older browser-use.
    """
    config = config or _active_provider_config()
    if config.provider_id == "fake" or config.backend_type == "fake":
        raise ValueError("当前为 Fake Provider，外部 browser-use 需要真实 LLM provider。请在设置中选择 OpenAI-compatible / DeepSeek / Anthropic / Gemini。")
    if config.api_key_required and not config.api_key:
        raise ValueError(
            f"外部 browser-use 缺少 {config.display_name} API Key。"
            "请先在设置页保存 API Key，或设置对应环境变量；Electron 启动后端时会自动解密 api_key_encrypted。"
        )
    if config.openai_compatible and not config.base_url:
        raise ValueError(f"外部 browser-use 缺少 {config.display_name} Base URL。请在设置页填写供应商 OpenAI-compatible base_url。")
    if not config.model:
        raise ValueError(f"外部 browser-use 缺少 {config.display_name} 模型名。请在设置页选择或填写 model。")

    provider = config.provider_id
    if provider == "anthropic":
        try:
            from browser_use.llm.anthropic.chat import ChatAnthropic

            kwargs = _supported_kwargs(
                ChatAnthropic,
                {
                    "model": config.model or "claude-sonnet-4-20250514",
                    "api_key": config.api_key,
                    "base_url": config.base_url or None,
                    "temperature": 0.1,
                    "max_retries": 3,
                },
            )
            return ChatAnthropic(**kwargs), config
        except Exception as exc:
            raise RuntimeError(f"browser-use Anthropic LLM 初始化失败: {_sanitize_error_text(exc)}") from exc

    if provider == "gemini":
        try:
            from browser_use.llm.google.chat import ChatGoogle

            kwargs = _supported_kwargs(
                ChatGoogle,
                {
                    "model": config.model or "gemini-2.0-flash",
                    "api_key": config.api_key,
                    "temperature": 0.1,
                    "max_retries": 3,
                },
            )
            return ChatGoogle(**kwargs), config
        except Exception as exc:
            raise RuntimeError(f"browser-use Gemini LLM 初始化失败: {_sanitize_error_text(exc)}") from exc

    try:
        from browser_use.llm.openai.chat import ChatOpenAI as BrowserUseChatOpenAI

        kwargs = _supported_kwargs(
            BrowserUseChatOpenAI,
            {
                "model": config.model or "gpt-4o-mini",
                "api_key": config.api_key or "not-needed",
                "base_url": config.base_url or None,
                "temperature": 0.1,
                "frequency_penalty": None,
                "reasoning_effort": "none",
                "max_retries": 3,
                "max_completion_tokens": int(os.environ.get("METIS_BROWSER_USE_MAX_TOKENS", "4096") or 4096),
            },
        )
        llm = BrowserUseChatOpenAI(**kwargs)
        return llm, config
    except Exception as native_exc:
        # Legacy browser-use accepted LangChain chat models.  Keep this only as
        # a compatibility fallback; patch metadata fields that old browser-use
        # probes expect.
        try:
            from backend.runtime.pip_helper import ensure_import

            lc_openai = ensure_import("langchain_openai", pip="langchain-openai")
            ChatOpenAI = lc_openai.ChatOpenAI
            kwargs = {
                "model": config.model or "gpt-4o-mini",
                "api_key": config.api_key or "not-needed",
                "temperature": 0.1,
            }
            if config.base_url:
                kwargs["base_url"] = config.base_url
            llm = ChatOpenAI(**kwargs)
            object.__setattr__(llm, "provider", "openai")
            object.__setattr__(llm, "model", config.model or "gpt-4o-mini")
            object.__setattr__(llm, "model_name", config.model or "gpt-4o-mini")
            return llm, config
        except Exception as legacy_exc:
            raise RuntimeError(
                "browser-use LLM 初始化失败。"
                f" native={_sanitize_error_text(native_exc)}; legacy={_sanitize_error_text(legacy_exc)}"
            ) from legacy_exc


def _format_browser_failure(exc: Exception, *, config: BrowserLLMConfig | None = None, phase: str = "run") -> str:
    text = _sanitize_error_text(exc)
    lower = text.lower()
    provider_line = ""
    if config:
        provider_line = (
            f"\nProvider: {config.provider_id}"
            f"\nModel: {config.model}"
            f"\nBase URL: {config.base_url or '(empty)'}"
            f"\nAPI Key: {'configured via ' + config.api_key_source if config.api_key else 'missing'}"
        )
    hint = ""
    if "has no attribute 'provider'" in lower or "has no attribute provider" in lower:
        hint = "\nHint: 检测到 browser-use 与 LangChain ChatOpenAI 适配不兼容；请使用 browser-use 原生 LLM 适配器。"
    elif "api key" in lower or "apikey" in lower or "openai_api_key" in lower:
        hint = "\nHint: 请在设置页保存 API Key；桌面端会通过 safeStorage 解密后注入后端环境。"
    elif "base_url" in lower or "invalid url" in lower:
        hint = "\nHint: OpenAI-compatible provider 需要可用 base_url，例如 https://api.example.com/v1。"
    elif "browserprofile" in lower or "browser profile" in lower:
        hint = "\nHint: browser-use 的 BrowserProfile 参数可能随版本变化；当前代码会自动降级到最小参数。"
    elif "timeout" in lower:
        hint = "\nHint: 可以降低 max_steps，或检查目标网站/代理网络。"
    return f"{phase} failed: {text or exc.__class__.__name__}{provider_line}{hint}"


def _provider_summary(config: BrowserLLMConfig) -> str:
    key_state = f"configured:{config.api_key_source}" if config.api_key else "missing"
    warnings = f"; warnings={'; '.join(config.warnings)}" if config.warnings else ""
    return (
        f"provider={config.provider_id}, model={config.model}, "
        f"base_url={config.base_url or '(empty)'}, api_key={key_state}{warnings}"
    )


def _get_llm_for_browser() -> Any:
    llm, _config = _build_browser_use_llm()
    return llm


# ── Core runner ────────────────────────────────────────────────────────

async def _run_browser_task_async(task: BrowserTask) -> BrowserResult:
    llm_config: BrowserLLMConfig | None = None
    try:
        from backend.runtime.pip_helper import ensure_packages

        # Auto-install browser-use + playwright if missing
        ensure_packages({
            "browser_use": "browser-use",
            "playwright": "playwright",
        })
        from browser_use import Agent
        from browser_use.browser.profile import BrowserProfile
    except Exception as exc:
        return BrowserResult(ok=False, error=f"browser-use 自动安装/导入失败: {_sanitize_error_text(exc)}")

    # ── Detect system browser ────────────────────────────────────
    channel, exe_path = _detect_system_browser()
    if not channel:
        return BrowserResult(
            ok=False,
            error=(
                "未检测到系统浏览器 (Chrome / Edge)。\n"
                "请安装 Google Chrome 或 Microsoft Edge 后重试。"
            ),
        )

    # ── Build LLM ────────────────────────────────────────────────
    try:
        llm_config = _active_provider_config()
        llm, llm_config = _build_browser_use_llm(llm_config)
    except Exception as exc:
        return BrowserResult(ok=False, error=_format_browser_failure(exc, config=llm_config, phase="LLM init"))

    # ── Build BrowserProfile ─────────────────────────────────────
    # browser-use 0.13+ uses BrowserProfile with native channel / exe fields.
    profile_kwargs: dict[str, Any] = {
        "channel": channel,                 # "chrome" | "msedge"
        "executable_path": exe_path,
        "headless": task.headless,
        "disable_security": False,
    }

    if task.allowed_domains:
        profile_kwargs["allowed_domains"] = task.allowed_domains

    # Optionally reuse the user's real profile (cookies, logins)
    if task.use_user_profile:
        udd = _user_data_dir(channel)
        if os.path.isdir(udd):
            profile_kwargs["user_data_dir"] = udd
            profile_kwargs["profile_directory"] = "Default"

    try:
        browser_profile = BrowserProfile(**_supported_kwargs(BrowserProfile, profile_kwargs))
    except TypeError:
        # Fallback: minimal profile if some kwargs aren't accepted
        browser_profile = BrowserProfile(
            channel=channel,
            headless=task.headless,
        )

    # ── Build prompt ─────────────────────────────────────────────
    prompt = task.goal.strip()
    if task.start_url:
        prompt = f"Start at {task.start_url}. {prompt}"
    if task.allowed_domains:
        prompt += "\nOnly browse these domains: " + ", ".join(task.allowed_domains)
    if task.extract_content:
        prompt += "\nReturn the final extracted content in the answer."

    # ── Run ───────────────────────────────────────────────────────
    try:
        agent = Agent(task=prompt, llm=llm, browser_profile=browser_profile)
        history = await asyncio.wait_for(
            agent.run(max_steps=max(1, int(task.max_steps))),
            timeout=task.timeout,
        )
    except asyncio.TimeoutError:
        return BrowserResult(
            ok=False,
            error=f"浏览器任务超时 ({task.timeout}s)\nProvider: {_provider_summary(llm_config)}",
            provider_id=llm_config.provider_id if llm_config else "",
            model=llm_config.model if llm_config else "",
            base_url=llm_config.base_url if llm_config else "",
            browser_channel=channel,
        )
    except Exception as exc:
        return BrowserResult(
            ok=False,
            error=_format_browser_failure(exc, config=llm_config, phase="browser-use run"),
            provider_id=llm_config.provider_id if llm_config else "",
            model=llm_config.model if llm_config else "",
            base_url=llm_config.base_url if llm_config else "",
            browser_channel=channel,
        )

    # ── Parse result ──────────────────────────────────────────────
    output = ""
    extracted = ""
    final_url = task.start_url
    try:
        if hasattr(history, "final_result"):
            output = str(history.final_result() or "")
        if hasattr(history, "extracted_content"):
            extracted = str(history.extracted_content() or "")
        if hasattr(history, "urls"):
            urls = list(history.urls() or [])
            if urls:
                final_url = str(urls[-1])
        if not output:
            output = str(history)
    except Exception:
        output = str(history)

    return BrowserResult(
        ok=True,
        output=output,
        url=final_url,
        extracted_content=extracted,
        provider_id=llm_config.provider_id if llm_config else "",
        model=llm_config.model if llm_config else "",
        base_url=llm_config.base_url if llm_config else "",
        browser_channel=channel,
    )


def run_browser_task(task: BrowserTask) -> BrowserResult:
    """Synchronous entry point — safe to call from Flask request threads."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(_run_browser_task_async(task))

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(lambda: asyncio.run(_run_browser_task_async(task)))
        try:
            return future.result(timeout=task.timeout + 5)
        except concurrent.futures.TimeoutError:
            return BrowserResult(ok=False, error=f"浏览器任务超时 ({task.timeout}s)")
        except Exception as exc:
            return BrowserResult(ok=False, error=str(exc))

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
import os
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

def _active_provider_config() -> dict[str, Any]:
    try:
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
        api_key = _runtime_value("api_key", backend, file_values, "")
        if not api_key and resolved["backend"] != backend:
            api_key = _runtime_value("api_key", resolved["backend"], file_values, "")
        return {
            "provider_id": resolved["backend"],
            "base_url": resolved["base_url"],
            "model": resolved["model"],
            "api_key": api_key,
        }
    except Exception:
        return {}


def _get_llm_for_browser() -> Any:
    config = _active_provider_config()
    provider = str(config.get("provider_id") or "openai")
    model = str(config.get("model") or "")
    api_key = str(config.get("api_key") or "")
    base_url = str(config.get("base_url") or "")

    from backend.runtime.pip_helper import ensure_import

    if provider == "anthropic":
        lc_anthropic = ensure_import("langchain_anthropic", pip="langchain-anthropic")
        return lc_anthropic.ChatAnthropic(
            model_name=model or "claude-sonnet-4-20250514",
            **{"api_key": api_key, "temperature": 0.1},
        )
    if provider == "gemini":
        lc_gemini = ensure_import("langchain_google_genai", pip="langchain-google-genai")
        return lc_gemini.ChatGoogleGenerativeAI(
            model=model or "gemini-2.0-flash",
            google_api_key=api_key,
            temperature=0.1,
        )

    lc_openai = ensure_import("langchain_openai", pip="langchain-openai")
    ChatOpenAI = lc_openai.ChatOpenAI
    kwargs: dict[str, Any] = {
        "model": model or "gpt-4o-mini",
        "api_key": api_key,
        "temperature": 0.1,
    }
    if base_url:
        kwargs["base_url"] = base_url
    return ChatOpenAI(**kwargs)


# ── Core runner ────────────────────────────────────────────────────────

async def _run_browser_task_async(task: BrowserTask) -> BrowserResult:
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
        return BrowserResult(ok=False, error=f"browser-use 自动安装失败: {exc}")

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
        browser_profile = BrowserProfile(**profile_kwargs)
    except TypeError:
        # Fallback: minimal profile if some kwargs aren't accepted
        browser_profile = BrowserProfile(
            channel=channel,
            headless=task.headless,
        )

    # ── Build LLM ────────────────────────────────────────────────
    try:
        llm = _get_llm_for_browser()
    except Exception as exc:
        return BrowserResult(ok=False, error=f"无法为 browser-use 创建 LLM: {exc}")

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
        return BrowserResult(ok=False, error=f"浏览器任务超时 ({task.timeout}s)")
    except Exception as exc:
        return BrowserResult(ok=False, error=str(exc))

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
        ok=True, output=output, url=final_url, extracted_content=extracted,
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

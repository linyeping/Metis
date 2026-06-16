# -*- coding: utf-8 -*-
"""Agent-facing browser automation tools.

Uses the system Chrome / Edge — no separate Chromium download needed.
Set ``use_login=True`` to reuse the user's existing cookies and login sessions.
"""

from __future__ import annotations

from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution
from backend.tools.coding.network_external.web.web_content import extract_html_markdown

from .browser_agent import BrowserTask, run_browser_task


@trace_execution
def browse_web(
    task: str,
    url: str = "",
    max_steps: int = 15,
    extract_content: bool = False,
    use_login: bool = False,
    show_browser: bool = False,
) -> str:
    """Browse the web autonomously using the system Chrome / Edge.

    Args:
        task: What to do (e.g. "Find the pricing page and summarize it")
        url: Starting URL (optional)
        max_steps: Maximum browsing steps (default 15, max 50)
        extract_content: If True, also return raw page content
        show_browser: If True, run a visible browser window without reusing
                      the user's real profile. Use this for watch/playback tasks.
        use_login: If True, use the user's real browser profile so that
                   logged-in sites (GitHub, Gmail, etc.) are accessible
    """
    result = run_browser_task(
        BrowserTask(
            goal=task,
            start_url=url,
            max_steps=max(1, min(int(max_steps or 15), 50)),
            headless=not (use_login or show_browser),
            extract_content=bool(extract_content),
            use_user_profile=bool(use_login),
        )
    )
    if result.ok and result.extracted_content:
        result.extracted_content = _normalize_browser_content(result.extracted_content, result.url or url)
    return str(result)


@trace_execution
def browse_and_extract(url: str, what_to_extract: str, use_login: bool = False, show_browser: bool = False) -> str:
    """Navigate to a URL and extract specific information.

    Args:
        url: The URL to visit
        what_to_extract: Description of what to extract
        show_browser: If True, run a visible browser window without reusing
                      the user's real profile.
        use_login: If True, use the user's real browser profile
    """
    result = run_browser_task(
        BrowserTask(
            goal=f"Extract this from the page: {what_to_extract}",
            start_url=url,
            max_steps=10,
            headless=not (use_login or show_browser),
            extract_content=True,
            use_user_profile=bool(use_login),
        )
    )
    if result.ok and result.extracted_content:
        result.extracted_content = _normalize_browser_content(result.extracted_content, result.url or url)
    return str(result)


def _normalize_browser_content(content: str, final_url: str = "") -> str:
    text = str(content or "")
    if "<html" in text[:1000].lower() or "<!doctype html" in text[:1000].lower():
        extracted = extract_html_markdown(text, base_url=final_url)
        if extracted:
            return extracted[:12000] + ("\n\n[... browser extracted content truncated ...]" if len(extracted) > 12000 else "")
    return text[:12000] + ("\n\n[... browser extracted content truncated ...]" if len(text) > 12000 else "")

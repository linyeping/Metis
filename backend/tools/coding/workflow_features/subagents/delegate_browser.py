"""Browser sub-agent backed by browser-use — uses system Chrome / Edge."""
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution


@trace_execution
def delegate_browser(task: str, url: str = "", use_login: bool = False) -> str:
    """Run a browser automation task using the system Chrome / Edge.

    Args:
        task: What to do on the web
        url: Starting URL (optional)
        use_login: If True, use the user's real browser profile (cookies, sessions)
    """
    from backend.tools.browser_automation.browser_agent import BrowserTask, run_browser_task

    result = run_browser_task(
        BrowserTask(
            goal=task,
            start_url=url,
            max_steps=15,
            headless=not use_login,
            extract_content=True,
            use_user_profile=bool(use_login),
        )
    )
    return str(result)

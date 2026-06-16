"""工具执行前钩子（可扩展注册表）。"""
from typing import Any, Callable, Dict, List

_pre: List[Callable[[str, Dict[str, Any]], None]] = []


def register_pre_hook(fn: Callable[[str, Dict[str, Any]], None]) -> None:
    _pre.append(fn)
    try:
        from backend.runtime.hook_lifecycle_bus import subscribe_hook_lifecycle

        subscribe_hook_lifecycle(
            lambda event: fn(event.tool_name, event.arguments),
            kinds=["tool.start"],
        )
    except Exception:
        pass


def pre_tool_hook(tool_name: str, kwargs: Dict[str, Any]) -> None:
    for fn in _pre:
        fn(tool_name, kwargs)

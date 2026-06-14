"""工具执行前钩子（可扩展注册表）。"""
from typing import Any, Callable, Dict, List

_pre: List[Callable[[str, Dict[str, Any]], None]] = []


def register_pre_hook(fn: Callable[[str, Dict[str, Any]], None]) -> None:
    _pre.append(fn)


def pre_tool_hook(tool_name: str, kwargs: Dict[str, Any]) -> None:
    for fn in _pre:
        fn(tool_name, kwargs)

"""工具执行后钩子。"""
from typing import Any, Callable, Dict, List

_post: List[Callable[[str, Dict[str, Any], str], None]] = []
_FILE_MODIFY_TOOLS = {
    "robust_replace_in_file",
    "write_file",
    "apply_patch",
    "editCode",
    "edit_notebook",
    "append_to_file",
    "delete_file",
    "rename_file_update_refs",
}


def register_post_hook(fn: Callable[[str, Dict[str, Any], str], None]) -> None:
    _post.append(fn)


def post_tool_reminder(tool_name: str, result: str) -> str:
    if tool_name in _FILE_MODIFY_TOOLS:
        return result + (
            "\n\n💡 Principle #5 reminder: Consider running tests or linting to verify this change is correct."
        )
    return result


def _register_builtin_hooks() -> None:
    """注册内置 post-tool hooks（仅首次调用生效）。"""
    if getattr(_register_builtin_hooks, "_done", False):
        return
    _register_builtin_hooks._done = True  # type: ignore[attr-defined]
    try:
        from .memory_hook import update_memory_from_tool_result
        register_post_hook(update_memory_from_tool_result)
    except Exception:
        pass  # 非关键


def post_tool_hook(tool_name: str, kwargs: Dict[str, Any], result: str) -> str:
    _register_builtin_hooks()
    for fn in _post:
        fn(tool_name, kwargs, result)
    # 反馈回路：改完文件就地跑诊断，有真实报错则回灌（取代泛泛提醒）；无错/不适用回退到提醒。
    try:
        from .edit_diagnostics import edit_diagnostics_feedback

        diagnostics = edit_diagnostics_feedback(tool_name, kwargs, _FILE_MODIFY_TOOLS)
    except Exception:
        diagnostics = ""
    if diagnostics:
        return result + diagnostics
    return post_tool_reminder(tool_name, result)

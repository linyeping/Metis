"""C 风格 Task 统一入口：按 subagent_type 路由；可选真子进程隔离（块5）。"""
import os
from typing import List, Optional

from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError
from backend.tools.coding.foundation.core_mechanisms.trace_execution import trace_execution

from .delegate_workspace import resolve_delegate_workspace_for_task


def _use_subprocess() -> bool:
    v = os.environ.get("MIRO_TASK_SUBPROCESS", "1").strip().lower()
    return v not in ("0", "false", "no", "off")


@trace_execution
def task_dispatch(
    prompt: str,
    description: str = "",
    subagent_type: str = "explore",
    model: str = "",
    readonly: bool = False,
    resume: str = "",
    run_in_background: bool = False,
    workspace_root: str = ".",
) -> str:
    """
    子任务派发（对齐 C Task 参数形状）。

    - prompt: 子代理专用、自洽的任务说明（子进程内同样看不到用户原话）。
    - subagent_type: explore | shell | browser | best_of_n | context_gatherer | custom
    - workspace_root: 工作区根（子进程 cwd、delegate 根路径）。
    - 默认 MIRO_TASK_SUBPROCESS=1：在独立子进程中执行，见 TASK_SUBPROCESS_PROTOCOL.md。
    """
    del model, readonly, run_in_background

    try:
        workspace_root = str(resolve_delegate_workspace_for_task(workspace_root))
    except PathSecurityError as e:
        return str(e)

    st = (subagent_type or "explore").strip().lower().replace("-", "_")
    meta = (
        f"[Task 元信息] description={description!r} type={st!r} "
        f"subprocess={_use_subprocess()} resume={resume!r} workspace_root={workspace_root!r}\n\n"
    )

    if _use_subprocess():
        from backend.tools.coding.workflow_features.subagents.task_subprocess_runner import run_task_subprocess

        ok, body, code, sid = run_task_subprocess(
            prompt=prompt,
            subagent_type=st,
            workspace_root=workspace_root,
            timeout_sec=int(os.environ.get("MIRO_TASK_TIMEOUT_SEC", "180")),
            resume=resume or "",
        )
        from backend.tools.coding.workflow_features.subagents.task_session_persistence import write_task_session_state

        write_task_session_state(workspace_root, sid, "task_dispatch", body, ok)
        tail = f"\n\n[子进程 exit_code={code} ok={ok} session_id={sid}]"
        return meta + body + tail

    return meta + _dispatch_in_process(prompt, st, workspace_root)


def _dispatch_in_process(prompt: str, st: str, workspace_root: str) -> str:
    from backend.tools.coding.workflow_features.subagents.delegate_best_of_n import delegate_best_of_n
    from backend.tools.coding.workflow_features.subagents.delegate_browser import delegate_browser
    from backend.tools.coding.workflow_features.subagents.delegate_explore import delegate_explore
    from backend.tools.coding.workflow_features.subagents.delegate_shell import delegate_shell
    from backend.tools.coding.workflow_features.subagents.summon_context_gatherer import summon_context_gatherer
    from backend.tools.coding.workflow_features.subagents.custom_agent_creator import custom_agent_creator

    root = workspace_root or "."
    try:
        if st in ("explore", "generalpurpose", "general_purpose"):
            return delegate_explore(goal=prompt, root=root, max_depth=3)
        if st in ("shell",):
            return delegate_shell(script_description=prompt, cwd=root)
        if st in ("browser", "browser_use"):
            return delegate_browser(task=prompt, url="")
        if st in ("best_of_n", "bestofn"):
            return delegate_best_of_n(task=prompt, n=3)
        if st in ("context_gatherer", "summon_context", "context"):
            paths: Optional[List[str]] = None
            return summon_context_gatherer(workspace=root, extra_paths=paths, max_depth=2)
        if st in ("custom", "custom_agent"):
            return custom_agent_creator(
                name="ad_hoc",
                system_prompt=prompt,
                tools_allow="*",
            )
        return (
            f"❌ 未知 subagent_type: {st!r}。\n"
            "支持: explore, shell, browser, best_of_n, context_gatherer, custom"
        )
    except Exception as e:
        return f"❌ task_dispatch 执行异常: {e}"

# -*- coding: utf-8 -*-
"""目标运行器：编排引擎的主循环。

负责:
1. 接收最终目标 → 分解为粗粒度步骤（由外部 AI 做细化）
2. 逐步执行：委派 Cursor / 其他 AI / 本地工具
3. 检查暂停标志 → 暂停时等待恢复
4. 步骤完成后自动推进下一步
5. 异常时记录日志并暂停等待人工介入

这是一个单线程协作式循环，不占用额外线程池。
由 HTTP API 的 /api/goal/run 端点在后台线程启动。
"""

from __future__ import annotations

import threading
import time
import traceback
from typing import Any

from .. import config
from . import task_state
from .ai_bridge import (
    compose_prompt_b,
    delegate_to_clipboard,
    delegate_to_cursor,
    suggest_routing,
)
from .cursor_bridge import (
    cursor_is_available,
    find_cursor_window,
    wait_cursor_idle,
)


_runner_thread: threading.Thread | None = None
_stop_event = threading.Event()


def is_running() -> bool:
    return _runner_thread is not None and _runner_thread.is_alive()


def request_stop() -> None:
    _stop_event.set()


def start_goal(goal: str, steps: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """启动目标运行器。

    Args:
        goal: 最终目标描述
        steps: 可选的预定义步骤列表 [{"action": "...", "detail": "..."}]
               如果不提供，运行器会先委派 AI 做分解。
    """
    global _runner_thread

    if is_running():
        return {"ok": False, "error": "已有目标在运行中，请先停止或等待完成"}

    _stop_event.clear()
    task_state.set_goal(goal)

    if steps:
        for s in steps:
            task_state.add_step(s.get("action", "未知"), s.get("detail", ""))

    _runner_thread = threading.Thread(target=_run_loop, daemon=True, name="goal_runner")
    _runner_thread.start()

    return {"ok": True, "goal": goal, "step_count": len(steps or [])}


def _run_loop() -> None:
    """主执行循环。"""
    try:
        st = task_state.load_state()
        steps = st.get("steps", [])

        if not steps:
            task_state.append_log("info", "无预定义步骤，尝试委派 AI 分解目标…")
            _delegate_goal_decomposition(st["current_goal"])
            st = task_state.load_state()
            steps = st.get("steps", [])

        for step in steps:
            if _stop_event.is_set():
                task_state.append_log("warn", "收到停止信号")
                task_state.pause_goal()
                return

            _wait_if_paused()

            if step["status"] in ("done", "skipped"):
                continue

            task_state.update_step(step["id"], "running")
            task_state.append_log("info", f"▶ 执行: {step['action']}")

            try:
                result = _execute_step(step)
                task_state.update_step(step["id"], "done", result)
            except PermissionError as e:
                task_state.update_step(step["id"], "error", str(e))
                task_state.append_log("error", f"权限受限: {e}")
                task_state.pause_goal()
                config.set_paused(True)
                return
            except Exception as e:
                task_state.update_step(step["id"], "error", str(e))
                task_state.append_log("error", f"步骤失败: {traceback.format_exc()}")
                task_state.pause_goal()
                config.set_paused(True)
                return

        task_state.finish_goal(True, "所有步骤完成")

    except Exception as e:
        task_state.append_log("error", f"运行器异常: {traceback.format_exc()}")
        task_state.finish_goal(False, str(e))


def _wait_if_paused() -> None:
    """阻塞等待直到暂停解除或收到停止信号。"""
    while config.is_paused() and not _stop_event.is_set():
        time.sleep(1)


def _execute_step(step: dict[str, Any]) -> str:
    """执行单个步骤，返回结果摘要。"""
    action = step.get("action", "")
    detail = step.get("detail", "")

    if action.startswith("cursor:"):
        prompt = action.split(":", 1)[1].strip() or detail
        if cursor_is_available() and find_cursor_window()["found"]:
            r = delegate_to_cursor(prompt)
            if r.get("ok"):
                task_state.append_log("info", "等待 Cursor 完成…")
                wait_result = wait_cursor_idle(timeout=180)
                return f"已发送并等待: {wait_result}"
            return f"发送失败: {r}"
        else:
            r = delegate_to_clipboard(prompt)
            return f"Cursor 不可用，已复制到剪贴板: {r}"

    if action.startswith("ask:"):
        prompt = action.split(":", 1)[1].strip() or detail
        routing = suggest_routing("planning")
        task_state.append_log("info", f"路由建议: {routing}")
        r = delegate_to_clipboard(prompt)
        return f"已复制到剪贴板待粘贴: {r}"

    if action.startswith("shell:"):
        import subprocess
        cmd = action.split(":", 1)[1].strip() or detail
        try:
            result = subprocess.run(
                cmd, shell=True, capture_output=True, text=True, timeout=60,
                encoding="utf-8", errors="replace",
            )
            out = result.stdout[-500:] if result.stdout else ""
            err = result.stderr[-200:] if result.stderr else ""
            return f"exit={result.returncode}\n{out}\n{err}".strip()
        except subprocess.TimeoutExpired:
            return "命令超时 (60s)"

    if action.startswith("screenshot"):
        try:
            config.assert_automation_allowed()
            from ..capture.screenshot import grab_screen_png
            png = grab_screen_png()
            save_path = config._config_path().parent / "tmp" / f"step_{step['id']}.png"
            save_path.parent.mkdir(parents=True, exist_ok=True)
            save_path.write_bytes(png)
            return f"截图已保存: {save_path}"
        except (PermissionError, RuntimeError) as e:
            return f"截图失败: {e}"

    if action.startswith("wait:"):
        try:
            secs = int(action.split(":", 1)[1].strip())
        except ValueError:
            secs = 10
        task_state.append_log("info", f"等待 {secs} 秒…")
        for _ in range(secs):
            if _stop_event.is_set() or config.is_paused():
                return "等待被中断"
            time.sleep(1)
        return f"等待 {secs}s 完成"

    if action == "verify" or action.startswith("check:"):
        task_state.append_log("info", "验收步骤 — 需人工确认")
        config.set_paused(True)
        task_state.pause_goal()
        _wait_if_paused()
        return "人工验收通过（用户已点击继续）"

    task_state.append_log("warn", f"未知 action 类型: {action}，跳过")
    return f"跳过: {action}"


def _delegate_goal_decomposition(goal: str) -> None:
    """委派外部 AI 做目标分解。简化版：生成 3 个通用步骤。"""
    prompt = compose_prompt_b(
        goal=goal,
        constraints="Windows 环境，可用 Cursor / PowerShell / Python。请给出 3-5 个粗粒度执行步骤。",
    )
    delegate_to_clipboard(prompt)
    task_state.append_log("info", "目标分解提示已复制到剪贴板，请粘贴给 AI 后将步骤填回。")

    task_state.add_step("ask:目标分解", f"请将 AI 给出的步骤手动添加回来。原始目标: {goal}")
    task_state.add_step("verify", "确认步骤列表完整后继续")

    config.set_paused(True)
    task_state.pause_goal()

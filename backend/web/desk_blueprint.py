# -*- coding: utf-8 -*-
"""Flask Blueprint：把 desk_automation 的全部 API 挂到同一 Flask 进程（不再需要独立 httpd）。"""
from __future__ import annotations

import json
import os
import queue
from typing import Any

from flask import Blueprint, jsonify, request, Response, send_file, stream_with_context

desk_bp = Blueprint("desk", __name__)


@desk_bp.errorhandler(PermissionError)
def _desk_handle_permission(e: PermissionError) -> Any:
    """总开关关闭或已暂停时，键鼠链路会抛 PermissionError；返回 JSON+403，避免浏览器只看到 500 HTML。"""
    return jsonify({"ok": False, "error": str(e)}), 403


# ~/.miro/tmp/vision/ 下允许被浏览器拉取的文件名（防路径穿越）
_VISION_ARTIFACT_ALLOW: frozenset[str] = frozenset(
    {
        "vision_som_latest.png",
        "vision_raw_latest.png",
        "vision_latest_meta.json",
    }
)


def _json_body() -> dict[str, Any]:
    return request.get_json(force=True, silent=True) or {}


def _check_vision_support() -> tuple[bool, str]:
    try:
        from backend.runtime.agent_loop import _create_backend
        from backend.web.app import _active_workspace_root, _load_config_for_workspace

        config = _load_config_for_workspace(_active_workspace_root())
        backend = _create_backend(config)
        if backend.supports_vision:
            return True, ""

        # Even if the legacy supports_vision flag is False, the model may
        # have a native Computer-Use API (e.g. GPT-5.x computer_use_preview,
        # Claude computer_use).  Check the CUA protocol detector before
        # rejecting.
        try:
            from backend.tools.desk_automation.orchestrator.screen_reader import (
                _should_use_native_cua,
            )
            model_name = config.llm_model or ""
            backend_type = getattr(config, "provider_id", "") or ""
            if _should_use_native_cua(model_name, backend_type):
                return True, ""
        except Exception:
            pass

        return False, (
            f"当前模型 {config.llm_model or '(未配置)'} 不支持视觉功能，无法使用桌面操控。\n\n"
            "支持视觉的模型包括 Claude Opus/Sonnet、GPT-4o/5.x、Gemini、Qwen-VL "
            "以及本地 LLaVA/BakLLaVA。DeepSeek 系列目前不支持视觉。"
        )
    except Exception as exc:
        return False, f"无法确认当前模型是否支持视觉功能: {type(exc).__name__}: {exc}"


# ── 状态 / 开关 ──────────────────────────────────────────────

@desk_bp.route("/api/status")
def desk_status():
    from backend.tools.desk_automation import config
    from backend.tools.desk_automation.orchestrator import task_state, vision_loop
    from backend.tools.desk_automation.orchestrator.goal_runner import is_running as goal_is_running

    cfg = config.load_config()
    ts = task_state.load_state()
    vs = vision_loop.get_state()
    return jsonify({
        "enabled": cfg.get("enabled"),
        "paused": config.is_paused(),
        "port": cfg.get("http_port"),
        "exec_mode": cfg.get("exec_mode", "auto"),
        "human_core": config.get_human_policy().get("human_core", "som"),
        "goal": ts.get("current_goal", ""),
        "goal_status": ts.get("status", "idle"),
        "goal_running": goal_is_running(),
        "vision_status": vs.get("status", "idle"),
        "vision_running": vision_loop.is_running(),
        "vision_goal": vs.get("goal", ""),
        "vision_step": vs.get("step_count", 0),
        "vision_max_steps": vs.get("max_steps", 0),
        # 前端 <img> 轮询用（加 query 可破缓存）
        "vision_som_url": "/api/vision/artifacts/vision_som_latest.png",
        "vision_raw_url": "/api/vision/artifacts/vision_raw_latest.png",
    })


@desk_bp.route("/api/desk/stream")
def desk_sse_stream():
    """Server-Sent Events：急停 interrupt + vision_state（顶栏徽章零延迟）。"""
    from backend.web import desk_sse

    desk_sse.ensure_interrupt_forwarder()

    def _gen():
        snap = desk_sse.initial_snapshot_payload()
        yield f"data: {json.dumps(snap, ensure_ascii=False)}\n\n"
        q: queue.Queue[str] = desk_sse.subscribe()
        try:
            while True:
                try:
                    line = q.get(timeout=25.0)
                    yield f"data: {line}\n\n"
                except queue.Empty:
                    yield ": keepalive\n\n"
        finally:
            desk_sse.unsubscribe(q)

    return Response(
        stream_with_context(_gen()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@desk_bp.route("/api/enabled", methods=["POST"])
def desk_enabled():
    from backend.tools.desk_automation import config
    body = _json_body()
    config.save_config({"enabled": bool(body.get("enabled"))})
    return jsonify({"ok": True, "enabled": bool(body.get("enabled"))})


@desk_bp.route("/api/pause", methods=["POST"])
def desk_pause():
    from backend.tools.desk_automation import config
    config.set_paused(True)
    return jsonify({"ok": True})


@desk_bp.route("/api/resume", methods=["POST"])
def desk_resume_pause():
    from backend.tools.desk_automation import config
    config.set_paused(False)
    return jsonify({"ok": True})


@desk_bp.route("/api/exec_mode", methods=["POST"])
def desk_exec_mode():
    from backend.tools.desk_automation import config
    body = _json_body()
    mode = str(body.get("mode", "auto"))
    if mode not in config.VALID_EXEC_MODES:
        return jsonify({"error": f"invalid mode: {mode}"}), 400
    config.save_config({"exec_mode": mode})
    return jsonify({"ok": True, "exec_mode": mode})


@desk_bp.route("/api/human_core", methods=["GET", "POST"])
def desk_human_core():
    """读写 human 模式的核心实现: som / llm / multimodal。"""
    from backend.tools.desk_automation import config
    if request.method == "GET":
        hp = config.get_human_policy()
        return jsonify({"human_core": hp.get("human_core", "som")})
    body = _json_body()
    core = str(body.get("core", "som"))
    if core not in ("som", "llm", "multimodal"):
        return jsonify({"error": f"invalid core: {core}"}), 400
    cfg = config.load_config()
    hp = cfg.get("human_policy") or {}
    hp["human_core"] = core
    config.save_config({"human_policy": hp})
    return jsonify({"ok": True, "human_core": core})


# ── 环境感知 ──────────────────────────────────────────────────

@desk_bp.route("/api/inventory/software")
def inv_software():
    from backend.tools.desk_automation.inventory.scan_software import scan_installed_software
    return jsonify(scan_installed_software())


@desk_bp.route("/api/inventory/cli")
def inv_cli():
    from backend.tools.desk_automation.inventory.scan_cli import scan_cli_candidates
    return jsonify(scan_cli_candidates())


@desk_bp.route("/api/inventory/env")
def inv_env():
    from backend.tools.desk_automation.inventory.scan_env import snapshot_environment
    return jsonify(snapshot_environment())


@desk_bp.route("/api/inventory/windows")
def inv_windows():
    from backend.tools.desk_automation.inventory.scan_windows import list_visible_windows
    return jsonify({"windows": list_visible_windows()})


@desk_bp.route("/api/inventory/processes")
def inv_processes():
    from backend.tools.desk_automation.inventory.scan_windows import list_running_processes
    return jsonify({"processes": list_running_processes()})


@desk_bp.route("/api/inventory/shortcuts")
def inv_shortcuts():
    from backend.tools.desk_automation.inventory.scan_windows import list_start_menu_shortcuts
    return jsonify({"shortcuts": list_start_menu_shortcuts()})


# ── 截图 ──────────────────────────────────────────────────────

@desk_bp.route("/api/screenshot.png")
def desk_screenshot():
    from backend.tools.desk_automation.capture.screenshot import grab_screen_png
    png = grab_screen_png()
    return Response(png, mimetype="image/png")


@desk_bp.route("/api/capture/window", methods=["POST"])
def desk_window_shot():
    from backend.tools.desk_automation.capture.window_shot import grab_window_png
    body = _json_body()
    title = str(body.get("title", ""))
    if not title:
        return jsonify({"error": "need title"}), 400
    png = grab_window_png(title)
    if not png:
        return jsonify({"error": "window not found"}), 404
    return Response(png, mimetype="image/png")


@desk_bp.route("/api/capture/monitor")
def desk_monitor():
    from backend.tools.desk_automation.capture.screenshot import monitor_info
    return jsonify(monitor_info())


# ── 视觉循环 ─────────────────────────────────────────────────

@desk_bp.route("/api/vision/start", methods=["POST"])
def vision_start():
    from backend.tools.desk_automation.orchestrator import vision_loop
    body = _json_body()
    goal = str(body.get("goal", ""))
    if not goal:
        return jsonify({"error": "need goal"}), 400
    supported, message = _check_vision_support()
    if not supported:
        return jsonify({"ok": False, "error": message}), 400
    ms = int(body.get("max_steps", 50))
    mode = body.get("exec_mode")
    r = vision_loop.start(goal, max_steps=ms, exec_mode=mode)
    return jsonify(r)


@desk_bp.route("/api/vision/stop", methods=["POST"])
def vision_stop():
    from backend.tools.desk_automation.orchestrator import vision_loop
    return jsonify(vision_loop.stop())


@desk_bp.route("/api/vision/resume", methods=["POST"])
def vision_resume():
    """恢复视觉循环：清除 interrupt_manager ESC 标志 + pause 文件；可选更新目标与附加指令。"""
    from backend.tools.desk_automation.orchestrator import vision_loop

    body = _json_body()
    goal_raw = body.get("goal")
    extra_raw = body.get("extra_context") or body.get("new_instruction")
    g = goal_raw.strip() if isinstance(goal_raw, str) else None
    ex = extra_raw.strip() if isinstance(extra_raw, str) else None
    return jsonify(vision_loop.resume(new_goal=g or None, extra_context=ex or None))


@desk_bp.route("/api/vision/terminate", methods=["POST"])
def vision_terminate():
    """彻底结束当前视觉任务（与 pause 不同：置 idle，不再保留 paused 可恢复态）。"""
    from backend.tools.desk_automation.orchestrator import vision_loop

    return jsonify(vision_loop.terminate_run())


@desk_bp.route("/api/vision/state")
def vision_state():
    from backend.tools.desk_automation.orchestrator import vision_loop
    return jsonify(vision_loop.get_state())


@desk_bp.route("/api/vision/screenshot")
def vision_screenshot():
    from backend.tools.desk_automation.orchestrator import vision_loop
    import os
    path = vision_loop.get_state().get("last_screenshot_path", "")
    if path and os.path.isfile(path):
        return send_file(path, mimetype="image/png")
    return jsonify({"error": "no screenshot yet"}), 404


@desk_bp.route("/api/vision/artifacts/<path:filename>", methods=["GET"])
def vision_artifacts(filename: str) -> Any:
    """只读暴露 ~/.miro/tmp/vision/ 下最新 SoM / 原图，供前端 <img> 轮询。

    仅允许白名单文件名，禁止子路径穿越（不用 secure_filename，避免误改合法文件名）。
    """
    from backend.tools.desk_automation import config

    if "/" in filename or "\\" in filename or filename != os.path.basename(filename):
        return jsonify({"error": "forbidden"}), 403
    if filename not in _VISION_ARTIFACT_ALLOW:
        return jsonify({"error": "forbidden"}), 403

    root = config.vision_artifacts_dir().resolve()
    target = (root / filename).resolve()
    try:
        target.relative_to(root)
    except ValueError:
        return jsonify({"error": "forbidden"}), 403

    if not target.is_file():
        return jsonify({"error": "not found"}), 404

    mt = "image/png" if filename.endswith(".png") else "application/json; charset=utf-8"
    return send_file(target, mimetype=mt, max_age=0)


# ── 目标编排 ──────────────────────────────────────────────────

@desk_bp.route("/api/goal/start", methods=["POST"])
def goal_start():
    from backend.tools.desk_automation.orchestrator.goal_runner import start_goal
    body = _json_body()
    goal = str(body.get("goal", ""))
    if not goal:
        return jsonify({"error": "need goal"}), 400
    r = start_goal(goal)
    return jsonify(r)


@desk_bp.route("/api/goal/stop", methods=["POST"])
def goal_stop():
    from backend.tools.desk_automation.orchestrator.goal_runner import request_stop
    request_stop()
    return jsonify({"ok": True})


@desk_bp.route("/api/goal/state")
def goal_state():
    from backend.tools.desk_automation.orchestrator import task_state
    return jsonify(task_state.load_state())


@desk_bp.route("/api/goal/step", methods=["POST"])
def goal_add_step():
    from backend.tools.desk_automation.orchestrator import task_state
    body = _json_body()
    action = str(body.get("action", ""))
    detail = body.get("detail", "")
    task_state.add_step(action, detail)
    return jsonify({"ok": True})


@desk_bp.route("/api/goal/log")
def goal_log():
    from backend.tools.desk_automation.orchestrator import task_state
    n = int(request.args.get("n", 50))
    full_log = task_state.load_state().get("log", [])
    return jsonify({"log": full_log[-n:]})


# ── NLU / 路由 ───────────────────────────────────────────────

@desk_bp.route("/api/nlu/classify", methods=["POST"])
def nlu_classify():
    from backend.tools.desk_automation.orchestrator.nlu import classify_intent, compile_context
    body = _json_body()
    msg = str(body.get("message", ""))
    intent = classify_intent(msg)
    ctx = compile_context(msg)
    return jsonify({"intent": intent, "constraints": ctx.constraints})


@desk_bp.route("/api/routing")
def desk_routing():
    from backend.tools.desk_automation.orchestrator.ai_bridge import suggest_routing
    t = request.args.get("type", "code_generation")
    return jsonify(suggest_routing(t))


# ── Cursor / 委派 ────────────────────────────────────────────

@desk_bp.route("/api/cursor/status")
def cursor_status():
    from backend.tools.desk_automation.orchestrator.cursor_bridge import find_cursor_window
    info = find_cursor_window()
    return jsonify(info or {"found": False})


@desk_bp.route("/api/delegate/clipboard", methods=["POST"])
def delegate_clipboard():
    from backend.tools.desk_automation.orchestrator.ai_bridge import delegate_to_clipboard
    body = _json_body()
    delegate_to_clipboard(str(body.get("prompt", "")))
    return jsonify({"ok": True})


@desk_bp.route("/api/delegate/compose", methods=["POST"])
def delegate_compose():
    from backend.tools.desk_automation.orchestrator.ai_bridge import compose_prompt_a, compose_prompt_b
    body = _json_body()
    tpl = body.get("template", "a")
    if tpl == "b":
        prompt = compose_prompt_b(body.get("goal", ""), body.get("constraints", ""))
    else:
        prompt = compose_prompt_a(
            body.get("background", ""),
            body.get("problem", ""),
            body.get("tried", ""),
            body.get("need", ""),
        )
    return jsonify({"prompt": prompt})


# ── human / OCR / archive 策略查询 ───────────────────────────

@desk_bp.route("/api/human/policy")
def human_policy():
    from backend.tools.desk_automation import config
    return jsonify(config.get_human_policy())


@desk_bp.route("/api/ocr/status")
def ocr_status():
    from backend.tools.desk_automation.orchestrator.ocr_locate import get_ocr_info
    return jsonify(get_ocr_info())


@desk_bp.route("/api/archive/status")
def archive_status():
    from backend.tools.desk_automation.inventory.machine_archive import get_archive_status
    return jsonify(get_archive_status())


@desk_bp.route("/api/archive/scan", methods=["POST"])
def archive_scan():
    from backend.tools.desk_automation.inventory.machine_archive import get_or_refresh_listing
    body = _json_body()
    p = str(body.get("path", ""))
    if not p:
        return jsonify({"error": "need path"}), 400
    return jsonify(get_or_refresh_listing(p, force=bool(body.get("force"))))


@desk_bp.route("/api/archive/note", methods=["POST"])
def archive_note():
    from backend.tools.desk_automation.inventory.machine_archive import set_path_note
    body = _json_body()
    p = str(body.get("path", ""))
    if not p:
        return jsonify({"error": "need path"}), 400
    return jsonify(set_path_note(p, str(body.get("note", ""))))

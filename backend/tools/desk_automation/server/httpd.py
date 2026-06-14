# -*- coding: utf-8 -*-
"""本机 HTTP 控制面：仅绑定 127.0.0.1。启动: python -m tools.desk_automation"""

from __future__ import annotations

import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from .. import config
from ..capture.screenshot import grab_screen_png, monitor_info
from ..capture.window_shot import grab_window_png
from ..input.actions import click_at, press_key, type_text
from ..input.file_ops import open_in_explorer, prepare_for_upload
from ..inventory.scan_cli import scan_cli_candidates
from ..inventory.scan_env import snapshot_environment
from ..inventory.scan_software import scan_installed_software
from ..inventory.machine_archive import (
    build_context_snippet_for_goal,
    get_archive_status,
    get_or_refresh_listing,
    set_path_note,
)
from ..inventory.scan_windows import list_running_processes, list_start_menu_shortcuts, list_visible_windows
from ..orchestrator import task_state
from ..orchestrator.goal_runner import is_running as goal_is_running, request_stop, start_goal
from ..orchestrator.cursor_bridge import find_cursor_window
from ..orchestrator.ai_bridge import compose_prompt_a, compose_prompt_b, delegate_to_clipboard, suggest_routing
from ..orchestrator import vision_loop
from ..orchestrator.nlu import classify_intent, compile_context, build_extra_context
from ..orchestrator.ocr_locate import get_ocr_info

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"


def _client_local(handler: BaseHTTPRequestHandler) -> bool:
    host = handler.client_address[0]
    return host in ("127.0.0.1", "::1", "localhost")


def _json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    n = int(handler.headers.get("Content-Length") or 0)
    if n <= 0:
        return {}
    raw = handler.rfile.read(n)
    try:
        return json.loads(raw.decode("utf-8"))
    except json.JSONDecodeError:
        return {}


class DeskHTTPHandler(BaseHTTPRequestHandler):
    server_version = "DeskAutomation/0.1"

    def log_message(self, fmt: str, *args: Any) -> None:
        # 减少控制台噪音
        pass

    def _send(
        self,
        code: int,
        body: bytes,
        ctype: str,
        *,
        no_cache: bool = False,
    ) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        # 控制页曾被浏览器强缓存成「永远旧版」；禁止缓存 HTML，改完即生效
        if no_cache:
            self.send_header("Cache-Control", "no-store, max-age=0, must-revalidate")
            self.send_header("Pragma", "no-cache")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, code: int, obj: Any) -> None:
        data = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self._send(code, data, "application/json; charset=utf-8")

    def do_GET(self) -> None:  # noqa: N802
        if not _client_local(self):
            self._json(403, {"error": "forbidden"})
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"

        if path in ("/", "/control.html"):
            p = STATIC_DIR / "control.html"
            if p.is_file():
                self._send(
                    200,
                    p.read_bytes(),
                    "text/html; charset=utf-8",
                    no_cache=True,
                )
            else:
                self._json(404, {"error": "missing static/control.html"})
            return

        if path == "/api/status":
            cfg = config.load_config()
            ts = task_state.load_state()
            vs = vision_loop.get_state()
            self._json(
                200,
                {
                    "enabled": cfg.get("enabled"),
                    "paused": config.is_paused(),
                    "port": cfg.get("http_port"),
                    "exec_mode": cfg.get("exec_mode", "auto"),
                    "goal": ts.get("current_goal", ""),
                    "goal_status": ts.get("status", "idle"),
                    "goal_running": goal_is_running(),
                    "vision_status": vs.get("status", "idle"),
                },
            )
            return

        if path == "/api/inventory/software":
            self._json(200, scan_installed_software())
            return

        if path == "/api/inventory/cli":
            self._json(200, scan_cli_candidates())
            return

        if path == "/api/inventory/env":
            self._json(200, snapshot_environment())
            return

        if path == "/api/inventory/windows":
            self._json(200, {"windows": list_visible_windows()})
            return

        if path == "/api/inventory/processes":
            self._json(200, {"processes": list_running_processes()})
            return

        if path == "/api/inventory/shortcuts":
            self._json(200, {"shortcuts": list_start_menu_shortcuts()})
            return

        if path == "/api/monitors":
            try:
                self._json(200, monitor_info())
            except RuntimeError as e:
                self._json(500, {"error": str(e)})
            return

        if path == "/api/screenshot.png":
            try:
                png = grab_screen_png()
                self._send(200, png, "image/png")
            except (PermissionError, RuntimeError) as e:
                self._json(403 if isinstance(e, PermissionError) else 500, {"error": str(e)})
            return

        # ── 编排引擎 GET ──

        if path == "/api/goal/state":
            self._json(200, task_state.load_state())
            return

        if path == "/api/goal/log":
            st = task_state.load_state()
            qs = urllib.parse.parse_qs(parsed.query)
            last_n = int(qs.get("n", ["50"])[0])
            self._json(200, {"log": st.get("log", [])[-last_n:]})
            return

        if path == "/api/cursor/status":
            self._json(200, find_cursor_window())
            return

        if path == "/api/routing":
            qs = urllib.parse.parse_qs(parsed.query)
            task_type = qs.get("type", [""])[0]
            self._json(200, suggest_routing(task_type))
            return

        # ── 视觉循环 GET ──

        if path == "/api/vision/state":
            self._json(200, vision_loop.get_state())
            return

        if path == "/api/vision/screenshot":
            tmp = config._config_path().parent / "tmp" / "vision_latest.png"
            if tmp.is_file():
                self._send(200, tmp.read_bytes(), "image/png")
            else:
                self._json(404, {"error": "no screenshot yet"})
            return

        if path == "/api/human/policy":
            self._json(200, config.get_human_policy())
            return

        if path == "/api/ocr/status":
            self._json(200, get_ocr_info())
            return

        if path == "/api/archive/status":
            self._json(200, get_archive_status())
            return

        self._json(404, {"error": "not_found", "path": path})

    def do_POST(self) -> None:  # noqa: N802
        if not _client_local(self):
            self._json(403, {"error": "forbidden"})
            return

        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        body = _json_body(self)

        if path == "/api/enabled":
            en = bool(body.get("enabled"))
            config.save_config({"enabled": en})
            self._json(200, {"ok": True, "enabled": en})
            return

        if path == "/api/pause":
            config.set_paused(True)
            self._json(200, {"ok": True, "paused": True})
            return

        if path == "/api/resume":
            config.set_paused(False)
            self._json(200, {"ok": True, "paused": False})
            return

        if path == "/api/input/click":
            try:
                r = click_at(int(body["x"]), int(body["y"]), str(body.get("button", "left")), int(body.get("clicks", 1)))
                self._json(200, r)
            except (KeyError, ValueError, TypeError) as e:
                self._json(400, {"error": "bad_request", "detail": str(e)})
            except PermissionError as e:
                self._json(403, {"error": str(e)})
            except RuntimeError as e:
                self._json(500, {"error": str(e)})
            return

        if path == "/api/input/type":
            try:
                r = type_text(str(body["text"]), float(body.get("interval", 0.02)))
                self._json(200, r)
            except KeyError as e:
                self._json(400, {"error": "bad_request", "detail": str(e)})
            except PermissionError as e:
                self._json(403, {"error": str(e)})
            except RuntimeError as e:
                self._json(500, {"error": str(e)})
            return

        if path == "/api/input/key":
            try:
                r = press_key(str(body["key"]), int(body.get("presses", 1)))
                self._json(200, r)
            except KeyError as e:
                self._json(400, {"error": "bad_request", "detail": str(e)})
            except PermissionError as e:
                self._json(403, {"error": str(e)})
            except RuntimeError as e:
                self._json(500, {"error": str(e)})
            return

        if path == "/api/capture/window":
            try:
                title = str(body.get("title", ""))
                if not title:
                    self._json(400, {"error": "need title"})
                    return
                png = grab_window_png(title)
                if png is None:
                    self._json(404, {"error": f"no window matching '{title}'"})
                    return
                self._send(200, png, "image/png")
            except PermissionError as e:
                self._json(403, {"error": str(e)})
            except RuntimeError as e:
                self._json(500, {"error": str(e)})
            return

        if path == "/api/file/prepare":
            paths_arg = body.get("paths", [])
            if not paths_arg:
                self._json(400, {"error": "need paths[]"})
                return
            r = prepare_for_upload(*paths_arg)
            self._json(200, r)
            return

        if path == "/api/file/explore":
            target = str(body.get("path", ""))
            if not target:
                self._json(400, {"error": "need path"})
                return
            r = open_in_explorer(target)
            self._json(200 if r.get("ok") else 400, r)
            return

        # ── 编排引擎 POST ──

        if path == "/api/goal/start":
            goal = str(body.get("goal", ""))
            if not goal:
                self._json(400, {"error": "need goal"})
                return
            steps = body.get("steps")
            r = start_goal(goal, steps)
            self._json(200 if r.get("ok") else 409, r)
            return

        if path == "/api/goal/stop":
            request_stop()
            task_state.pause_goal()
            self._json(200, {"ok": True})
            return

        if path == "/api/goal/step":
            action = str(body.get("action", ""))
            detail = str(body.get("detail", ""))
            if not action:
                self._json(400, {"error": "need action"})
                return
            s = task_state.add_step(action, detail)
            self._json(200, s)
            return

        if path == "/api/goal/finish":
            success = bool(body.get("success", True))
            summary = str(body.get("summary", ""))
            r = task_state.finish_goal(success, summary)
            self._json(200, r)
            return

        if path == "/api/delegate/clipboard":
            prompt = str(body.get("prompt", ""))
            if not prompt:
                self._json(400, {"error": "need prompt"})
                return
            attachments = body.get("attachments")
            r = delegate_to_clipboard(prompt, attachments)
            self._json(200, r)
            return

        if path == "/api/delegate/compose":
            template = str(body.get("template", "a"))
            if template == "b":
                p = compose_prompt_b(
                    goal=str(body.get("goal", "")),
                    constraints=str(body.get("constraints", "")),
                )
            else:
                p = compose_prompt_a(
                    background=str(body.get("background", "")),
                    problem=str(body.get("problem", "")),
                    tried=str(body.get("tried", "")),
                    need=str(body.get("need", "")),
                )
            self._json(200, {"prompt": p})
            return

        # ── 视觉循环 POST ──

        if path == "/api/exec_mode":
            mode = str(body.get("mode", "auto"))
            if mode == "program":
                mode = "skill"
            if mode not in ("auto", "human", "skill"):
                self._json(400, {"error": "mode must be auto/human/skill"})
                return
            config.save_config({"exec_mode": mode})
            self._json(200, {"ok": True, "exec_mode": mode})
            return

        if path == "/api/vision/start":
            goal = str(body.get("goal", ""))
            if not goal:
                self._json(400, {"error": "need goal"})
                return
            max_steps = int(body.get("max_steps", 50))
            exec_mode = body.get("exec_mode")
            ctx = compile_context(goal)
            extra = build_extra_context(ctx)
            arc = build_context_snippet_for_goal(goal)
            if arc:
                extra = f"{extra}\n\n{arc}"
            try:
                r = vision_loop.start(goal, max_steps=max_steps, extra_context=extra, exec_mode=exec_mode)
                self._json(200 if r.get("ok") else 409, r)
            except PermissionError as e:
                self._json(403, {"error": str(e)})
            return

        if path == "/api/vision/stop":
            self._json(200, vision_loop.stop())
            return

        if path == "/api/vision/resume":
            try:
                self._json(200, vision_loop.resume())
            except PermissionError as e:
                self._json(403, {"error": str(e)})
            return

        if path == "/api/nlu/classify":
            msg = str(body.get("message", ""))
            if not msg:
                self._json(400, {"error": "need message"})
                return
            intent = classify_intent(msg)
            ctx = compile_context(msg)
            ex = build_extra_context(ctx)
            arc = build_context_snippet_for_goal(msg)
            if arc:
                ex = f"{ex}\n\n{arc}"
            self._json(200, {
                "intent": intent,
                "constraints": ctx.constraints,
                "extra_context": ex,
            })
            return

        if path == "/api/archive/scan":
            p = str(body.get("path", ""))
            if not p:
                self._json(400, {"error": "need path"})
                return
            force = bool(body.get("force", False))
            self._json(200, get_or_refresh_listing(p, force=force))
            return

        if path == "/api/archive/note":
            p = str(body.get("path", ""))
            if not p:
                self._json(400, {"error": "need path"})
                return
            note = str(body.get("note", ""))
            self._json(200, set_path_note(p, note))
            return

        self._json(404, {"error": "not_found"})


def main() -> None:
    cfg = config.load_config()
    port = int(cfg.get("http_port") or config.DEFAULT_PORT)
    host = "127.0.0.1"
    httpd = ThreadingHTTPServer((host, port), DeskHTTPHandler)
    print(f"desk_automation 控制面 http://{host}:{port}/  （仅本机）")
    print("  总开关默认关；在网页打开「允许桌面自动化」后再截图/键鼠。")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。")


if __name__ == "__main__":
    main()

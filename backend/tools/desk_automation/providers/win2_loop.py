from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import config
from ..orchestrator.screen_reader import ActionType, ScreenAction, call_vision_llm_batch


WIN2_PROVIDER_NAME = "metis-python-window2"
_WINDOW_WAIT_SECONDS = 8.0


@dataclass
class Win2Observation:
    hwnd: int
    title: str
    exe: str
    rect: dict[str, int]
    screenshot_path: str = ""
    screenshot_width: int = 0
    screenshot_height: int = 0
    accessibility: dict[str, Any] = field(default_factory=dict)
    ocr_items: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "hwnd": self.hwnd,
            "title": self.title,
            "exe": self.exe,
            "rect": self.rect,
            "screenshot_path": self.screenshot_path,
            "screenshot_width": self.screenshot_width,
            "screenshot_height": self.screenshot_height,
            "accessibility": self.accessibility,
            "ocr_items": self.ocr_items[:80],
        }


def win2_enabled() -> bool:
    raw = os.environ.get("METIS_DESKTOP_WIN2", "1").strip().lower()
    return raw not in {"0", "false", "no", "off"} and sys.platform == "win32"


def status() -> dict[str, Any]:
    payload: dict[str, Any] = {
        "ok": False,
        "provider": WIN2_PROVIDER_NAME,
        "platform": sys.platform,
        "enabled": win2_enabled(),
        "features": [
            "list_windows",
            "activate_window",
            "window_capture",
            "accessibility_metadata",
            "window_relative_click",
            "type_text",
            "press_key",
            "scroll",
            "vision_planner",
            "legacy_fallback",
        ],
    }
    if not win2_enabled():
        payload["error"] = "Win2 provider disabled or not running on Windows"
        return payload
    try:
        from ..capture.window_manager import list_windows

        windows = list_windows()
        payload.update(
            {
                "ok": True,
                "window_count": len(windows),
                "windows": [_compact_window(w) for w in windows[:40]],
                "shortcuts": _list_launch_shortcuts()[:80],
            }
        )
    except Exception as exc:
        payload["error"] = f"{type(exc).__name__}: {exc}"
    return payload


def observe(hwnd: int = 0, title: str = "", include_ocr: bool = False) -> dict[str, Any]:
    if not win2_enabled():
        return _error("Win2 provider disabled or unavailable", fallback=True)
    try:
        win = _resolve_window(hwnd=hwnd, title=title)
        if win is None:
            return _error("No matching window", fallback=True)
        obs = _capture_observation(win.hwnd, include_ocr=include_ocr)
        return {"ok": True, "provider": WIN2_PROVIDER_NAME, "observation": obs.to_dict()}
    except Exception as exc:
        return _error(f"{type(exc).__name__}: {exc}", fallback=True)


def act(
    hwnd: int,
    action: str,
    x: int = 0,
    y: int = 0,
    text: str = "",
    key: str = "",
    keys: list[str] | str | None = None,
    scroll_delta: int = 0,
    start_x: int = 0,
    start_y: int = 0,
    end_x: int = 0,
    end_y: int = 0,
) -> dict[str, Any]:
    if not win2_enabled():
        return _error("Win2 provider disabled or unavailable", fallback=True)
    try:
        config.assert_automation_allowed()
        win = _resolve_window(hwnd=hwnd)
        if win is None:
            return _error(f"Window not found: {hwnd}", fallback=True)
        if str(action or "").strip().lower() == "activate":
            from ..capture.window_manager import activate_window

            return {
                "ok": bool(activate_window(win.hwnd)),
                "provider": WIN2_PROVIDER_NAME,
                "action": "activate",
                "hwnd": win.hwnd,
            }
        result = _execute_action(
            win.hwnd,
            ScreenAction(
                _action_type(action),
                {
                    "x": int(x),
                    "y": int(y),
                    "text": str(text or ""),
                    "key": str(key or ""),
                    "keys": _normalize_key_list(keys, fallback=key),
                    "clicks": int(scroll_delta or 0),
                    "start_x": int(start_x),
                    "start_y": int(start_y),
                    "end_x": int(end_x),
                    "end_y": int(end_y),
                },
                "manual win2 action",
            ),
        )
        return {"ok": True, "provider": WIN2_PROVIDER_NAME, "action": action, "result": result}
    except Exception as exc:
        return _error(f"{type(exc).__name__}: {exc}", fallback=True)


def run_task(goal: str, max_steps: int = 20, prefer_existing: bool = False) -> dict[str, Any]:
    """Run a synchronous Window2-style observe-plan-act-verify loop.

    This is intentionally conservative: it only drives a resolved target window.
    If it cannot find or launch one, it asks the caller to fall back to the legacy
    full-screen vision loop.
    """
    goal = str(goal or "").strip()
    max_steps = max(1, min(int(max_steps or 20), 80))
    if not goal:
        return _error("Goal is required", fallback=False)
    if not win2_enabled():
        return _error("Win2 provider disabled or unavailable", fallback=True)

    try:
        config.assert_automation_allowed()
    except Exception as exc:
        return _error(f"Automation not allowed: {exc}", fallback=False)

    try:
        win = _select_or_launch_target(goal, prefer_existing=prefer_existing)
    except Exception as exc:
        return _error(f"Target selection failed: {type(exc).__name__}: {exc}", fallback=True)
    if win is None:
        return _error("No target window found or launched", fallback=True)

    history: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    start = time.time()
    steps_used = 0

    while steps_used < max_steps:
        try:
            obs = _capture_observation(win.hwnd, include_ocr=False)
        except Exception as exc:
            return {
                **_error(f"Observation failed: {type(exc).__name__}: {exc}", fallback=True),
                "history": history[-12:],
            }
        observations.append(_observation_for_log(obs))

        actions = _plan_actions(goal, obs, history)
        if not actions:
            return {
                **_error("Planner returned no actions", fallback=True),
                "history": history[-12:],
                "observations": observations[-4:],
            }

        for planned in actions[:4]:
            if steps_used >= max_steps:
                break
            if planned.action == ActionType.DONE:
                return {
                    "ok": True,
                    "provider": WIN2_PROVIDER_NAME,
                    "status": "done",
                    "goal": goal,
                    "hwnd": win.hwnd,
                    "title": win.title,
                    "steps": steps_used,
                    "elapsed_sec": round(time.time() - start, 2),
                    "reason": planned.reasoning,
                    "history": history[-20:],
                    "observations": observations[-4:],
                    "fallback_recommended": False,
                }
            if planned.action == ActionType.FAIL:
                reason = planned.params.get("reason") or planned.reasoning or "planner failed"
                return {
                    **_error(str(reason), fallback=True),
                    "history": history[-12:],
                    "observations": observations[-4:],
                }

            try:
                result = _execute_action(win.hwnd, planned)
            except Exception as exc:
                result = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}

            steps_used += 1
            row = {
                "step": steps_used,
                "action": planned.action.value,
                "params": planned.params,
                "reasoning": planned.reasoning,
                "result": result,
            }
            history.append(row)
            if not result.get("ok", False):
                break

            if planned.action != ActionType.WAIT:
                time.sleep(0.35)

        # Refresh the window object after possible title/geometry changes.
        refreshed = _resolve_window(hwnd=win.hwnd)
        if refreshed is not None:
            win = refreshed

    return {
        "ok": False,
        "provider": WIN2_PROVIDER_NAME,
        "status": "max_steps",
        "goal": goal,
        "hwnd": win.hwnd,
        "title": win.title,
        "steps": steps_used,
        "elapsed_sec": round(time.time() - start, 2),
        "history": history[-20:],
        "observations": observations[-4:],
        "fallback_recommended": True,
        "error": f"Win2 loop reached max_steps={max_steps}",
    }


def format_tool_result(payload: dict[str, Any]) -> str:
    """Return JSON plus a screenshot line that the multimodal runtime can attach."""
    lines: list[str] = []
    screenshot = _find_screenshot_path(payload)
    if screenshot:
        lines.append(f"Screenshot saved: {screenshot}")
    lines.append(json.dumps(payload, ensure_ascii=False, indent=2, default=str))
    return "\n".join(lines)


def _resolve_window(hwnd: int = 0, title: str = "") -> Any | None:
    from ..capture.window_manager import find_window, get_window

    hwnd = int(hwnd or 0)
    if hwnd:
        return get_window(hwnd)
    if title:
        return find_window(title)
    return None


def _select_or_launch_target(goal: str, *, prefer_existing: bool = False) -> Any | None:
    from ..capture.window_manager import list_windows

    windows = list_windows()
    win = _best_window_match(goal, windows)
    if win is not None or prefer_existing:
        return win

    launch = _best_launch_match(goal)
    if launch:
        try:
            os.startfile(launch["path"])
        except Exception:
            launch = None
        if launch:
            deadline = time.time() + _WINDOW_WAIT_SECONDS
            while time.time() < deadline:
                time.sleep(0.5)
                win = _best_window_match(goal, list_windows(), extra_terms=[launch["name"]])
                if win is not None:
                    return win
    return _best_window_match(goal, list_windows())


def _best_window_match(goal: str, windows: list[Any], extra_terms: list[str] | None = None) -> Any | None:
    terms = _goal_terms(goal)
    terms.extend(_goal_terms(" ".join(extra_terms or [])))
    best: tuple[int, Any] | None = None
    for win in windows:
        text = _norm_text(" ".join([getattr(win, "title", ""), getattr(win, "exe_name", "")]))
        if not text:
            continue
        score = 0
        for term in terms:
            if term and term in text:
                score += 10 + min(len(term), 12)
        title = _norm_text(getattr(win, "title", ""))
        if title and title in _norm_text(goal):
            score += 12
        if _looks_like_metis_window(win) and score < 24:
            score -= 20
        if score > 0 and (best is None or score > best[0]):
            best = (score, win)
    return best[1] if best and best[0] >= 10 else None


def _best_launch_match(goal: str) -> dict[str, str] | None:
    goal_norm = _norm_text(goal)
    terms = _goal_terms(goal)
    best: tuple[int, dict[str, str]] | None = None
    for item in _list_launch_shortcuts():
        name_norm = _norm_text(item["name"])
        score = 0
        if name_norm and name_norm in goal_norm:
            score += 40
        for term in terms:
            if term and (term in name_norm or name_norm in term):
                score += 12 + min(len(term), 10)
        if score > 0 and (best is None or score > best[0]):
            best = (score, item)
    return best[1] if best and best[0] >= 12 else None


def _list_launch_shortcuts() -> list[dict[str, str]]:
    if sys.platform != "win32":
        return []
    roots = [
        Path(os.environ.get("APPDATA", "")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(os.environ.get("ProgramData", "C:/ProgramData")) / "Microsoft" / "Windows" / "Start Menu" / "Programs",
        Path(os.environ.get("USERPROFILE", "")) / "Desktop",
        Path(os.environ.get("PUBLIC", "C:/Users/Public")) / "Desktop",
    ]
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for path in root.rglob("*.lnk"):
            key = str(path).lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({"name": path.stem, "path": str(path)})
    return sorted(out, key=lambda item: item["name"].lower())


def _capture_observation(hwnd: int, *, include_ocr: bool = False) -> Win2Observation:
    from ..capture.window_manager import capture_window, get_window
    from backend.runtime.tool_registry import _png_dimensions

    win = get_window(int(hwnd))
    if win is None:
        raise RuntimeError(f"Window not found: {hwnd}")
    png = capture_window(win.hwnd)
    if not png:
        raise RuntimeError(f"Failed to capture window {hwnd}")

    path = os.path.join(tempfile.gettempdir(), f"metis_win2_{win.hwnd}.png")
    with open(path, "wb") as handle:
        handle.write(png)
    width, height = _png_dimensions(png)
    ocr_items: list[dict[str, Any]] = []
    if include_ocr:
        ocr_items = _try_ocr(png)
    return Win2Observation(
        hwnd=win.hwnd,
        title=win.title,
        exe=win.exe_name,
        rect=win.rect,
        screenshot_path=path,
        screenshot_width=width,
        screenshot_height=height,
        accessibility=_try_accessibility(win.hwnd),
        ocr_items=ocr_items,
    )


def _plan_actions(goal: str, obs: Win2Observation, history: list[dict[str, Any]]) -> list[ScreenAction]:
    extra = (
        "You are controlling exactly one Windows app window through a Window2-style provider.\n"
        f"Window title: {obs.title}\n"
        f"Window exe: {obs.exe}\n"
        f"Screenshot path: {obs.screenshot_path}\n"
        f"{_format_accessibility_for_prompt(obs.accessibility)}"
        "All coordinates you return must be relative to this window screenshot, "
        "where (0,0) is the top-left of the captured window image. "
        "After each batch, Metis will capture the window again and verify before continuing. "
        "Prefer 1-3 safe actions. Use done only when the goal is visibly complete."
    )
    with open(obs.screenshot_path, "rb") as handle:
        png = handle.read()
    return call_vision_llm_batch(
        screenshot_png=png,
        goal=goal,
        action_history=history,
        extra_context=extra,
        screen_width=obs.screenshot_width or int(obs.rect.get("width") or 0),
        screen_height=obs.screenshot_height or int(obs.rect.get("height") or 0),
    )


def _execute_action(hwnd: int, action: ScreenAction) -> dict[str, Any]:
    from ..capture.window_manager import (
        activate_window,
        click_in_window,
        press_key_in_window,
        scroll_in_window,
        type_in_window,
        window_to_screen,
    )
    from ..input.actions import double_click, drag_to, move_to

    config.assert_automation_allowed()
    p = action.params or {}
    kind = action.action
    if kind == ActionType.CLICK:
        return click_in_window(hwnd, int(p.get("x", 0)), int(p.get("y", 0)))
    if kind == ActionType.DOUBLE_CLICK:
        activate_window(hwnd)
        x, y = window_to_screen(hwnd, int(p.get("x", 0)), int(p.get("y", 0)))
        return double_click(x, y)
    if kind == ActionType.RIGHT_CLICK:
        return click_in_window(hwnd, int(p.get("x", 0)), int(p.get("y", 0)), button="right")
    if kind == ActionType.TYPE:
        return type_in_window(hwnd, str(p.get("text", "")))
    if kind == ActionType.KEY:
        return press_key_in_window(hwnd, _normalize_key(str(p.get("key", ""))))
    if kind == ActionType.HOTKEY:
        from ..input.actions import hotkey

        activate_window(hwnd)
        keys = p.get("keys") or []
        if isinstance(keys, str):
            keys = [keys]
        return hotkey(*[_normalize_key(str(k)) for k in keys])
    if kind == ActionType.SCROLL:
        return scroll_in_window(
            hwnd,
            int(p.get("x", 0)),
            int(p.get("y", 0)),
            delta=int(p.get("clicks", p.get("scroll_delta", -3)) or -3),
        )
    if kind == ActionType.DRAG:
        activate_window(hwnd)
        x1, y1 = window_to_screen(hwnd, int(p.get("start_x", 0)), int(p.get("start_y", 0)))
        x2, y2 = window_to_screen(hwnd, int(p.get("end_x", 0)), int(p.get("end_y", 0)))
        return drag_to(x1, y1, x2, y2)
    if kind == ActionType.MOVE:
        activate_window(hwnd)
        x, y = window_to_screen(hwnd, int(p.get("x", 0)), int(p.get("y", 0)))
        return move_to(x, y)
    if kind == ActionType.WAIT:
        secs = min(30.0, max(0.0, float(p.get("seconds", p.get("duration", 1.0)) or 1.0)))
        time.sleep(secs)
        return {"ok": True, "waited_sec": secs}
    raise ValueError(f"Unsupported Win2 action: {kind}")


def _try_ocr(png: bytes) -> list[dict[str, Any]]:
    try:
        from ..orchestrator.ocr_locate import ocr_scan_all

        items = ocr_scan_all(png)
        return [dict(item) for item in items[:80] if isinstance(item, dict)]
    except Exception:
        return []


def _try_accessibility(hwnd: int) -> dict[str, Any]:
    if sys.platform != "win32":
        return {"available": False, "provider": "win32-child-window", "note": "Windows only"}
    try:
        import ctypes
        import ctypes.wintypes as wt

        user32 = ctypes.windll.user32
        root = _win32_rect(user32, int(hwnd))
        if root["width"] <= 0 or root["height"] <= 0:
            return {
                "available": False,
                "provider": "win32-child-window",
                "note": "Target window has no readable bounds",
            }

        elements: list[dict[str, Any]] = []
        WNDENUMPROC = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)

        def callback(child_hwnd: int, _lparam: int) -> bool:
            if len(elements) >= 120:
                return False
            try:
                if not user32.IsWindowVisible(child_hwnd):
                    return True
                rect = _win32_rect(user32, child_hwnd)
                if rect["width"] <= 0 or rect["height"] <= 0:
                    return True
                text = _win32_text(user32, child_hwnd)
                class_name = _win32_class(user32, child_hwnd)
                if not text and not class_name:
                    return True
                elements.append(
                    {
                        "hwnd": int(child_hwnd),
                        "role": "win32_child",
                        "text": text,
                        "class_name": class_name,
                        "rect": {
                            "left": int(rect["left"] - root["left"]),
                            "top": int(rect["top"] - root["top"]),
                            "width": int(rect["width"]),
                            "height": int(rect["height"]),
                        },
                    }
                )
            except Exception:
                return True
            return True

        user32.EnumChildWindows(int(hwnd), WNDENUMPROC(callback), 0)
        return {
            "available": bool(elements),
            "provider": "win32-child-window",
            "element_count": len(elements),
            "elements": elements,
            "note": (
                "Best-effort Win32 child-window metadata. Modern Chromium/canvas apps "
                "may expose little text here; screenshot vision remains authoritative."
            ),
        }
    except Exception as exc:
        return {
            "available": False,
            "provider": "win32-child-window",
            "error": f"{type(exc).__name__}: {exc}",
        }


def _win32_text(user32: Any, hwnd: int) -> str:
    import ctypes

    length = int(user32.GetWindowTextLengthW(hwnd))
    if length <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buf, length + 1)
    return str(buf.value or "")


def _win32_class(user32: Any, hwnd: int) -> str:
    import ctypes

    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return str(buf.value or "")


def _win32_rect(user32: Any, hwnd: int) -> dict[str, int]:
    import ctypes
    import ctypes.wintypes as wt

    rect = wt.RECT()
    if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
        return {"left": 0, "top": 0, "width": 0, "height": 0}
    return {
        "left": int(rect.left),
        "top": int(rect.top),
        "width": int(rect.right - rect.left),
        "height": int(rect.bottom - rect.top),
    }


def _format_accessibility_for_prompt(accessibility: dict[str, Any]) -> str:
    if not isinstance(accessibility, dict) or not accessibility.get("available"):
        note = str(accessibility.get("note") or accessibility.get("error") or "not available")
        return f"Accessibility metadata: unavailable ({note}).\n"
    elements = accessibility.get("elements")
    if not isinstance(elements, list) or not elements:
        return "Accessibility metadata: available but empty.\n"
    lines = ["Accessibility metadata (window-relative rects):"]
    for item in elements[:30]:
        if not isinstance(item, dict):
            continue
        rect = item.get("rect") if isinstance(item.get("rect"), dict) else {}
        text = str(item.get("text") or "").strip()
        class_name = str(item.get("class_name") or "").strip()
        label = text or class_name or "unnamed"
        lines.append(
            "- "
            f"{label[:80]} "
            f"class={class_name[:40] or '?'} "
            f"rect=({rect.get('left', '?')},{rect.get('top', '?')},"
            f"{rect.get('width', '?')},{rect.get('height', '?')})"
        )
    return "\n".join(lines) + "\n"


def _goal_terms(goal: str) -> list[str]:
    text = _norm_text(goal)
    candidates = re.split(r"[\s,，。；;:：/\\|()\[\]\"'“”（）【】]+", text)
    stop = {
        "打开",
        "桌面",
        "桌面的",
        "应用",
        "软件",
        "搜索",
        "查找",
        "输入",
        "里面",
        "其中",
        "open",
        "search",
        "find",
        "app",
    }
    terms: list[str] = []
    for item in candidates:
        item = item.strip()
        if len(item) < 2 or item in stop:
            continue
        stripped = item
        for prefix in ("打开", "启动", "运行", "搜索", "查找", "找"):
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix):]
        for prefix in ("桌面的", "桌面"):
            if stripped.startswith(prefix):
                stripped = stripped[len(prefix):]
        for suffix in ("应用", "软件", "客户端"):
            if stripped.endswith(suffix):
                stripped = stripped[: -len(suffix)]
        stripped = stripped.strip()
        if len(stripped) >= 2 and stripped not in stop:
            terms.append(stripped)
    # Chinese goals often contain "打开桌面的X"; recover X as a separate term.
    match = re.search(r"(?:打开|启动|运行)(?:桌面(?:的)?|电脑上(?:的)?|本机(?:的)?)?([^,，。；;]+)", text)
    if match:
        name = match.group(1)
        name = re.split(r"(?:搜索|查找|找|输入|并|然后)", name, maxsplit=1)[0]
        for junk in ("应用", "软件", "客户端"):
            name = name.replace(junk, "")
        name = re.sub(r"^(?:的|上|里|中的)+", "", name)
        name = name.strip()
        if len(name) >= 2:
            terms.insert(0, name)
    dedup: list[str] = []
    for term in terms:
        if term and term not in dedup:
            dedup.append(term)
    return dedup[:12]


def _norm_text(value: Any) -> str:
    text = str(value or "").lower()
    return re.sub(r"\s+", "", text)


def _compact_window(win: Any) -> dict[str, Any]:
    return {
        "hwnd": getattr(win, "hwnd", 0),
        "title": getattr(win, "title", ""),
        "exe": getattr(win, "exe_name", ""),
        "rect": getattr(win, "rect", {}),
        "is_foreground": getattr(win, "is_foreground", False),
    }


def _observation_for_log(obs: Win2Observation) -> dict[str, Any]:
    return {
        "hwnd": obs.hwnd,
        "title": obs.title,
        "exe": obs.exe,
        "size": [obs.screenshot_width, obs.screenshot_height],
        "screenshot_path": obs.screenshot_path,
    }


def _looks_like_metis_window(win: Any) -> bool:
    title = _norm_text(getattr(win, "title", ""))
    exe = _norm_text(getattr(win, "exe_name", ""))
    return "metis" in title or "miro" in title or "electron" in exe


def _action_type(value: str) -> ActionType:
    mapping = {
        "activate": ActionType.WAIT,
        "click": ActionType.CLICK,
        "double_click": ActionType.DOUBLE_CLICK,
        "right_click": ActionType.RIGHT_CLICK,
        "type": ActionType.TYPE,
        "key": ActionType.KEY,
        "hotkey": ActionType.HOTKEY,
        "scroll": ActionType.SCROLL,
        "drag": ActionType.DRAG,
        "move": ActionType.MOVE,
        "wait": ActionType.WAIT,
    }
    return mapping.get(str(value or "").strip().lower(), ActionType.WAIT)


def _normalize_key(key: str) -> str:
    lowered = str(key or "").strip().lower()
    aliases = {
        "return": "enter",
        "enter": "enter",
        "escape": "esc",
        "esc": "esc",
        "space": "space",
        "backspace": "backspace",
        "delete": "delete",
        "del": "delete",
        "tab": "tab",
    }
    return aliases.get(lowered, lowered or "enter")


def _normalize_key_list(value: list[str] | str | None, *, fallback: str = "") -> list[str]:
    raw: list[Any]
    if isinstance(value, str):
        raw = [part for part in re.split(r"[+\s,]+", value) if part]
    elif isinstance(value, list):
        raw = value
    else:
        raw = []
    if not raw and fallback:
        raw = [part for part in re.split(r"[+\s,]+", str(fallback)) if part]
    return [_normalize_key(str(item)) for item in raw if str(item).strip()]


def _find_screenshot_path(payload: Any) -> str:
    if isinstance(payload, dict):
        direct = payload.get("screenshot_path")
        if isinstance(direct, str) and direct:
            return direct
        obs = payload.get("observation")
        if isinstance(obs, dict):
            shot = obs.get("screenshot_path")
            if isinstance(shot, str) and shot:
                return shot
        for value in payload.values():
            found = _find_screenshot_path(value)
            if found:
                return found
    if isinstance(payload, list):
        for item in payload:
            found = _find_screenshot_path(item)
            if found:
                return found
    return ""


def _error(message: str, *, fallback: bool) -> dict[str, Any]:
    return {
        "ok": False,
        "provider": WIN2_PROVIDER_NAME,
        "error": message,
        "fallback_recommended": bool(fallback),
    }

from __future__ import annotations

import json
from types import SimpleNamespace

from backend.runtime.tool_registry import ToolRegistry, register_desktop_tools
from backend.runtime import expert_tools
from backend.tools.desk_automation.orchestrator import screen_reader
from backend.tools.desk_automation.providers import win2_loop


def test_goal_terms_extract_chinese_app_name():
    terms = win2_loop._goal_terms("打开桌面的哔哩哔哩，搜索龙族幻想")
    assert "哔哩哔哩" in terms
    assert "龙族幻想" in terms


def test_best_window_match_prefers_task_window_over_metis():
    metis = SimpleNamespace(hwnd=1, title="Metis Desktop", exe_name="electron.exe")
    bili = SimpleNamespace(hwnd=2, title="哔哩哔哩", exe_name="bilibili.exe")
    match = win2_loop._best_window_match("打开哔哩哔哩搜索龙族幻想", [metis, bili])
    assert match is bili


def test_format_tool_result_exposes_screenshot_path():
    payload = {
        "ok": True,
        "observation": {
            "screenshot_path": r"C:\Users\20118\AppData\Local\Temp\metis_win2_1.png"
        },
    }
    text = win2_loop.format_tool_result(payload)
    assert "Screenshot saved:" in text
    assert "metis_win2_1.png" in text


def test_registers_win2_tools():
    registry = ToolRegistry()
    register_desktop_tools(registry)
    for name in {
        "desktop_win2_status",
        "desktop_win2_observe",
        "desktop_win2_action",
        "desktop_win2_task",
        "desktop_win2_verify",
    }:
        assert registry.get(name) is not None


def test_win2_action_schema_matches_manual_actions():
    registry = ToolRegistry()
    register_desktop_tools(registry)
    schema = registry.get("desktop_win2_action").parameters
    props = schema["properties"]

    assert "keys" in props
    assert props["keys"]["type"] == "array"
    for name in {"start_x", "start_y", "end_x", "end_y"}:
        assert name in props


def test_win2_verify_schema_advertises_evidence_chain():
    registry = ToolRegistry()
    register_desktop_tools(registry)
    tool = registry.get("desktop_win2_verify")

    assert tool is not None
    assert "evidence_chain" in tool.description
    assert "assertion" in tool.parameters["properties"]
    assert "require_foreground" in tool.parameters["properties"]


def test_win2_manual_hotkey_and_drag_params_are_passed(monkeypatch):
    captured = []
    monkeypatch.setattr(win2_loop, "win2_enabled", lambda: True)
    monkeypatch.setattr(win2_loop.config, "assert_automation_allowed", lambda: None)
    monkeypatch.setattr(
        win2_loop,
        "_resolve_window",
        lambda hwnd=0, title="": SimpleNamespace(hwnd=123, title="Demo", exe_name="demo.exe"),
    )

    def fake_execute(hwnd, action):
        captured.append((hwnd, action))
        return {"ok": True}

    monkeypatch.setattr(win2_loop, "_execute_action", fake_execute)

    hotkey = win2_loop.act(hwnd=123, action="hotkey", keys=["ctrl", "l"])
    drag = win2_loop.act(
        hwnd=123,
        action="drag",
        start_x=10,
        start_y=20,
        end_x=30,
        end_y=40,
    )

    assert hotkey["ok"] is True
    assert drag["ok"] is True
    assert captured[0][1].params["keys"] == ["ctrl", "l"]
    assert captured[1][1].params["start_x"] == 10
    assert captured[1][1].params["end_y"] == 40


def test_win2_scroll_uses_pagedown_fallback_when_wheel_does_not_move(monkeypatch):
    actions = []
    probes = [
        {"available": True, "signature": "before"},
        {"available": True, "signature": "before"},
        {"available": True, "signature": "after"},
    ]
    monkeypatch.setattr(win2_loop, "win2_enabled", lambda: True)
    monkeypatch.setattr(win2_loop.config, "assert_automation_allowed", lambda: None)
    monkeypatch.setattr(
        win2_loop,
        "_resolve_window",
        lambda hwnd=0, title="": SimpleNamespace(hwnd=123, title="Demo", exe_name="demo.exe"),
    )
    monkeypatch.setattr(win2_loop, "_capture_scroll_probe", lambda hwnd: probes.pop(0))

    def fake_scroll(hwnd, x, y, delta=-3):
        actions.append(("wheel", hwnd, x, y, delta))
        return {"ok": True, "clicks": delta}

    def fake_key(hwnd, key):
        actions.append(("key", hwnd, key))
        return {"ok": True, "key": key}

    monkeypatch.setattr(
        "backend.tools.desk_automation.capture.window_manager.scroll_in_window",
        fake_scroll,
    )
    monkeypatch.setattr(
        "backend.tools.desk_automation.capture.window_manager.press_key_in_window",
        fake_key,
    )

    result = win2_loop.act(hwnd=123, action="scroll", x=20, y=30, scroll_delta=-4)

    assert result["ok"] is True
    payload = result["result"]
    assert payload["fallback_used"] is True
    assert payload["method"] == "pagedown"
    assert payload["scroll_verification"]["changed"] is True
    assert [item["method"] for item in payload["attempts"]] == ["wheel", "pagedown"]
    assert actions == [("wheel", 123, 20, 30, -4), ("key", 123, "pagedown")]


def test_win2_scroll_reports_no_visible_change_after_bounded_fallbacks(monkeypatch):
    probes = [
        {"available": True, "signature": "same"},
        {"available": True, "signature": "same"},
        {"available": True, "signature": "same"},
        {"available": True, "signature": "same"},
    ]
    monkeypatch.setattr(win2_loop, "win2_enabled", lambda: True)
    monkeypatch.setattr(win2_loop.config, "assert_automation_allowed", lambda: None)
    monkeypatch.setattr(
        win2_loop,
        "_resolve_window",
        lambda hwnd=0, title="": SimpleNamespace(hwnd=123, title="Demo", exe_name="demo.exe"),
    )
    monkeypatch.setattr(win2_loop, "_capture_scroll_probe", lambda hwnd: probes.pop(0))
    monkeypatch.setattr(
        "backend.tools.desk_automation.capture.window_manager.scroll_in_window",
        lambda hwnd, x, y, delta=-3: {"ok": True, "clicks": delta},
    )
    monkeypatch.setattr(
        "backend.tools.desk_automation.capture.window_manager.press_key_in_window",
        lambda hwnd, key: {"ok": True, "key": key},
    )
    monkeypatch.setattr(win2_loop, "_press_hotkey_in_window", lambda hwnd, keys: {"ok": True, "keys": keys})

    result = win2_loop.act(hwnd=123, action="scroll", x=20, y=30, scroll_delta=3)

    payload = result["result"]
    assert payload["fallback_used"] is True
    assert payload["scroll_verification"]["changed"] is False
    assert payload["warning"].startswith("Scroll did not produce")
    assert [item["method"] for item in payload["attempts"]] == ["wheel", "pageup", "ctrl+home"]


def test_batch_planner_receives_win2_extra_context(monkeypatch):
    seen = {}
    monkeypatch.setattr(screen_reader, "_load_vision_config", lambda: {"backend": "openai"})

    def fake_call_backend(backend, png, prompt, system, vcfg):
        seen["prompt"] = prompt
        return '[{"action":"done","params":{},"reasoning":"ok"}]'

    monkeypatch.setattr(screen_reader, "_call_backend_with_system", fake_call_backend)

    actions = screen_reader.call_vision_llm_batch(
        screenshot_png=b"png",
        goal="测试窗口任务",
        extra_context="Window title: Demo App\nWindow exe: demo.exe",
        screen_width=100,
        screen_height=80,
    )

    assert actions[0].action == screen_reader.ActionType.DONE
    assert "Window title: Demo App" in seen["prompt"]
    assert "Window exe: demo.exe" in seen["prompt"]


def test_accessibility_summary_uses_window_relative_rects():
    text = win2_loop._format_accessibility_for_prompt(
        {
            "available": True,
            "provider": "win32-child-window",
            "elements": [
                {
                    "text": "Search",
                    "class_name": "Button",
                    "rect": {"left": 12, "top": 34, "width": 56, "height": 20},
                }
            ],
        }
    )

    assert "Accessibility metadata" in text
    assert "Search" in text
    assert "class=Button" in text
    assert "rect=(12,34,56,20)" in text


def test_notepad_fast_path_runs_deterministic_macro(monkeypatch):
    calls = []
    window = SimpleNamespace(hwnd=456, title="无标题 - 记事本", exe_name="notepad.exe")

    monkeypatch.setattr(win2_loop, "win2_enabled", lambda: True)
    monkeypatch.setattr(win2_loop.config, "assert_automation_allowed", lambda: None)
    monkeypatch.setattr(win2_loop.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(win2_loop.subprocess, "Popen", lambda args: calls.append(("popen", args)) or SimpleNamespace(pid=1))
    monkeypatch.setattr(
        "backend.tools.desk_automation.capture.window_manager.list_windows",
        lambda: [] if len([item for item in calls if item[0] == "list"]) == 0 and not calls.append(("list", None)) else [window],
    )
    monkeypatch.setattr(
        "backend.tools.desk_automation.capture.window_manager.activate_window",
        lambda hwnd: calls.append(("activate", hwnd)) or True,
    )

    def fake_execute(hwnd, action):
        calls.append(("execute", hwnd, action.action.value, action.params))
        return {"ok": True}

    monkeypatch.setattr(win2_loop, "_execute_action", fake_execute)
    monkeypatch.setattr(
        win2_loop,
        "verify",
        lambda **kwargs: calls.append(("verify", kwargs)) or {"ok": True, "verdict": {"ok": True}},
    )

    result = win2_loop.run_task("打开记事本，输入“hello metis”", max_steps=20)

    assert result["ok"] is True
    assert result["fast_path"] is True
    assert result["status"] == "fast_path_done"
    assert result["steps"] == 4
    assert any(call[0] == "popen" and call[1] == ["notepad.exe"] for call in calls)
    assert any(call[0] == "execute" and call[3]["text"] == "hello metis" for call in calls)
    assert any(call[0] == "verify" and call[1]["text_contains"] == "hello metis" for call in calls)


def test_desktop_expert_prefers_win2_tools():
    definition = expert_tools._EXPERT_DEFINITIONS["desktop_expert"]
    assert "desktop_win2_task" in definition["tool_whitelist"]
    assert "desktop_win2_observe" in definition["tool_whitelist"]
    assert "desktop_win2_verify" in definition["tool_whitelist"]
    assert definition["max_turns"] > 5


def test_win2_verify_returns_checks_and_evidence(monkeypatch, tmp_path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    monkeypatch.setattr(win2_loop, "win2_enabled", lambda: True)
    monkeypatch.setattr(
        win2_loop,
        "_resolve_window",
        lambda hwnd=0, title="": SimpleNamespace(hwnd=123, title="Demo App", exe_name="demo.exe", is_foreground=True),
    )
    monkeypatch.setattr(
        win2_loop,
        "_capture_observation",
        lambda hwnd, include_ocr=False: win2_loop.Win2Observation(
            hwnd=123,
            title="Demo App",
            exe="demo.exe",
            rect={"width": 800, "height": 600},
            screenshot_path=str(shot),
            screenshot_width=800,
            screenshot_height=600,
            accessibility={
                "available": True,
                "elements": [
                    {
                        "text": "hello world",
                        "class_name": "Edit",
                        "rect": {"left": 10, "top": 20, "width": 200, "height": 30},
                    }
                ],
            },
        ),
    )

    result = win2_loop.verify(
        hwnd=123,
        assertion='检查输入框是否真的写入 "hello"',
        require_foreground=True,
    )

    assert result["ok"] is True
    assert result["checks"]["window_foreground"] is True
    assert result["checks"]["text_visible"] is True
    assert result["checks"]["screenshot_captured"] is True
    assert result["evidence_chain"][-1]["kind"] == "text_match"
    assert result["evidence_schema"] == "metis.verifier.evidence_chain.v2"
    assert result["verdict"]["ok"] is True
    assert result["verdict"]["surface"] == "desktop_win2"
    assert result["evidence_chain_v2"][0]["kind"] == "window"
    assert any(item.get("check") == "text_visible" for item in result["evidence_chain_v2"])


def test_desktop_vision_task_returns_win2_success(monkeypatch):
    registry = ToolRegistry()
    register_desktop_tools(registry)

    def fake_run_task(goal: str, max_steps: int = 20):
        return {
            "ok": True,
            "provider": "metis-python-window2",
            "status": "done",
            "goal": goal,
            "steps": max_steps,
        }

    monkeypatch.setattr(win2_loop, "run_task", fake_run_task)
    result = registry.execute("desktop_vision_task", {"goal": "打开记事本", "max_steps": 2})
    assert '"provider": "metis-python-window2"' in result
    assert '"status": "done"' in result


def test_win2_tool_result_includes_debug_summary() -> None:
    payload = json.loads(
        win2_loop.format_tool_result(
            {
                "ok": False,
                "provider": "metis-python-window2",
                "status": "max_steps",
                "steps": 3,
                "history": [{"action": "click"}],
            }
        )
    )

    assert payload["debug_category"] == "max_steps"
    assert payload["status_chain"] == ["started", "acting", "max_steps"]


def test_desktop_vision_task_falls_back_to_legacy(monkeypatch):
    registry = ToolRegistry()
    register_desktop_tools(registry)

    monkeypatch.setattr(
        win2_loop,
        "run_task",
        lambda goal, max_steps=20: {
            "ok": False,
            "provider": "metis-python-window2",
            "fallback_recommended": True,
            "error": "no target",
        },
    )

    from backend.tools.desk_automation.orchestrator import vision_loop

    monkeypatch.setattr(vision_loop, "start", lambda goal, max_steps, exec_mode: {"ok": True})
    monkeypatch.setattr(
        vision_loop,
        "get_state",
        lambda: {
            "status": "done",
            "action_history": [{"action": "done"}],
            "error": "",
        },
    )

    result = registry.execute("desktop_vision_task", {"goal": "打开未知应用", "max_steps": 2})
    assert '"status": "done"' in result
    assert '"steps": 1' in result
    assert '"win2_attempt"' in result
    assert '"error": "no target"' in result

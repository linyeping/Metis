from __future__ import annotations

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


def test_desktop_expert_prefers_win2_tools():
    definition = expert_tools._EXPERT_DEFINITIONS["desktop_expert"]
    assert "desktop_win2_task" in definition["tool_whitelist"]
    assert "desktop_win2_observe" in definition["tool_whitelist"]
    assert definition["max_turns"] > 5


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

from __future__ import annotations

import json
import threading

from flask import Flask

from backend.web import preview_bridge
from backend.runtime.tool_registry import ToolRegistry, register_desktop_tools
from backend.web.preview_bridge import (
    _reset_preview_bridge_for_tests,
    preview_bridge_bp,
    request_preview_command,
)


def test_preview_bridge_round_trips_command_result() -> None:
    _reset_preview_bridge_for_tests()
    app = Flask(__name__)
    app.register_blueprint(preview_bridge_bp)
    result_holder: dict[str, object] = {}

    def caller() -> None:
        result_holder["result"] = request_preview_command(
            "observe",
            {"maxElements": 3},
            timeout=2,
        )

    thread = threading.Thread(target=caller)
    thread.start()

    with app.test_client() as client:
        response = client.get("/api/preview-browser/next?timeout=1")
        assert response.status_code == 200
        payload = response.get_json()
        request = payload["request"]
        assert request["kind"] == "observe"
        assert request["payload"]["maxElements"] == 3

        response = client.post(
            "/api/preview-browser/result",
            json={"id": request["id"], "result": {"ok": True, "title": "Preview"}},
        )
        assert response.status_code == 200

    thread.join(timeout=2)
    assert result_holder["result"] == {"ok": True, "title": "Preview"}


def test_preview_browser_tools_registered() -> None:
    registry = ToolRegistry()
    register_desktop_tools(registry)

    for name in {
        "preview_browser_status",
        "preview_browser_navigate",
        "preview_browser_observe",
        "preview_browser_action",
        "preview_browser_screenshot",
        "preview_browser_verify",
    }:
        tool = registry.get(name)
        assert tool is not None
        assert tool.source == "desktop"


def test_preview_browser_navigate_allows_auto_local_resolution() -> None:
    registry = ToolRegistry()
    register_desktop_tools(registry)

    tool = registry.get("preview_browser_navigate")

    assert tool is not None
    assert tool.parameters["required"] == []
    assert "METIS_DESKTOP_DEV_SERVER" in tool.description
    assert "5173/5174/3000/4200/8000/8080" in tool.description
    assert "blank/current" in tool.parameters["properties"]["url"]["description"]


def test_preview_browser_tools_advertise_diagnostics() -> None:
    registry = ToolRegistry()
    register_desktop_tools(registry)

    observe = registry.get("preview_browser_observe")
    screenshot = registry.get("preview_browser_screenshot")
    verify = registry.get("preview_browser_verify")

    assert observe is not None
    assert screenshot is not None
    assert verify is not None
    assert "console warnings/errors" in observe.description
    assert "failed network" in observe.description
    assert "page_health" in screenshot.description
    assert "diagnostics" in verify.description


def test_preview_browser_observe_returns_debug_summary(monkeypatch) -> None:
    def fake_request_preview_command(kind: str, payload: dict[str, object], timeout: int = 12) -> dict[str, object]:
        assert kind == "observe"
        return {
            "ok": True,
            "url": "http://localhost:5174",
            "title": "Broken App",
            "diagnostics": {"counts": {"console_errors": 1, "exceptions": 0, "network_failed": 0}},
            "page_health": {"blank": False, "reasons": []},
        }

    monkeypatch.setattr(preview_bridge, "request_preview_command", fake_request_preview_command)
    registry = ToolRegistry()
    register_desktop_tools(registry)
    observe = registry.get("preview_browser_observe")

    assert observe is not None
    result = json.loads(observe.execute_fn())
    assert result["debug_category"] == "console_errors"
    assert "JS" in result["debug_summary"]


def test_preview_browser_verify_supports_browser_verifier(monkeypatch) -> None:
    calls: list[str] = []

    def fake_request_preview_command(kind: str, payload: dict[str, object], timeout: int = 12) -> dict[str, object]:
        calls.append(kind)
        if kind == "observe":
            return {
                "ok": True,
                "url": "http://localhost:5174/login",
                "title": "Metis Login",
                "text": "欢迎 登录 邮箱",
                "elements": [
                    {
                        "element_id": "preview-1",
                        "tag": "button",
                        "role": "",
                        "type": "submit",
                        "text": "登录",
                        "disabled": False,
                        "rect": {"width": 80, "height": 32},
                    },
                    {
                        "element_id": "preview-2",
                        "tag": "input",
                        "type": "email",
                        "placeholder": "邮箱",
                        "labelText": "邮箱",
                        "disabled": False,
                        "readOnly": False,
                        "rect": {"width": 240, "height": 36},
                    },
                ],
                "diagnostics": {
                    "counts": {
                        "console_errors": 0,
                        "exceptions": 0,
                        "network_failed": 0,
                    }
                },
                "dom_summary": {"buttons": 1, "inputs": 1},
                "page_health": {"status": "ok", "blank": False, "reasons": []},
            }
        if kind == "screenshot":
            return {
                "ok": True,
                "url": "http://localhost:5174/login",
                "title": "Metis Login",
                "width": 800,
                "height": 600,
                "page_health": {"status": "ok", "blank": False, "reasons": []},
                "screenshot_health": {
                    "ok": True,
                    "appears_blank": False,
                    "near_white_ratio": 0.2,
                    "near_black_ratio": 0.0,
                    "reasons": [],
                },
            }
        raise AssertionError(f"unexpected preview command: {kind}")

    monkeypatch.setattr(preview_bridge, "request_preview_command", fake_request_preview_command)
    registry = ToolRegistry()
    register_desktop_tools(registry)
    verify = registry.get("preview_browser_verify")

    assert verify is not None
    result = json.loads(
        verify.execute_fn(
            assertion="确认登录按钮可见并可点击",
            input_label="邮箱",
            require_input_editable=True,
            require_no_blank=True,
            require_no_console_errors=True,
            require_no_network_failures=True,
            require_screenshot_not_blank=True,
        )
    )

    assert result["ok"] is True
    assert result["checks"]["button_visible"] is True
    assert result["checks"]["button_clickable"] is True
    assert result["checks"]["input_editable"] is True
    assert result["checks"]["page_not_blank"] is True
    assert result["checks"]["no_console_errors"] is True
    assert result["checks"]["no_network_failures"] is True
    assert result["checks"]["screenshot_not_blank"] is True
    assert result["evidence_schema"] == "metis.verifier.evidence_chain.v2"
    assert result["verdict"]["ok"] is True
    assert result["verdict"]["surface"] == "preview_browser"
    assert result["evidence_chain_v2"][0]["kind"] == "page"
    assert result["evidence_chain"] == result["evidence_chain_v2"]
    assert result["matched_elements"]["button"]["text"] == "登录"
    assert result["matched_elements"]["input"]["placeholder"] == "邮箱"
    assert calls == ["observe", "screenshot"]


def test_preview_browser_verify_extracts_success_prompt_from_assertion(monkeypatch) -> None:
    def fake_request_preview_command(kind: str, payload: dict[str, object], timeout: int = 12) -> dict[str, object]:
        assert kind == "observe"
        return {
            "ok": True,
            "url": "http://localhost:5174/form",
            "title": "Form",
            "text": "保存成功",
            "elements": [],
            "diagnostics": {"counts": {}},
            "dom_summary": {},
            "page_health": {"status": "ok", "blank": False, "reasons": []},
        }

    monkeypatch.setattr(preview_bridge, "request_preview_command", fake_request_preview_command)
    registry = ToolRegistry()
    register_desktop_tools(registry)
    verify = registry.get("preview_browser_verify")

    assert verify is not None
    result = json.loads(verify.execute_fn(assertion="确认提交后出现保存成功提示"))

    assert result["ok"] is True
    assert result["checks"]["visible_text"] is True
    assert result["verdict"]["ok"] is True
    assert result["check_details"]["visible_text"]["query"] == "保存成功"

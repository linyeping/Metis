from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

import pytest

from backend.runtime.cancellation import OperationCancelled, current_cancel_event
from backend.runtime.agent_loop import AgentConfig, run_stream as real_run_stream
from backend.runtime.llm_backends import LLMBackend, LLMResponse, ToolCall
from backend.runtime.tool_registry import ToolDefinition, ToolRegistry
from backend.web import app as web_app
from backend.web import session_routes as web_session_routes
from backend.web import workspace_routes as web_workspace_routes
from backend.web.session_db import MetisSessionDB
from backend.web.sessions import SessionManager
from backend.web.workspaces import WorkspaceManager


class FakeResponse:
    def __init__(self, status_code: int) -> None:
        self.status_code = status_code


class FakeHTTPError(Exception):
    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.response = FakeResponse(status_code)


class CrashingStreamBackend(LLMBackend):
    def chat(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> LLMResponse:
        return LLMResponse(content="unused")

    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> Generator[str, None, LLMResponse]:
        yield "partial"
        raise RuntimeError("flask smoke stream crashed")


class AuthErrorBackend(CrashingStreamBackend):
    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
    ) -> Generator[str, None, LLMResponse]:
        if False:
            yield ""
        raise FakeHTTPError(401, "401 unauthorized invalid api key")


class BlockingStreamBackend(CrashingStreamBackend):
    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        cancel_event: Optional[Any] = None,
    ) -> Generator[str, None, LLMResponse]:
        while cancel_event is None or not cancel_event.is_set():
            time.sleep(0.02)
        raise OperationCancelled("provider stream canceled")


class ToolCallingBackend(CrashingStreamBackend):
    def chat_stream(
        self,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        *,
        temperature: float = 0.3,
        max_tokens: int = 4096,
        timeout: float = 120.0,
        cancel_event: Optional[Any] = None,
    ) -> Generator[str, None, LLMResponse]:
        if False:
            yield ""
        return LLMResponse(
            tool_calls=[
                ToolCall(
                    id="slow-tool-call",
                    name="slow_cancel_tool",
                    arguments={},
                )
            ]
        )


@pytest.fixture
def isolated_flask_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db = MetisSessionDB(data_root=str(tmp_path / ".metis"))
    session_manager = SessionManager(db=db)
    workspace_manager = WorkspaceManager(db=db)
    workspace = workspace_manager.create_workspace(str(tmp_path / "project"), name="Smoke Project")
    registry = ToolRegistry()

    monkeypatch.setattr(web_app, "get_session_manager", lambda: session_manager)
    monkeypatch.setattr(web_app, "get_workspace_manager", lambda: workspace_manager)
    monkeypatch.setattr(web_session_routes, "get_session_manager", lambda: session_manager)
    monkeypatch.setattr(web_session_routes, "get_workspace_manager", lambda: workspace_manager)
    monkeypatch.setattr(web_workspace_routes, "get_session_manager", lambda: session_manager)
    monkeypatch.setattr(web_workspace_routes, "get_workspace_manager", lambda: workspace_manager)
    monkeypatch.setattr(web_app, "get_registry", lambda: registry)
    web_app._runtime_state.clear_session()
    web_app._runtime_state.active_workspace_id = workspace.id
    web_app._runtime_state.learning_nudged_sessions.clear()
    monkeypatch.setattr(web_app, "_permission_locks", {})
    monkeypatch.setattr(web_app, "_permission_results", {})
    monkeypatch.setattr(web_app, "_generate_smart_title", lambda *args, **kwargs: None)
    monkeypatch.setattr(web_app, "should_auto_compact", lambda *args, **kwargs: False)
    monkeypatch.setattr(web_app, "_maybe_record_learning", lambda *args, **kwargs: None)
    with web_app._runs_lock:
        web_app._runs.clear()
    monkeypatch.setattr(
        web_app,
        "_load_config",
        lambda: AgentConfig(
            llm_backend="fake",
            llm_model="fake_flask_smoke",
            timeout=1,
            max_turns=4,
            max_consecutive_errors=1,
        ),
    )

    yield web_app.app, session_manager

    with web_app._runs_lock:
        web_app._runs.clear()


def _collect_sse(response: Any) -> Tuple[List[Dict[str, Any]], bool]:
    text = "".join(
        chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
        for chunk in response.response
    )
    events: List[Dict[str, Any]] = []
    saw_done_marker = False

    for packet in text.split("\n\n"):
        if not packet.strip():
            continue
        for raw_line in packet.splitlines():
            line = raw_line.strip()
            if not line.startswith("data: "):
                continue
            payload = line[len("data: ") :]
            if payload == "[DONE]":
                saw_done_marker = True
            else:
                events.append(json.loads(payload))

    return events, saw_done_marker


def _post_chat(
    client: Any,
    message: str = "hello flask runtime",
    *,
    session_id: str = "",
) -> Tuple[List[Dict[str, Any]], bool]:
    payload = {"message": message}
    if session_id:
        payload["session_id"] = session_id
    response = client.post("/chat", json=payload, buffered=False)
    assert response.status_code == 200
    assert response.content_type.startswith("text/event-stream")
    return _collect_sse(response)


def _run_events(client: Any, run_id: str) -> Tuple[List[Dict[str, Any]], bool]:
    response = client.get(f"/runs/{run_id}/events", buffered=False)
    assert response.status_code == 200
    assert response.content_type.startswith("text/event-stream")
    return _collect_sse(response)


def _phases(events: List[Dict[str, Any]]) -> List[str]:
    return [
        str(event.get("phase") or event.get("payload", {}).get("phase") or "")
        for event in events
        if event.get("kind") == "runtime_status"
    ]


def test_chat_sse_fake_provider_emits_runtime_status_done_and_done_marker(isolated_flask_app: Any) -> None:
    app, _session_manager = isolated_flask_app
    with app.test_client() as client:
        events, saw_done_marker = _post_chat(client)

    assert saw_done_marker is True
    assert all(event["schema"] == "metis.agent_event.v1" for event in events)
    assert [phase for phase in _phases(events) if phase] == [
        "starting",
        "llm_request",
        "streaming",
        "completed",
    ]
    assert "content_delta" in [event["kind"] for event in events]
    assert "content" in [event["kind"] for event in events]
    assert events[-1]["kind"] == "done"


def test_chat_sse_stream_crash_emits_failed_error_done_and_done_marker(
    isolated_flask_app: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def patched_run_stream(messages: List[Dict[str, Any]], config: AgentConfig, registry: Optional[ToolRegistry] = None):
        return real_run_stream(messages, config, registry=registry, backend=CrashingStreamBackend())

    monkeypatch.setattr(web_app, "run_stream", patched_run_stream)
    app, _session_manager = isolated_flask_app
    with app.test_client() as client:
        events, saw_done_marker = _post_chat(client, "crash please")

    assert saw_done_marker is True
    assert "failed" in _phases(events)
    assert any(event["kind"] == "error" and event["code"] == "LLM_ERROR" for event in events)
    assert any(event["kind"] == "done" for event in events)


def test_chat_sse_auth_error_is_classified_and_not_retried(
    isolated_flask_app: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def patched_run_stream(messages: List[Dict[str, Any]], config: AgentConfig, registry: Optional[ToolRegistry] = None):
        return real_run_stream(messages, config, registry=registry, backend=AuthErrorBackend())

    monkeypatch.setattr(web_app, "run_stream", patched_run_stream)
    app, _session_manager = isolated_flask_app
    with app.test_client() as client:
        events, saw_done_marker = _post_chat(client, "auth please")

    assert saw_done_marker is True
    assert _phases(events).count("llm_request") == 1
    assert "failed" in _phases(events)
    error = next(event for event in events if event["kind"] == "error")
    assert error["code"] == "LLM_AUTH_FAILED"
    assert error["recoverable"] is False
    assert any(event["kind"] == "done" for event in events)


def test_chat_sse_persists_to_isolated_session_db(isolated_flask_app: Any) -> None:
    app, session_manager = isolated_flask_app
    with app.test_client() as client:
        events, saw_done_marker = _post_chat(client, "persist this")

    assert saw_done_marker is True
    assert events[-1]["kind"] == "done"
    sessions = session_manager.list_sessions()
    assert len(sessions) == 1
    saved = session_manager.get_session(sessions[0].id)
    assert saved is not None
    assert any(item.get("role") == "user" for item in saved.history)
    assert any(item.get("role") == "assistant" for item in saved.history)


def test_chat_sse_honors_request_session_id_without_polluting_active_session(isolated_flask_app: Any) -> None:
    app, session_manager = isolated_flask_app
    workspace_id = web_app._runtime_state.active_workspace_id
    target = session_manager.create_session(title="Target", workspace_id=workspace_id)
    active = session_manager.create_session(title="Active", workspace_id=workspace_id)
    web_app._runtime_state.activate_session(active.id, history=list(active.history), mode=active.mode)

    with app.test_client() as client:
        events, saw_done_marker = _post_chat(client, "write only to target", session_id=target.id)

    assert saw_done_marker is True
    assert events[-1]["kind"] == "done"
    saved_target = session_manager.get_session(target.id)
    saved_active = session_manager.get_session(active.id)
    assert saved_target is not None
    assert saved_active is not None
    assert [item.get("role") for item in saved_target.history] == ["user", "assistant"]
    assert saved_target.history[0]["content"] == "write only to target"
    assert saved_active.history == []
    assert web_app._runtime_state.active_session_id == active.id


def test_file_preview_html_serves_relative_assets_with_token_root(isolated_flask_app: Any) -> None:
    app, _session_manager = isolated_flask_app
    workspace_root = Path(web_workspace_routes.active_workspace_root())
    workspace_root.mkdir(parents=True, exist_ok=True)
    page = workspace_root / "index.html"
    style = workspace_root / "styles.css"
    page.write_text("<html><head><link rel=\"stylesheet\" href=\"styles.css\"></head><body>Preview</body></html>", encoding="utf-8")
    style.write_text("body { color: rgb(1, 2, 3); }", encoding="utf-8")

    with app.test_client() as client:
        response = client.get("/file-preview", query_string={"path": str(page)})
        assert response.status_code == 200
        html = response.get_data(as_text=True)
        match = re.search(r'<base href="/file-preview-root/([a-f0-9]+)/">', html)
        assert match

        css_response = client.get(f"/file-preview-root/{match.group(1)}/styles.css")
        assert css_response.status_code == 200
        assert "rgb(1, 2, 3)" in css_response.get_data(as_text=True)

        traversal = client.get(f"/file-preview-root/{match.group(1)}/../secret.txt")
        assert traversal.status_code == 403


def test_run_registry_streams_replayable_events_to_target_session(isolated_flask_app: Any) -> None:
    app, session_manager = isolated_flask_app
    workspace_id = web_app._runtime_state.active_workspace_id
    target = session_manager.create_session(title="Target run", workspace_id=workspace_id)
    active = session_manager.create_session(title="Active run", workspace_id=workspace_id)
    web_app._runtime_state.activate_session(active.id, history=[], mode="auto")

    with app.test_client() as client:
        created = client.post(
            "/runs",
            json={
                "message": "background registry run",
                "session_id": target.id,
                "assistant_id": "assistant-run-smoke",
            },
        )
        assert created.status_code == 200
        run_id = created.get_json()["run_id"]
        events, saw_done_marker = _run_events(client, run_id)
        replay, replay_done_marker = _run_events(client, run_id)
        status = client.get(f"/runs/{run_id}").get_json()
        active_run = client.get(f"/sessions/{target.id}/runs/active").get_json()

    assert saw_done_marker is True
    assert replay_done_marker is True
    assert [event["kind"] for event in events] == [event["kind"] for event in replay]
    assert any(event["kind"] == "content_delta" for event in events)
    assert events[-1]["kind"] == "done"
    assert all(event["run_id"] == run_id and event["session_id"] == target.id for event in events)
    assert status["status"] == "done"
    assert active_run["ok"] is False
    saved_target = session_manager.get_session(target.id)
    saved_active = session_manager.get_session(active.id)
    assert saved_target is not None
    assert saved_active is not None
    assert [item.get("role") for item in saved_target.history] == ["user", "assistant"]
    assert saved_active.history == []
    assert web_app._runtime_state.active_session_id == active.id


def test_run_registry_cancel_endpoint_marks_active_run_canceling(isolated_flask_app: Any) -> None:
    app, _session_manager = isolated_flask_app
    with app.test_client() as client:
        created = client.post("/runs", json={"message": "cancel registry run"})
        assert created.status_code == 200
        run_id = created.get_json()["run_id"]
        canceled = client.post(f"/runs/{run_id}/cancel")
        assert canceled.status_code == 200
        payload = canceled.get_json()

    assert payload["ok"] is True
    assert payload["run_id"] == run_id
    assert payload["status"] in {"canceling", "canceled", "done"}


def test_run_registry_rejects_second_active_run_for_same_session(isolated_flask_app: Any) -> None:
    app, session_manager = isolated_flask_app
    workspace_id = web_app._runtime_state.active_workspace_id
    target = session_manager.create_session(title="Busy run", workspace_id=workspace_id)
    web_app._create_run_state(
        session_id=target.id,
        assistant_id="assistant-busy",
        history=[{"role": "user", "content": "already running"}],
        mode="auto",
    )

    with app.test_client() as client:
        blocked = client.post(
            "/runs",
            json={
                "message": "second run should be rejected",
                "session_id": target.id,
                "assistant_id": "assistant-second",
            },
        )
        payload = blocked.get_json()

    assert blocked.status_code == 409
    assert payload["ok"] is False
    assert payload["run"]["assistant_id"] == "assistant-busy"


def test_run_registry_cancel_aborts_blocking_provider_stream(
    isolated_flask_app: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def patched_run_stream(messages: List[Dict[str, Any]], config: AgentConfig, registry: Optional[ToolRegistry] = None, **kwargs: Any):
        return real_run_stream(messages, config, registry=registry, backend=BlockingStreamBackend(), **kwargs)

    monkeypatch.setattr(web_app, "run_stream", patched_run_stream)
    app, _session_manager = isolated_flask_app
    with app.test_client() as client:
        created = client.post("/runs", json={"message": "cancel blocking provider"})
        assert created.status_code == 200
        run_id = created.get_json()["run_id"]
        # 慢机器(CI)上后台线程要更久才发出事件；阻塞操作有 5s 余量，等久一点再取消，避免 race。
        time.sleep(1.0)
        canceled = client.post(f"/runs/{run_id}/cancel")
        assert canceled.status_code == 200
        events, saw_done_marker = _run_events(client, run_id)
        status = client.get(f"/runs/{run_id}").get_json()

    assert saw_done_marker is True
    assert status["status"] == "canceled"
    assert any(event["kind"] == "error" and event["code"] == "RUN_CANCELLED" for event in events)


def test_run_registry_cancel_releases_blocking_tool_execution(
    isolated_flask_app: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def patched_run_stream(messages: List[Dict[str, Any]], config: AgentConfig, registry: Optional[ToolRegistry] = None, **kwargs: Any):
        return real_run_stream(messages, config, registry=registry, backend=ToolCallingBackend(), **kwargs)

    def slow_cancel_tool() -> str:
        cancel_event = current_cancel_event()
        deadline = time.time() + 5
        while time.time() < deadline:
            if cancel_event is not None and cancel_event.is_set():
                raise OperationCancelled("slow tool canceled")
            time.sleep(0.02)
        return "slow tool unexpectedly completed"

    monkeypatch.setattr(web_app, "run_stream", patched_run_stream)
    app, registry = isolated_flask_app[0], web_app.get_registry()
    registry.register(
        ToolDefinition(
            name="slow_cancel_tool",
            description="Smoke tool that waits until the run is canceled.",
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=slow_cancel_tool,
            source="test",
            requires_approval=False,
        )
    )

    with app.test_client() as client:
        created = client.post("/runs", json={"message": "cancel blocking tool"})
        assert created.status_code == 200
        run_id = created.get_json()["run_id"]
        # 慢机器(CI)上后台线程要更久才发出事件；阻塞操作有 5s 余量，等久一点再取消，避免 race。
        time.sleep(1.0)
        canceled = client.post(f"/runs/{run_id}/cancel")
        assert canceled.status_code == 200
        events, saw_done_marker = _run_events(client, run_id)
        status = client.get(f"/runs/{run_id}").get_json()

    assert saw_done_marker is True
    assert status["status"] == "canceled"
    assert any(event["kind"] == "tool_call" and event["tool"] == "slow_cancel_tool" for event in events)
    assert any(event["kind"] == "error" and event["code"] == "RUN_CANCELLED" for event in events)

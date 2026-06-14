from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, Generator, List, Tuple

import pytest

from backend.runtime.agent_loop import AgentConfig, ContentEvent, DoneEvent, ToolCallEvent
from backend.runtime.tool_registry import ToolRegistry
from backend.web import app as web_app
from backend.web import session_routes as web_session_routes
from backend.web import workspace_routes as web_workspace_routes
from backend.web.session_db import MetisSessionDB
from backend.web.sessions import SessionManager
from backend.web.workspaces import WorkspaceManager


@pytest.fixture
def isolated_flask_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    db = MetisSessionDB(data_root=str(tmp_path / ".metis"))
    session_manager = SessionManager(db=db)
    workspace_manager = WorkspaceManager(db=db)
    workspace = workspace_manager.create_workspace(str(tmp_path / "project"), name="FABLEADV-10")
    registry = ToolRegistry()

    monkeypatch.setattr(web_app, "get_session_manager", lambda: session_manager)
    monkeypatch.setattr(web_app, "get_workspace_manager", lambda: workspace_manager)
    monkeypatch.setattr(web_session_routes, "get_session_manager", lambda: session_manager)
    monkeypatch.setattr(web_session_routes, "get_workspace_manager", lambda: workspace_manager)
    monkeypatch.setattr(web_workspace_routes, "get_session_manager", lambda: session_manager)
    monkeypatch.setattr(web_workspace_routes, "get_workspace_manager", lambda: workspace_manager)
    monkeypatch.setattr(web_app, "get_registry", lambda: registry)
    monkeypatch.setattr(web_app, "_generate_smart_title", lambda *args, **kwargs: None)
    monkeypatch.setattr(web_app, "should_auto_compact", lambda *args, **kwargs: False)
    monkeypatch.setattr(web_app, "_maybe_record_learning", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        web_app,
        "_load_config",
        lambda: AgentConfig(
            llm_backend="fake",
            llm_model="fake_fableadv_10",
            timeout=1,
            max_turns=2,
        ),
    )
    web_app._runtime_state.clear_session()
    web_app._runtime_state.active_workspace_id = workspace.id
    web_app._runtime_state.learning_nudged_sessions.clear()
    with web_app._runs_lock:
        web_app._runs.clear()

    yield web_app.app, session_manager

    with web_app._runs_lock:
        web_app._runs.clear()
    web_app._runtime_state.clear_session()


def _history(count: int = 8) -> List[Dict[str, Any]]:
    roles = ["user", "assistant"] * ((count + 1) // 2)
    return [
        {"id": f"m{index + 1}", "role": roles[index], "content": f"message-{index + 1}"}
        for index in range(count)
    ]


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


def test_manual_compact_preserves_history_and_writes_compact_state(
    isolated_flask_app: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, session_manager = isolated_flask_app
    session = session_manager.create_session("Compact transcript", workspace_id=web_app._runtime_state.active_workspace_id)
    original_history = _history()
    session_manager.update_session(session.id, history=original_history)
    web_app._runtime_state.activate_session(session.id, history=list(original_history), mode="auto")

    def fake_compact(history: List[Dict[str, Any]], keep_recent: int = 4, **_: Any) -> List[Dict[str, Any]]:
        return [{"role": "system", "content": "[Context Summary]\nmessages 1-4"}] + history[-keep_recent:]

    monkeypatch.setattr(web_app, "_compact_history", fake_compact)

    with app.test_client() as client:
        response = client.post("/compact", json={})
        payload = response.get_json()

    saved = session_manager.get_session(session.id)
    assert response.status_code == 200
    assert payload["ok"] is True
    assert payload["history_count"] == len(original_history)
    assert saved is not None
    assert saved.history == original_history
    assert saved.compact_state["summary"].startswith("[Context Summary]")
    assert saved.compact_state["boundary_index"] == len(original_history) - 4
    assert saved.compact_state["boundary_message_id"] == original_history[-4]["id"]
    assert web_app._runtime_state.chat_history == original_history


def test_chat_sync_uses_summary_plus_boundary_tail_after_compaction(
    isolated_flask_app: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, session_manager = isolated_flask_app
    session = session_manager.create_session("Model context", workspace_id=web_app._runtime_state.active_workspace_id)
    history = _history()
    compact_state = {
        "summary": "[Context Summary]\nmessages 1-4",
        "boundary_message_id": "m5",
        "boundary_index": 4,
        "compacted_at": 1.0,
        "compact_count": 1,
    }
    session_manager.update_session(session.id, history=history, compact_state=compact_state)
    web_app._runtime_state.activate_session(session.id, history=list(history), compact_state=compact_state, mode="auto")
    captured: Dict[str, Any] = {}

    def fake_run(messages: List[Dict[str, Any]], config: AgentConfig) -> Generator[Any, None, None]:
        captured["messages"] = messages
        yield ContentEvent(text="assistant after compact")
        yield DoneEvent(total_turns=1, prompt_tokens=8)

    monkeypatch.setattr(web_app, "run", fake_run)

    with app.test_client() as client:
        response = client.post("/chat/sync", json={"message": "next message"})

    assert response.status_code == 200
    model_messages = captured["messages"]
    assert [message["role"] for message in model_messages] == ["system", "user", "assistant", "user", "assistant", "user"]
    assert model_messages[0]["content"] == compact_state["summary"]
    assert [message["content"] for message in model_messages[1:-1]] == [f"message-{index}" for index in range(5, 9)]
    assert model_messages[-1]["content"] == "next message"
    saved = session_manager.get_session(session.id)
    assert saved is not None
    assert [message["content"] for message in saved.history[-2:]] == ["next message", "assistant after compact"]
    assert saved.history[0]["content"] == "message-1"


def test_manual_compact_rejects_active_run(isolated_flask_app: Any) -> None:
    app, session_manager = isolated_flask_app
    session = session_manager.create_session("Busy compact", workspace_id=web_app._runtime_state.active_workspace_id)
    history = _history()
    session_manager.update_session(session.id, history=history)
    web_app._runtime_state.activate_session(session.id, history=list(history), mode="auto")
    web_app._create_run_state(
        session_id=session.id,
        assistant_id="assistant-busy",
        history=history,
        mode="auto",
    )

    with app.test_client() as client:
        response = client.post("/compact", json={})
        payload = response.get_json()

    assert response.status_code == 409
    assert payload["ok"] is False
    assert "等待当前任务完成" in payload["error"]


def test_run_with_tool_call_persists_assistant_final(
    isolated_flask_app: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, session_manager = isolated_flask_app

    def fake_run_stream(
        messages: List[Dict[str, Any]],
        config: AgentConfig,
        registry: ToolRegistry | None = None,
        **kwargs: Any,
    ) -> Generator[Any, None, None]:
        yield ToolCallEvent(tool_name="fake_tool", arguments={}, call_id="tool-1")
        yield ContentEvent(text="assistant final after tool")
        yield DoneEvent(total_turns=1, total_tool_calls=1)

    monkeypatch.setattr(web_app, "run_stream", fake_run_stream)

    with app.test_client() as client:
        created = client.post("/runs", json={"message": "use a tool"})
        assert created.status_code == 200
        run_id = created.get_json()["run_id"]
        events, saw_done_marker = _collect_sse(client.get(f"/runs/{run_id}/events", buffered=False))

    assert saw_done_marker is True
    assert any(event["kind"] == "tool_call" for event in events)
    sessions = session_manager.list_sessions()
    saved = session_manager.get_session(sessions[0].id)
    assert saved is not None
    assert [message["role"] for message in saved.history] == ["user", "assistant"]
    assert saved.history[-1]["content"] == "assistant final after tool"

from __future__ import annotations

from backend.web.runtime_state import RuntimeState


def test_runtime_state_activates_and_clears_session() -> None:
    state = RuntimeState()
    state.active_workspace_id = "workspace-1"
    state.activate_session("session-1", history=[{"role": "user", "content": "hi"}], mode="edit")

    assert state.active_session_id == "session-1"
    assert state.active_workspace_id == "workspace-1"
    assert state.chat_history == [{"role": "user", "content": "hi"}]
    assert state.execution_mode == "edit"

    state.clear_session()

    assert state.active_session_id is None
    assert state.chat_history == []
    assert state.execution_mode == "auto"
    assert state.active_workspace_id == "workspace-1"


def test_runtime_state_snapshot_is_ui_safe() -> None:
    state = RuntimeState(active_session_id="s", active_workspace_id="w")
    state.chat_history.append({"role": "assistant", "content": "ok"})

    assert state.snapshot() == {
        "active_session_id": "s",
        "active_workspace_id": "w",
        "history_length": 1,
        "execution_mode": "auto",
        "compact": {"running": False},
    }


def test_runtime_state_endpoint_hides_message_text() -> None:
    from backend.web.app import app

    with app.test_client() as flask_client:
        response = flask_client.get("/runtime/state")
    assert response.status_code == 200
    data = response.get_json()
    assert "active_session_id" in data
    assert "active_workspace_id" in data
    assert "history_length" in data
    assert "chat_history" not in data

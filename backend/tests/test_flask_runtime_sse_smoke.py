from __future__ import annotations

import json
import re
import time
import zipfile
from pathlib import Path
from typing import Any, Dict, Generator, List, Optional, Tuple

import pytest

from backend.core.paths import clear_metis_home_cache
from backend.runtime.cancellation import OperationCancelled, current_cancel_event
from backend.runtime import cowork_coordinator
from backend.runtime.agent_loop import (
    AgentConfig,
    DoneEvent,
    _chat_surface_blocked_tool_result,
    _chat_surface_blocks_tool,
    run_stream as real_run_stream,
)
from backend.runtime.worktree_manager import WorktreeRecord
from backend.runtime.llm_backends import LLMBackend, LLMResponse, ToolCall
from backend.runtime.tool_registry import ToolDefinition, ToolRegistry
from backend.web import app as web_app
from backend.web import helpers as web_helpers
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
    monkeypatch.setenv("METIS_HOME", str(tmp_path / ".metis-home"))
    clear_metis_home_cache()
    db = MetisSessionDB(data_root=str(tmp_path / ".metis"))
    session_manager = SessionManager(db=db)
    workspace_manager = WorkspaceManager(db=db)
    workspace = workspace_manager.create_workspace(str(tmp_path / "project"), name="Smoke Project")
    registry = ToolRegistry()

    monkeypatch.setattr(web_app, "get_session_manager", lambda: session_manager)
    monkeypatch.setattr(web_app, "get_workspace_manager", lambda: workspace_manager)
    monkeypatch.setattr(web_helpers, "get_workspace_manager", lambda: workspace_manager)
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
    monkeypatch.setattr(web_app, "_permission_contexts", {})
    web_app._permission_request_store.clear()
    monkeypatch.setattr(web_app, "_generate_smart_title", lambda *args, **kwargs: None)
    monkeypatch.setattr(web_app, "should_auto_compact", lambda *args, **kwargs: False)
    monkeypatch.setattr(web_app, "_maybe_record_learning", lambda *args, **kwargs: None)
    with web_app._runs_lock:
        web_app._runs.clear()
    clear_metis_home_cache()
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


def _run_events_v2(client: Any, run_id: str, after: int = 0) -> Tuple[List[Dict[str, Any]], bool]:
    response = client.get(f"/runs/{run_id}/events?schema=v2&after={after}", buffered=False)
    assert response.status_code == 200
    assert response.content_type.startswith("text/event-stream")
    return _collect_sse(response)


def _phases(events: List[Dict[str, Any]]) -> List[str]:
    return [
        str(event.get("phase") or event.get("payload", {}).get("phase") or "")
        for event in events
        if event.get("kind") == "runtime_status"
    ]


def _fake_worktree_record(source_root: str, worktree_root: Path, *, run_id: str, session_id: str, index: int) -> WorktreeRecord:
    worktree_root.mkdir(parents=True, exist_ok=True)
    return WorktreeRecord(
        worktree_id=f"wt_cowork_{index}",
        workspace_root=source_root,
        repo_root=source_root,
        worktree_path=str(worktree_root),
        worktree_workspace_root=str(worktree_root),
        branch=f"metis/run/cowork-{index}",
        base_ref="HEAD",
        run_id=run_id,
        session_id=session_id,
        label=f"cowork-{index}",
    )


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
        "llm_response",
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


def test_workspace_file_previews_pdf_and_docx(isolated_flask_app: Any) -> None:
    app, _session_manager = isolated_flask_app
    workspace_root = Path(web_workspace_routes.active_workspace_root())
    workspace_root.mkdir(parents=True, exist_ok=True)
    pdf = workspace_root / "sample.pdf"
    pdf.write_bytes(b"%PDF-1.4\n%metis smoke\n")
    docx = workspace_root / "sample.docx"
    with zipfile.ZipFile(docx, "w") as archive:
        archive.writestr(
            "word/document.xml",
            (
                '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                "<w:body><w:p><w:r><w:t>Metis DOCX preview</w:t></w:r></w:p></w:body>"
                "</w:document>"
            ),
        )

    with app.test_client() as client:
        pdf_response = client.get("/workspace/file", query_string={"path": str(pdf)})
        assert pdf_response.status_code == 200
        pdf_payload = pdf_response.get_json()
        assert pdf_payload["type"] == "pdf"
        assert pdf_payload["preview_url"].startswith("/file-preview?path=")

        docx_response = client.get("/workspace/file", query_string={"path": str(docx)})
        assert docx_response.status_code == 200
        docx_payload = docx_response.get_json()
        assert docx_payload["type"] == "office"
        assert "Metis DOCX preview" in docx_payload["content"]


def test_run_registry_streams_replayable_events_to_target_session(
    isolated_flask_app: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app, session_manager = isolated_flask_app
    workspace_id = web_app._runtime_state.active_workspace_id
    target = session_manager.create_session(title="Target run", workspace_id=workspace_id)
    active = session_manager.create_session(title="Active run", workspace_id=workspace_id)
    web_app._runtime_state.activate_session(active.id, history=[], mode="auto")
    source_root = web_app._active_workspace_root()

    def fake_create_worktree(workspace_root: str, *, run_id: str = "", session_id: str = "", label: str = "") -> WorktreeRecord:
        assert workspace_root == source_root
        assert run_id
        assert session_id == target.id
        assert label == "code-run"
        record = _fake_worktree_record(
            source_root,
            tmp_path / "code-default-worktree",
            run_id=run_id,
            session_id=session_id,
            index=1,
        )
        record.worktree_id = "wt_code_default"
        record.label = "code-run"
        return record

    monkeypatch.setattr(web_app, "create_worktree", fake_create_worktree)

    with app.test_client() as client:
        created = client.post(
            "/runs",
            json={
                "message": "background registry run",
                "session_id": target.id,
                "assistant_id": "assistant-run-smoke",
                "surface_mode": "code",
            },
        )
        assert created.status_code == 200
        created_payload = created.get_json()
        run_id = created_payload["run_id"]
        assert created_payload["turn_id"] == f"turn_{run_id}"
        assert created_payload["surface_mode"] == "code"
        assert created_payload["execution_profile"] == "local_worktree"
        assert created_payload["source_workspace_root"] == source_root
        assert created_payload["worktree_id"] == "wt_code_default"
        assert created_payload["last_seq"] == 0
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
    assert all(event["turn_id"] == f"turn_{run_id}" and event["surface_mode"] == "code" for event in events)
    assert all(event.get("payload", {}).get("message_id") == "assistant-run-smoke" for event in events if isinstance(event.get("payload"), dict))
    assert status["status"] == "done"
    assert status["turn_id"] == f"turn_{run_id}"
    assert status["surface_mode"] == "code"
    assert status["execution_profile"] == "local_worktree"
    assert status["last_seq"] == len(events)
    assert active_run["ok"] is False
    saved_target = session_manager.get_session(target.id)
    saved_active = session_manager.get_session(active.id)
    assert saved_target is not None
    assert saved_active is not None
    assert [item.get("role") for item in saved_target.history] == ["user", "assistant"]
    assert saved_active.history == []
    assert web_app._runtime_state.active_session_id == active.id


def test_run_registry_streams_agent_event_v2_envelopes(
    isolated_flask_app: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app, session_manager = isolated_flask_app
    workspace_id = web_app._runtime_state.active_workspace_id
    target = session_manager.create_session(title="Target run v2", workspace_id=workspace_id)
    source_root = web_app._active_workspace_root()

    def fake_create_worktree(workspace_root: str, *, run_id: str = "", session_id: str = "", label: str = "") -> WorktreeRecord:
        assert workspace_root == source_root
        assert run_id
        assert session_id == target.id
        assert label == "code-run"
        record = _fake_worktree_record(
            source_root,
            tmp_path / "code-v2-default-worktree",
            run_id=run_id,
            session_id=session_id,
            index=2,
        )
        record.worktree_id = "wt_code_v2_default"
        record.label = "code-run"
        return record

    monkeypatch.setattr(web_app, "create_worktree", fake_create_worktree)

    with app.test_client() as client:
        created = client.post(
            "/runs",
            json={
                "message": "background registry run v2",
                "session_id": target.id,
                "assistant_id": "assistant-run-v2",
                "surface_mode": "code",
            },
        )
        assert created.status_code == 200
        created_payload = created.get_json()
        run_id = created_payload["run_id"]
        assert created_payload["execution_profile"] == "local_worktree"
        assert created_payload["worktree_id"] == "wt_code_v2_default"
        events, saw_done_marker = _run_events_v2(client, run_id)
        replay_tail, replay_done_marker = _run_events_v2(client, run_id, after=1)

    assert saw_done_marker is True
    assert replay_done_marker is True
    assert events
    assert [event["seq"] for event in events] == list(range(1, len(events) + 1))
    assert all(event["schema"] == "metis.agent_event.v2" for event in events)
    assert all(event["version"] == 2 for event in events)
    assert all(event["event_id"] == f"evt_{run_id}_{event['seq']:06d}" for event in events)
    assert all("type" not in event for event in events)
    assert all(
        all(field in event for field in ("run_id", "session_id", "turn_id", "message_id", "timestamp", "kind", "payload"))
        for event in events
    )
    assert any(event["kind"] == "message_delta" for event in events)
    assert events[-1]["kind"] == "run_completed"
    assert all(event["run_id"] == run_id and event["session_id"] == target.id for event in events)
    assert all(event["turn_id"] == f"turn_{run_id}" and event["message_id"] == "assistant-run-v2" for event in events)
    assert replay_tail[0]["seq"] == 2


def test_run_registry_accepts_local_worktree_execution_profile(
    isolated_flask_app: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app, session_manager = isolated_flask_app
    workspace_id = web_app._runtime_state.active_workspace_id
    target = session_manager.create_session(title="Target worktree run", workspace_id=workspace_id)
    source_root = web_app._active_workspace_root()
    fake_worktree = tmp_path / "fake-worktree"
    fake_worktree.mkdir()

    class FakeWorktree:
        def to_dict(self) -> Dict[str, Any]:
            return {
                "schema": "metis.worktree.v1",
                "worktree_id": "wt_test",
                "workspace_root": source_root,
                "repo_root": source_root,
                "worktree_path": str(fake_worktree),
                "worktree_workspace_root": str(fake_worktree),
                "branch": "metis/run/test",
                "base_ref": "HEAD",
                "status": "active",
            }

    def fake_create_worktree(workspace_root: str, *, run_id: str = "", session_id: str = "", label: str = "") -> FakeWorktree:
        assert workspace_root == source_root
        assert run_id
        assert session_id == target.id
        assert label == "code-run"
        return FakeWorktree()

    monkeypatch.setattr(web_app, "create_worktree", fake_create_worktree)

    with app.test_client() as client:
        created = client.post(
            "/runs",
            json={
                "message": "worktree registry run",
                "session_id": target.id,
                "assistant_id": "assistant-run-worktree",
                "surface_mode": "code",
                "execution_profile": "local_worktree",
            },
        )
        assert created.status_code == 200
        payload = created.get_json()

    assert payload["execution_profile"] == "local_worktree"
    assert payload["source_workspace_root"] == source_root
    assert payload["workspace_root"] == str(fake_worktree)
    assert payload["worktree_id"] == "wt_test"
    assert payload["worktree_workspace_root"] == str(fake_worktree)


def test_code_run_local_vm_profile_still_creates_worktree(
    isolated_flask_app: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app, session_manager = isolated_flask_app
    workspace_id = web_app._runtime_state.active_workspace_id
    target = session_manager.create_session(title="Target local vm code run", workspace_id=workspace_id)
    source_root = web_app._active_workspace_root()
    fake_worktree = tmp_path / "fake-code-vm-worktree"
    fake_worktree.mkdir()
    started_runs: List[Dict[str, Any]] = []

    class FakeWorktree:
        def to_dict(self) -> Dict[str, Any]:
            return {
                "schema": "metis.worktree.v1",
                "worktree_id": "wt_vm_test",
                "workspace_root": source_root,
                "repo_root": source_root,
                "worktree_path": str(fake_worktree),
                "worktree_workspace_root": str(fake_worktree),
                "branch": "metis/run/vm-test",
                "base_ref": "HEAD",
                "status": "active",
            }

    def fake_create_worktree(workspace_root: str, *, run_id: str = "", session_id: str = "", label: str = "") -> FakeWorktree:
        assert workspace_root == source_root
        assert run_id
        assert session_id == target.id
        assert label == "code-run"
        return FakeWorktree()

    def fake_start_run_thread(run: Dict[str, Any]) -> None:
        started_runs.append(dict(run))

    monkeypatch.setattr(web_app, "create_worktree", fake_create_worktree)
    monkeypatch.setattr(web_app, "_start_run_thread", fake_start_run_thread)

    with app.test_client() as client:
        created = client.post(
            "/runs",
            json={
                "message": "run tests in vm",
                "session_id": target.id,
                "assistant_id": "assistant-run-local-vm",
                "surface_mode": "code",
                "execution_profile": "local_vm",
            },
        )
        assert created.status_code == 200
        payload = created.get_json()

    assert payload["execution_profile"] == "local_vm"
    assert payload["source_workspace_root"] == source_root
    assert payload["workspace_root"] == str(fake_worktree)
    assert payload["worktree_id"] == "wt_vm_test"
    assert payload["worktree_workspace_root"] == str(fake_worktree)
    assert started_runs and started_runs[0]["execution_profile"] == "local_vm"
    assert started_runs[0]["workspace_root"] == str(fake_worktree)


def test_cowork_run_streams_subruns_diff_and_summary_artifact(
    isolated_flask_app: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app, session_manager = isolated_flask_app
    workspace_id = web_app._runtime_state.active_workspace_id
    target = session_manager.create_session(title="Target cowork run", workspace_id=workspace_id)
    source_root = web_app._active_workspace_root()
    records: List[WorktreeRecord] = []

    def fake_create_worktree(workspace_root: str, *, run_id: str = "", session_id: str = "", label: str = "") -> WorktreeRecord:
        assert workspace_root == source_root
        assert run_id
        assert session_id == target.id
        assert label.startswith("cowork-")
        record = _fake_worktree_record(
            source_root,
            tmp_path / f"cowork-worktree-{len(records) + 1}",
            run_id=run_id,
            session_id=session_id,
            index=len(records) + 1,
        )
        records.append(record)
        return record

    def fake_diff_worktree(workspace_root: str, worktree_id: str, *, max_chars: int = 200000) -> Dict[str, Any]:
        assert workspace_root == source_root
        record = next(item for item in records if item.worktree_id == worktree_id)
        return {
            "schema": "metis.worktree_diff.v1",
            "worktree": record.to_dict(),
            "status": " M app.py\n",
            "stat": "app.py | 1 +\n",
            "patch": "diff --git a/app.py b/app.py\n+print('cowork')\n",
            "truncated": False,
            "base_ref": "HEAD",
        }

    monkeypatch.setattr(cowork_coordinator, "create_worktree", fake_create_worktree)
    monkeypatch.setattr(cowork_coordinator, "diff_worktree", fake_diff_worktree)

    with app.test_client() as client:
        created = client.post(
            "/runs",
            json={
                "message": "Inspect current implementation\nValidate and summarize diffs",
                "session_id": target.id,
                "assistant_id": "assistant-run-cowork",
                "surface_mode": "cowork",
                "execution_profile": "local_worktree",
            },
        )
        assert created.status_code == 200
        payload = created.get_json()
        run_id = payload["run_id"]
        events, saw_done_marker = _run_events(client, run_id)
        events_v2, saw_done_marker_v2 = _run_events_v2(client, run_id)
        status = client.get(f"/runs/{run_id}").get_json()

    assert payload["surface_mode"] == "cowork"
    assert payload["execution_profile"] == "local_worktree"
    assert payload["workspace_root"] == source_root
    assert payload["worktree_id"] == ""
    assert saw_done_marker is True
    assert saw_done_marker_v2 is True
    assert status["status"] == "done"
    assert [event["kind"] for event in events].count("subrun_planned") == 2
    assert [event["kind"] for event in events].count("subrun_running") >= 2
    assert [event["kind"] for event in events].count("subrun_succeeded") == 2
    assert "subagent_start" not in [event["kind"] for event in events]
    assert "subagent_done" not in [event["kind"] for event in events]
    assert all(event["schema"] == "metis.agent_event.v2" for event in events_v2)
    assert [event["kind"] for event in events_v2].count("subrun_succeeded") == 2
    assert all(
        event["payload"]["schema"] == cowork_coordinator.COWORK_SUBRUN_EVENT_SCHEMA
        for event in events_v2
        if event["kind"].startswith("subrun_")
    )
    assert any(event["kind"] == "artifact_created" for event in events)
    assert events[-1]["kind"] == "done"
    summary_event = next(event for event in events if event["kind"] == "artifact_created")
    artifact = summary_event["payload"]["artifact"]
    assert artifact["kind"] == "report"
    summary_path = Path(artifact["path"])
    assert summary_path.is_file()
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["schema"] == cowork_coordinator.COWORK_SUMMARY_SCHEMA
    assert summary["subrun_count"] == 2
    assert len(summary["artifacts"]) == 2
    assert len(summary["diffs"]) == 2
    saved_target = session_manager.get_session(target.id)
    assert saved_target is not None
    assert [item.get("role") for item in saved_target.history] == ["user", "assistant"]


def test_cowork_local_vm_subruns_use_metis_wsl_runner(
    isolated_flask_app: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app, session_manager = isolated_flask_app
    workspace_id = web_app._runtime_state.active_workspace_id
    target = session_manager.create_session(title="Target cowork vm run", workspace_id=workspace_id)
    source_root = web_app._active_workspace_root()
    records: List[WorktreeRecord] = []
    vm_roots: List[str] = []

    def fake_create_worktree(workspace_root: str, *, run_id: str = "", session_id: str = "", label: str = "") -> WorktreeRecord:
        assert workspace_root == source_root
        record = _fake_worktree_record(
            source_root,
            tmp_path / f"cowork-vm-worktree-{len(records) + 1}",
            run_id=run_id,
            session_id=session_id,
            index=len(records) + 1,
        )
        records.append(record)
        return record

    def fake_run_local_vm_command(request: Any) -> Dict[str, Any]:
        vm_roots.append(str(request.workspace_root))
        assert request.collect_artifacts is False
        assert request.export_patch is True
        assert request.export_diagnostics == "on_failure"
        return {
            "ok": True,
            "runner": "local_vm",
            "backend": "metis_wsl",
            "run_id": "vmcmd_test",
            "job": {
                "job_id": "job_test",
                "status": "done",
                "returncode": 0,
                "timed_out": False,
                "stdout": "VALIDATION_OK\n",
                "stderr": "",
                "artifacts_dir": str(tmp_path / "vm-artifacts"),
                "patch_path": str(tmp_path / "vm-artifacts" / "changes.patch"),
                "changed_files": [{"path": "vm-output.txt", "status": "A"}],
                "artifacts": [{"path": str(tmp_path / "vm-artifacts" / "report.json"), "relative_path": "report.json", "size": 12}],
            },
        }

    def fake_diff_worktree(workspace_root: str, worktree_id: str, *, max_chars: int = 200000) -> Dict[str, Any]:
        record = next(item for item in records if item.worktree_id == worktree_id)
        return {
            "schema": "metis.worktree_diff.v1",
            "worktree": record.to_dict(),
            "status": "",
            "stat": "",
            "patch": "",
            "truncated": False,
            "base_ref": "HEAD",
        }

    monkeypatch.setattr(cowork_coordinator, "create_worktree", fake_create_worktree)
    monkeypatch.setattr(cowork_coordinator, "run_local_vm_command", fake_run_local_vm_command)
    monkeypatch.setattr(cowork_coordinator, "diff_worktree", fake_diff_worktree)

    with app.test_client() as client:
        created = client.post(
            "/runs",
            json={
                "message": "Run VM check\nCollect VM result",
                "session_id": target.id,
                "assistant_id": "assistant-run-cowork-vm",
                "surface_mode": "cowork",
                "execution_profile": "local_vm",
            },
        )
        assert created.status_code == 200
        payload = created.get_json()
        run_id = payload["run_id"]
        events, saw_done_marker = _run_events(client, run_id)

    assert payload["worktree_id"] == ""
    assert saw_done_marker is True
    assert len(vm_roots) == 2
    assert vm_roots == [record.worktree_workspace_root for record in records]
    done_events = [event for event in events if event["kind"] == "subrun_succeeded"]
    assert len(done_events) == 2
    assert all(event["payload"]["result"]["local_vm"]["backend"] == "metis_wsl" for event in done_events)
    assert all("hcs" not in json.dumps(event["payload"]["result"]["local_vm"]).lower() for event in done_events)


def test_cowork_run_resume_reuses_terminal_subruns_and_runs_unfinished(
    isolated_flask_app: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app, session_manager = isolated_flask_app
    workspace_id = web_app._runtime_state.active_workspace_id
    target = session_manager.create_session(title="Target cowork resume", workspace_id=workspace_id)
    source_root = web_app._active_workspace_root()
    Path(source_root).mkdir(parents=True, exist_ok=True)
    source_run_id = "run_resume_source"
    plan = {
        "schema": cowork_coordinator.COWORK_PLAN_SCHEMA,
        "version": cowork_coordinator.COWORK_PLAN_VERSION,
        "run_id": source_run_id,
        "session_id": target.id,
        "goal": "Resume cowork work",
        "status": "planned",
        "created_at": time.time(),
        "subruns": [
            {
                "subrun_id": "subrun_done",
                "title": "Already done",
                "objective": "Reuse completed result.",
                "inputs": ["source"],
                "expected_artifacts": ["diff"],
                "acceptance_criteria": ["has evidence"],
                "dependencies": [],
                "prompt": "Already done",
                "execution_profile": "local_worktree",
                "status": "succeeded",
                "diff": {"status": " M done.txt\n", "stat": "done.txt | 1 +\n", "patch_preview": "+done"},
                "evidence": {
                    "schema": cowork_coordinator.COWORK_SUBRUN_EVIDENCE_SCHEMA,
                    "success_evidence": True,
                    "counts": {"diff": 1, "artifacts": 0, "stdout_test": 0, "failure_reasons": 0},
                },
                "artifacts": [],
            },
            {
                "subrun_id": "subrun_pending",
                "title": "Still pending",
                "objective": "Run only unfinished work.",
                "inputs": ["source"],
                "expected_artifacts": ["diff"],
                "acceptance_criteria": ["has diff"],
                "dependencies": ["subrun_done"],
                "prompt": "Still pending",
                "execution_profile": "local_worktree",
                "status": "running",
            },
        ],
    }
    state_dir = Path(source_root) / ".metis" / "cowork"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / f"scheduler-{source_run_id}.json"
    state_path.write_text(
        json.dumps(
            {
                "schema": cowork_coordinator.COWORK_SCHEDULER_SCHEMA,
                "version": 1,
                "run_id": source_run_id,
                "session_id": target.id,
                "status": "canceled",
                "updated_at": time.time(),
                "mode": "dag_parallel",
                "subruns": plan["subruns"],
                "plan": plan,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    source_run = web_app._create_run_state(
        run_id=source_run_id,
        session_id=target.id,
        assistant_id="assistant-source",
        history=[{"role": "user", "content": "Resume cowork work"}],
        mode="auto",
        surface_mode="cowork",
        execution_profile="local_worktree",
        source_workspace_root=source_root,
        workspace_root=source_root,
    )
    web_app._set_run_status(source_run, "canceled", phase="canceled")

    records: List[WorktreeRecord] = []

    def fake_create_worktree(workspace_root: str, *, run_id: str = "", session_id: str = "", label: str = "") -> WorktreeRecord:
        assert workspace_root == source_root
        assert label == "cowork-2"
        record = _fake_worktree_record(
            source_root,
            tmp_path / "cowork-resume-worktree",
            run_id=run_id,
            session_id=session_id,
            index=2,
        )
        records.append(record)
        return record

    def fake_diff_worktree(workspace_root: str, worktree_id: str, *, max_chars: int = 200000) -> Dict[str, Any]:
        record = records[0]
        assert worktree_id == record.worktree_id
        return {
            "schema": "metis.worktree_diff.v1",
            "worktree": record.to_dict(),
            "status": " M pending.txt\n",
            "stat": "pending.txt | 1 +\n",
            "patch": "diff --git a/pending.txt b/pending.txt\n+resumed\n",
            "truncated": False,
            "base_ref": "HEAD",
        }

    def fake_run_agent_loop(messages: List[Dict[str, Any]], config: AgentConfig):
        yield DoneEvent(total_turns=1, total_tool_calls=0, total_tokens=1)

    monkeypatch.setattr(cowork_coordinator, "create_worktree", fake_create_worktree)
    monkeypatch.setattr(cowork_coordinator, "diff_worktree", fake_diff_worktree)
    monkeypatch.setattr(cowork_coordinator, "run_agent_loop", fake_run_agent_loop)

    with app.test_client() as client:
        source_payload = client.get(f"/runs/{source_run_id}").get_json()
        resumed = client.post(f"/runs/{source_run_id}/resume", json={"assistant_id": "assistant-resume"})
        assert resumed.status_code == 200
        resumed_payload = resumed.get_json()
        events, saw_done_marker = _run_events(client, resumed_payload["run_id"])
        final_status = client.get(f"/runs/{resumed_payload['run_id']}").get_json()

    assert source_payload["resumable"] is True
    assert resumed_payload["resume_from_run_id"] == source_run_id
    assert resumed_payload["surface_mode"] == "cowork"
    assert saw_done_marker is True
    assert final_status["status"] == "done"
    assert len(records) == 1
    succeeded = [event for event in events if event["kind"] == "subrun_succeeded"]
    assert [event["payload"]["subrun_id"] for event in succeeded] == ["subrun_done", "subrun_pending"]
    assert succeeded[0]["payload"]["stage"] == "resume_reused"
    assert succeeded[0]["payload"]["result"]["resumed"] is True


def test_run_registry_agent_event_v2_tool_lifecycle_uses_call_id(
    isolated_flask_app: Any,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class OneToolBackend(CrashingStreamBackend):
        def __init__(self) -> None:
            self.calls = 0

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
            self.calls += 1
            if self.calls == 1:
                return LLMResponse(
                    tool_calls=[
                        ToolCall(
                            id="slow-tool-call",
                            name="slow_cancel_tool",
                            arguments={},
                        )
                    ]
                )
            return LLMResponse(content="done")

    def patched_run_stream(messages: List[Dict[str, Any]], config: AgentConfig, registry: Optional[ToolRegistry] = None, **kwargs: Any):
        return real_run_stream(messages, config, registry=registry, backend=OneToolBackend(), **kwargs)

    monkeypatch.setattr(web_app, "run_stream", patched_run_stream)
    app, session_manager = isolated_flask_app
    registry = web_app.get_registry()
    code_session = session_manager.create_session(
        title="Tool lifecycle v2",
        workspace_id=web_app._runtime_state.active_workspace_id,
        mode="code",
    )
    source_root = web_app._active_workspace_root()

    def fake_create_worktree(workspace_root: str, *, run_id: str = "", session_id: str = "", label: str = "") -> WorktreeRecord:
        assert workspace_root == source_root
        assert run_id
        assert session_id == code_session.id
        assert label == "code-run"
        record = _fake_worktree_record(
            source_root,
            tmp_path / "code-tool-lifecycle-worktree",
            run_id=run_id,
            session_id=session_id,
            index=3,
        )
        record.worktree_id = "wt_code_tool_lifecycle"
        record.label = "code-run"
        return record

    monkeypatch.setattr(web_app, "create_worktree", fake_create_worktree)
    registry.register(
        ToolDefinition(
            name="slow_cancel_tool",
            description="Smoke tool that returns successfully.",
            parameters={"type": "object", "properties": {}, "required": []},
            execute_fn=lambda: "tool ok",
            source="test",
            requires_approval=False,
        )
    )

    with app.test_client() as client:
        created = client.post(
            "/runs",
            json={"message": "run one tool", "session_id": code_session.id},
        )
        assert created.status_code == 200
        run_id = created.get_json()["run_id"]
        events, saw_done_marker = _run_events_v2(client, run_id)

    assert saw_done_marker is True
    lifecycle = [event for event in events if str(event.get("kind", "")).startswith("tool_")]
    assert [event["kind"] for event in lifecycle] == ["tool_requested", "tool_running", "tool_succeeded"]
    assert [event["payload"]["call_id"] for event in lifecycle] == ["slow-tool-call"] * 3
    assert [event["payload"]["tool_name"] for event in lifecycle] == ["slow_cancel_tool"] * 3
    assert all("tool" not in event and "call_id" not in event and "callId" not in event for event in lifecycle)


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


def test_run_registry_preserves_chat_surface_mode_for_active_session(
    isolated_flask_app: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, session_manager = isolated_flask_app
    workspace_id = web_app._runtime_state.active_workspace_id
    session = session_manager.create_session(title="Chat research", workspace_id=workspace_id, mode="chat")
    web_app._runtime_state.activate_session(session.id, history=[], compact_state={}, mode="chat")
    # Simulate the permission mode being changed after the chat session became active.
    # The run mode must still come from the session.mode, not this permission value.
    web_app._runtime_state.execution_mode = "auto_guard"
    monkeypatch.setattr(web_app, "_start_run_thread", lambda run: None)

    with app.test_client() as client:
        created = client.post(
            "/runs",
            json={
                "message": "research should stay on chat surface",
                "session_id": session.id,
                "assistant_id": "assistant-chat-surface",
                "deep_research": True,
            },
        )

    assert created.status_code == 200
    run_id = created.get_json()["run_id"]
    run = web_app._get_run(run_id)
    assert run is not None
    assert run["mode"] == "chat"
    assert run["deep_research"] is True


def test_chat_surface_blocks_workspace_tool_calls() -> None:
    registry = ToolRegistry()
    registry.register(ToolDefinition(name="todo_write", description="", parameters={}, execute_fn=lambda **_: ""))
    registry.register(ToolDefinition(name="deep_research_plan", description="", parameters={}, execute_fn=lambda **_: ""))
    registry.register(ToolDefinition(name="deep_research_run", description="", parameters={}, execute_fn=lambda **_: ""))
    call = ToolCall(id="call-1", name="todo_write", arguments={})
    config = AgentConfig(surface_mode="chat")

    assert _chat_surface_blocks_tool(call, registry, config) is True
    result = _chat_surface_blocked_tool_result(call, registry)
    assert "not available in Chat or Research" in result
    assert "web_research" in result
    assert "deep_research_plan" in result
    assert _chat_surface_blocks_tool(ToolCall(id="call-2", name="deep_research_plan", arguments={}), registry, config) is False
    assert _chat_surface_blocks_tool(ToolCall(id="call-3", name="deep_research_run", arguments={}), registry, config) is False


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
    tmp_path: Path,
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
    app, session_manager = isolated_flask_app
    registry = web_app.get_registry()
    code_session = session_manager.create_session(
        title="Cancel blocking tool",
        workspace_id=web_app._runtime_state.active_workspace_id,
        mode="code",
    )
    source_root = web_app._active_workspace_root()

    def fake_create_worktree(workspace_root: str, *, run_id: str = "", session_id: str = "", label: str = "") -> WorktreeRecord:
        assert workspace_root == source_root
        assert run_id
        assert session_id == code_session.id
        assert label == "code-run"
        record = _fake_worktree_record(
            source_root,
            tmp_path / "code-cancel-tool-worktree",
            run_id=run_id,
            session_id=session_id,
            index=4,
        )
        record.worktree_id = "wt_code_cancel_tool"
        record.label = "code-run"
        return record

    monkeypatch.setattr(web_app, "create_worktree", fake_create_worktree)
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
        created = client.post(
            "/runs",
            json={"message": "cancel blocking tool", "session_id": code_session.id},
        )
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

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from backend.core.paths import clear_metis_home_cache
from backend.runtime import agent_loop
from backend.web import session_db as session_db_module
from metis import Agent, AgentEvent, AgentRunError
from metis import agent as sdk_agent


@pytest.fixture(autouse=True)
def isolated_sdk_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("METIS_HOME", str(tmp_path / "metis-home"))
    monkeypatch.setattr(session_db_module, "legacy_data_root", lambda: str(tmp_path / "legacy-miro"))
    clear_metis_home_cache()
    yield
    clear_metis_home_cache()


def _install_runtime(monkeypatch: pytest.MonkeyPatch, run: Any, captured: dict[str, Any]) -> None:
    config = SimpleNamespace(permission_checker=None)

    def build(args: Any, **kwargs: Any):
        captured["args"] = args
        captured["build"] = kwargs
        captured["api_key_during_build"] = os.environ.get("METIS_LLM_API_KEY")
        return config, object()

    monkeypatch.setattr(sdk_agent, "build_cli_runtime", build)
    monkeypatch.setattr(sdk_agent, "build_permission_checker", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent_loop, "run", run)


def test_sdk_stream_uses_shared_event_contract_and_explicit_permission(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    def runtime(messages: list[dict[str, str]], *args: Any, **kwargs: Any):
        captured["messages"] = messages
        captured["approved"] = yield SimpleNamespace(
            type="permission_request",
            tool_name="write_file",
            arguments={"path": "notes.md"},
            call_id="call-1",
            request_id="request-1",
        )
        yield SimpleNamespace(type="content", text="saved")
        yield SimpleNamespace(
            type="done",
            total_turns=1,
            total_tool_calls=1,
            prompt_tokens=4,
            completion_tokens=1,
            total_tokens=5,
            prompt_cache_hit_tokens=0,
            prompt_cache_miss_tokens=4,
            context_ledger={},
        )

    _install_runtime(monkeypatch, runtime, captured)
    monkeypatch.delenv("METIS_LLM_API_KEY", raising=False)
    agent = Agent(
        backend="fake",
        model="fake-model",
        api_key="sdk-secret",
        permission_mode="ask",
        allowed_tools=["write_file"],
    )
    stream = agent.run("save it", workspace=tmp_path)

    permission = next(stream)
    assert permission.schema == "metis.agent_event.v1"
    assert permission.kind == "permission_request"
    assert permission.tool == "write_file"
    content = stream.send(True)
    assert content.kind == "content"
    done = next(stream)
    assert done.kind == "done"
    with pytest.raises(StopIteration) as stopped:
        next(stream)
    result = stopped.value.value

    assert captured["approved"] is True
    assert captured["messages"] == [{"role": "user", "content": "save it"}]
    assert captured["api_key_during_build"] == "sdk-secret"
    assert "METIS_LLM_API_KEY" not in os.environ
    assert result.ok is True
    assert result.final_text == "saved"
    assert result.usage["total_tokens"] == 5
    assert result.session_id.startswith("cli_")


def test_sdk_permission_defaults_to_deny_and_callback_can_allow(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    decisions: list[bool | None] = []
    captured: dict[str, Any] = {}

    def runtime(*args: Any, **kwargs: Any):
        decision = yield SimpleNamespace(
            type="permission_request",
            tool_name="execute_bash_command",
            arguments={"command": "pytest"},
        )
        decisions.append(decision)
        yield SimpleNamespace(type="content", text="done")
        yield SimpleNamespace(type="done", total_turns=1, total_tool_calls=0, total_tokens=1)

    _install_runtime(monkeypatch, runtime, captured)
    result = Agent().run_to_completion("test", workspace=tmp_path)
    assert result.ok
    assert decisions == [False]

    decisions.clear()
    result = Agent().run_to_completion(
        "test again",
        workspace=tmp_path,
        permission_handler=lambda event: event.tool == "execute_bash_command",
    )
    assert result.ok
    assert decisions == [True]


def test_sdk_runtime_exception_becomes_error_event_and_typed_exception(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_build(*args: Any, **kwargs: Any):
        raise RuntimeError("provider exploded")

    monkeypatch.setattr(sdk_agent, "build_cli_runtime", fail_build)
    monkeypatch.setattr(sdk_agent, "build_permission_checker", lambda *args, **kwargs: None)
    events: list[AgentEvent] = []

    with pytest.raises(AgentRunError) as error:
        Agent().run_to_completion("fail", workspace=tmp_path, on_event=events.append)

    assert error.value.result.exit_code == 3
    assert error.value.result.error["code"] == "SDK_RUNTIME_EXCEPTION"
    assert len(events) == 1
    assert events[0].kind == "error"
    assert "provider exploded" in str(events[0]["message"])


def test_agent_event_is_mapping_with_defensive_copy() -> None:
    source = {
        "schema": "metis.agent_event.v1",
        "kind": "content",
        "type": "content",
        "event_id": "evt-1",
        "timestamp": 1.0,
        "payload": {"text": "hello", "items": [{"name": "first"}]},
        "text": "hello",
    }
    event = AgentEvent(source)
    source["text"] = "changed"
    exported = event.as_dict()
    exported["text"] = "also changed"

    assert event.kind == "content"
    assert event.text == "hello"
    assert event.payload["text"] == "hello"
    with pytest.raises(TypeError):
        event.payload["items"][0]["name"] = "changed"
    exported["payload"]["items"][0]["name"] = "exported change"
    assert event.payload["items"][0]["name"] == "first"


def test_sdk_validates_prompt_workspace_and_resume_options(tmp_path: Path) -> None:
    agent = Agent()
    with pytest.raises(ValueError, match="prompt is required"):
        next(agent.run("", workspace=tmp_path))
    with pytest.raises(ValueError, match="workspace does not exist"):
        next(agent.run("task", workspace=tmp_path / "missing"))
    with pytest.raises(ValueError, match="mutually exclusive"):
        next(agent.run("task", workspace=tmp_path, session_id="one", continue_session=True))


def test_closing_sdk_stream_restores_environment_and_working_directory(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    def runtime(*args: Any, **kwargs: Any):
        yield SimpleNamespace(type="content_delta", text="partial")
        yield SimpleNamespace(type="content", text="never reached")

    _install_runtime(monkeypatch, runtime, captured)
    monkeypatch.delenv("METIS_LLM_API_KEY", raising=False)
    previous = Path.cwd()
    stream = Agent(api_key="temporary-secret").run("partial", workspace=tmp_path)

    assert next(stream).text == "partial"
    assert Path.cwd() == tmp_path.resolve()
    assert os.environ["METIS_LLM_API_KEY"] == "temporary-secret"
    stream.close()

    assert Path.cwd() == previous
    assert "METIS_LLM_API_KEY" not in os.environ

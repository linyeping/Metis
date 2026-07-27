from __future__ import annotations

import io
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Mapping

import pytest
from backend.cli import app as cli_app
from backend.cli import tui
from backend.cli.args import parse_args
from backend.cli.headless import EXIT_SUCCESS


class FakeFrontend:
    def __init__(self, prompts: list[str | None] | None = None, *, approve: bool = True) -> None:
        self.prompts = list(prompts or [])
        self.approve = approve
        self.output: list[str] = []
        self.permission: tuple[str, Mapping[str, Any]] | None = None
        self.cleared = False

    def read_prompt(self, *, toolbar: str) -> str | None:
        self.output.append(f"toolbar:{toolbar}")
        return self.prompts.pop(0) if self.prompts else None

    def write(self, text: str, *, style: str = "") -> None:
        self.output.append(text)

    def write_chunk(self, text: str) -> None:
        self.output.append(text)

    def confirm_permission(self, *, tool: str, arguments: Mapping[str, Any]) -> bool:
        self.permission = (tool, arguments)
        return self.approve

    def clear(self) -> None:
        self.cleared = True


def test_app_selects_tui_only_for_interactive_text_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    def fake_tui(args: Any, **kwargs: Any) -> int:
        captured["args"] = args
        captured.update(kwargs)
        return 17

    monkeypatch.setattr(cli_app, "_isatty", lambda stream: True)
    monkeypatch.setattr(tui, "run_tui", fake_tui)

    stdin = io.StringIO()
    stdout = io.StringIO()
    stderr = io.StringIO()
    assert cli_app.main([], stdin=stdin, stdout=stdout, stderr=stderr) == 17
    assert captured["args"].prompt == ""
    assert captured["stdin"] is stdin

    args = parse_args(["task", "-p"])
    assert cli_app._should_run_tui(args, stdin=stdin, stdout=stdout) is False
    args = parse_args(["task", "--output-format", "json"])
    assert cli_app._should_run_tui(args, stdin=stdin, stdout=stdout) is False


def test_drive_tui_resumes_generator_with_redacted_permission_decision() -> None:
    captured: dict[str, Any] = {}

    def events():
        captured["approved"] = yield SimpleNamespace(
            type="permission_request",
            tool_name="write_file",
            arguments={"path": "notes.md", "api_key": "never-print"},
        )
        yield SimpleNamespace(type="content", text="saved")
        yield SimpleNamespace(
            type="done",
            total_turns=1,
            total_tool_calls=1,
            prompt_tokens=2,
            completion_tokens=1,
            total_tokens=3,
        )

    frontend = FakeFrontend(approve=True)
    renderer = tui.TuiRenderer(frontend, serializer=lambda event: {})
    result = tui.drive_tui(
        events(),
        renderer=renderer,
        session_id="session-1",
        permission_handler=lambda event: frontend.confirm_permission(
            tool=event.tool_name,
            arguments=tui._redact(event.arguments),
        ),
    )

    assert result.exit_code == EXIT_SUCCESS
    assert result.final_text == "saved"
    assert captured["approved"] is True
    assert frontend.permission == ("write_file", {"path": "notes.md", "api_key": "***"})
    assert "never-print" not in "\n".join(frontend.output)


def test_tui_commands_switch_session_and_workspace(tmp_path: Path) -> None:
    frontend = FakeFrontend()
    state = tui.TuiState(workspace=tmp_path, session_id="old-session")
    store = SimpleNamespace(
        list_payload=lambda **kwargs: {
            "sessions": [
                {
                    "id": "old-session",
                    "title": "Existing task",
                    "workspace": str(tmp_path),
                }
            ]
        },
        resolve_resume=lambda session_id="", latest=False: SimpleNamespace(
            session_id="latest-session" if latest else "resolved-session",
            workspace=tmp_path,
        ),
    )

    assert tui._handle_command("/sessions", state=state, store=store, ui=frontend, attach=False)
    assert any("Existing task" in line for line in frontend.output)
    assert tui._handle_command("/resume prefix", state=state, store=store, ui=frontend, attach=False)
    assert state.session_id == "resolved-session"
    assert tui._handle_command("/continue", state=state, store=store, ui=frontend, attach=False)
    assert state.session_id == "latest-session"
    assert tui._handle_command(f"/workspace {tmp_path}", state=state, store=store, ui=frontend, attach=False)
    assert state.session_id == ""
    assert state.workspace_explicit is True
    assert tui._handle_command("/exit", state=state, store=store, ui=frontend, attach=False) is False


def test_attached_tui_waits_for_desktop_permission_without_sending_a_decision() -> None:
    sent: list[Any] = []

    def events():
        value = yield SimpleNamespace(type="permission_request", tool_name="execute_bash_command")
        sent.append(value)
        yield SimpleNamespace(type="content", text="done")
        yield SimpleNamespace(type="done", total_turns=1, total_tool_calls=1, total_tokens=1)

    frontend = FakeFrontend()
    result = tui.drive_tui(
        events(),
        renderer=tui.TuiRenderer(frontend, serializer=lambda event: {}),
        session_id="attached-session",
        permission_handler=None,
    )

    assert result.exit_code == EXIT_SUCCESS
    assert sent == [None]
    assert any("Metis desktop" in line for line in frontend.output)

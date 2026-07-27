from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

import pytest
from backend.cli import app as cli_app
from backend.cli import config as cli_config
from backend.cli.args import ParsedCliArgs, SessionCommandArgs, parse_args
from backend.cli.headless import EXIT_SUCCESS, EXIT_USAGE
from backend.cli.sessions import CliSessionStore
from backend.core.paths import clear_metis_home_cache
from backend.runtime import agent_loop
from backend.web import session_db as session_db_module
from backend.web.session_db import MetisSessionDB


@pytest.fixture(autouse=True)
def isolated_cli_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("METIS_HOME", str(tmp_path / "metis-home"))
    monkeypatch.setattr(cli_config, "read_api_key", lambda: None)
    monkeypatch.setattr(session_db_module, "legacy_data_root", lambda: str(tmp_path / "legacy-miro"))
    clear_metis_home_cache()
    yield
    clear_metis_home_cache()


def _events(text: str, *, with_tool: bool = False) -> Iterable[Any]:
    if with_tool:
        yield SimpleNamespace(type="tool_result", tool_name="read_file", call_id="call-1", result="file contents")
    yield SimpleNamespace(type="content", text=text)
    yield SimpleNamespace(
        type="done",
        total_turns=1,
        total_tool_calls=1 if with_tool else 0,
        prompt_tokens=4,
        completion_tokens=2,
        total_tokens=6,
        prompt_cache_hit_tokens=0,
        prompt_cache_miss_tokens=4,
    )


def _run_cli(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    *,
    answer: str = "done",
    capture: dict[str, Any] | None = None,
    with_tool: bool = False,
) -> tuple[int, str, str]:
    config = SimpleNamespace(permission_checker=None)
    monkeypatch.setattr(cli_app, "build_cli_runtime", lambda *args, **kwargs: (config, object()))
    monkeypatch.setattr(cli_app, "build_permission_checker", lambda *args, **kwargs: None)

    def fake_run(messages, *_args, **_kwargs):
        if capture is not None:
            capture["messages"] = messages
            capture["cwd"] = str(Path.cwd())
        return iter(_events(answer, with_tool=with_tool))

    monkeypatch.setattr(agent_loop, "run", fake_run)
    stdout = io.StringIO()
    stderr = io.StringIO()
    code = cli_app.main(argv, stdin=io.StringIO(""), stdout=stdout, stderr=stderr)
    return code, stdout.getvalue(), stderr.getvalue()


def test_parser_supports_sessions_and_resume_aliases() -> None:
    sessions = parse_args(["sessions", "list", "--limit", "5", "--output-format", "json"])
    assert isinstance(sessions, SessionCommandArgs)
    assert sessions.action == "list"
    assert sessions.limit == 5

    resume = parse_args(["resume", "cli_123", "continue this"])
    assert isinstance(resume, ParsedCliArgs)
    assert resume.resume_id == "cli_123"
    assert resume.prompt == "continue this"


def test_headless_run_persists_shared_desktop_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    code, _stdout, stderr = _run_cli(
        monkeypatch,
        ["inspect repository", "--workspace", str(tmp_path), "--output-format", "json"],
        answer="inspection complete",
        with_tool=True,
    )

    assert code == EXIT_SUCCESS
    assert stderr == ""
    db = MetisSessionDB()
    items = db.list_sessions()
    assert len(items) == 1
    assert items[0]["id"].startswith("cli_")
    session = db.get_session(items[0]["id"])
    assert session is not None
    assert [message["role"] for message in session["history"]] == ["user", "assistant", "assistant"]
    assert session["history"][1]["metis_kind"] == "tool"
    assert session["history"][2]["content"] == "inspection complete"


def test_resume_reuses_history_and_stored_workspace(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    first_code, _stdout, _stderr = _run_cli(
        monkeypatch,
        ["first task", "--workspace", str(tmp_path)],
        answer="first answer",
    )
    assert first_code == EXIT_SUCCESS
    session_id = MetisSessionDB().list_sessions()[0]["id"]
    captured: dict[str, Any] = {}

    second_code, _stdout, stderr = _run_cli(
        monkeypatch,
        ["--resume", session_id[:12], "follow up"],
        answer="second answer",
        capture=captured,
    )

    assert second_code == EXIT_SUCCESS
    assert stderr == ""
    assert Path(captured["cwd"]) == tmp_path
    assert [message["content"] for message in captured["messages"]] == ["first task", "first answer", "follow up"]
    session = MetisSessionDB().get_session(session_id)
    assert session is not None
    assert [message["content"] for message in session["history"]] == [
        "first task",
        "first answer",
        "follow up",
        "second answer",
    ]


def test_continue_uses_most_recent_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _run_cli(monkeypatch, ["original", "--workspace", str(tmp_path)], answer="answer")
    captured: dict[str, Any] = {}

    code, _stdout, _stderr = _run_cli(
        monkeypatch,
        ["--continue", "latest follow-up"],
        capture=captured,
    )

    assert code == EXIT_SUCCESS
    assert captured["messages"][-1]["content"] == "latest follow-up"


def test_resume_preserves_existing_desktop_surface(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    db = MetisSessionDB()
    workspace = db.create_workspace(str(tmp_path))
    session = db.create_session("Desktop chat", workspace_id=workspace["id"], mode="chat")
    db.update_session_fields(
        session["id"],
        history=[{"role": "user", "content": "desktop question"}, {"role": "assistant", "content": "desktop answer"}],
    )

    code, _stdout, _stderr = _run_cli(
        monkeypatch,
        ["--resume", session["id"], "terminal follow-up"],
        answer="terminal answer",
    )

    assert code == EXIT_SUCCESS
    assert MetisSessionDB().get_session(session["id"])["mode"] == "chat"


def test_sessions_list_show_and_export_contracts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    _run_cli(monkeypatch, ["portable session", "--workspace", str(tmp_path)], answer="portable answer")
    session_id = MetisSessionDB().list_sessions()[0]["id"]

    list_stdout = io.StringIO()
    list_code = cli_app.main(
        ["sessions", "list", "--output-format", "json"],
        stdout=list_stdout,
        stderr=io.StringIO(),
    )
    listed = json.loads(list_stdout.getvalue())
    assert list_code == EXIT_SUCCESS
    assert listed["schema"] == "metis.cli_sessions.v1"
    assert listed["sessions"][0]["id"] == session_id

    show_stdout = io.StringIO()
    show_code = cli_app.main(
        ["sessions", "show", session_id[:12], "--output-format", "json"],
        stdout=show_stdout,
        stderr=io.StringIO(),
    )
    shown = json.loads(show_stdout.getvalue())
    assert show_code == EXIT_SUCCESS
    assert shown["schema"] == "metis.cli_session.v1"
    assert shown["history"][-1]["content"] == "portable answer"

    export_path = tmp_path / "exports" / "session.json"
    export_code = cli_app.main(
        ["sessions", "export", session_id, "--output", str(export_path)],
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )
    exported = json.loads(export_path.read_text(encoding="utf-8"))
    assert export_code == EXIT_SUCCESS
    assert exported["schema"] == "metis.session_export.v1"
    assert exported["session"]["id"] == session_id

    markdown = io.StringIO()
    markdown_code = cli_app.main(
        ["sessions", "export", session_id, "--format", "markdown"],
        stdout=markdown,
        stderr=io.StringIO(),
    )
    assert markdown_code == EXIT_SUCCESS
    assert "# portable session" in markdown.getvalue()
    assert "## Assistant" in markdown.getvalue()


def test_missing_resume_session_returns_usage_error(monkeypatch: pytest.MonkeyPatch) -> None:
    code, _stdout, stderr = _run_cli(monkeypatch, ["--resume", "missing", "continue"])

    assert code == EXIT_USAGE
    assert "session not found" in stderr


def test_session_store_rejects_ambiguous_prefix(tmp_path: Path) -> None:
    db = MetisSessionDB()
    now = db.next_timestamp()
    for session_id in ("cli_same_one", "cli_same_two"):
        db.upsert_session(
            {
                "id": session_id,
                "title": session_id,
                "history": [],
                "compact_state": {},
                "mode": "code",
                "workspace_id": "",
                "created_at": now,
                "updated_at": now,
            }
        )

    with pytest.raises(ValueError, match="ambiguous"):
        CliSessionStore(db).resolve_resume("cli_same")

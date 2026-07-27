from __future__ import annotations

import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterator

import pytest
from backend.cli import app as cli_app
from backend.cli import attach as cli_attach
from backend.cli.args import parse_args
from backend.cli.headless import EXIT_SUCCESS, EXIT_USAGE, HeadlessRenderer, drive_headless
from backend.core import cli_attach_path
from backend.core import paths as core_paths
from backend.web import cli_attach as web_attach


def test_parse_attach_flag() -> None:
    args = parse_args(["--attach", "ship it", "--output-format", "stream-json"])

    assert args.attach is True
    assert args.prompt == "ship it"
    assert args.output_format == "stream-json"


def test_attach_main_does_not_start_a_local_session(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run_attached(args: Any, **kwargs: Any):
        captured.update(kwargs)
        return SimpleNamespace(exit_code=EXIT_SUCCESS)

    monkeypatch.setattr(cli_attach, "run_attached", fake_run_attached)
    monkeypatch.setattr(
        cli_app.CliSessionStore,
        "begin_run",
        lambda *args, **kwargs: pytest.fail("attach must not write the prompt to the local session first"),
    )
    stdout = io.StringIO()
    stderr = io.StringIO()

    exit_code = cli_app.main(
        ["--attach", "ship it", "--workspace", str(tmp_path)],
        stdin=io.StringIO(),
        stdout=stdout,
        stderr=stderr,
    )

    assert exit_code == EXIT_SUCCESS
    assert captured["prompt"] == "ship it"
    assert captured["workspace"] == tmp_path.resolve()
    assert stderr.getvalue() == ""


def test_attach_rejects_runtime_overrides_before_discovery(tmp_path: Path) -> None:
    stderr = io.StringIO()

    exit_code = cli_app.main(
        ["--attach", "ship it", "--model", "ignored-model", "--workspace", str(tmp_path)],
        stdin=io.StringIO(),
        stdout=io.StringIO(),
        stderr=stderr,
    )

    assert exit_code == EXIT_USAGE
    assert "unsupported override(s): --model" in stderr.getvalue()


def test_attached_resume_is_resolved_by_desktop_not_local_database(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_run_attached(args: Any, **kwargs: Any):
        captured["args"] = args
        captured.update(kwargs)
        return SimpleNamespace(exit_code=EXIT_SUCCESS)

    monkeypatch.setattr(cli_attach, "run_attached", fake_run_attached)
    monkeypatch.setattr(
        cli_app.CliSessionStore,
        "resolve_resume",
        lambda *args, **kwargs: pytest.fail("attached resume must be resolved by the desktop session store"),
    )

    exit_code = cli_app.main(
        ["--attach", "--resume", "desktop-prefix", "continue"],
        stdin=io.StringIO(),
        stdout=io.StringIO(),
        stderr=io.StringIO(),
    )

    assert exit_code == EXIT_SUCCESS
    assert captured["workspace"] is None
    assert captured["args"].resume_id == "desktop-prefix"


def test_windows_discovery_path_is_independent_from_metis_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    local_app_data = tmp_path / "local-app-data"
    monkeypatch.delenv("METIS_CLI_ATTACH_DISCOVERY", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(local_app_data))
    monkeypatch.setenv("METIS_HOME", str(tmp_path / "portable-install" / "metis"))
    monkeypatch.setattr(cli_attach_path.sys, "platform", "win32")

    assert cli_attach_path.cli_attach_discovery_path() == (
        local_app_data / "Metis" / "runtime" / "desktop-attach.json"
    ).resolve()


def test_cli_uses_published_desktop_data_home(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime_dir = tmp_path / "runtime"
    desktop_home = tmp_path / "installed-data" / "metis"
    runtime_dir.mkdir()
    desktop_home.mkdir(parents=True)
    (runtime_dir / "data-home.json").write_text(
        json.dumps({"schema": "metis.cli_data_home.v1", "metis_home": str(desktop_home)}),
        encoding="utf-8",
    )
    monkeypatch.delenv("METIS_HOME", raising=False)
    monkeypatch.setattr(cli_attach_path, "cli_runtime_dir", lambda: runtime_dir)
    monkeypatch.setattr(core_paths, "_portable_data_dir", lambda: None)
    core_paths.clear_metis_home_cache()
    try:
        assert core_paths.metis_home() == desktop_home.resolve()
    finally:
        core_paths.clear_metis_home_cache()


def test_discovery_publish_load_and_instance_aware_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    discovery = tmp_path / "desktop-attach.json"
    monkeypatch.setenv("METIS_CLI_ATTACH_DISCOVERY", str(discovery))
    monkeypatch.setattr(web_attach, "_PUBLISHED_PATH", None)
    monkeypatch.setattr(web_attach, "_restrict_windows_acl", lambda path: True)
    monkeypatch.setattr(cli_attach, "_process_alive", lambda pid: pid == os.getpid())

    published = web_attach.publish_attach_discovery(host="127.0.0.1", port=54321)
    endpoint = cli_attach.load_attach_endpoint(discovery)

    assert published == discovery
    assert endpoint.port == 54321
    assert endpoint.pid == os.getpid()
    assert len(endpoint.token) >= 32

    payload = json.loads(discovery.read_text(encoding="utf-8"))
    payload["instance_id"] = "another-desktop-instance"
    discovery.write_text(json.dumps(payload), encoding="utf-8")
    assert web_attach.clear_attach_discovery() is False
    assert discovery.exists()


def test_invalid_numeric_discovery_values_are_attach_errors(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    discovery = tmp_path / "desktop-attach.json"
    discovery.write_text(
        json.dumps(
            {
                "schema": cli_attach.DISCOVERY_SCHEMA,
                "protocol": cli_attach.ATTACH_PROTOCOL,
                "host": "127.0.0.1",
                "port": "not-a-port",
                "pid": os.getpid(),
                "instance_id": "a" * 32,
                "token": "b" * 43,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(cli_attach.CliAttachError, match="invalid endpoint data"):
        cli_attach.load_attach_endpoint(discovery)


def test_stable_desktop_publishes_current_user_data_home_pointer(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    pointer = tmp_path / "runtime" / "data-home.json"
    desktop_home = tmp_path / "desktop-data" / "metis"
    desktop_home.mkdir(parents=True)
    monkeypatch.delenv("METIS_CLI_ATTACH_DISCOVERY", raising=False)
    monkeypatch.setenv("METIS_CLI_ATTACH_CHANNEL", "stable")
    monkeypatch.setattr(web_attach, "cli_data_home_pointer_path", lambda: pointer)
    monkeypatch.setattr(web_attach, "metis_home", lambda: desktop_home)
    monkeypatch.setattr(web_attach, "_restrict_windows_acl", lambda path: True)

    published = web_attach._publish_data_home_pointer()
    payload = json.loads(pointer.read_text(encoding="utf-8"))

    assert published == pointer
    assert payload["schema"] == "metis.cli_data_home.v1"
    assert payload["metis_home"] == str(desktop_home)


def test_attached_permission_waits_for_later_done_event() -> None:
    raw_events = [
        {
            "schema": "metis.agent_event.v1",
            "kind": "permission_request",
            "type": "permission_request",
            "tool": "write_file",
            "request_id": "request-1",
            "payload": {"arguments": {"path": "notes.md"}},
        },
        {
            "schema": "metis.agent_event.v1",
            "kind": "content",
            "type": "content",
            "text": "done",
            "payload": {"text": "done"},
        },
        {
            "schema": "metis.agent_event.v1",
            "kind": "done",
            "type": "done",
            "turns": 2,
            "tool_calls": 1,
            "usage": {"prompt_tokens": 8, "completion_tokens": 2, "total_tokens": 10},
            "payload": {},
        },
    ]

    def events() -> Iterator[Any]:
        for item in raw_events:
            yield cli_attach._event_object(item)

    stdout = io.StringIO()
    stderr = io.StringIO()
    renderer = HeadlessRenderer(
        output_format="text",
        stdout=stdout,
        stderr=stderr,
        serializer=lambda event: event._raw,
    )

    result = drive_headless(
        events(),
        renderer=renderer,
        session_id="session-1",
        permission_fail_fast=False,
    )

    assert result.exit_code == EXIT_SUCCESS
    assert result.final_text == "done"
    assert result.turns == 2
    assert result.tool_calls == 1
    assert result.usage["total_tokens"] == 10
    assert "Approve or deny write_file" in stderr.getvalue()

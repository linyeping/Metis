from __future__ import annotations

import io
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Iterable

import pytest
from backend.cli import app as cli_app
from backend.cli import config as cli_config
from backend.cli.args import parse_args
from backend.cli.config import merged_settings
from backend.cli.headless import EXIT_BUDGET, EXIT_PERMISSION, EXIT_SUCCESS, EXIT_USAGE
from backend.cli.policy import CliPolicyError, build_permission_checker
from backend.core.paths import clear_metis_home_cache
from backend.runtime import agent_loop
from backend.web import session_db as session_db_module


@pytest.fixture(autouse=True)
def isolated_cli_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    monkeypatch.setenv("METIS_HOME", str(tmp_path / "metis-home"))
    monkeypatch.setattr(cli_config, "read_api_key", lambda: None)
    monkeypatch.setattr(session_db_module, "legacy_data_root", lambda: str(tmp_path / "legacy-miro"))
    clear_metis_home_cache()
    yield
    clear_metis_home_cache()


@pytest.fixture(autouse=True)
def no_real_credential_store(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli_config, "read_api_key", lambda: None)


def _event_generator(events: Iterable[Any]):
    yield from events


def _invoke(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    events: Iterable[Any],
    *argv: str,
    stdin_text: str = "",
) -> tuple[int, str, str]:
    config = SimpleNamespace(permission_checker=None)
    monkeypatch.setattr(cli_app, "build_cli_runtime", lambda *args, **kwargs: (config, object()))
    monkeypatch.setattr(cli_app, "build_permission_checker", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent_loop, "run", lambda *args, **kwargs: _event_generator(events))
    stdout = io.StringIO()
    stderr = io.StringIO()
    exit_code = cli_app.main(
        [*argv, "--workspace", str(tmp_path)],
        stdin=io.StringIO(stdin_text),
        stdout=stdout,
        stderr=stderr,
    )
    return exit_code, stdout.getvalue(), stderr.getvalue()


def test_print_mode_accepts_prompt_from_stdin(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    captured: dict[str, Any] = {}
    config = SimpleNamespace(permission_checker=None)
    monkeypatch.setattr(cli_app, "build_cli_runtime", lambda *args, **kwargs: (config, object()))
    monkeypatch.setattr(cli_app, "build_permission_checker", lambda *args, **kwargs: None)

    def fake_run(messages: list[dict[str, str]], *args: Any, **kwargs: Any):
        captured["messages"] = messages
        return _event_generator(
            [
                SimpleNamespace(type="content", text="ok"),
                SimpleNamespace(type="done", total_turns=1, total_tool_calls=0),
            ]
        )

    monkeypatch.setattr(agent_loop, "run", fake_run)
    stdout = io.StringIO()
    exit_code = cli_app.main(
        ["-p", "--workspace", str(tmp_path)],
        stdin=io.StringIO("  从管道读取任务  \n"),
        stdout=stdout,
        stderr=io.StringIO(),
    )

    assert exit_code == EXIT_SUCCESS
    assert captured["messages"] == [{"role": "user", "content": "从管道读取任务"}]
    assert stdout.getvalue() == "ok\n"


def test_stream_json_uses_existing_event_contract(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    events = [
        SimpleNamespace(type="content", text="done"),
        SimpleNamespace(
            type="done",
            total_turns=2,
            total_tool_calls=1,
            prompt_tokens=10,
            completion_tokens=3,
            total_tokens=13,
            prompt_cache_hit_tokens=4,
            prompt_cache_miss_tokens=6,
            context_ledger={},
        ),
    ]

    exit_code, stdout, stderr = _invoke(
        monkeypatch,
        tmp_path,
        events,
        "ship it",
        "-p",
        "--output-format",
        "stream-json",
    )

    assert exit_code == EXIT_SUCCESS
    assert stderr == ""
    lines = [json.loads(line) for line in stdout.splitlines()]
    assert [line["kind"] for line in lines] == ["content", "done"]
    assert all(line["schema"] == "metis.agent_event.v1" for line in lines)
    assert lines[0]["payload"]["text"] == "done"


def test_json_output_is_one_final_summary(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    events = [
        SimpleNamespace(type="content", text="final answer"),
        SimpleNamespace(
            type="done",
            total_turns=1,
            total_tool_calls=0,
            prompt_tokens=5,
            completion_tokens=2,
            total_tokens=7,
            prompt_cache_hit_tokens=0,
            prompt_cache_miss_tokens=5,
        ),
    ]

    exit_code, stdout, _ = _invoke(
        monkeypatch,
        tmp_path,
        events,
        "answer",
        "-p",
        "--output-format",
        "json",
    )

    payload = json.loads(stdout)
    assert exit_code == EXIT_SUCCESS
    assert payload["schema"] == "metis.cli_result.v1"
    assert payload["exit"] == 0
    assert payload["text"] == "final answer"
    assert payload["event_count"] == 2
    assert payload["usage"]["total_tokens"] == 7


def test_permission_request_fails_fast_without_resuming_generator(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    resumed = {"value": False}

    def permission_events():
        yield SimpleNamespace(
            type="permission_request",
            tool_name="write_file",
            arguments={"path": "notes.md", "api_key": "do-not-print"},
            call_id="call-1",
            request_id="request-1",
        )
        resumed["value"] = True

    exit_code, stdout, stderr = _invoke(
        monkeypatch,
        tmp_path,
        permission_events(),
        "write",
        "-p",
        "--output-format",
        "stream-json",
    )

    assert exit_code == EXIT_PERMISSION
    assert resumed["value"] is False
    assert json.loads(stdout)["kind"] == "permission_request"
    error = json.loads(stderr)
    assert error["error"] == "permission_required"
    assert error["tool"] == "write_file"
    assert error["arguments"]["api_key"] == "***"
    assert "do-not-print" not in stderr


def test_max_turns_error_uses_budget_exit_code(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    events = [
        SimpleNamespace(
            type="error",
            code="RUNTIME_MAX_TURNS",
            title="limit",
            message="too many turns",
            hint="increase limit",
            recoverable=False,
        ),
        SimpleNamespace(
            type="done",
            total_turns=3,
            total_tool_calls=2,
            prompt_tokens=0,
            completion_tokens=0,
            total_tokens=0,
            prompt_cache_hit_tokens=0,
            prompt_cache_miss_tokens=0,
        ),
    ]

    exit_code, stdout, _ = _invoke(
        monkeypatch,
        tmp_path,
        events,
        "loop",
        "-p",
        "--output-format",
        "json",
    )

    assert exit_code == EXIT_BUDGET
    assert json.loads(stdout)["exit"] == EXIT_BUDGET


def test_missing_prompt_and_invalid_workspace_are_usage_errors(tmp_path: Path) -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    missing_prompt = cli_app.main([], stdin=io.StringIO(""), stdout=stdout, stderr=stderr)
    assert missing_prompt == EXIT_USAGE
    assert "prompt is required" in stderr.getvalue()

    stderr = io.StringIO()
    missing_workspace = cli_app.main(
        ["task", "--workspace", str(tmp_path / "missing")],
        stdin=io.StringIO(""),
        stdout=io.StringIO(),
        stderr=stderr,
    )
    assert missing_workspace == EXIT_USAGE
    assert "workspace does not exist" in stderr.getvalue()


def test_config_precedence_is_cli_then_env_then_workspace_then_user(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    user_root = tmp_path / "user"
    workspace = tmp_path / "workspace"
    (workspace / ".metis").mkdir(parents=True)
    user_root.mkdir()
    (user_root / "config.json").write_text(json.dumps({"model": "user-model", "backend": "deepseek"}), encoding="utf-8")
    (workspace / ".metis" / "settings.json").write_text(
        json.dumps({"model": "workspace-model", "permission_mode": "edit"}),
        encoding="utf-8",
    )
    monkeypatch.setenv("METIS_HOME", str(user_root))
    monkeypatch.setenv("METIS_LLM_MODEL", "env-model")

    env_args = parse_args(["task"])
    assert merged_settings(env_args, workspace=workspace)["model"] == "env-model"

    cli_args = parse_args(["task", "--model", "cli-model"])
    settings = merged_settings(cli_args, workspace=workspace)
    assert settings["model"] == "cli-model"
    assert settings["backend"] == "deepseek"
    assert settings["permission_mode"] == "edit"


def test_canonical_metis_environment_wins_provider_aliases(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setenv("METIS_LLM_MODEL", "canonical-model")
    monkeypatch.setenv("OPENAI_MODEL", "openai-alias")
    monkeypatch.setenv("ANTHROPIC_MODEL", "anthropic-alias")
    monkeypatch.setenv("METIS_LLM_API_KEY", "canonical-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "provider-key")

    settings = merged_settings(parse_args(["task"]), workspace=tmp_path)

    assert settings["model"] == "canonical-model"
    assert settings["api_key"] == "canonical-key"


def test_cli_uses_shared_credential_as_final_api_key_fallback(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    user_root = tmp_path / "user"
    user_root.mkdir()
    monkeypatch.setenv("METIS_HOME", str(user_root))
    monkeypatch.setattr(cli_config, "read_api_key", lambda: "credential-key")

    settings = merged_settings(parse_args(["task"]), workspace=tmp_path)

    assert settings["api_key"] == "credential-key"


def test_cli_explicit_configuration_wins_shared_credential(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    user_root = tmp_path / "user"
    user_root.mkdir()
    (user_root / "config.json").write_text(json.dumps({"api_key": "config-key"}), encoding="utf-8")
    monkeypatch.setenv("METIS_HOME", str(user_root))
    monkeypatch.setattr(cli_config, "read_api_key", lambda: "credential-key")

    assert merged_settings(parse_args(["task"]), workspace=tmp_path)["api_key"] == "config-key"

    monkeypatch.setenv("METIS_LLM_API_KEY", "environment-key")
    assert merged_settings(parse_args(["task"]), workspace=tmp_path)["api_key"] == "environment-key"


def test_cli_ignores_credential_manager_read_failure(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail_read() -> str:
        raise cli_config.CredentialStoreError("read failed")

    monkeypatch.setattr(cli_config, "read_api_key", fail_read)

    assert "api_key" not in merged_settings(parse_args(["task"]), workspace=tmp_path)


def test_policy_supports_current_and_p0_future_rule_shapes(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    (workspace / ".metis").mkdir(parents=True)
    (workspace / ".metis" / "permissions.json").write_text(
        json.dumps({"rules": [{"tool": "delete_file", "action": "deny"}]}),
        encoding="utf-8",
    )
    policy = workspace / "ci-policy.json"
    policy.write_text(
        json.dumps(
            {
                "version": 1,
                "rules": [
                    {
                        "id": "allow-tests",
                        "match": {"tool": "execute_bash_command", "cmd": "pytest*"},
                        "effect": "allow",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    checker = build_permission_checker(workspace, str(policy))
    assert checker("delete_file", {"path": "x"}) == "deny"
    assert checker("execute_bash_command", {"command": "pytest -q"}) == "allow"
    assert checker("read_file", {"path": "x"}) is None


def test_policy_rejects_unimplemented_match_fields(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    policy = workspace / "policy.json"
    policy.write_text(
        json.dumps({"version": 1, "rules": [{"match": {"level": "irreversible"}, "effect": "ask"}]}),
        encoding="utf-8",
    )
    with pytest.raises(CliPolicyError, match="not supported"):
        build_permission_checker(workspace, str(policy))


def test_help_does_not_initialize_runtime(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli_app.main(["--help"]) == 0
    captured = capsys.readouterr()
    assert "Metis agent CLI" in captured.out
    assert "FALLBACK" not in captured.out + captured.err

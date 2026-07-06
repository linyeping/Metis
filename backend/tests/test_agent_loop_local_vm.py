from __future__ import annotations

import json
from pathlib import Path

from backend.runtime import agent_loop
from backend.runtime.agent_loop import _execute_tool_with_hooks_sync
from backend.runtime.llm_backends import ToolCall
from backend.runtime.tool_registry import ToolDefinition, ToolRegistry


def test_execute_bash_command_uses_local_vm_profile(tmp_path: Path, monkeypatch) -> None:
    called = {"registry": False}
    captured: dict[str, object] = {}

    def host_shell(**_kwargs: object) -> str:
        called["registry"] = True
        return "host shell"

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="execute_bash_command",
            description="shell",
            parameters={"type": "object", "properties": {}},
            execute_fn=host_shell,
        )
    )

    def fake_vm(request):
        captured["request"] = request
        return {
            "schema": "metis.local_vm_runner.v1",
            "ok": True,
            "runner": "local_vm",
            "backend": "metis_wsl",
            "run_id": "vmcmd_test",
            "job": {
                "ok": True,
                "status": "done",
                "job_id": "job_test",
                "session_id": "rt_test",
                "backend": "metis_wsl",
                "command": request.command,
                "returncode": 0,
                "stdout": "hi",
                "stderr": "",
                "artifacts": [],
                "changed_files": [],
            },
        }

    monkeypatch.setattr("backend.runtime.local_vm_runner.run_local_vm_command", fake_vm)

    result = _execute_tool_with_hooks_sync(
        registry,
        ToolCall(id="call-shell", name="execute_bash_command", arguments={"command": "echo hi", "cwd": "."}),
        workspace_root=str(tmp_path),
        execution_profile="local_vm",
        session_id="desktop_session",
    )

    payload = json.loads(result.split("\n", 1)[1])
    assert called["registry"] is False
    assert payload["backend"] == "metis_wsl"
    assert payload["stdout"] == "hi"
    assert captured["request"].command == "echo hi"
    assert captured["request"].cwd == "."


def test_execute_bash_command_can_request_local_vm_inside_worktree_profile(tmp_path: Path, monkeypatch) -> None:
    called = {"registry": False}
    captured: dict[str, object] = {}

    def host_shell(**_kwargs: object) -> str:
        called["registry"] = True
        return "host shell"

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="execute_bash_command",
            description="shell",
            parameters={"type": "object", "properties": {}},
            execute_fn=host_shell,
        )
    )

    def fake_vm(request):
        captured["request"] = request
        return {
            "schema": "metis.local_vm_runner.v1",
            "ok": True,
            "runner": "local_vm",
            "backend": "metis_wsl",
            "run_id": "vmcmd_selector",
            "job": {
                "ok": True,
                "status": "done",
                "job_id": "job_selector",
                "session_id": "rt_selector",
                "backend": "metis_wsl",
                "command": request.command,
                "returncode": 0,
                "stdout": "selector ok",
                "stderr": "",
                "artifacts": [],
                "changed_files": [],
            },
        }

    monkeypatch.setattr("backend.runtime.local_vm_runner.run_local_vm_command", fake_vm)

    result = _execute_tool_with_hooks_sync(
        registry,
        ToolCall(
            id="call-shell-selector",
            name="execute_bash_command",
            arguments={"command": "python -V", "execution_profile": "local_vm"},
        ),
        workspace_root=str(tmp_path),
        execution_profile="local_worktree",
        session_id="desktop_session",
    )

    payload = json.loads(result.split("\n", 1)[1])
    assert called["registry"] is False
    assert payload["backend"] == "metis_wsl"
    assert payload["stdout"] == "selector ok"
    assert captured["request"].command == "python -V"


def test_execute_bash_command_strips_execution_selector_for_host_tool(tmp_path: Path, monkeypatch) -> None:
    seen: dict[str, object] = {}

    def host_shell(command: str, cwd: str = ".") -> str:
        seen["command"] = command
        seen["cwd"] = cwd
        return "host shell"

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="execute_bash_command",
            description="shell",
            parameters={"type": "object", "properties": {}},
            execute_fn=host_shell,
        )
    )
    monkeypatch.setattr(
        "backend.runtime.local_vm_runner.run_local_vm_command",
        lambda _request: (_ for _ in ()).throw(AssertionError("local_vm runner should not be called")),
    )

    result = _execute_tool_with_hooks_sync(
        registry,
        ToolCall(
            id="call-shell-host",
            name="execute_bash_command",
            arguments={"command": "echo hi", "cwd": ".", "execution_profile": "local_worktree", "use_local_vm": False},
        ),
        workspace_root=str(tmp_path),
        execution_profile="local_worktree",
        session_id="desktop_session",
    )

    assert result == "host shell"
    assert seen == {"command": "echo hi", "cwd": "."}


def test_local_vm_profile_does_not_intercept_write_tools(tmp_path: Path, monkeypatch) -> None:
    called = {"registry": False}

    def write_file(path: str, content: str = "") -> str:
        called["registry"] = True
        target = tmp_path / path
        target.write_text(content, encoding="utf-8")
        return str(target)

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="write_file",
            description="write",
            parameters={"type": "object", "properties": {}},
            execute_fn=write_file,
        )
    )
    monkeypatch.setattr(
        "backend.runtime.local_vm_runner.run_local_vm_command",
        lambda _request: (_ for _ in ()).throw(AssertionError("local_vm runner should not be called")),
    )

    result = _execute_tool_with_hooks_sync(
        registry,
        ToolCall(id="call-write", name="write_file", arguments={"path": "out.txt", "content": "ok"}),
        workspace_root=str(tmp_path),
        execution_profile="local_vm",
        session_id="desktop_session",
    )

    assert called["registry"] is True
    assert "out.txt" in result
    assert (tmp_path / "out.txt").read_text(encoding="utf-8") == "ok"


def test_run_tests_autodetects_command_for_local_vm(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "pyproject.toml").write_text("[tool.pytest.ini_options]\n", encoding="utf-8")
    captured: dict[str, object] = {}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="run_tests",
            description="tests",
            parameters={"type": "object", "properties": {}},
            execute_fn=lambda **_kwargs: "host tests",
        )
    )

    def fake_vm(request):
        captured["request"] = request
        return {
            "schema": "metis.local_vm_runner.v1",
            "ok": True,
            "runner": "local_vm",
            "backend": "metis_wsl",
            "run_id": "vmcmd_test",
            "job": {
                "ok": True,
                "status": "done",
                "job_id": "job_test",
                "session_id": "rt_test",
                "backend": "metis_wsl",
                "command": request.command,
                "returncode": 0,
                "stdout": "passed",
                "stderr": "",
                "artifacts": [],
                "changed_files": [],
            },
        }

    monkeypatch.setattr("backend.runtime.local_vm_runner.run_local_vm_command", fake_vm)

    result = _execute_tool_with_hooks_sync(
        registry,
        ToolCall(id="call-tests", name="run_tests", arguments={}),
        workspace_root=str(tmp_path),
        execution_profile="local_vm",
        session_id="desktop_session",
    )

    payload = json.loads(result.split("\n", 1)[1])
    assert captured["request"].command == "python -m pytest --tb=short -q"
    assert payload["stdout"] == "passed"


def test_run_tests_can_request_local_vm_inside_worktree_profile(tmp_path: Path, monkeypatch) -> None:
    (tmp_path / "package.json").write_text('{"scripts":{"test":"echo ok"}}\n', encoding="utf-8")
    captured: dict[str, object] = {}

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="run_tests",
            description="tests",
            parameters={"type": "object", "properties": {}},
            execute_fn=lambda **_kwargs: "host tests",
        )
    )

    def fake_vm(request):
        captured["request"] = request
        return {
            "schema": "metis.local_vm_runner.v1",
            "ok": True,
            "runner": "local_vm",
            "backend": "metis_wsl",
            "run_id": "vmcmd_test_selector",
            "job": {
                "ok": True,
                "status": "done",
                "job_id": "job_test_selector",
                "session_id": "rt_test_selector",
                "backend": "metis_wsl",
                "command": request.command,
                "returncode": 0,
                "stdout": "tests passed",
                "stderr": "",
                "artifacts": [],
                "changed_files": [],
            },
        }

    monkeypatch.setattr("backend.runtime.local_vm_runner.run_local_vm_command", fake_vm)

    result = _execute_tool_with_hooks_sync(
        registry,
        ToolCall(id="call-tests-selector", name="run_tests", arguments={"use_local_vm": True}),
        workspace_root=str(tmp_path),
        execution_profile="local_worktree",
        session_id="desktop_session",
    )

    payload = json.loads(result.split("\n", 1)[1])
    assert captured["request"].command == "npm test"
    assert payload["stdout"] == "tests passed"


def test_local_vm_result_registers_artifacts(tmp_path: Path, monkeypatch) -> None:
    artifact_path = tmp_path / ".metis" / "artifacts" / "rt_test" / "report.md"
    patch_path = tmp_path / ".metis" / "artifacts" / "rt_test" / "rt_test.patch"
    artifact_path.parent.mkdir(parents=True)
    artifact_path.write_text("# Report\n", encoding="utf-8")
    patch_path.write_text("diff --git a/a.txt b/a.txt\n", encoding="utf-8")
    registered: list[dict[str, object]] = []

    def fake_register_artifact(**kwargs):
        registered.append(kwargs)
        return {"artifact_id": f"art_{len(registered)}", "kind": kwargs["kind"], "path": kwargs["path"]}

    monkeypatch.setattr("backend.runtime.artifact_registry.register_artifact", fake_register_artifact)
    payload = {
        "schema": "metis.local_vm_runner.v1",
        "ok": True,
        "runner": "local_vm",
        "backend": "metis_wsl",
        "run_id": "vmcmd_test",
        "job": {
            "job_id": "job_test",
            "session_id": "rt_test",
            "backend": "metis_wsl",
            "artifacts": [{"path": str(artifact_path), "relative_path": "report.md", "size": 9}],
            "patch_path": str(patch_path),
            "changed_files": [{"path": "a.txt", "status": "modified"}],
        },
    }

    records, errors = agent_loop._register_local_vm_result_artifacts(
        payload,
        workspace_root=str(tmp_path),
        session_id="desktop_session",
        source_tool_call_id="call-shell",
    )

    assert errors == []
    assert [item["kind"] for item in registered] == ["document", "diff"]
    assert len(records) == 2
    assert registered[0]["session_id"] == "desktop_session"

from __future__ import annotations

import sys
from pathlib import Path

from backend.runtime.python_env import shell_command_with_configured_python
from backend.runtime import agent_loop
from backend.runtime.tool_tiers import TIER_2_TOOLS
from backend.tools.coding.execution import runtime_manager
from backend.tools.coding.foundation.cli import execute_shell
from backend.tools.desk_automation.inventory import scan_cli
from backend.tools.registry import AVAILABLE_TOOLS, TOOLS_SCHEMA


def test_new_120_command_precheck_reports_missing_runtime(monkeypatch) -> None:
    monkeypatch.setattr(runtime_manager.shutil, "which", lambda _name: None)

    missing = runtime_manager.check_command_runtime("node app.js")

    assert missing is not None
    assert missing["runtime"] == "Node.js"
    assert "OpenJS.NodeJS.LTS" in missing["install_cmd"]


def test_new_120_shell_precheck_message_mentions_install_tool(monkeypatch) -> None:
    monkeypatch.setattr(
        runtime_manager,
        "check_command_runtime",
        lambda _command: {
            "runtime": "Python",
            "description": "Python 3 interpreter",
            "install_cmd": "winget install --id Python.Python.3.12",
        },
    )

    message = execute_shell._runtime_precheck_message("python demo.py")

    assert 'install_dev_runtime("Python")' in message
    assert "winget install --id Python.Python.3.12" in message


def test_new_120_configured_python_satisfies_python_runtime(monkeypatch) -> None:
    monkeypatch.setenv("METIS_PYTHON", sys.executable)
    monkeypatch.setattr(runtime_manager.shutil, "which", lambda _name: None)

    assert runtime_manager.check_command_runtime("python -m pip --version") is None
    assert runtime_manager.check_command_runtime("py -0p") is None

    rewritten = shell_command_with_configured_python("py -0p && python -m pip --version")
    assert "print(sys.executable)" in rewritten
    assert sys.executable in rewritten
    assert " -m pip --version" in rewritten


def test_new_120_project_detection_and_cli_install_hints(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(runtime_manager.shutil, "which", lambda _name: None)

    needed = runtime_manager.detect_project_requirements(str(tmp_path))
    names = {runtime.name for runtime in needed}

    assert "Node.js" in names
    assert "Git" in names

    monkeypatch.setattr(scan_cli.shutil, "which", lambda _name: None)
    scan = scan_cli.scan_cli_candidates(("python", "git"))
    assert scan["missing"] == ["python", "git"]
    assert "Python.Python.3.12" in scan["install_hints"]["python"]
    assert "Git.Git" in scan["install_hints"]["git"]


def test_new_120_runtime_tools_are_registered_and_tiered() -> None:
    names = {(tool.get("function") or {}).get("name") for tool in TOOLS_SCHEMA}

    for name in ("check_dev_environment", "install_dev_runtime", "setup_workspace"):
        assert name in AVAILABLE_TOOLS
        assert name in names
        assert name in TIER_2_TOOLS


def test_new_120_environment_context_uses_independent_system_block(monkeypatch) -> None:
    monkeypatch.setattr(agent_loop, "_build_environment_context", lambda: "Available runtimes: Python.")

    prompt = agent_loop._system_prompt_with_environment_context("You are Metis.")
    messages = agent_loop._prepare_working_messages(
        [{"role": "user", "content": "hello"}],
        agent_loop.AgentConfig(system_prompt="You are Metis."),
    )

    assert prompt.startswith("You are Metis.")
    assert "[Loop Discipline]" in prompt
    assert messages[0]["role"] == "system"
    assert messages[0]["content"].startswith("You are Metis.")
    assert "[Loop Discipline]" in messages[0]["content"]
    assert messages[1]["role"] == "system"
    assert "## Development Environment" in messages[1]["content"]
    assert "Available runtimes: Python." in messages[1]["content"]
    assert messages[2]["role"] == "user"

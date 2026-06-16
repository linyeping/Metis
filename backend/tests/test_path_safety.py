from __future__ import annotations

import zipfile
from pathlib import Path

from backend.runtime.path_safety import validate_path_access, validate_tool_paths
from backend.runtime.agent_loop import _execute_tool_with_hooks_sync
from backend.runtime.llm_backends import ToolCall
from backend.runtime.tool_registry import ToolDefinition, ToolRegistry
from backend.tools.coding.foundation.core_mechanisms.execution_boundary_context import boundary_override
from backend.tools.coding.foundation.core_mechanisms.path_security import PathSecurityError, safe_path_for_read, safe_path_for_write
from backend.tools.coding.read_search.read_single.read_file import read_file


def test_path_safety_denies_secret_read(tmp_path: Path) -> None:
    target = tmp_path / ".env"
    target.write_text("SECRET=value", encoding="utf-8")

    decision = validate_path_access(str(target), action="read", workspace_root=str(tmp_path))

    assert not decision.allowed
    assert decision.code == "PATH_SENSITIVE"


def test_path_safety_denies_ssh_directory_read(tmp_path: Path) -> None:
    target = tmp_path / ".ssh" / "config"
    target.parent.mkdir()
    target.write_text("Host *", encoding="utf-8")

    decision = validate_path_access(str(target), action="read", workspace_root=str(tmp_path))

    assert not decision.allowed
    assert decision.code == "PATH_SENSITIVE"


def test_path_safety_denies_private_key_extension(tmp_path: Path) -> None:
    target = tmp_path / "codesign.pfx"
    target.write_text("private", encoding="utf-8")

    decision = validate_path_access(str(target), action="read", workspace_root=str(tmp_path))

    assert not decision.allowed
    assert decision.code == "PATH_SENSITIVE"


def test_path_safety_denies_write_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"

    decision = validate_tool_paths(
        "write_file",
        {"path": str(outside)},
        workspace_root=str(tmp_path),
    )

    assert not decision.allowed
    assert decision.code == "PATH_OUTSIDE_WORKSPACE"


def test_path_safety_denies_artifact_output_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.pdf"

    decision = validate_tool_paths(
        "pdf_create",
        {"output_path": str(outside)},
        workspace_root=str(tmp_path),
    )

    assert not decision.allowed
    assert decision.code == "PATH_OUTSIDE_WORKSPACE"


def test_path_safety_denies_code_report_artifact_dir_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "report-artifacts"

    decision = validate_tool_paths(
        "office_report_from_code_run",
        {"output_path": "output/docx/report.docx", "artifacts_dir": str(outside)},
        workspace_root=str(tmp_path),
    )

    assert not decision.allowed
    assert decision.code == "PATH_OUTSIDE_WORKSPACE"


def test_path_safety_denies_symlink_write(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("real", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        return

    decision = validate_tool_paths("write_file", {"path": str(link)}, workspace_root=str(tmp_path))

    assert not decision.allowed
    assert decision.code == "PATH_SYMLINK_WRITE"


def test_path_safety_allows_normal_workspace_read(tmp_path: Path) -> None:
    target = tmp_path / "notes.md"
    target.write_text("hello", encoding="utf-8")

    decision = validate_tool_paths("read_file", {"path": "notes.md"}, workspace_root=str(tmp_path))

    assert decision.allowed


def test_legacy_path_security_allows_outside_read_with_boundary_override(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("hello from another root", encoding="utf-8")

    try:
        safe_path_for_read(str(outside), workspace_root=str(project))
    except PathSecurityError as exc:
        assert "拒绝访问工作区外" in str(exc)
    else:
        raise AssertionError("outside read should be blocked before the boundary override")

    with boundary_override(allow_paths_outside_workspace=True):
        assert safe_path_for_read(str(outside), workspace_root=str(project)) == outside.resolve()


def test_legacy_path_security_denies_symlink_write(tmp_path: Path) -> None:
    target = tmp_path / "target.txt"
    target.write_text("real", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        return

    try:
        safe_path_for_write(str(link), workspace_root=str(tmp_path))
    except PathSecurityError as exc:
        assert "拒绝写入符号链接" in str(exc)
    else:
        raise AssertionError("symlink writes must be blocked")


def test_read_file_extracts_cross_workspace_docx_text_with_boundary_override(tmp_path: Path) -> None:
    outside_docx = tmp_path / "ospf-report.docx"
    xml = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        "<w:body><w:p><w:r><w:t>OSPF 路由协议配置</w:t></w:r></w:p></w:body>"
        "</w:document>"
    )
    with zipfile.ZipFile(outside_docx, "w") as archive:
        archive.writestr("word/document.xml", xml)

    with boundary_override(allow_paths_outside_workspace=True):
        result = read_file(str(outside_docx))

    assert "OSPF 路由协议配置" in result
    assert "UnicodeDecodeError" not in result


def test_tool_registry_does_not_execute_denied_tool(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.chdir(tmp_path)
    executed = {"value": False}

    def write_file(path: str, content: str = "") -> str:
        executed["value"] = True
        return "wrote"

    registry = ToolRegistry()
    registry.register(
        ToolDefinition(
            name="write_file",
            description="write",
            parameters={"type": "object", "properties": {}},
            execute_fn=write_file,
        )
    )

    result = registry.execute("write_file", {"path": str(tmp_path.parent / "outside.txt"), "content": "x"})

    assert "Access denied" in result
    assert executed["value"] is False


def test_agent_tool_execution_uses_workspace_root_context(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)

    def write_file(path: str, content: str = "") -> str:
        target = safe_path_for_write(path)
        target.parent.mkdir(parents=True, exist_ok=True)
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

    target = workspace / "ok.txt"
    result = _execute_tool_with_hooks_sync(
        registry,
        ToolCall(id="call-root", name="write_file", arguments={"path": str(target), "content": "hello"}),
        workspace_root=str(workspace),
    )

    assert "ok.txt" in result
    assert target.read_text(encoding="utf-8") == "hello"

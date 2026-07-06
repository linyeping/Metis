from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from backend.runtime.tool_registry import ToolRegistry, register_builtin_tools
from backend.tools.artifacts.docx_tools import (
    docx_create,
    docx_edit,
    docx_inspect_layout,
    docx_render_pages,
    docx_to_pdf,
)
from backend.tools.artifacts.office_tools import (
    pptx_create,
    pptx_inspect,
    xlsx_create,
    xlsx_inspect,
)
from backend.tools.artifacts.pdf_tools import (
    pdf_create,
    pdf_extract_text,
    pdf_info,
    pdf_render_pages,
)
from backend.tools.artifacts.report_tools import office_report_from_code_run
from backend.tools.coding.foundation.core_mechanisms.execution_boundary_context import (
    workspace_root_override,
)


def _json(text: str) -> dict:
    payload = json.loads(text)
    assert isinstance(payload, dict)
    return payload


def test_pdf_tools_create_info_extract_and_report_renderer_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)
    monkeypatch.setenv("METIS_PYTHON", sys.executable)

    with workspace_root_override(str(workspace)):
        created = _json(
            pdf_create(
                "output/pdf/sample.pdf",
                title="Hello PDF",
                lines=["This PDF was created by Metis.", "Second line."],
            )
        )

        if not created.get("ok"):
            assert created.get("dependency") == "reportlab"
            pytest.skip("reportlab is not installed in this environment")

        target = workspace / "output" / "pdf" / "sample.pdf"
        assert target.is_file()

        info = _json(pdf_info("output/pdf/sample.pdf"))
        assert info["ok"] is True
        assert info["pages"] >= 1

        extracted = _json(pdf_extract_text("output/pdf/sample.pdf", max_pages=1))
        if extracted.get("ok"):
            joined = "\n".join(page.get("text", "") for page in extracted.get("pages", []))
            assert "Metis" in joined or "Hello PDF" in joined
        else:
            assert extracted.get("dependency") == "pdfplumber or pypdf"

        rendered = _json(pdf_render_pages("output/pdf/sample.pdf", output_dir="tmp/pdfs"))
        if rendered.get("ok"):
            assert rendered.get("images")
        else:
            assert rendered.get("dependency") == "poppler pdftoppm"


def test_docx_tools_create_edit_inspect_and_report_renderer_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)

    with workspace_root_override(str(workspace)):
        created = _json(
            docx_create(
                "output/docx/report.docx",
                title="Experiment Report",
                body="Status: draft",
                sections=[
                    {"heading": "Result", "text": "The initial result is stable."},
                    {"heading": "Checks", "bullets": ["Rendered when dependencies exist"]},
                ],
            )
        )

        if not created.get("ok"):
            assert created.get("dependency") == "python-docx"
            pytest.skip("python-docx is not installed in this environment")

        source = workspace / "output" / "docx" / "report.docx"
        assert source.is_file()

        edited = _json(
            docx_edit(
                "output/docx/report.docx",
                output_path="output/docx/report-edited.docx",
                find="draft",
                replace="complete",
                append_text="Final note.",
            )
        )
        assert edited["ok"] is True
        assert edited["replacements"] >= 1

        inspected = _json(docx_inspect_layout("output/docx/report-edited.docx"))
        assert inspected["ok"] is True
        assert inspected["paragraphs"] >= 3
        assert "Result" in inspected["headings"]

        converted = _json(docx_to_pdf("output/docx/report-edited.docx", output_dir="tmp/docx"))
        if converted.get("ok"):
            assert Path(converted["pdf_path"]).is_file()
        else:
            assert converted.get("dependency") == "LibreOffice soffice"

        rendered = _json(docx_render_pages("output/docx/report-edited.docx", output_dir="tmp/docx"))
        if rendered.get("ok"):
            assert rendered.get("images")
        else:
            assert rendered.get("dependency") in {"LibreOffice soffice", "poppler pdftoppm"}


def test_artifact_tools_are_registered_and_exposed_in_lean_profile() -> None:
    registry = ToolRegistry()
    register_builtin_tools(registry)

    names = set(registry.tool_names)
    expected = {
        "pdf_info",
        "pdf_extract_text",
        "pdf_render_pages",
        "pdf_screenshot_page",
        "pdf_merge_split",
        "pdf_create",
        "docx_create",
        "docx_edit",
        "docx_to_pdf",
        "docx_render_pages",
        "docx_inspect_layout",
        "xlsx_create",
        "xlsx_inspect",
        "pptx_create",
        "pptx_inspect",
        "office_report_from_code_run",
    }
    assert expected.issubset(names)

    lean_names = {
        (schema.get("function") or {}).get("name")
        for schema in registry.get_schemas_for_profile("lean", format="openai", include_desktop=False)
    }
    assert expected.issubset(lean_names)

    assert registry.get_tool_profile("pdf_info").toolset == "artifact"  # type: ignore[union-attr]
    pdf_create_profile = registry.get_tool_profile("pdf_create")
    assert pdf_create_profile is not None
    assert pdf_create_profile.toolset == "artifact"
    assert pdf_create_profile.destructive is True
    assert registry.get_tool_profile("xlsx_inspect").destructive is False  # type: ignore[union-attr]
    assert registry.get_tool_profile("pptx_create").destructive is True  # type: ignore[union-attr]


def test_xlsx_and_pptx_tools_create_and_inspect(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)

    with workspace_root_override(str(workspace)):
        xlsx = _json(
            xlsx_create(
                "output/office/results.xlsx",
                title="Results",
                rows=[["metric", "value"], ["period", 12.5]],
            )
        )
        if not xlsx.get("ok"):
            assert xlsx.get("dependency") == "openpyxl"
            pytest.skip("openpyxl is not installed in this environment")
        xlsx_info = _json(xlsx_inspect("output/office/results.xlsx"))
        assert xlsx_info["ok"] is True
        assert xlsx_info["sheets"][0]["max_row"] >= 2

        pptx = _json(
            pptx_create(
                "output/office/summary.pptx",
                title="Summary",
                slides=[{"title": "Result", "bullets": ["period=12.5", "stable"]}],
            )
        )
        if not pptx.get("ok"):
            assert pptx.get("dependency") == "python-pptx"
            pytest.skip("python-pptx is not installed in this environment")
        pptx_info = _json(pptx_inspect("output/office/summary.pptx"))
        assert pptx_info["ok"] is True
        assert pptx_info["slides"] >= 1


def test_office_report_from_code_run_creates_docx_and_activity_payload(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    backend_cwd = tmp_path / "backend"
    workspace.mkdir()
    backend_cwd.mkdir()
    monkeypatch.chdir(backend_cwd)

    code = """
import os
from pathlib import Path
out = Path(os.environ["METIS_REPORT_ARTIFACTS_DIR"])
out.mkdir(parents=True, exist_ok=True)
(out / "results.txt").write_text("period=12.5\\n", encoding="utf-8")
print("period=12.5")
""".strip()

    with workspace_root_override(str(workspace)):
        result = _json(
            office_report_from_code_run(
                output_path="output/docx/lab-report.docx",
                title="Lab Report",
                assignment="Run code and write the result.",
                code=code,
                artifacts_dir="output/report_artifacts/lab",
                conclusion="The generated result is stable.",
            )
        )

    if not result.get("ok") and "python-docx" in str(result.get("report_error", "")):
        pytest.skip("python-docx is not installed in this environment")

    report = workspace / "output" / "docx" / "lab-report.docx"
    assert result["ok"] is True
    assert result["status"] == "done"
    assert report.is_file()
    assert result["returncode"] == 0
    assert result["command"].startswith(sys.executable)
    assert "period=12.5" in result["stdout"]
    assert result["artifact_activity"]["kind"] == "code_to_report"
    assert any(item["event"] == "run_code" and item["ok"] for item in result["activity"])
    assert any(str(item["path"]).endswith("results.txt") for item in result["artifacts"])

from __future__ import annotations

import io
import zipfile

import pytest

from backend.web.file_extractors import UnsupportedFileType, extract_uploaded_file, truncate_text


def test_extract_docx_reads_chinese_paragraphs_and_tables() -> None:
    docx = pytest.importorskip("docx")

    document = docx.Document()
    document.add_paragraph("实验报告：离散时间信号和系统")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "项目"
    table.cell(0, 1).text = "结果"
    table.cell(1, 0).text = "周期"
    table.cell(1, 1).text = "12.5"
    buffer = io.BytesIO()
    document.save(buffer)

    parsed = extract_uploaded_file("实验报告1.docx", buffer.getvalue())

    assert parsed.extension == ".docx"
    assert "离散时间信号" in parsed.text
    assert "周期" in parsed.text
    assert "12.5" in parsed.text


def test_extract_xlsx_reads_sheet_rows() -> None:
    openpyxl = pytest.importorskip("openpyxl")

    workbook = openpyxl.Workbook()
    worksheet = workbook.active
    worksheet.title = "Results"
    worksheet.append(["name", "value"])
    worksheet.append(["period", 12.5])
    buffer = io.BytesIO()
    workbook.save(buffer)

    parsed = extract_uploaded_file("results.xlsx", buffer.getvalue())

    assert parsed.extension == ".xlsx"
    assert "--- Sheet: Results ---" in parsed.text
    assert "period\t12.5" in parsed.text


def test_extract_pptx_reads_slide_text_with_ooxml_fallback() -> None:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<p:sld xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main"
       xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">
  <p:cSld><p:spTree><p:sp><p:txBody><a:p><a:r><a:t>Metis slide title</a:t></a:r></a:p></p:txBody></p:sp></p:spTree></p:cSld>
</p:sld>
"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("ppt/slides/slide1.xml", xml)

    parsed = extract_uploaded_file("deck.pptx", buffer.getvalue())

    assert parsed.extension == ".pptx"
    assert "Slide 1" in parsed.text
    assert "Metis slide title" in parsed.text


def test_extract_html_returns_visible_text() -> None:
    parsed = extract_uploaded_file(
        "page.html",
        b"<html><head><style>.x{}</style></head><body><h1>Hello</h1><script>bad()</script><p>Visible text</p></body></html>",
    )

    assert "Hello" in parsed.text
    assert "Visible text" in parsed.text
    assert "bad()" not in parsed.text


def test_legacy_doc_reports_converter_requirement_when_unavailable() -> None:
    try:
        parsed = extract_uploaded_file("legacy.doc", b"not a real legacy office file")
    except UnsupportedFileType as exc:
        assert "LibreOffice" in str(exc)
    except ValueError as exc:
        assert "converter" in str(exc).lower() or "libreoffice" in str(exc).lower()
    else:
        assert parsed.extension == ".doc"


def test_truncate_text_reports_original_count() -> None:
    text, original_count, truncated = truncate_text("abcdef", max_chars=3)

    assert truncated is True
    assert original_count == 6
    assert text.startswith("abc")
    assert "6 total characters" in text

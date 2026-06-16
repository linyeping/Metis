---
name: documents
builtin: true
description: "Create, edit, inspect, render, and verify DOCX/Word-style document artifacts with background tools."
when_to_use: "Use for Word/DOCX reports, homework documents, code-to-report workflows, document editing, DOCX to PDF conversion, and layout-sensitive document QA."
allowed-tools:
  - office_report_from_code_run
  - docx_create
  - docx_edit
  - docx_to_pdf
  - docx_render_pages
  - docx_inspect_layout
  - pdf_info
  - pdf_render_pages
  - read_file
  - write_file
  - append_to_file
  - execute_bash_command
  - run_tests
disallowed-tools:
  - desktop_action
  - desktop_win2_action
  - desktop_win2_task
  - desktop_vision_task
---

# Documents Skill

## Core Rule

Prefer background file, code, chart, DOCX, and PDF tools for document deliverables. Computer Use is a fallback only when the user explicitly needs a visible app operated, such as clicking WPS, submitting a form, or validating a real desktop state.

For tasks like "write code, run it, generate charts, and complete the report", do the work in the background:

1. Read or parse the assignment.
2. Prefer `office_report_from_code_run` when the task is code + execution + DOCX report.
3. Generate code or analysis scripts when needed.
4. Run the code and save outputs such as charts or tables.
5. Create or edit a DOCX artifact.
6. Render or inspect the DOCX.
7. Return the final artifact path and a concise result summary.

## DOCX Workflow

1. Use `docx_create` for new DOCX files.
2. Use `docx_edit` for conservative edits to existing documents.
3. Use `docx_inspect_layout` for structural checks: paragraphs, headings, tables, images.
4. Use `docx_render_pages` for visual QA when layout matters.
5. Use `docx_to_pdf` when a PDF deliverable is needed or when rendering requires an intermediate PDF.
6. Use `office_report_from_code_run` for code-to-report tasks that should run code, capture output, collect artifacts, and write a DOCX report in one background workflow. Generated code can write charts/results to `METIS_REPORT_ARTIFACTS_DIR`.

## Dependency Behavior

The DOCX tools report missing dependencies honestly:

- `python-docx` is required for create/edit/inspect.
- LibreOffice or `soffice` is required for DOCX to PDF conversion.
- Poppler `pdftoppm` is required for PNG page rendering after conversion.

If rendering is unavailable because LibreOffice or Poppler is missing, say that visual QA could not be completed. Do not claim the document passed a render gate.

## Quality Gate

For final DOCX or Word-like reports, prefer a render-and-check loop. Verify that headings, body text, figures, tables, page breaks, and captions are legible and not clipped or overlapping. For simple text-only drafts where rendering is unavailable, at least run `docx_inspect_layout` and explain the limitation.

---
name: pdf
builtin: true
description: "Read, create, render, merge, split, and verify PDF files with a render-first quality workflow."
when_to_use: "Use for PDF reading, PDF text extraction, PDF creation, page rendering, screenshot evidence, merge/split operations, and layout-sensitive PDF review."
allowed-tools:
  - pdf_info
  - pdf_extract_text
  - pdf_render_pages
  - pdf_screenshot_page
  - pdf_merge_split
  - pdf_create
  - read_file
  - write_file
  - append_to_file
  - execute_bash_command
disallowed-tools:
  - desktop_action
  - desktop_win2_action
  - desktop_win2_task
  - desktop_vision_task
---

# PDF Skill

## Core Rule

Prefer background artifact tools for PDF work. Do not use Computer Use to open a PDF viewer unless the user explicitly asks to operate a visible desktop app or a UI-only button.

## Workflow

1. Inspect the file with `pdf_info` before doing deeper work.
2. Use `pdf_extract_text` for quick content understanding.
3. Use `pdf_render_pages` or `pdf_screenshot_page` whenever layout, tables, charts, glyphs, spacing, or page breaks matter.
4. For creation, use `pdf_create` for simple text PDFs. For complex reports, generate DOCX or HTML first, render to PDF, then verify rendered pages.
5. For merge/split tasks, use `pdf_merge_split` and then run `pdf_info` on the output.

## Dependency Behavior

The PDF tools report missing dependencies honestly:

- `reportlab` is required for `pdf_create`.
- `pypdf` is required for PDF info fallback and merge/split.
- `pdfplumber` improves text extraction, with `pypdf` fallback.
- Poppler `pdftoppm` is required for page rendering.

If a dependency is missing, state the missing dependency and the install hint from the tool result. Do not pretend visual QA passed when rendering was unavailable.

## Quality Gate

Before calling a PDF final, verify the latest rendered PNGs or screenshots when visual fidelity matters. Check for clipped text, overlapping elements, broken tables, missing glyphs, unreadable charts, and incorrect page order.

---
name: workspace-documents
description: Create, edit, render, and verify document artifacts in the configured workspace.
interface:
  display_name: "Workspace Documents"
  short_description: "Document creation and verification"
  icon_small: "../../assets/icon.svg"
  icon_large: "../../assets/logo.svg"
  brand_color: "#10B981"
allowed-tools: [office_report_from_code_run, docx_create, docx_render_pages, pdf_create, pdf_render_pages, read_file]
---

# Workspace Documents

Use this skill for document work that should remain inside the configured workspace.

1. Inspect the source material before writing.
2. Create the document with the appropriate document tool.
3. Render the result and visually verify layout, clipping, and page breaks.
4. Iterate until the rendered artifact is correct.
5. Return the final artifact path and a concise verification summary.

The Filesystem MCP component is installed separately and remains disabled until the user configures an allowed root and explicitly enables the Plugin.

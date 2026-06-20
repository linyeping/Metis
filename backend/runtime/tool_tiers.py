"""Tool tiers for model capability adaptation.

Two axes shrink the tool surface a model sees:

1. INTERNAL_TOOLS — maintainer-only tools that build/verify the sandbox runtime
   pack (metis_rootfs_* / metis_vm_* / metis_wsl_* / metis_runtime_bundle_*) plus
   the low-level runtime primitives that the high-level `metis_runtime_job`
   already composes. End-user agents never need these; exposing them just
   confuses weaker models into long, wrong tool chains. They stay registered
   and executable — only hidden from the model — and reappear when
   METIS_EXPOSE_INTERNAL_TOOLS is set (so we can drive them while building Metis).

2. Tiers — weaker models (e.g. DeepSeek = tier 2) get a smaller curated set.
"""
from __future__ import annotations

import os
from typing import Optional, Set


# Maintainer-only sandbox build/verify tools + redundant low-level runtime
# primitives. Hidden from end-user agents on every tier unless explicitly
# exposed. The user-facing sandbox surface is just: metis_runtime_job,
# metis_runtime_job_status, metis_runtime_status, metis_sandbox_status.
INTERNAL_TOOLS: frozenset[str] = frozenset(
    {
        # rootfs build/verify
        "metis_rootfs_asset_status",
        "metis_rootfs_source_status",
        "metis_rootfs_asset_download",
        "metis_rootfs_builder_status",
        "metis_rootfs_build",
        "metis_rootfs_image_builder_status",
        "metis_rootfs_image_build",
        "metis_rootfs_asset_register",
        # runtime pack packaging
        "metis_runtime_bundle_prepare",
        "metis_runtime_bundle_package",
        "metis_runtime_bundle_package_v2",
        # VM direct/hcs/guest/boot scaffolding + verification
        "metis_vm_direct_assets_prepare",
        "metis_vm_direct_runner_prepare",
        "metis_vm_direct_runner_smoke",
        "metis_vm_hcs_starter_prepare",
        "metis_vm_hcs_starter_start",
        "metis_vm_guest_handshake_prepare",
        "metis_vm_guest_handshake_verify",
        "metis_vm_rootfs_boot_verifier_prepare",
        "metis_vm_rootfs_boot_verify",
        "metis_vm_bundle_status",
        "metis_vm_pack_scaffold",
        "metis_vm_pack_adopt_reference",
        # WSL runtime management
        "metis_wsl_runtime_status",
        "metis_wsl_runtime_import",
        # low-level runtime primitives — composed by metis_runtime_job
        "metis_runtime_create",
        "metis_runtime_run",
        "metis_runtime_collect_artifacts",
        "metis_runtime_export_patch",
        "metis_runtime_export_diagnostics",
    }
)


def expose_internal_tools() -> bool:
    """True when maintainer-only tools should be visible to the model."""
    return os.environ.get("METIS_EXPOSE_INTERNAL_TOOLS", "").strip().lower() in {"1", "true", "yes", "on"}


# User-facing sandbox tools that survive the tier cut (composite + status only).
_SANDBOX_USER_TOOLS = {
    "metis_sandbox_status",
    "metis_runtime_status",
    "metis_runtime_job",
    "metis_runtime_job_status",
}


TIER_3_TOOLS: Set[str] = {
    "read_file",
    "write_file",
    "robust_replace_in_file",
    "grep_search",
    "glob_search",
    "list_directory",
    "execute_bash_command",
    "ask_question",
    "todo_write",
    "load_skill",
    "switch_mode",
    "delete_file",
    "append_to_file",
    "read_lints",
    "verify_compilation",
    "task_dispatch",
    "preview_browser_status",
    "preview_browser_observe",
    "preview_browser_screenshot",
    "preview_browser_verify",
    "desktop_win2_status",
    "desktop_win2_observe",
    "desktop_win2_verify",
    "pdf_info",
    "pdf_extract_text",
    "pdf_render_pages",
    "pdf_screenshot_page",
    "pdf_merge_split",
    "pdf_create",
    "office_report_from_code_run",
    "docx_create",
    "docx_edit",
    "docx_to_pdf",
    "docx_render_pages",
    "docx_inspect_layout",
    *_SANDBOX_USER_TOOLS,
}

TIER_2_TOOLS: Set[str] = TIER_3_TOOLS | {
    "semantic_search",
    "generate_repo_map",
    "rename_symbol",
    "extract_method",
    "edit_code_ast",
    "rename_file_update_refs",
    "read_multiple_files",
    "check_git_status",
    "git_commit_pr",
    "git_workflow",
    "web_search",
    "web_fetch",
    "browse_web",
    "browse_and_extract",
    "preview_browser_navigate",
    "preview_browser_action",
    "desktop_win2_action",
    "desktop_win2_task",
    "desktop_vision_task",
    "auto_install_package",
    "check_dev_environment",
    "install_dev_runtime",
    "setup_workspace",
    "manage_long_running",
    "analyze_complexity",
    "edit_notebook",
    "load_workflow_guidelines",
}


def tools_for_tier(tier: int) -> Optional[Set[str]]:
    """Return allowed tool names for a tier, or None for all tools."""
    if tier <= 1:
        return None
    if tier == 2:
        return set(TIER_2_TOOLS)
    return set(TIER_3_TOOLS)

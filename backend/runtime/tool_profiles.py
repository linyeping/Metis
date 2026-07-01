from __future__ import annotations

from typing import Optional


LEAN_PROFILE = frozenset(
    {
        "read_file",
        "list_directory",
        "grep_search",
        "glob_search",
        "write_file",
        "robust_replace_in_file",
        "execute_bash_command",
        "run_tests",
        "generate_repo_map",
        "check_git_status",
        "git_diff",
        "todo_write",
        "load_skill",
        "delegate_explore",
        "delegate_shell",
        "web_search",
        "web_research",
        "fetch_content",
        "web_fetch",
        "browse_web",
        "browse_and_extract",
        "preview_browser_status",
        "preview_browser_navigate",
        "preview_browser_observe",
        "preview_browser_action",
        "preview_browser_screenshot",
        "preview_browser_verify",
        "desktop_screenshot",
        "desktop_action",
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
        "metis_rootfs_asset_status",
        "metis_rootfs_asset_register",
        "metis_rootfs_source_status",
        "metis_rootfs_asset_download",
        "metis_rootfs_builder_status",
        "metis_rootfs_build",
        "metis_rootfs_image_builder_status",
        "metis_rootfs_image_build",
        "metis_runtime_bundle_package",
        "metis_runtime_bundle_package_v2",
        "metis_runtime_bundle_prepare",
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
        "metis_vm_pack_adopt_reference",
        "metis_wsl_runtime_status",
        "metis_vm_pack_scaffold",
        "metis_wsl_runtime_import",
        "metis_sandbox_status",
        "metis_runtime_create",
        "metis_runtime_run",
        "metis_runtime_collect_artifacts",
        "metis_runtime_export_patch",
        "metis_runtime_export_diagnostics",
        "metis_runtime_job",
        "metis_runtime_job_status",
        "metis_runtime_status",
    }
)

PROFILE_NAMES = {"lean", "full"}


def normalize_tool_profile(profile: str) -> str:
    value = str(profile or "").strip().lower()
    if value in PROFILE_NAMES:
        return value
    return "lean"


def tool_names_for_profile(profile: str, *, include_desktop: bool = True) -> Optional[frozenset[str]]:
    normalized = normalize_tool_profile(profile)
    if normalized == "full":
        return None
    names = set(LEAN_PROFILE)
    if not include_desktop:
        names.difference_update({"desktop_screenshot", "desktop_action"})
    return frozenset(names)

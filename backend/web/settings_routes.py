"""Settings, providers, and first-run Blueprint."""
from __future__ import annotations

import os
import webbrowser
from typing import Any
from urllib.parse import urlparse

from flask import Blueprint, abort, jsonify, request, send_file

from backend.runtime.error_catalog import classify_llm_error
from backend.web.desktop_window import handle_window_action
from backend.web.helpers import error_response_payload, request_client_is_loopback
from backend.web.llm_state import (
    first_run_status_payload,
    get_provider_models,
    get_provider_status,
    get_provider_usage,
    get_runtime_settings,
    save_first_run_config,
    update_runtime_settings,
    verify_provider_settings,
)

settings_bp = Blueprint("settings", __name__)

_BACKEND_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ASSETS_DIR = os.path.join(_BACKEND_ROOT, "assets")
_URL_TRAILING_MARKERS = ("，", "。", "！", "？", "；", "：", "、", "）", "】", "》", "」", "』")


# --- Settings routes -------------------------------------------------------

@settings_bp.route("/settings", methods=["GET"])
def get_settings() -> Any:
    return jsonify(get_runtime_settings())


@settings_bp.route("/settings/document-converters", methods=["GET"])
def document_converters_status() -> Any:
    from backend.runtime.document_converters import document_converter_status

    return jsonify(document_converter_status().to_dict())


@settings_bp.route("/settings/runtime-manager", methods=["GET"])
def runtime_manager_status_route() -> Any:
    from backend.runtime.runtime_manager import runtime_manager_status

    return jsonify(runtime_manager_status())


@settings_bp.route("/settings/runtime-manager/import-plan", methods=["POST"])
def runtime_manager_import_plan_route() -> Any:
    from backend.runtime.runtime_manager import runtime_manager_import_plan

    return jsonify(runtime_manager_import_plan())


@settings_bp.route("/settings/runtime-manager/import", methods=["POST"])
def runtime_manager_import_route() -> Any:
    from backend.runtime.runtime_manager import runtime_manager_import

    return jsonify(runtime_manager_import())


@settings_bp.route("/settings/runtime-manager/build-plan", methods=["POST"])
def runtime_manager_build_plan_route() -> Any:
    from backend.runtime.runtime_manager import runtime_manager_build_plan

    data = request.get_json(silent=True) or {}
    return jsonify(runtime_manager_build_plan(profile=str(data.get("profile") or "standard")))


@settings_bp.route("/settings/runtime-manager/prepare-bundle", methods=["POST"])
def runtime_manager_prepare_bundle_route() -> Any:
    from backend.runtime.runtime_manager import runtime_manager_prepare_bundle

    data = request.get_json(silent=True) or {}
    return jsonify(
        runtime_manager_prepare_bundle(
            version=str(data.get("version") or ""),
            channel=str(data.get("channel") or "local"),
        )
    )


@settings_bp.route("/settings/runtime-manager/package-bundle", methods=["POST"])
def runtime_manager_package_bundle_route() -> Any:
    from backend.runtime.runtime_manager import runtime_manager_package_bundle

    data = request.get_json(silent=True) or {}
    return jsonify(
        runtime_manager_package_bundle(
            version=str(data.get("version") or ""),
            channel=str(data.get("channel") or "local"),
        )
    )


@settings_bp.route("/settings/runtime-manager/package-vm-bundle", methods=["POST"])
def runtime_manager_package_vm_bundle_route() -> Any:
    from backend.runtime.runtime_manager import runtime_manager_package_vm_bundle

    data = request.get_json(silent=True) or {}
    return jsonify(
        runtime_manager_package_vm_bundle(
            version=str(data.get("version") or ""),
            channel=str(data.get("channel") or "direct"),
        )
    )


@settings_bp.route("/settings/runtime-manager/build-vm-assets", methods=["POST"])
def runtime_manager_build_vm_assets_route() -> Any:
    from backend.runtime.runtime_manager import runtime_manager_build_vm_assets

    data = request.get_json(silent=True) or {}
    return jsonify(
        runtime_manager_build_vm_assets(
            version=str(data.get("version") or ""),
            channel=str(data.get("channel") or "direct"),
            profile=str(data.get("profile") or "standard"),
            allow_network=bool(data.get("allow_network") or data.get("allowNetwork")),
            dry_run=bool(data.get("dry_run", data.get("dryRun", True))),
            force=bool(data.get("force")),
            package_bundle=bool(data.get("package_bundle") or data.get("packageBundle")),
            rootfs_vhdx_path=str(data.get("rootfs_vhdx_path") or data.get("rootfsVhdxPath") or ""),
            kernel_path=str(data.get("kernel_path") or data.get("kernelPath") or ""),
            initrd_path=str(data.get("initrd_path") or data.get("initrdPath") or ""),
            metis_bin_path=str(data.get("metis_bin_path") or data.get("metisBinPath") or ""),
        )
    )


@settings_bp.route("/settings/runtime-manager/validate-release", methods=["POST"])
def runtime_manager_validate_release_route() -> Any:
    from backend.runtime.runtime_manager import runtime_manager_validate_release_source

    data = request.get_json(silent=True) or {}
    return jsonify(runtime_manager_validate_release_source(url=str(data.get("url") or "")))


@settings_bp.route("/settings/runtime-manager/startup-test", methods=["POST"])
def runtime_manager_startup_test_route() -> Any:
    from backend.runtime.runtime_manager import runtime_manager_startup_test

    return jsonify(runtime_manager_startup_test())


@settings_bp.route("/settings/runtime-manager/repair", methods=["POST"])
def runtime_manager_repair_route() -> Any:
    from backend.runtime.runtime_manager import runtime_manager_repair

    data = request.get_json(silent=True) or {}
    return jsonify(
        runtime_manager_repair(
            source=str(data.get("source") or "auto"),
            allow_download=bool(data.get("allow_download") or data.get("allowDownload")),
            force=bool(data.get("force")),
        )
    )


@settings_bp.route("/settings/runtime-manager/ensure", methods=["POST"])
def runtime_manager_ensure_route() -> Any:
    from backend.runtime.runtime_manager import runtime_manager_ensure

    return jsonify(runtime_manager_ensure())


@settings_bp.route("/settings/runtime-manager/smoke", methods=["POST"])
def runtime_manager_smoke_route() -> Any:
    from backend.runtime.runtime_manager import runtime_manager_smoke

    return jsonify(runtime_manager_smoke())


@settings_bp.route("/settings/runtime-manager/diagnostics", methods=["POST"])
def runtime_manager_diagnostics_route() -> Any:
    from backend.runtime.runtime_manager import runtime_manager_export_diagnostics

    data = request.get_json(silent=True) or {}
    return jsonify(runtime_manager_export_diagnostics(session_id=str(data.get("session_id") or "")))


@settings_bp.route("/settings", methods=["POST"])
def update_settings() -> Any:
    data = request.get_json(silent=True) or {}
    updated = update_runtime_settings(data)
    return jsonify({"updated": updated})


# --- Provider routes -------------------------------------------------------

@settings_bp.route("/providers", methods=["GET"])
def providers_status() -> Any:
    return jsonify(get_provider_status())


@settings_bp.route("/providers/verify", methods=["POST"])
def providers_verify() -> Any:
    data = request.get_json(silent=True) or {}
    return jsonify(verify_provider_settings(data))


@settings_bp.route("/providers/models", methods=["POST"])
def providers_models() -> Any:
    data = request.get_json(silent=True) or {}
    return jsonify(get_provider_models(data))


@settings_bp.route("/providers/usage", methods=["POST"])
def providers_usage() -> Any:
    data = request.get_json(silent=True) or {}
    return jsonify(get_provider_usage(data))


# --- FABLEADV-15: config-driven provider registry --------------------------

@settings_bp.route("/providers/registry", methods=["GET"])
def providers_registry() -> Any:
    from backend.bridges.provider_registry import is_builtin_provider_id, list_provider_profiles, provider_profile_payload

    profiles = []
    for profile in list_provider_profiles():
        payload = provider_profile_payload(profile)
        payload["source"] = getattr(profile, "source", "builtin")
        payload["api_key_env"] = getattr(profile, "api_key_env", "")
        payload["deletable"] = not is_builtin_provider_id(str(profile.provider_id))
        profiles.append(payload)
    return jsonify({"providers": profiles})


@settings_bp.route("/providers/registry", methods=["POST"])
def providers_registry_upsert() -> Any:
    from backend.bridges.provider_registry import reload_provider_registry
    from backend.bridges.provider_user_config import save_user_provider

    data = request.get_json(silent=True) or {}
    try:
        profile = save_user_provider(data)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    count = reload_provider_registry()
    return jsonify({"ok": True, "provider_id": str(profile.provider_id), "count": count})


@settings_bp.route("/providers/registry/<provider_id>", methods=["DELETE"])
def providers_registry_delete(provider_id: str) -> Any:
    from backend.bridges.provider_registry import is_builtin_provider_id, reload_provider_registry
    from backend.bridges.provider_user_config import delete_user_provider

    if is_builtin_provider_id(provider_id):
        removed = delete_user_provider(provider_id)
        count = reload_provider_registry()
        message = "已恢复内置供应商默认配置" if removed else "内置供应商不可删除"
        return jsonify({"ok": removed, "error": "" if removed else message, "message": message, "count": count})
    removed = delete_user_provider(provider_id)
    count = reload_provider_registry()
    return jsonify({"ok": removed, "count": count})


@settings_bp.route("/providers/registry/<provider_id>/probe", methods=["POST"])
def providers_registry_probe(provider_id: str) -> Any:
    from backend.bridges.model_capability import detect_from_model_name
    from backend.bridges.provider_registry import get_provider_profile, reload_provider_registry
    from backend.bridges.provider_user_config import save_user_provider
    from backend.runtime.provider_conformance import run_provider_conformance_probe

    data = request.get_json(silent=True) or {}
    try:
        profile = get_provider_profile(provider_id)
    except Exception:
        return jsonify({"ok": False, "error": "供应商不存在，请先保存后再探测。"}), 404

    base_url = str(data.get("base_url") or profile.base_url or "").strip().rstrip("/")
    model = str(data.get("model") or profile.default_model or (profile.fallback_models[0] if profile.fallback_models else "")).strip()
    api_key = str(data.get("api_key") or "").strip()
    if not api_key and profile.api_key_env:
        api_key = os.environ.get(profile.api_key_env, "").strip()

    models_result = get_provider_models(
        {
            "provider_id": str(profile.provider_id),
            "base_url": base_url,
            "model": model,
            "api_key": api_key,
        }
    )
    models = [
        str(item.get("id") or "").strip()
        for item in models_result.get("models", [])
        if isinstance(item, dict) and str(item.get("id") or "").strip()
    ]
    if models and (not model or model not in models):
        model = models[0]

    conformance = None
    if profile.openai_compatible and api_key and base_url and model:
        conformance = run_provider_conformance_probe(
            provider_id=str(profile.provider_id),
            base_url=base_url,
            api_key=api_key,
            model=model,
        )

    capability_model = model or (models[0] if models else "")
    vision_protocol = detect_from_model_name(capability_model).vision_protocol if capability_model else "legacy"
    supports_vision = bool(profile.supports_vision or (vision_protocol != "none"))
    parallel_tool_calls = bool(
        conformance.get("parallel_tool_calls")
        if isinstance(conformance, dict) and isinstance(conformance.get("parallel_tool_calls"), bool)
        else profile.parallel_tool_calls
    )
    requires_reasoning_passback = bool(
        conformance.get("requires_reasoning_passback")
        if isinstance(conformance, dict) and isinstance(conformance.get("requires_reasoning_passback"), bool)
        else profile.requires_reasoning_passback
    )

    try:
        saved = save_user_provider(
            {
                "id": str(profile.provider_id),
                "display_name": profile.display_name,
                "backend_type": profile.backend_type,
                "base_url": base_url,
                "api_key_env": profile.api_key_env,
                "default_model": model,
                "models": models or list(profile.fallback_models),
                "supports_stream": profile.supports_stream,
                "supports_tools": profile.supports_tools,
                "supports_vision": supports_vision,
                "parallel_tool_calls": parallel_tool_calls,
                "requires_reasoning_passback": requires_reasoning_passback,
                "openai_compatible": profile.openai_compatible,
                "context_windows": dict(profile.model_context_windows),
            }
        )
        count = reload_provider_registry()
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc), "models": models, "models_result": models_result, "conformance": conformance}), 400

    return jsonify(
        {
            "ok": True,
            "provider_id": str(saved.provider_id),
            "count": count,
            "models": models,
            "models_result": models_result,
            "conformance": conformance,
            "supports_vision": supports_vision,
            "parallel_tool_calls": parallel_tool_calls,
            "requires_reasoning_passback": requires_reasoning_passback,
            "vision_detection": vision_protocol,
        }
    )


# --- First-run routes ------------------------------------------------------

@settings_bp.route("/first-run", methods=["GET"])
def first_run_status() -> Any:
    return jsonify(first_run_status_payload())


@settings_bp.route("/first-run/complete", methods=["POST"])
def first_run_complete() -> Any:
    data = request.get_json(silent=True) or {}
    try:
        saved = save_first_run_config(data)
    except ValueError as exc:
        info = classify_llm_error(message=str(exc), recoverable=False)
        return jsonify({"ok": False, **error_response_payload(info)}), 400
    return jsonify({"ok": True, "config_path": saved["config_path"]})


@settings_bp.route("/first-run/verify", methods=["POST"])
def first_run_verify() -> Any:
    data = request.get_json(silent=True) or {}
    return jsonify(verify_provider_settings(data))


# --- Window control --------------------------------------------------------

@settings_bp.route("/window/<action>", methods=["POST"])
def desktop_window_action(action: str) -> Any:
    if not request_client_is_loopback():
        return jsonify({"error": "forbidden"}), 403
    result = handle_window_action(action)
    if not result.get("ok"):
        return jsonify({"error": result.get("error")}), int(result.get("status") or 500)
    return jsonify(result)


# --- External URL -----------------------------------------------------------

@settings_bp.route("/open-url", methods=["POST"])
def open_url() -> Any:
    if not request_client_is_loopback():
        return jsonify({"error": "forbidden"}), 403
    data = request.get_json(silent=True) or {}
    url = _clean_external_url(str(data.get("url") or ""))
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return jsonify({"error": "invalid url"}), 400
    webbrowser.open(url, new=2)
    return jsonify({"ok": True, "url": url})


def _clean_external_url(url: str) -> str:
    value = str(url or "").strip()
    for marker in _URL_TRAILING_MARKERS:
        if marker in value:
            value = value.split(marker, 1)[0]
    return value.strip()


# --- Static assets ----------------------------------------------------------

@settings_bp.route("/assets/<path:filename>", methods=["GET"])
def serve_asset(filename: str) -> Any:
    filepath = os.path.abspath(os.path.join(_ASSETS_DIR, filename))
    assets_root = os.path.abspath(_ASSETS_DIR)
    if not filepath.startswith(assets_root + os.sep) and filepath != assets_root:
        abort(404)
    if not os.path.isfile(filepath):
        abort(404)
    return send_file(filepath)

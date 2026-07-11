from __future__ import annotations

from typing import Any, Dict

from flask import Blueprint, jsonify, request

from backend.runtime.marketplace import (
    MarketplaceError,
    configure_item,
    get_item,
    install_item,
    install_source,
    list_catalog,
    load_manifest,
    search_mcp_registry,
    set_item_enabled,
    uninstall_item,
)
from backend.runtime.marketplace_sources import (
    MarketplaceSourceError,
    add_source,
    delete_source,
    list_sources,
    refresh_source,
)


marketplace_bp = Blueprint("marketplace", __name__, url_prefix="/marketplace")


def _body() -> Dict[str, Any]:
    value = request.get_json(silent=True) or {}
    return value if isinstance(value, dict) else {}


@marketplace_bp.errorhandler(MarketplaceError)
@marketplace_bp.errorhandler(MarketplaceSourceError)
def _marketplace_error(exc: MarketplaceError) -> Any:
    return jsonify({"ok": False, "error": str(exc)}), 400


@marketplace_bp.route("/catalog", methods=["GET"])
def catalog() -> Any:
    return jsonify(
        list_catalog(
            query=str(request.args.get("q") or ""),
            kind=str(request.args.get("kind") or ""),
            source=str(request.args.get("source") or ""),
        )
    )


@marketplace_bp.route("/manifest", methods=["GET"])
def manifest() -> Any:
    return jsonify(load_manifest())


@marketplace_bp.route("/items/<path:item_id>", methods=["GET"])
def item_detail(item_id: str) -> Any:
    return jsonify({"ok": True, "item": get_item(item_id)})


@marketplace_bp.route("/items/<path:item_id>/install", methods=["POST"])
def item_install(item_id: str) -> Any:
    data = _body()
    return jsonify({"ok": True, "item": install_item(item_id, config=data.get("config") if isinstance(data.get("config"), dict) else {})})


@marketplace_bp.route("/items/<path:item_id>/configure", methods=["POST"])
def item_configure(item_id: str) -> Any:
    data = _body()
    values = data.get("values") if isinstance(data.get("values"), dict) else {}
    secret_configured = data.get("secretConfigured") if isinstance(data.get("secretConfigured"), list) else []
    return jsonify({"ok": True, "item": configure_item(item_id, values, secret_configured)})


@marketplace_bp.route("/items/<path:item_id>/enable", methods=["POST"])
def item_enable(item_id: str) -> Any:
    return jsonify({"ok": True, "item": set_item_enabled(item_id, True)})


@marketplace_bp.route("/items/<path:item_id>/disable", methods=["POST"])
def item_disable(item_id: str) -> Any:
    return jsonify({"ok": True, "item": set_item_enabled(item_id, False)})


@marketplace_bp.route("/items/<path:item_id>", methods=["DELETE"])
def item_uninstall(item_id: str) -> Any:
    return jsonify({"ok": True, "item": uninstall_item(item_id, force=True)})


@marketplace_bp.route("/mcp-registry", methods=["GET"])
def mcp_registry() -> Any:
    return jsonify(
        search_mcp_registry(
            search=str(request.args.get("search") or ""),
            cursor=str(request.args.get("cursor") or ""),
            limit=int(request.args.get("limit") or 24),
        )
    )


@marketplace_bp.route("/install-source", methods=["POST"])
def source_install() -> Any:
    data = _body()
    return jsonify({"ok": True, "item": install_source(str(data.get("source") or ""), str(data.get("name") or ""))})


@marketplace_bp.route("/sources", methods=["GET"])
def sources() -> Any:
    return jsonify(list_sources())


@marketplace_bp.route("/sources", methods=["POST"])
def source_add() -> Any:
    data = _body()
    source = add_source(str(data.get("name") or ""), str(data.get("url") or ""), str(data.get("adapter") or ""))
    return jsonify({"ok": True, "source": source})


@marketplace_bp.route("/sources/<source_id>", methods=["DELETE"])
def source_delete(source_id: str) -> Any:
    delete_source(source_id)
    return jsonify({"ok": True})


@marketplace_bp.route("/sources/<source_id>/refresh", methods=["POST"])
def source_refresh(source_id: str) -> Any:
    return jsonify({"ok": True, "source": refresh_source(source_id)})


__all__ = ["marketplace_bp"]

from __future__ import annotations

import logging
import traceback
from typing import Any

from flask import jsonify
from werkzeug.exceptions import BadRequest, HTTPException, NotFound

logger = logging.getLogger(__name__)


def register_error_handlers(app: Any) -> None:
    @app.errorhandler(BadRequest)
    def bad_request(error: BadRequest) -> Any:
        return jsonify({
            "error": "bad_request",
            "message": error.description or "请求格式不正确。",
        }), 400

    @app.errorhandler(NotFound)
    def not_found(_: NotFound) -> Any:
        return jsonify({
            "error": "not_found",
            "message": "请求的资源不存在。",
        }), 404

    @app.errorhandler(Exception)
    def internal_error(error: Exception) -> Any:
        if isinstance(error, HTTPException):
            return jsonify({
                "error": error.name.lower().replace(" ", "_"),
                "message": error.description or error.name,
            }), error.code or 500

        logger.error("Unhandled backend error:\n%s", traceback.format_exc())
        return jsonify({
            "error": "internal_error",
            "message": "后端出现内部错误，请查看 Metis 后端日志获取详情。",
        }), 500

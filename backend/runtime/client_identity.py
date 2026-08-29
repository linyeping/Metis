"""Stable product identity headers for upstream model requests."""

from __future__ import annotations

import os
import platform

from backend.version import __version__


def model_client_user_agent() -> str:
    """Return the product-specific User-Agent for model-provider requests."""

    kind = str(os.environ.get("METIS_CLIENT_KIND") or "desktop").strip().lower()
    product = "MetisCLI" if kind == "cli" else "MetisDesktop"
    return f"{product}/{__version__}"


def model_client_headers() -> dict[str, str]:
    return {
        "User-Agent": model_client_user_agent(),
        "X-Metis-Client": model_client_user_agent(),
        "X-Metis-Client-Platform": platform.system().lower(),
    }


__all__ = ["model_client_headers", "model_client_user_agent"]

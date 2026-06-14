from __future__ import annotations

from .config import DEFAULT_CONFIG


def public_settings() -> dict:
    return {"retry_count": DEFAULT_CONFIG["retry_count"]}

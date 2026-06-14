from __future__ import annotations

from .config import load_config


def retry_budget() -> int:
    return int(load_config()["retry_count"])

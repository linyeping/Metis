from __future__ import annotations

from .config import load_config
from .pipeline import normalize_records


def main() -> list[int]:
    config = load_config()
    raw = ["1", "2", "", "3"] if config.get("enable_empty_records") else ["1", "2", "3"]
    return normalize_records(raw)


if __name__ == "__main__":
    print(main())

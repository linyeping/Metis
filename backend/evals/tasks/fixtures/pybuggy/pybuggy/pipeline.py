from __future__ import annotations


def normalize_records(records: list[str]) -> list[int]:
    return [int(item) for item in records]


def live_helper(value: int) -> int:
    return value + 1

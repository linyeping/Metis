from __future__ import annotations

from .dead import used_transform


def report(values: list[int]) -> list[int]:
    return [used_transform(value) for value in values]

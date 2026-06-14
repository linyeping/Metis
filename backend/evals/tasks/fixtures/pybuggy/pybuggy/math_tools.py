from __future__ import annotations


def clamp(value: int, low: int, high: int) -> int:
    return max(low, min(high, value))


def median_or_zero(values: list[int]) -> float:
    """Return the median of values, or 0.0 for an empty list."""
    raise NotImplementedError("TODO")

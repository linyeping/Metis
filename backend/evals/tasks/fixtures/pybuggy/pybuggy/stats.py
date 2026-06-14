from __future__ import annotations


def adjacent_pairs(items: list[int]) -> list[tuple[int, int]]:
    """Return adjacent pairs from all items."""
    return [(items[index], items[index + 1]) for index in range(len(items) - 2)]

from __future__ import annotations


def used_transform(value: int) -> int:
    return value * 2


def unused_alpha(value: int) -> int:
    return value + 101


def unused_beta(text: str) -> str:
    return text[::-1]

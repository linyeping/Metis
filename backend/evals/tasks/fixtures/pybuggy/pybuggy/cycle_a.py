from __future__ import annotations

from .cycle_b import VALUE_B

VALUE_A = "A"


def combined() -> str:
    return VALUE_A + VALUE_B

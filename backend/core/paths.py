from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path
from typing import Optional


@lru_cache(maxsize=1)
def metis_home() -> Path:
    """Return the Metis user-data root.

    Resolution order:
    1. METIS_HOME environment override.
    2. Portable marker next to the executable: metis-portable.marker -> data/metis.
    3. Backward-compatible fallback: ~/.metis.
    """
    env = os.environ.get("METIS_HOME", "").strip()
    if env:
        return Path(env).expanduser().resolve(strict=False)
    portable = _portable_data_dir()
    if portable is not None:
        return portable.resolve(strict=False)
    return (Path.home() / ".metis").resolve(strict=False)


def metis_path(*parts: str) -> Path:
    path = metis_home().joinpath(*parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def metis_dir(*parts: str) -> Path:
    path = metis_home().joinpath(*parts)
    path.mkdir(parents=True, exist_ok=True)
    return path


def legacy_miro_home() -> Path:
    return (Path.home() / ".miro").resolve(strict=False)


def legacy_miro_path(*parts: str) -> Path:
    return legacy_miro_home().joinpath(*parts)


def _portable_data_dir() -> Optional[Path]:
    executable = Path(getattr(sys, "executable", "") or "")
    if not executable:
        return None
    root = executable.resolve(strict=False).parent
    if (root / "metis-portable.marker").is_file():
        return root / "data" / "metis"
    return None


def clear_metis_home_cache() -> None:
    metis_home.cache_clear()

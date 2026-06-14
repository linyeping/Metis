# -*- coding: utf-8 -*-
"""Transparent auto-install for missing Python packages.

Usage in any tool module::

    from backend.runtime.pip_helper import ensure_import

    # Single package — returns the imported module
    browser_use = ensure_import("browser_use", pip="browser-use")

    # After ensure_import you can do normal sub-imports
    from browser_use.browser.profile import BrowserProfile

    # Batch — install several packages at once, no return value
    from backend.runtime.pip_helper import ensure_packages
    ensure_packages({
        "browser_use": "browser-use",
        "playwright": "playwright",
    })

Design choices:
  • Uses ``sys.executable -m pip`` so we always target the right venv.
  • Caches successes in-process to avoid repeated subprocess calls.
  • Logs what it installs (but never blocks silently for too long — 120 s
    timeout per batch).
"""

from __future__ import annotations

import importlib
import logging
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)

# Packages that have been successfully installed (or confirmed importable)
# in this process lifetime.  Keyed by pip name.
_confirmed: set[str] = set()


def ensure_import(
    module: str,
    *,
    pip: str | None = None,
    extras: list[str] | None = None,
) -> Any:
    """Import *module*, auto-installing via pip if the import fails.

    Args:
        module:  Python dotted module name (e.g. ``"browser_use"``).
        pip:     pip package name if different from *module*
                 (e.g. ``"browser-use"``).  Defaults to *module*.
        extras:  Additional pip packages to install alongside
                 (e.g. ``["playwright"]``).

    Returns:
        The imported module object.

    Raises:
        ImportError: If installation itself fails.
    """
    # Fast path — already importable
    try:
        return importlib.import_module(module)
    except ImportError:
        pass

    # Install
    pip_name = pip or module
    packages = [pip_name] + (extras or [])
    _pip_install(packages)

    # Retry import — if still fails, let the ImportError propagate
    return importlib.import_module(module)


def ensure_packages(mapping: dict[str, str]) -> None:
    """Make sure every module in *mapping* is importable.

    *mapping* is ``{import_name: pip_name}``, e.g.::

        {"browser_use": "browser-use", "playwright": "playwright"}

    Only the packages that actually fail to import will be installed.
    """
    missing: list[str] = []
    for mod_name, pip_name in mapping.items():
        try:
            importlib.import_module(mod_name)
        except ImportError:
            missing.append(pip_name)

    if missing:
        _pip_install(missing)


# ── internal ─────────────────────────────────────────────────────────

def _pip_install(packages: list[str]) -> None:
    """Run ``pip install`` for *packages* that haven't been installed yet."""
    to_install = [p for p in dict.fromkeys(packages) if p not in _confirmed]
    if not to_install:
        return

    cmd = [sys.executable, "-m", "pip", "install", "--quiet", *to_install]
    label = " ".join(to_install)
    logger.info("Auto-installing pip packages: %s", label)

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except FileNotFoundError:
        raise ImportError(
            f"无法执行 pip（{sys.executable} -m pip）。"
            f"请手动安装: pip install {label}"
        )
    except subprocess.TimeoutExpired:
        raise ImportError(f"pip install 超时 (300s): {label}")

    if result.returncode != 0:
        # Include last 15 lines of stderr for diagnosis
        stderr_tail = "\n".join(result.stderr.strip().splitlines()[-15:])
        raise ImportError(
            f"pip install {label} 失败 (exit {result.returncode}):\n{stderr_tail}"
        )

    _confirmed.update(to_install)
    logger.info("Successfully installed: %s", label)

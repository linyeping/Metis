from __future__ import annotations

import os
import sys

_BACKEND_ROOT = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_BACKEND_ROOT)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from backend.runtime.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

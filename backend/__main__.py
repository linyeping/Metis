from __future__ import annotations

import os
import sys

_BACKEND_ROOT = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_BACKEND_ROOT)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

if __name__ == "__main__":
    # Keep the desktop/backend development modes available without making the
    # old experimental CLI the public `python -m backend` surface.
    if any(arg == "--mode" or arg.startswith("--mode=") for arg in sys.argv[1:]):
        from backend.runtime.cli import main  # noqa: E402
    else:
        from backend.cli import main  # noqa: E402

    raise SystemExit(main())

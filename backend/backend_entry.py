from __future__ import annotations

import os
import sys


_BACKEND_ROOT = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_BACKEND_ROOT)
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

os.environ.setdefault("METIS_MODE", "web")

from backend.runtime.cli import main  # noqa: E402


if __name__ == "__main__":
    port = (
        os.environ.get("METIS_HTTP_PORT")
        or os.environ.get("METIS_PORT")
        or os.environ.get("MIRO_HTTP_PORT")
        or os.environ.get("MIRO_PORT")
        or "5000"
    )
    sys.argv = [
        "metis-backend",
        "--mode",
        "web",
        "--port",
        port,
    ]
    raise SystemExit(main())

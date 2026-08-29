from __future__ import annotations

import os

from .app import main

if __name__ == "__main__":
    os.environ.setdefault("METIS_CLIENT_KIND", "cli")
    raise SystemExit(main())

"""Print the current time and Python version."""

from __future__ import annotations

import platform
from datetime import datetime


if __name__ == "__main__":
    print(f"Current time: {datetime.now().isoformat(timespec='seconds')}")
    print(f"Python version: {platform.python_version()}")

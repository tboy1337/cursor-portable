"""Platform checks that stay typed on every operating system."""

from __future__ import annotations

import sys

PLATFORM: str = sys.platform


def is_windows() -> bool:
    """Return whether this process is running on Windows."""
    return PLATFORM == "win32"

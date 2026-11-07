"""Platform checks that stay typed on every operating system."""

from __future__ import annotations

import sys

PLATFORM: str = sys.platform
# CreateProcess flag. A console program started by pythonw otherwise opens a window.
_CREATE_NO_WINDOW = 0x08000000


def is_windows() -> bool:
    """Return whether this process is running on Windows."""
    return PLATFORM == "win32"


def no_console_flag() -> int:
    """Return the flag that keeps a child console program from opening a window."""
    if is_windows():
        return _CREATE_NO_WINDOW
    return 0

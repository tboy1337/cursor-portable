"""Show a Windows dialog when a launch was started with no console."""

from __future__ import annotations

import ctypes
import logging
import os
import sys

from cursor_portable.runtime import is_windows

LOGGER = logging.getLogger(__name__)

HEADLESS_ENV = "CURSOR_PORTABLE_HEADLESS"
# Topmost error dialog. A pythonw launch has no console to print into.
_ERROR_DIALOG = 0x00050010


def notify_headless(message: str) -> None:
    """Show ``message`` when the Windows launcher started pythonw."""
    if os.environ.get(HEADLESS_ENV) != "1":
        return
    if not is_windows():
        return
    if _console_window() != 0:
        return
    try:
        _message_box(message)
    except OSError as exc:
        LOGGER.error("Could not show the launch error: %s", exc)


def _win_dll(name: str) -> ctypes.CDLL:
    """Load a Windows DLL. ``WinDLL`` is omitted from the non-Windows stubs."""
    load = getattr(ctypes, "WinDLL", None)
    if load is None:
        raise OSError(f"WinDLL is only available on Windows, not {sys.platform}.")
    return load(name, use_last_error=True)  # type: ignore[no-any-return]


def _console_window() -> int:
    kernel32 = _win_dll("kernel32")
    get_window = kernel32.GetConsoleWindow
    get_window.argtypes = ()
    get_window.restype = ctypes.c_void_p
    handle = get_window()
    if not handle:
        return 0
    return int(handle)


def _message_box(message: str) -> None:
    user32 = _win_dll("user32")
    message_box = user32.MessageBoxW
    message_box.argtypes = (
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
    )
    message_box.restype = ctypes.c_int
    message_box(None, message, "Cursor Portable", _ERROR_DIALOG)

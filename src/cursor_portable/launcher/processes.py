"""Names of installer processes that must not install onto this computer."""

from __future__ import annotations

import ctypes
from dataclasses import dataclass
import os
from pathlib import PurePath

from cursor_portable.runtime import is_windows


@dataclass(frozen=True)
class ProcessInfo:
    """A process the watcher can see."""

    pid: int
    name: str


def is_cursor_editor_name(name: str) -> bool:
    """Return whether this process is the Cursor editor, not its installer."""
    lowered = PurePath(name).name.casefold()
    return lowered in {"cursor.exe", "cursor"}


def is_cursor_installer_name(name: str) -> bool:
    """Return whether this process is a Cursor desktop installer."""
    lowered = PurePath(name).name.casefold()
    if is_cursor_editor_name(lowered):
        return False
    if "inno_updater" in lowered:
        return True
    if "cursor" not in lowered:
        return False
    return any(part in lowered for part in ("setup", "installer", "updater"))


def pid_alive(pid: int) -> bool:
    """Return whether a process id exists without signalling it."""
    if pid <= 0:
        return False
    if is_windows():
        return _windows_pid_exists(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _windows_pid_exists(pid: int) -> bool:
    kernel = ctypes.CDLL("kernel32", use_last_error=True)
    open_process = kernel.OpenProcess
    open_process.argtypes = (ctypes.c_uint32, ctypes.c_int, ctypes.c_uint32)
    open_process.restype = ctypes.c_void_p
    close_handle = kernel.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_int
    handle = open_process(0x1000, False, pid)
    if not handle:
        return False
    close_handle(handle)
    return True


def installer_pids(processes: tuple[ProcessInfo, ...]) -> tuple[int, ...]:
    """Return pids of Cursor installer processes."""
    return tuple(
        process.pid for process in processes if is_cursor_installer_name(process.name)
    )

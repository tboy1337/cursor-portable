"""PATH stores. Windows registry edits keep the existing value kind."""

from __future__ import annotations

import ctypes
import importlib
import logging
from pathlib import Path
from typing import Protocol, cast

from cursor_portable.launcher.path_edit import (
    insert_path_block,
    prepend_segment,
    remove_path_block,
    remove_segments,
    split_path,
)
from cursor_portable.models import HostSystem
from cursor_portable.runtime import is_windows

LOGGER = logging.getLogger(__name__)


class _RegistryKey(Protocol):
    """The subset of a registry key handle this store uses."""

    def __enter__(self) -> _RegistryKey:
        """Enter the key context."""

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        """Close the key."""


class _Winreg(Protocol):
    """The subset of ``winreg`` used to edit the user PATH."""

    HKEY_CURRENT_USER: int
    REG_SZ: int
    REG_EXPAND_SZ: int
    KEY_SET_VALUE: int

    def OpenKey(
        self,
        key: int,
        sub_key: str,
        reserved: int = 0,
        access: int = 0,
    ) -> _RegistryKey:
        """Open a registry key."""

    def QueryValueEx(self, key: _RegistryKey, name: str) -> tuple[object, int]:
        """Read a registry value and its kind."""

    def SetValueEx(
        self,
        key: _RegistryKey,
        name: str,
        reserved: int,
        value_type: int,
        value: str,
    ) -> None:
        """Write a registry value."""


_WINREG: _Winreg | None
if is_windows():
    _WINREG = cast(_Winreg, importlib.import_module("winreg"))
else:
    _WINREG = None


class PathStore(Protocol):
    """Read and replace the user PATH."""

    def read(self) -> tuple[str, str]:
        """Return the PATH value and ``plain`` or ``expand``."""

    def write(self, value: str, kind: str) -> None:
        """Write the PATH value without changing its kind unless asked."""


def broadcast_environment() -> None:
    """Tell Windows that the user environment changed."""
    if not is_windows():
        return
    user32 = ctypes.CDLL("user32", use_last_error=True)
    send = user32.SendMessageTimeoutW
    send.argtypes = (
        ctypes.c_void_p,
        ctypes.c_uint,
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.c_uint,
        ctypes.c_uint,
        ctypes.POINTER(ctypes.c_void_p),
    )
    send.restype = ctypes.c_void_p
    result = ctypes.c_void_p()
    returned = send(
        ctypes.c_void_p(0xFFFF),
        0x001A,
        None,
        "Environment",
        0x0002,
        1000,
        ctypes.byref(result),
    )
    if not returned:
        LOGGER.warning("Windows did not confirm the PATH change.")


def prepend_user_path(
    store: PathStore,
    segments: tuple[str, ...],
    separator: str,
) -> tuple[str, ...]:
    """Prepend missing segments. Return only the segments that were added."""
    if isinstance(store, RcPathStore):
        return _prepend_rc(store, segments)
    value, kind = store.read()
    updated = value
    added: list[str] = []
    for segment in reversed(segments):
        if segment not in split_path(updated, separator):
            updated = prepend_segment(updated, segment, separator)
            added.append(segment)
    if updated != value:
        store.write(updated, kind)
    return tuple(reversed(added))


def _prepend_rc(store: RcPathStore, segments: tuple[str, ...]) -> tuple[str, ...]:
    for path in store.paths:
        if not update_rc_block(path, segments, remove=False):
            LOGGER.error("Could not update %s", path)
            return ()
    return segments


def restore_user_path(
    store: PathStore,
    segments: tuple[str, ...],
    separator: str,
) -> None:
    """Remove exact segments and leave every other entry in place."""
    value, kind = store.read()
    store.write(remove_segments(value, segments, separator), kind)


class MemoryPathStore:
    """PATH store used by tests and as the type Windows and Unix stores match."""

    def __init__(self, value: str = "", kind: str = "plain") -> None:
        self.value = value
        self.kind = kind

    def read(self) -> tuple[str, str]:
        """Return the stored PATH."""
        return self.value, self.kind

    def write(self, value: str, kind: str) -> None:
        """Replace the stored PATH."""
        self.value = value
        self.kind = kind


class RcPathStore:
    """Prepend PATH from one or more shell rc files using a marked block."""

    def __init__(self, path: Path | tuple[Path, ...]) -> None:
        self.paths = path if isinstance(path, tuple) else (path,)
        self.path = self.paths[0]

    def read(self) -> tuple[str, str]:
        """Return the rc file text. The kind is always plain."""
        if not self.path.is_file():
            return "", "plain"
        return self.path.read_text(encoding="utf-8"), "plain"

    def write(self, value: str, kind: str) -> None:
        """Write the rc file. ``kind`` is ignored on Unix."""
        del kind
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(value, encoding="utf-8")


def update_rc_block(
    path: Path, segment: str | tuple[str, ...], *, remove: bool
) -> bool:
    """Insert or remove the marked block. Return False if removal is unsafe."""
    text = path.read_text(encoding="utf-8") if path.is_file() else ""
    if remove:
        updated = remove_path_block(text)
        if updated is None:
            return False
        path.write_text(updated, encoding="utf-8")
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(insert_path_block(text, segment), encoding="utf-8")
    return True


class WinregPathStore:
    """User PATH in ``HKCU\\Environment``. ``REG_EXPAND_SZ`` stays expanded-string."""

    def read(self) -> tuple[str, str]:
        """Read the user Path value without expanding variables."""
        if _WINREG is None:
            raise RuntimeError("The Windows PATH store is only available on Windows.")
        try:
            with _WINREG.OpenKey(_WINREG.HKEY_CURRENT_USER, "Environment") as key:
                value, kind = _WINREG.QueryValueEx(key, "Path")
        except FileNotFoundError:
            return "", "plain"
        kind_name = "expand" if kind == _WINREG.REG_EXPAND_SZ else "plain"
        return str(value), kind_name

    def write(self, value: str, kind: str) -> None:
        """Write the user Path value using the requested registry kind."""
        if _WINREG is None:
            raise RuntimeError("The Windows PATH store is only available on Windows.")
        reg_kind = _WINREG.REG_EXPAND_SZ if kind == "expand" else _WINREG.REG_SZ
        with _WINREG.OpenKey(
            _WINREG.HKEY_CURRENT_USER,
            "Environment",
            0,
            _WINREG.KEY_SET_VALUE,
        ) as key:
            _WINREG.SetValueEx(key, "Path", 0, reg_kind, value)
        broadcast_environment()


def shell_rc_paths(home: Path, system: HostSystem) -> tuple[Path, ...]:
    """Shell startup files that should see the portable Python."""
    if system is HostSystem.MACOS:
        return (home / ".zprofile", home / ".zshrc", home / ".profile")
    if system is HostSystem.LINUX:
        return (home / ".profile", home / ".bashrc")
    return ()


def path_store_for(
    system: HostSystem,
    rc_path: Path | tuple[Path, ...] | None = None,
) -> PathStore:
    """Return the PATH store for this operating system."""
    if system is HostSystem.WINDOWS:
        return WinregPathStore()
    if rc_path is None:
        raise RuntimeError("Unix PATH edits need an rc path.")
    return RcPathStore(rc_path)

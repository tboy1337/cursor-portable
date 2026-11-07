"""Locate the Tcl/Tk files shipped beside a relocatable interpreter.

Windows finds those files next to ``python.exe``. Linux and macOS need
``TCL_LIBRARY`` and ``TK_LIBRARY`` before tkinter is imported, or Tcl may
load a different install.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
import sys

LOGGER = logging.getLogger(__name__)


def prepare_tkinter() -> None:
    """Point Tcl at this interpreter before tkinter is imported."""
    apply_tcl_paths(Path(sys.base_prefix), sys.platform)


def apply_tcl_paths(root: Path, platform: str) -> None:
    """Set Tcl paths for a POSIX interpreter. Windows locates them itself."""
    if platform == "win32":
        LOGGER.info("Windows locates Tcl beside %s", root)
        return
    LOGGER.info("Looking for Tcl/Tk under %s", root)
    _assign("TCL_LIBRARY", _versioned(root, "tcl", "init.tcl"))
    _assign("TK_LIBRARY", _versioned(root, "tk", "tk.tcl"))


def _versioned(root: Path, name: str, marker: str) -> Path | None:
    found: list[Path] = []
    for directory in (root / "lib", root / "tcl"):
        if not directory.is_dir():
            continue
        found.extend(
            path
            for path in directory.glob(f"{name}[0-9]*")
            if path.is_dir() and (path / marker).is_file()
        )
    if not found:
        return None
    return max(found, key=_version_suffix)


def _version_suffix(path: Path) -> tuple[int, ...]:
    numbers: list[int] = []
    current = ""
    for char in path.name:
        if char.isdigit():
            current += char
            continue
        if current:
            numbers.append(int(current))
            current = ""
    if current:
        numbers.append(int(current))
    return tuple(numbers)


def _assign(variable: str, path: Path | None) -> None:
    if path is None:
        LOGGER.info("%s was not found; leaving the environment unchanged", variable)
        return
    if os.environ.get(variable):
        LOGGER.info("%s is already set", variable)
        return
    LOGGER.info("Using %s=%s", variable, path)
    os.environ[variable] = str(path)

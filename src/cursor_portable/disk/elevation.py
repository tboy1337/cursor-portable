"""Whether format and repair are allowed to run on this host."""

from __future__ import annotations

import ctypes
import logging
import os

from cursor_portable.failure import Failure
from cursor_portable.models import HostSystem
from cursor_portable.runtime import is_windows

LOGGER = logging.getLogger(__name__)


def elevation_ready(system: HostSystem) -> bool:
    """Return whether this process may format or repair a volume."""
    if system is HostSystem.WINDOWS:
        if not is_windows():
            return True
        windll = getattr(ctypes, "windll", None)
        shell32 = getattr(windll, "shell32", None)
        is_admin = getattr(shell32, "IsUserAnAdmin", None)
        if is_admin is None:
            return False
        try:
            return bool(is_admin())
        except (AttributeError, OSError) as exc:
            LOGGER.error("Could not read the Windows elevation state: %s", exc)
            return False
    geteuid = getattr(os, "geteuid", None)
    if geteuid is None:
        return True
    return bool(geteuid() == 0)  # pylint: disable=not-callable


def elevation_failure(system: HostSystem) -> Failure | None:
    """Refuse format and repair when the process cannot change a volume."""
    if elevation_ready(system):
        return None
    if system is HostSystem.WINDOWS:
        return Failure(
            "elevation",
            "Format and repair need an elevated Windows terminal.",
        )
    return Failure("elevation", "Format and repair need root.")

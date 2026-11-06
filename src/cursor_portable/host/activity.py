"""Detect a running Cursor and a drive this launcher is using."""

from __future__ import annotations

import os
from pathlib import Path
import sys

from cursor_portable.disk.volume import Volume
from cursor_portable.host.cleanup import default_host_root
from cursor_portable.launcher.host_watch import read_processes
from cursor_portable.launcher.processes import is_cursor_editor_name, pid_alive
from cursor_portable.launcher.session_file import read_session
from cursor_portable.models import HostSystem


def cursor_editor_running() -> bool:
    """Return whether the Cursor editor process is open on this computer."""
    return any(is_cursor_editor_name(process.name) for process in read_processes())


def _under(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except (OSError, ValueError):
        return False
    return True


def volume_busy(volume: Volume, system: HostSystem) -> bool:
    """Return whether this process or a portable session is using the volume."""
    mount = Path(volume.mount_point)
    if _under(Path.cwd(), mount) or _under(Path(sys.executable), mount):
        return True
    local = os.environ.get("LOCALAPPDATA")
    host = default_host_root(
        Path.home(),
        Path(local) if local else None,
        windows=system is HostSystem.WINDOWS,
    )
    record = read_session(host / "session.txt")
    if record is None or not record.stick_root:
        return False
    if not _under(Path(record.stick_root), mount):
        return False
    try:
        pid = int(record.pid)
    except ValueError:
        return False
    return pid_alive(pid)

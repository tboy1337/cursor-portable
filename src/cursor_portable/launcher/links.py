"""Create and remove a directory link. Junctions are used on Windows."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import subprocess

from cursor_portable.failure import Failure
from cursor_portable.runtime import is_windows, no_console_flag

LOGGER = logging.getLogger(__name__)


def link_target(path: Path) -> str | None:
    """Return the target of a symlink or junction."""
    if not path.exists() and not path.is_symlink():
        return None
    try:
        return os.readlink(path)
    except OSError:
        return None


class DirectoryLinker:
    """Create a junction on Windows and a symlink on macOS and Linux."""

    def create(self, link: Path, target: Path) -> Failure | None:
        """Point ``link`` at ``target``. ``link`` must not already exist."""
        LOGGER.info("Linking %s -> %s", link, target)
        if is_windows():
            completed = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                check=False,
                capture_output=True,
                text=True,
                stdin=subprocess.DEVNULL,
                creationflags=no_console_flag(),
            )
            if completed.returncode != 0:
                detail = completed.stderr.strip() or completed.stdout.strip()
                return Failure("link", f"Could not create junction: {detail}")
            return None
        try:
            link.symlink_to(target, target_is_directory=True)
        except OSError as exc:
            return Failure("link", f"Could not create symlink: {exc}")
        return None

    def remove(self, link: Path) -> Failure | None:
        """Remove a link without deleting the target directory."""
        if not link.exists() and not link.is_symlink():
            return None
        try:
            if is_windows():
                os.rmdir(link)
            else:
                link.unlink()
        except OSError as exc:
            return Failure("link", f"Could not remove link: {exc}")
        return None

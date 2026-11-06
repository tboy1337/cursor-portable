"""7-Zip command lines and a runner used by the packer."""

from __future__ import annotations

import logging
from pathlib import Path
import subprocess

from cursor_portable.failure import Failure

LOGGER = logging.getLogger(__name__)


def seven_zip_add(executable: str, archive: Path, source: Path) -> list[str]:
    """Create a ``.7z`` archive of the contents of ``source``."""
    return [
        executable,
        "a",
        "-t7z",
        "-mx=5",
        "-y",
        str(archive),
        str(source / "*"),
    ]


def seven_zip_extract(executable: str, archive: Path, destination: Path) -> list[str]:
    """Extract a ``.7z`` archive into ``destination``."""
    return [executable, "x", f"-o{destination}", "-y", str(archive)]


def run_command(command: list[str], *, cwd: Path | None = None) -> Failure | None:
    """Run a command. The arguments are not passed through a shell."""
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            cwd=cwd,
        )
    except OSError as exc:
        LOGGER.error("Could not start %s: %s", command[0], exc)
        return Failure("command", f"{command[0]} could not be started: {exc}")
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        return Failure(
            "command", f"{command[0]} failed ({completed.returncode}): {detail}"
        )
    return None

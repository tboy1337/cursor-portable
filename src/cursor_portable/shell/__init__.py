"""Write a script supplied by the module that owns it.

PowerShell runs that text with ``-File``. The same text is kept in the Python
module, and the matching file under ``tests/shell`` is what the linters run.
"""

from __future__ import annotations

import logging
from pathlib import Path
import tempfile

LOGGER = logging.getLogger(__name__)


def materialize_script(name: str, script: str) -> Path:
    """Write ``script`` where PowerShell can run it with ``-File``."""
    directory = Path(tempfile.gettempdir()) / "cursor-portable-shell"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / Path(name).name
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    if current != script:
        path.write_text(script, encoding="utf-8", newline="\n")
        LOGGER.info("Wrote shell script %s", path)
        return path
    LOGGER.info("Shell script %s is current", path)
    return path


def powershell_file_command(name: str, script: str) -> list[str]:
    """Return a powershell ``-File`` invocation of ``script``."""
    return [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(materialize_script(name, script)),
    ]

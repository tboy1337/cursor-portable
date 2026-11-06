"""Set the portable interpreter in settings.json without destroying JSONC."""

from __future__ import annotations

import logging
from pathlib import Path

from cursor_portable.json_io import dump_object, load_object

LOGGER = logging.getLogger(__name__)


def set_interpreter(settings_path: Path, interpreter: Path) -> bool:
    """Point ``python.defaultInterpreterPath`` at the portable interpreter.

    Returns False when the file is JSONC or otherwise not plain JSON. The file
    is left unchanged in that case so comments are not discarded.
    """
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if not settings_path.exists():
        document: dict[str, object] = {
            "python.defaultInterpreterPath": str(interpreter)
        }
        settings_path.write_text(dump_object(document), encoding="utf-8")
        LOGGER.info("Wrote interpreter setting to %s", settings_path)
        return True
    loaded = load_object(settings_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        LOGGER.warning("Left %s unchanged because it is not plain JSON.", settings_path)
        return False
    loaded["python.defaultInterpreterPath"] = str(interpreter)
    settings_path.write_text(dump_object(loaded), encoding="utf-8")
    LOGGER.info("Updated interpreter setting in %s", settings_path)
    return True

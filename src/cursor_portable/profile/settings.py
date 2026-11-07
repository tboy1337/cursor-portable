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
    return _set_json_setting(
        settings_path,
        "python.defaultInterpreterPath",
        str(interpreter),
        "interpreter",
    )


def set_git_path(settings_path: Path, executable: Path) -> bool:
    """Point ``git.path`` at the portable Git executable.

    Returns False when the file is JSONC or otherwise not plain JSON. The file
    is left unchanged in that case so comments are not discarded.
    """
    return _set_json_setting(settings_path, "git.path", str(executable), "git path")


def _set_json_setting(settings_path: Path, key: str, value: str, label: str) -> bool:
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    if not settings_path.exists():
        settings_path.write_text(dump_object({key: value}), encoding="utf-8")
        LOGGER.info("Wrote %s setting to %s", label, settings_path)
        return True
    loaded = load_object(settings_path.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        LOGGER.warning("Left %s unchanged because it is not plain JSON.", settings_path)
        return False
    loaded[key] = value
    settings_path.write_text(dump_object(loaded), encoding="utf-8")
    LOGGER.info("Updated %s setting in %s", label, settings_path)
    return True

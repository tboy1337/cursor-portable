"""Host locations Cursor uses for settings. These are read, never modified in place."""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Mapping

from cursor_portable.failure import Failure
from cursor_portable.models import HostSystem


@dataclass(frozen=True)
class ProfileSources:
    """Folders to read when the user asks for a copy of this computer."""

    user_data: Path
    dot_cursor: Path
    extensions: Path
    argv: Path


def _required(env: Mapping[str, str], key: str) -> str | Failure:
    value = env.get(key, "").strip()
    if not value:
        return Failure("profile-source", f"{key} is not set.")
    return value


def profile_sources(
    system: HostSystem,
    env: Mapping[str, str] | None = None,
) -> ProfileSources | Failure:
    """Return the Cursor profile paths for this computer."""
    values = os.environ if env is None else env
    home_key = "USERPROFILE" if system is HostSystem.WINDOWS else "HOME"
    home = _required(values, home_key)
    if isinstance(home, Failure):
        return home
    home_path = Path(home)
    dot_cursor = home_path / ".cursor"
    if system is HostSystem.WINDOWS:
        appdata = _required(values, "APPDATA")
        if isinstance(appdata, Failure):
            return appdata
        user_data = Path(appdata) / "Cursor"
    elif system is HostSystem.MACOS:
        user_data = home_path / "Library" / "Application Support" / "Cursor"
    else:
        user_data = home_path / ".config" / "Cursor"
    return ProfileSources(
        user_data=user_data,
        dot_cursor=dot_cursor,
        extensions=dot_cursor / "extensions",
        argv=dot_cursor / "argv.json",
    )

"""Copy the portable profile between the stick and the internal disk."""

from __future__ import annotations

import logging
from pathlib import Path
import shutil

from cursor_portable.constants import UNINSTALLER_NAMES
from cursor_portable.repack.copy_tree import copy_tree

LOGGER = logging.getLogger(__name__)

PROFILE_DIRECTORIES = ("user-data", "extensions", "dot-cursor")
PROFILE_FILES = ("argv.json",)
PROFILE_SYNC_SECONDS = 180.0
APP_SKIP_DIRS = frozenset({"data"})
APP_SKIP_ROOT_FILES = frozenset({"version.json", ".pending-sync"})


def portable_data_dir(cache_dir: Path) -> Path:
    """Return the ``data`` directory that sits beside the cached executable."""
    return cache_dir / "data"


def ensure_portable_data(cache_dir: Path) -> Path:
    """Create the session profile directories beside the cached executable."""
    data = portable_data_dir(cache_dir)
    for name in PROFILE_DIRECTORIES:
        (data / name).mkdir(parents=True, exist_ok=True)
    return data


def sync_profile(
    source: Path,
    destination: Path,
    *,
    skip_names: frozenset[str] = frozenset(),
    skip_dirs: frozenset[str] = frozenset(),
) -> None:
    """Copy profile directories that exist. Missing sources are left alone."""
    if not source.exists():
        LOGGER.info("Profile source %s is absent", source)
        return
    destination.mkdir(parents=True, exist_ok=True)
    names = UNINSTALLER_NAMES | skip_names
    for name in PROFILE_DIRECTORIES:
        child = source / name
        if child.is_dir():
            copy_tree(
                child,
                destination / name,
                skip_names=names,
                skip_dirs=skip_dirs,
            )
    for name in PROFILE_FILES:
        child = source / name
        if not child.is_file():
            continue
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(child, target)
    LOGGER.info("Synced profile %s -> %s", source, destination)

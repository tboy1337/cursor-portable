"""Remove host changes. This does not delete anything on the removable drive."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path
import shutil

from cursor_portable.constants import HOST_DIR_NAME, RUNTIME_DIR_NAME
from cursor_portable.launcher.home_link import is_our_home_target
from cursor_portable.launcher.links import DirectoryLinker, link_target
from cursor_portable.launcher.path_store import (
    PathStore,
    RcPathStore,
    restore_user_path,
    update_rc_block,
)
from cursor_portable.launcher.session_file import SessionRecord
from cursor_portable.launcher.shortcuts import managed_shortcut

LOGGER = logging.getLogger(__name__)


def cleanup_session(
    record: SessionRecord,
    store: PathStore,
    linker: DirectoryLinker,
    *,
    delete_cache: bool,
) -> None:
    """Undo PATH and profile-link changes recorded in a session."""
    _restore_path(record, store)
    _restore_home(record, linker)
    if delete_cache and record.cache_dir:
        _delete_tree(Path(record.cache_dir))


def _restore_path(record: SessionRecord, store: PathStore) -> None:
    if isinstance(store, RcPathStore):
        for path in store.paths:
            LOGGER.info("Removing portable Python block from %s", path)
            if not update_rc_block(path, "", remove=True):
                LOGGER.error(
                    "Left %s unchanged because the portable block is unfinished.",
                    path,
                )
        return
    if record.path_segments:
        LOGGER.info("Removing portable Python from PATH")
        restore_user_path(
            store,
            tuple(record.path_segments),
            record.path_separator or ";",
        )


def _same_path(left: str, right: str) -> bool:
    return Path(left) == Path(right)


def _restore_home(record: SessionRecord, linker: DirectoryLinker) -> None:
    if not record.home_link:
        return
    link = Path(record.home_link)
    target = link_target(link)
    if target is None:
        return
    expected = record.home_target
    ours = is_our_home_target(target) or bool(expected and _same_path(target, expected))
    if not ours:
        return
    failure = linker.remove(link)
    if failure is not None:
        LOGGER.error("%s", failure.message)
        return
    if record.home_backup:
        backup = Path(record.home_backup)
        if backup.exists() and not link.exists():
            backup.rename(link)
            LOGGER.info("Restored %s", link)


def delete_managed_shortcuts(paths: tuple[Path, ...]) -> tuple[Path, ...]:
    """Delete shortcuts this tool created. Return the paths removed."""
    removed: list[Path] = []
    for path in paths:
        if path.is_file() and managed_shortcut(str(path)):
            path.unlink()
            removed.append(path)
            LOGGER.info("Removed shortcut %s", path)
    return tuple(removed)


def delete_runtime_cache(host_root: Path) -> None:
    """Delete the local app cache and leave the session file for the caller."""
    runtime = host_root / RUNTIME_DIR_NAME
    _delete_tree(runtime)


def cache_directory(host_root: Path, stick_root: Path) -> Path:
    """Return the local app cache for one stick."""
    token = os.path.normcase(str(stick_root))
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    return host_root / RUNTIME_DIR_NAME / digest / "app"


def default_host_root(
    home: Path, local_app_data: Path | None, *, windows: bool
) -> Path:
    """Return the host directory used for the cache and the session file."""
    if windows and local_app_data is not None:
        return local_app_data / HOST_DIR_NAME
    return home / ".cache" / HOST_DIR_NAME


def _delete_tree(path: Path) -> None:
    if path.exists():
        shutil.rmtree(path)
        LOGGER.info("Deleted %s", path)

"""Decide which shortcuts to put back after Cursor rewrites them."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path, PurePath
import shutil
import stat

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class Shortcut:
    """A shortcut location, the program it starts, and an optional byte snapshot."""

    location: str
    target: str
    snapshot: str = ""


def restorations(
    before: tuple[Shortcut, ...],
    after: tuple[Shortcut, ...],
    blocked_targets: frozenset[str],
    portable_roots: tuple[str, ...] = (),
) -> tuple[Shortcut, ...]:
    """Return shortcuts whose target moved onto the portable app."""
    previous = {item.location: item for item in before}
    blocked = {target.casefold() for target in blocked_targets}
    changes: list[Shortcut] = []
    for item in after:
        old = previous.get(item.location)
        if old is None or item.target == old.target:
            continue
        moved = item.target.casefold() in blocked or is_portable_target(
            item.target, portable_roots
        )
        if moved:
            changes.append(Shortcut(item.location, old.target, old.snapshot))
    return tuple(changes)


def with_snapshots(
    items: tuple[Shortcut, ...], directory: Path
) -> tuple[Shortcut, ...]:
    """Copy shortcut bytes so a later rewrite can be replaced.

    An existing snapshot is replaced even when an older copy left it
    read-only. A shortcut that still cannot be copied is kept without a
    snapshot so launch can continue.
    """
    directory.mkdir(parents=True, exist_ok=True)
    return tuple(
        _snapshot_item(item, directory / f"{index}.bin")
        for index, item in enumerate(items)
    )


def _snapshot_item(item: Shortcut, destination: Path) -> Shortcut:
    source = Path(item.location)
    if not source.is_file():
        return item
    try:
        _replace_snapshot(source, destination)
    except OSError as exc:
        LOGGER.warning(
            "Could not snapshot shortcut %s to %s: %s",
            source,
            destination,
            exc,
        )
        return item
    return Shortcut(item.location, item.target, str(destination))


def _replace_snapshot(source: Path, destination: Path) -> None:
    """Store shortcut bytes without the source file's attributes."""
    _clear_read_only(destination)
    destination.unlink(missing_ok=True)
    shutil.copyfile(source, destination)


def _clear_read_only(path: Path) -> None:
    """Allow replacement of a snapshot left read-only by an older copy."""
    if not path.is_file():
        return
    mode = path.stat().st_mode
    if mode & stat.S_IWRITE:
        return
    LOGGER.info("Clearing read-only attribute on snapshot %s", path)
    path.chmod(mode | stat.S_IWRITE)


def is_portable_target(target: str, portable_roots: tuple[str, ...]) -> bool:
    """Return whether ``target`` lives under a portable or cache root."""
    normalized = target.replace("\\", "/").casefold()
    for root in portable_roots:
        prefix = root.replace("\\", "/").casefold().rstrip("/")
        if normalized == prefix or normalized.startswith(prefix + "/"):
            return True
    return False


def managed_shortcut(path: str) -> bool:
    """Return whether this tool created the shortcut."""
    name = PurePath(path).name.casefold()
    return name in {"cursor portable.lnk", "cursor-portable.desktop"}

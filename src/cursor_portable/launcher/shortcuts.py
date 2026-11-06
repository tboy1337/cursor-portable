"""Decide which shortcuts to put back after Cursor rewrites them."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path, PurePath
import shutil


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
    """Copy each shortcut file so a later rewrite can be replaced with the bytes."""
    directory.mkdir(parents=True, exist_ok=True)
    saved: list[Shortcut] = []
    for index, item in enumerate(items):
        source = Path(item.location)
        if not source.is_file():
            saved.append(item)
            continue
        destination = directory / f"{index}.bin"
        shutil.copy2(source, destination)
        saved.append(Shortcut(item.location, item.target, str(destination)))
    return tuple(saved)


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

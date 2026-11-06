"""Copy an allowlisted Cursor profile into the portable data directory."""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path
import shutil

from cursor_portable.constants import (
    CHAT_DIRECTORIES,
    CHAT_FILES,
    HOME_PROFILE_NAMES,
    USER_DATA_DIRECTORIES,
    USER_DATA_FILES,
)
from cursor_portable.profile.sources import ProfileSources
from cursor_portable.repack.copy_tree import copy_tree
from cursor_portable.repack.layout import StickLayout

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ProfileCopyReport:
    """What the profile copy did."""

    copied_user_data: bool
    skipped_chats: bool
    note: str


def _skip_chat(name: str, *, skip_chats: bool, include_backup: bool) -> bool:
    if name in CHAT_DIRECTORIES and skip_chats:
        return True
    if name not in CHAT_FILES:
        return False
    if skip_chats:
        return True
    if name == "state.vscdb.backup" and not include_backup:
        return True
    return False


def _copy_allowlisted_tree(
    source: Path,
    destination: Path,
    *,
    directories: frozenset[str],
    files: frozenset[str],
    skip_chats: bool,
    include_backup: bool,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if not source.is_dir():
        LOGGER.info("Profile source is missing: %s", source)
        return
    for child in source.iterdir():
        if _skip_chat(child.name, skip_chats=skip_chats, include_backup=include_backup):
            LOGGER.info("Skipping %s", child.name)
            continue
        target = destination / child.name
        if child.is_dir() and child.name in directories:
            _copy_filtered_dir(
                child,
                target,
                skip_chats=skip_chats,
                include_backup=include_backup,
            )
        elif child.is_file() and child.name in files:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(child, target)


def _copy_filtered_dir(
    source: Path,
    destination: Path,
    *,
    skip_chats: bool,
    include_backup: bool,
) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    for path in source.rglob("*"):
        relative = path.relative_to(source)
        if any(
            _skip_chat(part, skip_chats=skip_chats, include_backup=include_backup)
            for part in relative.parts
        ):
            continue
        target = destination / relative
        if path.is_dir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        if path.is_file():
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)


def copy_home_profile(source: Path, destination: Path) -> None:
    """Copy skills, rules, MCP config, and the other home-profile names."""
    destination.mkdir(parents=True, exist_ok=True)
    if not source.is_dir():
        return
    for name in sorted(HOME_PROFILE_NAMES):
        child = source / name
        if not child.exists():
            continue
        target = destination / name
        if child.is_dir():
            copy_tree(child, target)
        elif child.is_file():
            shutil.copy2(child, target)


def copy_profile(
    sources: ProfileSources,
    layout: StickLayout,
    *,
    cursor_running: bool,
    include_state_backup: bool,
) -> ProfileCopyReport:
    """Copy settings. Chat databases are skipped while Cursor is open."""
    skip_chats = cursor_running
    _copy_allowlisted_tree(
        sources.user_data,
        layout.user_data,
        directories=USER_DATA_DIRECTORIES,
        files=USER_DATA_FILES,
        skip_chats=skip_chats,
        include_backup=include_state_backup,
    )
    if sources.extensions.is_dir():
        copy_tree(sources.extensions, layout.extensions)
    if sources.argv.is_file():
        layout.argv.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sources.argv, layout.argv)
    copy_home_profile(sources.dot_cursor, layout.dot_cursor)
    note = ""
    if cursor_running:
        note_path = layout.data / "CHAT_NOT_COPIED.txt"
        note = "Cursor was open, so chat databases were not copied."
        note_path.write_text(note + "\n", encoding="utf-8")
        LOGGER.warning(note)
    return ProfileCopyReport(
        copied_user_data=True,
        skipped_chats=cursor_running,
        note=note,
    )

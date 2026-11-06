"""Decide whether the fast local copy or the stick has the newer app."""

from __future__ import annotations

from enum import Enum


class SyncDirection(Enum):
    """Which tree to copy."""

    NONE = "none"
    STICK_TO_CACHE = "stick-to-cache"
    CACHE_TO_STICK = "cache-to-stick"


def choose_sync(
    *,
    stick_version: str,
    cache_version: str | None,
    cache_ready: bool,
    pending_sync: bool,
) -> SyncDirection:
    """Pick a copy direction before Cursor starts."""
    if not cache_ready or cache_version is None:
        return SyncDirection.STICK_TO_CACHE
    if pending_sync and cache_version != stick_version:
        return SyncDirection.CACHE_TO_STICK
    if cache_version != stick_version:
        return SyncDirection.STICK_TO_CACHE
    return SyncDirection.NONE


def sync_after_exit(
    *,
    launched_version: str,
    cache_version: str | None,
) -> SyncDirection:
    """Copy an in-app update back to the stick."""
    if cache_version is None:
        return SyncDirection.NONE
    if cache_version != launched_version:
        return SyncDirection.CACHE_TO_STICK
    return SyncDirection.NONE

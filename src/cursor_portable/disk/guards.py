"""Refusals shared by format and repair."""

from __future__ import annotations

from cursor_portable.disk.filesystem import display_filesystem, filesystems_match
from cursor_portable.disk.volume import Volume, validate_device
from cursor_portable.failure import Failure
from cursor_portable.models import HostSystem


def guard_volume(
    volume: Volume,
    system: HostSystem,
    *,
    busy: bool,
) -> Failure | None:
    """Refuse a system disk, a non-removable disk, or a volume that is in use."""
    if volume.is_system or volume.is_boot:
        return Failure(
            "system-disk",
            f"{volume.mount_point} is a system or boot volume.",
        )
    if not volume.removable:
        return Failure(
            "not-removable",
            f"{volume.mount_point} is not a removable drive.",
        )
    device_failure = validate_device(volume.device_id, system)
    if device_failure is not None:
        return device_failure
    if busy:
        return Failure(
            "busy",
            f"Cursor or this launcher is running from {volume.mount_point}.",
        )
    return None


def require_filesystem(volume: Volume, filesystem: str) -> Failure | None:
    """Refuse a volume that is not the filesystem a stick needs."""
    if filesystems_match(volume.filesystem, filesystem):
        return None
    actual = volume.filesystem or "unknown"
    expected = display_filesystem(filesystem)
    return Failure(
        "filesystem",
        f"{volume.mount_point} is {actual}, not {expected}.",
    )

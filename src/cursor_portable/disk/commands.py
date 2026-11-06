"""Format and repair commands. Callers run them only after the guards pass."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import logging

from cursor_portable.disk.filesystem import (
    canonical_filesystem,
    display_filesystem,
    filesystem_for,
    filesystems_match,
    format_failure,
    host_can_format,
)
from cursor_portable.disk.guards import guard_volume, require_filesystem
from cursor_portable.disk.volume import (
    Volume,
    label_matches,
    label_refusal,
    volume_name,
)
from cursor_portable.failure import Failure
from cursor_portable.models import HostSystem

LOGGER = logging.getLogger(__name__)


class RepairMode(Enum):
    """Read-only check, or a repair after that check has been shown."""

    CHECK = "check"
    APPLY = "apply"


@dataclass(frozen=True)
class DiskPlan:
    """A command that passed the safeguards."""

    command: tuple[str, ...]
    needs_elevation: bool
    summary: str


def format_plan(
    volume: Volume,
    typed: str,
    system: HostSystem,
    *,
    busy: bool,
    target: HostSystem | None = None,
    available_tools: frozenset[str] | None = None,
) -> DiskPlan | Failure:
    """Plan a native format for ``target``. This erases the volume."""
    chosen = system if target is None else target
    failure = guard_volume(volume, system, busy=busy)
    if failure is not None:
        return failure
    expected = filesystem_for(chosen)
    if filesystems_match(volume.filesystem, expected):
        display = display_filesystem(expected)
        return Failure(
            "already-formatted",
            f"{volume.mount_point} is already {display}.",
        )
    if not host_can_format(system, expected, available_tools=available_tools):
        LOGGER.info(
            "Refusing to format %s as %s on %s",
            volume.mount_point,
            expected,
            system.value,
        )
        return format_failure(system, expected)
    if not label_matches(volume, typed):
        return Failure("label-mismatch", label_refusal(volume, "formatted"))
    name = volume_name(volume, expected)
    command = _format_command(volume, system, expected, name)
    if isinstance(command, Failure):
        return command
    display = display_filesystem(expected)
    LOGGER.info("Format %s as %s with %s", volume.mount_point, display, command[0])
    return DiskPlan(
        command=tuple(command),
        needs_elevation=True,
        summary=f"Erase {volume.mount_point} and format it as {display}.",
    )


def repair_plan(
    volume: Volume,
    typed: str,
    system: HostSystem,
    mode: RepairMode,
    *,
    busy: bool,
    target: HostSystem | None = None,
) -> DiskPlan | Failure:
    """Plan a check or repair for the target filesystem."""
    chosen = system if target is None else target
    failure = guard_volume(volume, system, busy=busy)
    if failure is not None:
        return failure
    expected = filesystem_for(chosen)
    filesystem_failure = require_filesystem(volume, expected)
    if filesystem_failure is not None:
        return filesystem_failure
    if not label_matches(volume, typed):
        return Failure("label-mismatch", label_refusal(volume, "repaired"))
    command = _repair_command(volume, system, expected, mode)
    if isinstance(command, Failure):
        return command
    summary = (
        f"Check {volume.mount_point}"
        if mode is RepairMode.CHECK
        else f"Repair {volume.mount_point}"
    )
    LOGGER.info("%s with %s", summary, command[0])
    return DiskPlan(
        command=tuple(command),
        needs_elevation=True,
        summary=summary,
    )


def _format_command(
    volume: Volume,
    system: HostSystem,
    filesystem: str,
    name: str,
) -> list[str] | Failure:
    key = (system, canonical_filesystem(filesystem))
    if key == (HostSystem.WINDOWS, "ntfs"):
        return [
            "format.com",
            volume.device_id,
            "/FS:NTFS",
            "/Q",
            "/Y",
            f"/V:{name}",
        ]
    if key == (HostSystem.MACOS, "apfs"):
        return ["diskutil", "eraseVolume", "APFS", name, volume.device_id]
    if key == (HostSystem.LINUX, "ext4"):
        return ["mkfs.ext4", "-L", name, volume.device_id]
    if key == (HostSystem.LINUX, "ntfs"):
        return ["mkfs.ntfs", "-f", "-L", name, volume.device_id]
    return format_failure(system, filesystem)


def _repair_command(
    volume: Volume,
    system: HostSystem,
    filesystem: str,
    mode: RepairMode,
) -> list[str] | Failure:
    key = (system, canonical_filesystem(filesystem))
    if key == (HostSystem.WINDOWS, "ntfs"):
        if mode is RepairMode.CHECK:
            return ["chkdsk", volume.device_id]
        return ["chkdsk", volume.device_id, "/f"]
    if key == (HostSystem.MACOS, "apfs"):
        verb = "verifyVolume" if mode is RepairMode.CHECK else "repairVolume"
        return ["diskutil", verb, volume.device_id]
    if key == (HostSystem.LINUX, "ext4"):
        flag = "-n" if mode is RepairMode.CHECK else "-p"
        return ["e2fsck", flag, volume.device_id]
    if key == (HostSystem.LINUX, "ntfs"):
        return Failure(
            "no-repair",
            f"{volume.mount_point} is NTFS. Repair it on Windows.",
        )
    return Failure(
        "no-repair",
        f"This computer does not repair {display_filesystem(filesystem)} volumes.",
    )


def refuses_reboot_repair(output: str) -> bool:
    """Return whether Windows would postpone the repair until reboot."""
    lowered = output.casefold()
    return "another process" in lowered or "scheduled" in lowered

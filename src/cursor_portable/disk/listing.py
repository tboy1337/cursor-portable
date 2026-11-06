"""Ask the operating system for mounted volumes."""

from __future__ import annotations

from dataclasses import replace
import json
import logging
from pathlib import Path
import plistlib
import subprocess
from typing import Mapping, assert_never, cast

from cursor_portable.disk.filesystem import canonical_filesystem, probed_filesystem
from cursor_portable.disk.probe import parse_volume_json
from cursor_portable.disk.volume import Volume
from cursor_portable.failure import Failure
from cursor_portable.models import HostSystem

LOGGER = logging.getLogger(__name__)

_WINDOWS = (
    "$usb = New-Object System.Collections.Generic.List[string]; "
    "Get-CimInstance Win32_DiskDrive | Where-Object { $_.InterfaceType -eq 'USB' } | "
    "ForEach-Object { "
    "Get-CimAssociatedInstance -InputObject $_ -ResultClassName Win32_DiskPartition | "
    "ForEach-Object { "
    "Get-CimAssociatedInstance -InputObject $_ -ResultClassName Win32_LogicalDisk | "
    "ForEach-Object { [void]$usb.Add($_.DeviceID) } } }; "
    "$access = @{}; "
    "Get-CimInstance Win32_Volume | ForEach-Object { "
    "$letter = [string]$_.DriveLetter; "
    "if ($letter) { "
    "if (-not $letter.EndsWith(':')) { $letter = $letter + ':' }; "
    "$access[$letter] = $_.Access } }; "
    "$system = $env:SystemDrive; "
    "$disks = @(Get-CimInstance Win32_LogicalDisk | ForEach-Object { "
    "$id = $_.DeviceID; "
    "$removable = ($_.DriveType -eq 2) -or ($usb -contains $id); "
    "$rights = $access[$id]; "
    "$writable = ($null -eq $rights) -or ($rights -eq 0) -or ($rights -eq 2) "
    "-or ($rights -eq 3); "
    "[PSCustomObject]@{ device_id = $id; "
    "mount_point = ($id + '\\'); label = [string]$_.VolumeName; "
    "filesystem = [string]$_.FileSystem; size_bytes = [int64]$_.Size; "
    "removable = $removable; writable = [bool]$writable; "
    "is_system = ($id -eq $system); "
    "is_boot = ($id -eq $system) } }); "
    "$disks | ConvertTo-Json -Compress"
)


def windows_probe_command() -> list[str]:
    """PowerShell command that prints volume JSON."""
    return [
        "powershell",
        "-NoProfile",
        "-Command",
        _WINDOWS,
    ]


def linux_probe_command() -> list[str]:
    """lsblk JSON with byte sizes and the device read-only flag."""
    return [
        "lsblk",
        "-b",
        "-J",
        "-o",
        "PATH,LABEL,FSTYPE,SIZE,RM,MOUNTPOINT,RO",
    ]


def volumes_from_lsblk(text: str) -> tuple[Volume, ...] | Failure:
    """Translate Linux lsblk JSON into volumes."""
    try:
        parsed: object = json.loads(text)
    except json.JSONDecodeError as exc:
        return Failure("volumes", f"lsblk JSON is invalid: {exc}")
    if not isinstance(parsed, dict):
        return Failure("volumes", "lsblk JSON is not an object.")
    block = cast(Mapping[object, object], parsed).get("blockdevices")
    if not isinstance(block, list):
        return Failure("volumes", "lsblk JSON has no blockdevices list.")
    records: list[object] = []
    for device in cast(list[object], block):
        if not isinstance(device, dict):
            continue
        item = cast(Mapping[object, object], device)
        mount = item.get("mountpoint")
        path = item.get("path")
        if not isinstance(mount, str) or not mount or not isinstance(path, str):
            continue
        removable = item.get("rm") in (True, 1, "1")
        raw_filesystem = item.get("fstype")
        filesystem = raw_filesystem if isinstance(raw_filesystem, str) else ""
        records.append(
            {
                "device_id": path,
                "mount_point": mount,
                "label": (
                    item.get("label") if isinstance(item.get("label"), str) else ""
                ),
                "filesystem": probed_filesystem(filesystem),
                "size_bytes": (
                    item.get("size") if isinstance(item.get("size"), int) else 0
                ),
                "removable": removable,
                "writable": item.get("ro") not in (True, 1, "1"),
                "is_system": mount == "/",
                "is_boot": mount == "/boot",
            }
        )
    return parse_volume_json(json.dumps(records))


def volumes_from_diskutil(text: str) -> tuple[Volume, ...] | Failure:
    """Translate ``diskutil list -plist`` into volumes."""
    try:
        parsed: object = plistlib.loads(text.encode("utf-8"))
    except (plistlib.InvalidFileException, ValueError) as exc:
        return Failure("volumes", f"diskutil plist is invalid: {exc}")
    records: list[object] = []
    _collect_diskutil(parsed, external=False, records=records)
    return parse_volume_json(json.dumps(records))


def _external_flag(item: Mapping[object, object], inherited: bool) -> bool:
    external = inherited
    internal = item.get("Internal")
    if isinstance(internal, bool):
        external = not internal
    if internal is True:
        return False
    if item.get("Ejectable") is True:
        return True
    return external


def _append_diskutil_volume(
    item: Mapping[object, object],
    *,
    external: bool,
    records: list[object],
) -> None:
    mount = item.get("MountPoint")
    device = item.get("DeviceIdentifier")
    if not isinstance(mount, str) or not mount or not isinstance(device, str):
        return
    content = item.get("Content")
    filesystem = probed_filesystem(content if isinstance(content, str) else "")
    size = item.get("Size")
    label = item.get("VolumeName")
    records.append(
        {
            "device_id": f"/dev/{device}",
            "mount_point": mount,
            "label": label if isinstance(label, str) else "",
            "filesystem": filesystem,
            "size_bytes": size if isinstance(size, int) else 0,
            "removable": external,
            "writable": _diskutil_writable(item, filesystem),
            "is_system": mount == "/",
            "is_boot": mount == "/",
        }
    )


def _collect_diskutil(node: object, *, external: bool, records: list[object]) -> None:
    if isinstance(node, list):
        for child in node:
            _collect_diskutil(child, external=external, records=records)
        return
    if not isinstance(node, dict):
        return
    item = cast(Mapping[object, object], node)
    own_external = _external_flag(item, external)
    _append_diskutil_volume(item, external=own_external, records=records)
    for key in ("AllDisksAndPartitions", "Partitions"):
        _collect_diskutil(item.get(key), external=own_external, records=records)


def _diskutil_writable(item: Mapping[object, object], filesystem: str) -> bool:
    flag = item.get("Writable")
    if isinstance(flag, bool):
        return flag
    return canonical_filesystem(filesystem) != "ntfs"


def parse_mount_writable(text: str) -> dict[str, bool]:
    """Map a mount point to whether ``/proc/mounts`` lists it read-write."""
    found: dict[str, bool] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 4:
            continue
        mount = parts[1].replace("\\040", " ")
        options = parts[3].split(",")
        found[mount] = "ro" not in options
    return found


def apply_mount_writable(
    volumes: tuple[Volume, ...],
    mounts: str,
) -> tuple[Volume, ...]:
    """Mark volumes read-only when the mount table says so."""
    flags = parse_mount_writable(mounts)
    if not flags:
        return volumes
    updated: list[Volume] = []
    for volume in volumes:
        writable = flags.get(volume.mount_point)
        if writable is None:
            updated.append(volume)
            continue
        updated.append(replace(volume, writable=volume.writable and writable))
    return tuple(updated)


def _read_mounts() -> str:
    try:
        return Path("/proc/mounts").read_text(encoding="utf-8")
    except OSError:
        LOGGER.info("Mount table is not available")
        return ""


def macos_probe_command() -> list[str]:
    """``diskutil`` command that prints a property list of mounted disks."""
    return ["diskutil", "list", "-plist"]


def load_probed_volumes(system: HostSystem) -> tuple[Volume, ...] | Failure:
    """Run the platform probe and parse it."""
    command = probe_command(system)
    if isinstance(command, Failure):
        return command
    LOGGER.info("Probing volumes with %s", command[0])
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or "Volume probe failed."
        return Failure("volumes", detail)
    if system is HostSystem.LINUX:
        probed = volumes_from_lsblk(completed.stdout)
        if isinstance(probed, Failure):
            return probed
        return apply_mount_writable(probed, _read_mounts())
    if system is HostSystem.MACOS:
        return volumes_from_diskutil(completed.stdout)
    return parse_volume_json(completed.stdout)


def probe_command(system: HostSystem) -> list[str] | Failure:
    """Return the volume probe for an operating system."""
    if system is HostSystem.WINDOWS:
        return windows_probe_command()
    if system is HostSystem.LINUX:
        return linux_probe_command()
    if system is HostSystem.MACOS:
        return macos_probe_command()
    assert_never(system)

"""Filesystem each target system can use, and what this computer can do with it."""

from __future__ import annotations

import logging
import shutil

from cursor_portable.failure import Failure
from cursor_portable.models import HostSystem

LOGGER = logging.getLogger(__name__)

_ABSENT = object()

_TARGET = {
    HostSystem.WINDOWS: "ntfs",
    HostSystem.MACOS: "apfs",
    HostSystem.LINUX: "ext4",
}

_DISPLAY = {
    "ntfs": "NTFS",
    "apfs": "APFS",
    "ext4": "ext4",
    "exfat": "exFAT",
}

_LABEL_LIMIT = {
    "ntfs": 32,
    "apfs": 127,
    "ext4": 16,
}

_WRITERS = frozenset(
    {
        (HostSystem.WINDOWS, "ntfs"),
        (HostSystem.MACOS, "apfs"),
        (HostSystem.LINUX, "ext4"),
        (HostSystem.LINUX, "ntfs"),
    }
)

# None means the operating system ships the formatter.
_FORMAT_TOOL: dict[tuple[HostSystem, str], str | None] = {
    (HostSystem.WINDOWS, "ntfs"): None,
    (HostSystem.MACOS, "apfs"): None,
    (HostSystem.LINUX, "ext4"): "mkfs.ext4",
    (HostSystem.LINUX, "ntfs"): "mkfs.ntfs",
}

_PREPARE_ON = {
    "ntfs": "Windows, or Linux with mkfs.ntfs and a read-write NTFS mount",
    "apfs": "macOS",
    "ext4": "Linux",
}


def filesystem_for(target: HostSystem) -> str:
    """Return the canonical filesystem name for a target system."""
    return _TARGET[target]


def canonical_filesystem(name: str) -> str:
    """Fold probe names such as ``ntfs3`` and ``Apple_APFS``."""
    folded = name.casefold().replace("-", "").replace("_", "").replace(" ", "")
    if "ntfs" in folded:
        return "ntfs"
    if "apfs" in folded:
        return "apfs"
    if "ext4" in folded:
        return "ext4"
    if "exfat" in folded:
        return "exfat"
    return folded


def display_filesystem(name: str) -> str:
    """Return the label shown to the user."""
    canonical = canonical_filesystem(name)
    return _DISPLAY.get(canonical, name or "unknown")


def probed_filesystem(name: str) -> str:
    """Store a recognized filesystem under its display name."""
    canonical = canonical_filesystem(name)
    if canonical in _DISPLAY:
        return _DISPLAY[canonical]
    return name


def filesystems_match(actual: str, expected: str) -> bool:
    """Return whether two probe names are the same filesystem."""
    return canonical_filesystem(actual) == canonical_filesystem(expected)


def label_limit(filesystem: str) -> int:
    """Maximum volume name length for a filesystem."""
    return _LABEL_LIMIT.get(canonical_filesystem(filesystem), 11)


def host_can_write(host: HostSystem, filesystem: str) -> bool:
    """Return whether ``host`` can write this filesystem when the mount is read-write."""
    return (host, canonical_filesystem(filesystem)) in _WRITERS


def host_can_format(
    host: HostSystem,
    filesystem: str,
    *,
    available_tools: frozenset[str] | None = None,
) -> bool:
    """Return whether ``host`` can create this filesystem with its own tools."""
    key = (host, canonical_filesystem(filesystem))
    if key not in _FORMAT_TOOL:
        return False
    tool = _FORMAT_TOOL[key]
    if tool is None:
        return True
    if available_tools is not None:
        return tool in available_tools
    found = shutil.which(tool) is not None
    LOGGER.info("Format tool %s on PATH: %s", tool, found)
    return found


def prepare_failure(mount_point: str, filesystem: str) -> Failure:
    """Explain which computer can create this stick."""
    canonical = canonical_filesystem(filesystem)
    display = display_filesystem(filesystem)
    where = _PREPARE_ON.get(canonical, "the system that uses it")
    return Failure(
        "unsupported-filesystem",
        f"{mount_point} cannot be prepared as {display} on this computer. "
        f"Make that stick on {where}.",
    )


def format_failure(host: HostSystem, filesystem: str) -> Failure:
    """Explain a refused format. No command is returned with it."""
    canonical = canonical_filesystem(filesystem)
    tool = _FORMAT_TOOL.get((host, canonical), _ABSENT)
    display = display_filesystem(filesystem)
    if isinstance(tool, str):
        return Failure(
            "missing-tool",
            f"Formatting as {display} needs {tool} on PATH.",
        )
    where = _PREPARE_ON.get(canonical, "the system that uses it")
    return Failure(
        "cannot-format",
        f"This computer cannot format a {display} volume. Make that stick on {where}.",
    )

"""Command lines for extracting official installers. They are never shell strings."""

from __future__ import annotations

from pathlib import Path
from typing import assert_never

from cursor_portable.failure import Failure
from cursor_portable.models import HostSystem


def innoextract_command(installer: Path, destination: Path) -> list[str]:
    """Extract a Windows installer without running it."""
    return [
        "innoextract",
        "--extract",
        "--output-dir",
        str(destination),
        str(installer),
    ]


def appimage_extract_command(appimage: Path) -> list[str]:
    """Extract an AppImage in its own directory."""
    return [str(appimage), "--appimage-extract"]


def dmg_attach_command(disk_image: Path, mount_point: Path) -> list[str]:
    """Attach a macOS disk image read-only at ``mount_point``."""
    return [
        "hdiutil",
        "attach",
        "-nobrowse",
        "-readonly",
        "-mountpoint",
        str(mount_point),
        str(disk_image),
    ]


def dmg_detach_command(mount_point: Path) -> list[str]:
    """Detach a disk image."""
    return ["hdiutil", "detach", str(mount_point)]


def extract_command(
    system: HostSystem,
    installer: Path,
    destination: Path,
) -> list[str] | Failure:
    """Return the extract command for an official IDE file."""
    if system is HostSystem.WINDOWS:
        return innoextract_command(installer, destination)
    if system is HostSystem.LINUX:
        return appimage_extract_command(installer)
    if system is HostSystem.MACOS:
        return dmg_attach_command(installer, destination)
    assert_never(system)

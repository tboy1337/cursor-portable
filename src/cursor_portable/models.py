"""Shared choices for the wizard, packer, and launcher."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import platform


class ProductKind(Enum):
    """What to put on the removable drive."""

    IDE = "ide"
    CLI = "cli"
    BOTH = "both"


class InstallScope(Enum):
    """Windows installer variant. Other systems have a single IDE build."""

    USER = "user"
    SYSTEM = "system"


class ProfileMode(Enum):
    """Whether the portable profile starts empty or from this computer."""

    FRESH = "fresh"
    COPY = "copy"


class HostSystem(Enum):
    """Operating system the tools are running on."""

    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"


def host_system_name() -> HostSystem:
    """Return the current operating system."""
    name = platform.system()
    if name == "Windows":
        return HostSystem.WINDOWS
    if name == "Darwin":
        return HostSystem.MACOS
    if name == "Linux":
        return HostSystem.LINUX
    raise ValueError(f"Unsupported operating system: {name}")


@dataclass(frozen=True)
class InstallChoices:
    """Answers collected before files are written to the drive."""

    product: ProductKind
    scope: InstallScope
    profile: ProfileMode
    volume_device: str
    typed_label: str
    include_state_backup: bool = False
    target_os: HostSystem = HostSystem.WINDOWS
    target_arch: str = "x64"

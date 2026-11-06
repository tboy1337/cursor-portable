"""Official Cursor download targets."""

from __future__ import annotations

from dataclasses import dataclass

from cursor_portable.constants import CURSOR_CLI_INSTALL, CURSOR_DOWNLOAD_API
from cursor_portable.failure import Failure
from cursor_portable.models import HostSystem, InstallScope


@dataclass(frozen=True)
class IdeTarget:
    """One official IDE build."""

    platform_id: str
    system: HostSystem
    arch: str
    scope: InstallScope | None


IDE_TARGETS: tuple[IdeTarget, ...] = (
    IdeTarget("win32-x64-user", HostSystem.WINDOWS, "x64", InstallScope.USER),
    IdeTarget("win32-x64-system", HostSystem.WINDOWS, "x64", InstallScope.SYSTEM),
    IdeTarget("win32-arm64-user", HostSystem.WINDOWS, "arm64", InstallScope.USER),
    IdeTarget("win32-arm64-system", HostSystem.WINDOWS, "arm64", InstallScope.SYSTEM),
    IdeTarget("darwin-arm64", HostSystem.MACOS, "arm64", None),
    IdeTarget("darwin-x64", HostSystem.MACOS, "x64", None),
    IdeTarget("linux-x64", HostSystem.LINUX, "x64", None),
    IdeTarget("linux-arm64", HostSystem.LINUX, "arm64", None),
)


def ide_target(platform_id: str) -> IdeTarget | Failure:
    """Return a known IDE target."""
    for target in IDE_TARGETS:
        if target.platform_id == platform_id:
            return target
    return Failure("unknown-platform", f"Unknown Cursor platform: {platform_id}")


def api_download_url(platform_id: str, track: str = "stable") -> str:
    """Return the official download URL for one platform id."""
    return f"{CURSOR_DOWNLOAD_API}?platform={platform_id}&releaseTrack={track}"


def cli_install_url(*, windows: bool) -> str:
    """Return the official CLI install script URL. The script is never executed."""
    if windows:
        return f"{CURSOR_CLI_INSTALL}?win32=true"
    return CURSOR_CLI_INSTALL


def setup_asset_name(system: HostSystem, arch: str) -> str:
    """File name of the self-extractor published for one host."""
    label = {
        HostSystem.WINDOWS: "Windows",
        HostSystem.MACOS: "macOS",
        HostSystem.LINUX: "Linux",
    }[system]
    suffix = "exe" if system is HostSystem.WINDOWS else "run"
    return f"CursorPortableSetup-{label}-{arch}.{suffix}"

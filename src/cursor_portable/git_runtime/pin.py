"""Choose a relocatable Git build for one stick."""

from __future__ import annotations

from pathlib import Path

from cursor_portable.models import HostSystem

_WINDOWS_RELEASE = "https://api.github.com/repos/git-for-windows/git/releases/latest"
_POSIX_RELEASE = "https://api.github.com/repos/desktop/dugite-native/releases/latest"
_WINDOWS_MARKERS = {
    "x64": "-64-bit.zip",
    "arm64": "-arm64.zip",
}
_POSIX_MARKERS = {
    "macos-x64": "-macOS-x64.tar.gz",
    "macos-arm64": "-macOS-arm64.tar.gz",
    "linux-x64": "-ubuntu-x64.tar.gz",
    "linux-arm64": "-ubuntu-arm64.tar.gz",
}
_HEX = frozenset("0123456789abcdef")


def git_release_url(system: HostSystem) -> str:
    """Return the GitHub latest-release URL for this operating system."""
    if system is HostSystem.WINDOWS:
        return _WINDOWS_RELEASE
    return _POSIX_RELEASE


def git_executable(folder: Path, system: HostSystem) -> Path:
    """Path of the portable ``git`` executable for ``system``."""
    if system is HostSystem.WINDOWS:
        return folder / "cmd" / "git.exe"
    return folder / "bin" / "git"


def git_tree_matches(folder: Path) -> bool:
    """Return whether ``folder`` contains a portable Git tree.

    Windows MinGit ships ``cmd/git.exe``. macOS and Linux builds from
    dugite-native ship ``bin/git``. Either file means the tree can be kept.
    """
    windows = folder / "cmd" / "git.exe"
    posix = folder / "bin" / "git"
    return windows.is_file() or posix.is_file()


def select_git_asset(
    names: tuple[str, ...],
    system: HostSystem,
    arch: str,
) -> str | None:
    """Return the newest Git asset for ``system`` and ``arch``."""
    if system is HostSystem.WINDOWS:
        marker = _WINDOWS_MARKERS.get(arch)
        prefix = "MinGit-"
    else:
        marker = _POSIX_MARKERS.get(f"{system.value}-{arch}")
        prefix = "dugite-native-"
    if marker is None:
        return None
    matches = [
        name
        for name in names
        if name.startswith(prefix)
        and name.endswith(marker)
        and "busybox" not in name.casefold()
    ]
    if not matches:
        return None
    return max(matches, key=_version_key)


def parse_sha256_text(text: str) -> str | None:
    """Return a SHA-256 hex digest from a sibling hash file."""
    stripped = text.strip()
    if not stripped:
        return None
    token = stripped.split()[0].casefold()
    if len(token) == 64 and all(char in _HEX for char in token):
        return token
    return None


def _version_key(name: str) -> tuple[int, ...]:
    """Return the numeric version encoded in a Git asset name.

    Names look like ``MinGit-2.56.0.4-64-bit.zip`` or
    ``dugite-native-v2.56.0-a44aeca-macOS-x64.tar.gz``. Comparing those
    strings would treat 2.9 as newer than 2.10.
    """
    stripped = name
    for prefix in ("dugite-native-v", "dugite-native-", "MinGit-"):
        if stripped.startswith(prefix):
            stripped = stripped.removeprefix(prefix)
            break
    token = stripped.split("-", 1)[0]
    parts: list[int] = []
    for piece in token.split("."):
        if not piece.isdigit():
            return tuple(parts)
        parts.append(int(piece))
    return tuple(parts)

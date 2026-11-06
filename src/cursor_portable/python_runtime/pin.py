"""Choose a relocatable CPython 3.14 build from python-build-standalone."""

from __future__ import annotations

PYTHON_MINOR = "3.14"

ASSET_MARKERS = {
    "windows-x64": "x86_64-pc-windows-msvc-install_only.tar.gz",
    "windows-arm64": "aarch64-pc-windows-msvc-install_only.tar.gz",
    "macos-x64": "x86_64-apple-darwin-install_only.tar.gz",
    "macos-arm64": "aarch64-apple-darwin-install_only.tar.gz",
    "linux-x64": "x86_64-unknown-linux-gnu-install_only.tar.gz",
    "linux-arm64": "aarch64-unknown-linux-gnu-install_only.tar.gz",
}


def select_python_asset(names: tuple[str, ...], marker: str) -> str | None:
    """Return the newest install_only asset whose name contains ``marker``."""
    prefix = f"cpython-{PYTHON_MINOR}."
    matches = [name for name in names if marker in name and name.startswith(prefix)]
    if not matches:
        return None
    return max(matches, key=_version_key)


def _version_key(name: str) -> tuple[int, ...]:
    """Return the numeric CPython version encoded in an asset name.

    Names look like ``cpython-3.14.10+20261026-...`` or ``cpython-3.14.1-...``.
    Comparing those strings would treat 3.14.9 as newer than 3.14.10.
    """
    version = name.removeprefix("cpython-").split("+", 1)[0].split("-", 1)[0]
    parts: list[int] = []
    for piece in version.split("."):
        if not piece.isdigit():
            return tuple(parts)
        parts.append(int(piece))
    return tuple(parts)

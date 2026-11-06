"""Choose a relocatable CPython 3.13 build from python-build-standalone."""

from __future__ import annotations

PYTHON_MINOR = "3.13"

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
    matches = [
        name
        for name in names
        if marker in name and name.startswith(f"cpython-{PYTHON_MINOR}")
    ]
    if not matches:
        return None
    return sorted(matches)[-1]

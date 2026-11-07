"""Portable directory names on the removable drive."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cursor_portable.constants import STICK_DIR_NAME


@dataclass(frozen=True)
class StickLayout:
    """Directories created under ``CursorPortable``."""

    root: Path
    app: Path
    data: Path
    user_data: Path
    extensions: Path
    dot_cursor: Path
    argv: Path
    temp: Path
    python: Path
    git: Path
    cli: Path
    tool: Path
    meta: Path
    version_file: Path
    windows_launcher: Path
    unix_launcher: Path

    @staticmethod
    def from_root(root: Path) -> StickLayout:
        """Build the layout when ``root`` is already the ``CursorPortable`` folder."""
        data = root / "data"
        return StickLayout(
            root=root,
            app=root / "app",
            data=data,
            user_data=data / "user-data",
            extensions=data / "extensions",
            dot_cursor=data / "dot-cursor",
            argv=data / "argv.json",
            temp=data / "tmp",
            python=root / "python",
            git=root / "git",
            cli=root / "cli",
            tool=root / "tool",
            meta=root / "meta",
            version_file=root / "meta" / "version.json",
            windows_launcher=root / "CursorPortable.cmd",
            unix_launcher=root / "cursor-portable",
        )

    @staticmethod
    def from_mount(mount: Path) -> StickLayout:
        """Build the layout for a drive mount point."""
        return StickLayout.from_root(mount / STICK_DIR_NAME)


def ensure_data_dirs(layout: StickLayout) -> None:
    """Create the empty portable data directories."""
    for path in (
        layout.user_data,
        layout.extensions,
        layout.dot_cursor,
        layout.temp,
        layout.meta,
    ):
        path.mkdir(parents=True, exist_ok=True)

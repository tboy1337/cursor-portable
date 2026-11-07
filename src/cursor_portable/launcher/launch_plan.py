"""Arguments used to start the cached Cursor executable."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from cursor_portable.models import HostSystem
from cursor_portable.repack.layout import StickLayout


@dataclass(frozen=True)
class LaunchCommand:
    """A Cursor command line and the environment added for this session."""

    argv: tuple[str, ...]
    extra_env: tuple[tuple[str, str], ...]


def python_segments(layout: StickLayout, system: HostSystem) -> tuple[str, ...]:
    """Directories prepended to PATH so the portable interpreter is first."""
    if system is HostSystem.WINDOWS:
        return (str(layout.python), str(layout.python / "Scripts"))
    return (str(layout.python / "bin"),)


def git_segments(layout: StickLayout, system: HostSystem) -> tuple[str, ...]:
    """Directory prepended to PATH so the portable Git is ahead of the host."""
    if system is HostSystem.WINDOWS:
        return (str(layout.git / "cmd"),)
    return (str(layout.git / "bin"),)


def session_segments(layout: StickLayout, system: HostSystem) -> tuple[str, ...]:
    """Python directories, then Git. Python stays ahead of Git."""
    return python_segments(layout, system) + git_segments(layout, system)


def interpreter_path(layout: StickLayout, system: HostSystem) -> Path:
    """Path of the portable interpreter on this operating system."""
    if system is HostSystem.WINDOWS:
        return layout.python / "python.exe"
    return layout.python / "bin" / "python3"


def cursor_executable(cache_dir: Path, system: HostSystem) -> Path:
    """Executable inside the local app cache."""
    if system is HostSystem.WINDOWS:
        return cache_dir / "Cursor.exe"
    if system is HostSystem.MACOS:
        return cache_dir / "Cursor.app" / "Contents" / "MacOS" / "Cursor"
    return cache_dir / "cursor"


def build_launch(
    layout: StickLayout,
    cache_dir: Path,
    scratch_dir: Path,
    system: HostSystem,
) -> LaunchCommand:
    """Build the Cursor command. The session profile sits beside the executable.

    ``NODE_NO_WARNINGS=1`` is set on the Cursor process. Packaged Electron
    ignores ``NODE_OPTIONS``, and Cursor's Node still calls ``url.parse()``.
    Without this, that deprecation (DEP0169) is printed in the launcher console.
    """
    del layout
    executable = cursor_executable(cache_dir, system)
    data = cache_dir / "data"
    argv = (
        str(executable),
        f"--user-data-dir={data / 'user-data'}",
        f"--extensions-dir={data / 'extensions'}",
        f"--disk-cache-dir={scratch_dir}",
    )
    env = (
        ("TMP", str(scratch_dir)),
        ("TEMP", str(scratch_dir)),
        ("TMPDIR", str(scratch_dir)),
        ("NODE_NO_WARNINGS", "1"),
    )
    return LaunchCommand(argv=argv, extra_env=env)

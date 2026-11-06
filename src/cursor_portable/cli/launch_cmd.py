"""Launch and host uninstall. These talk to the real user profile."""

from __future__ import annotations

from collections.abc import Callable
import logging
import os
from pathlib import Path

from cursor_portable.host.cleanup import (
    cache_directory,
    cleanup_session,
    default_host_root,
    delete_managed_shortcuts,
    delete_runtime_cache,
)
from cursor_portable.launcher.host_watch import (
    read_processes,
    read_shortcuts,
    restore_shortcuts,
    stop_processes,
    write_managed_shortcut,
)
from cursor_portable.launcher.launch_plan import LaunchCommand, build_launch
from cursor_portable.launcher.links import DirectoryLinker
from cursor_portable.launcher.path_store import (
    PathStore,
    path_store_for,
    shell_rc_paths,
)
from cursor_portable.launcher.processes import pid_alive
from cursor_portable.launcher.runner import run_until_exit
from cursor_portable.launcher.session import LaunchHooks, run_launch
from cursor_portable.launcher.session_file import read_session
from cursor_portable.launcher.shortcuts import Shortcut
from cursor_portable.models import HostSystem, host_system_name
from cursor_portable.repack.layout import StickLayout
from cursor_portable.runtime import is_windows

LOGGER = logging.getLogger(__name__)


def _alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if is_windows():
        return pid_alive(pid)
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def _host_root(system: HostSystem) -> Path:
    local = os.environ.get("LOCALAPPDATA")
    local_path = Path(local) if local else None
    return default_host_root(
        Path.home(),
        local_path,
        windows=system is HostSystem.WINDOWS,
    )


def _run_command(command: LaunchCommand, on_tick: Callable[[], None]) -> int:
    return run_until_exit(command, dict(os.environ), on_tick)


def _path_store(system: HostSystem) -> PathStore:
    paths = shell_rc_paths(Path.home(), system)
    return path_store_for(system, None if not paths else paths)


def _hooks(system: HostSystem, layout: StickLayout) -> LaunchHooks:
    launcher = (
        layout.windows_launcher
        if system is HostSystem.WINDOWS
        else layout.unix_launcher
    )
    return LaunchHooks(
        store=_path_store(system),
        linker=DirectoryLinker(),
        run=_run_command,
        processes=read_processes,
        stop=stop_processes,
        read_shortcuts=read_shortcuts,
        restore_shortcuts=restore_shortcuts,
        alive=_alive,
        ensure_shortcut=lambda: write_managed_shortcut(
            launcher,
            windows=system is HostSystem.WINDOWS,
        ),
    )


def _stop_nothing(pids: tuple[int, ...]) -> None:
    LOGGER.info("Installer pids not stopped by the default hook: %s", pids)


def _restore_nothing(items: tuple[Shortcut, ...]) -> None:
    LOGGER.info("Shortcut restore received %s items", len(items))


def launch_root(root: Path) -> int:
    """Start Cursor from a ``CursorPortable`` directory."""
    system = host_system_name()
    layout = StickLayout.from_root(root)
    host = _host_root(system)
    cache = cache_directory(host, layout.root)
    scratch = host / "scratch"
    LOGGER.info("Launching %s with cache %s", layout.root, cache)
    return run_launch(
        layout,
        host,
        cache,
        scratch,
        Path.home() / ".cursor",
        system,
        _hooks(system, layout),
    )


def uninstall_host() -> int:
    """Remove host leftovers. The removable drive is not changed."""
    system = host_system_name()
    host = _host_root(system)
    session_path = host / "session.txt"
    record = read_session(session_path)
    store = _path_store(system)
    if record is not None and _session_busy(record.pid):
        LOGGER.error("Cursor Portable is still running as pid %s", record.pid)
        return 3
    if record is not None:
        cleanup_session(record, store, DirectoryLinker(), delete_cache=True)
        session_path.unlink(missing_ok=True)
    delete_runtime_cache(host)
    removed = delete_managed_shortcuts(_shortcut_candidates())
    LOGGER.info("Removed %s managed shortcuts", len(removed))
    return 0


def _session_busy(pid_text: str) -> bool:
    try:
        previous = int(pid_text)
    except ValueError:
        return False
    return _alive(previous)


def _shortcut_candidates() -> tuple[Path, ...]:
    home = Path.home()
    roots = [
        home / "Desktop",
        home / ".local" / "share" / "applications",
    ]
    appdata = os.environ.get("APPDATA")
    if appdata:
        roots.append(
            Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
        )
    names = ("Cursor Portable.lnk", "cursor-portable.desktop")
    found: list[Path] = []
    for root in roots:
        for name in names:
            candidate = root / name
            if candidate.is_file():
                found.append(candidate)
    return tuple(found)


def describe_launch(root: Path, system: HostSystem) -> tuple[str, ...]:
    """Return the Cursor command without starting it."""
    layout = StickLayout.from_root(root)
    command = build_launch(layout, root / "cache", root / "scratch", system)
    return command.argv

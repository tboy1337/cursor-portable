"""Start the cached app and undo host changes when it exits."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
import logging
import os
from pathlib import Path
import time

from cursor_portable.constants import (
    CHAT_DIRECTORIES,
    CHAT_FILES,
    HOME_BACKUP_SUFFIX,
    SESSION_FILE_NAME,
)
from cursor_portable.failure import Failure
from cursor_portable.git_runtime.pin import git_executable
from cursor_portable.host.cleanup import cleanup_session
from cursor_portable.json_io import load_object
from cursor_portable.launcher.cache_sync import (
    SyncDirection,
    choose_sync,
    sync_after_exit,
)
from cursor_portable.launcher.headless import notify_headless
from cursor_portable.launcher.home_link import apply_home_link, plan_home_link
from cursor_portable.launcher.launch_plan import (
    LaunchCommand,
    build_launch,
    interpreter_path,
    session_segments,
)
from cursor_portable.launcher.links import DirectoryLinker, link_target
from cursor_portable.launcher.path_edit import prepend_segments, separator_for
from cursor_portable.launcher.path_store import (
    PathStore,
    RcPathStore,
    prepend_user_path,
    restore_user_path,
    update_rc_block,
)
from cursor_portable.launcher.processes import ProcessInfo, installer_pids
from cursor_portable.launcher.profile_sync import (
    APP_SKIP_DIRS,
    APP_SKIP_ROOT_FILES,
    PROFILE_SYNC_SECONDS,
    ensure_portable_data,
    portable_data_dir,
    sync_profile,
)
from cursor_portable.launcher.session_file import (
    SessionRecord,
    read_session,
    write_session,
)
from cursor_portable.launcher.shortcuts import (
    Shortcut,
    restorations,
    with_snapshots,
)
from cursor_portable.models import HostSystem, InstallScope, ProductKind
from cursor_portable.profile.settings import set_git_path, set_interpreter
from cursor_portable.repack.copy_tree import copy_tree
from cursor_portable.repack.layout import StickLayout, ensure_data_dirs
from cursor_portable.repack.meta import (
    read_cursor_version,
    read_version_fields,
    write_version_file,
)
from cursor_portable.repack.product_json import (
    find_product_json,
    read_product_version,
    strip_product_file,
)

LOGGER = logging.getLogger(__name__)
SHORTCUT_WATCH_SECONDS = 5.0


def _noop() -> None:
    """Hook used when a platform does not need extra work."""
    return None


@dataclass
class LaunchHooks:
    """Platform operations supplied by the CLI or by tests."""

    store: PathStore
    linker: DirectoryLinker
    run: Callable[[LaunchCommand, Callable[[], None]], int]
    processes: Callable[[], tuple[ProcessInfo, ...]]
    stop: Callable[[tuple[int, ...]], None]
    read_shortcuts: Callable[[], tuple[Shortcut, ...]]
    restore_shortcuts: Callable[[tuple[Shortcut, ...]], None]
    alive: Callable[[int], bool]
    ensure_shortcut: Callable[[], None] = field(default=_noop)


def _cache_version(cache_dir: Path) -> str | None:
    return read_cursor_version(cache_dir / "version.json")


def _write_cache_version(
    cache_dir: Path,
    version: str,
    scope: InstallScope,
    product: ProductKind,
) -> None:
    write_version_file(
        cache_dir / "version.json",
        cursor_version=version,
        commit="",
        scope=scope,
        product=product,
    )


def _sync_app(source: Path, destination: Path) -> str:
    copy_tree(
        source,
        destination,
        skip_dirs=APP_SKIP_DIRS,
        skip_root_files=APP_SKIP_ROOT_FILES,
    )
    product = find_product_json(destination)
    version = "unknown"
    if product is None:
        return version
    strip_product_file(product)
    loaded = load_object(product.read_text(encoding="utf-8"))
    if not isinstance(loaded, dict):
        return version
    return read_product_version(loaded)


def _apply_direction(
    direction: SyncDirection,
    layout: StickLayout,
    cache_dir: Path,
) -> str:
    recorded, commit, scope, product = read_version_fields(layout.version_file)
    if direction is SyncDirection.STICK_TO_CACHE:
        version = _sync_app(layout.app, cache_dir)
        if version == "unknown":
            version = recorded
        _write_cache_version(cache_dir, version, scope, product)
        return version
    if direction is SyncDirection.CACHE_TO_STICK:
        version = _sync_app(cache_dir, layout.app)
        write_version_file(
            layout.version_file,
            cursor_version=version,
            commit=commit,
            scope=scope,
            product=product,
        )
        _write_cache_version(cache_dir, version, scope, product)
        return version
    cached = _cache_version(cache_dir)
    if cached is None:
        return recorded
    return cached


def _cache_ready(cache_dir: Path) -> bool:
    names = ("Cursor.exe", "cursor", "Cursor.app")
    return any((cache_dir / name).exists() for name in names)


class _SessionWatch:
    """Stop installers, restore shortcuts, and copy the profile back."""

    def __init__(
        self,
        hooks: LaunchHooks,
        before: tuple[Shortcut, ...],
        roots: tuple[str, ...],
        layout: StickLayout,
        cache_dir: Path,
    ) -> None:
        self._hooks = hooks
        self._before = before
        self._roots = roots
        self._layout = layout
        self._cache_dir = cache_dir
        self._next_shortcut = 0.0
        self._next_profile = time.monotonic() + PROFILE_SYNC_SECONDS

    def tick(self) -> None:
        """One watcher pass. Shortcut and profile work are throttled."""
        self._stop_installers()
        now = time.monotonic()
        if now >= self._next_shortcut:
            self._restore()
            self._next_shortcut = now + SHORTCUT_WATCH_SECONDS
        if now >= self._next_profile:
            self._sync_profile(include_chats=False)
            self._next_profile = now + PROFILE_SYNC_SECONDS

    def finish(self) -> None:
        """Run every check once more as Cursor exits."""
        self._stop_installers()
        self._restore()
        self._sync_profile(include_chats=True)

    def _stop_installers(self) -> None:
        pids = installer_pids(self._hooks.processes())
        if not pids:
            return
        LOGGER.warning("Stopping Cursor installer processes: %s", pids)
        self._hooks.stop(pids)

    def _restore(self) -> None:
        current = self._hooks.read_shortcuts()
        changes = restorations(self._before, current, frozenset(), self._roots)
        if not changes:
            return
        LOGGER.info("Restoring %s shortcut targets", len(changes))
        self._hooks.restore_shortcuts(changes)

    def _sync_profile(self, *, include_chats: bool) -> None:
        skip_names: frozenset[str] = frozenset()
        skip_dirs: frozenset[str] = frozenset()
        if not include_chats:
            skip_names = CHAT_FILES
            skip_dirs = CHAT_DIRECTORIES
        sync_profile(
            portable_data_dir(self._cache_dir),
            self._layout.data,
            skip_names=skip_names,
            skip_dirs=skip_dirs,
        )


def _heal_previous(
    session_path: Path,
    hooks: LaunchHooks,
    pid: int,
    layout: StickLayout,
    cache_dir: Path,
) -> int | None:
    existing = read_session(session_path)
    if existing is None or not existing.pid:
        return None
    try:
        previous = int(existing.pid)
    except ValueError:
        previous = -1
    if previous == pid:
        return None
    if previous > 0 and hooks.alive(previous):
        message = f"Cursor Portable is already running as pid {previous}"
        LOGGER.error("%s", message)
        notify_headless(message)
        return 3
    source = Path(existing.cache_dir) if existing.cache_dir else cache_dir
    destination: Path | None = layout.data
    if existing.stick_root and not _same_stick(existing.stick_root, layout.root):
        old_root = Path(existing.stick_root)
        if old_root.is_dir():
            LOGGER.info("Returning the previous session profile to %s", old_root)
            destination = StickLayout.from_root(old_root).data
        else:
            LOGGER.info("Previous stick %s is not mounted", existing.stick_root)
            destination = None
    else:
        LOGGER.info("Copying the previous session profile back to the drive")
    if destination is not None:
        sync_profile(portable_data_dir(source), destination)
    cleanup_session(existing, hooks.store, hooks.linker, delete_cache=False)
    session_path.unlink(missing_ok=True)
    return None


def _same_stick(recorded: str, current: Path) -> bool:
    return os.path.normcase(recorded) == os.path.normcase(str(current))


def _undo_added_path(
    store: PathStore,
    added: tuple[str, ...],
    separator: str,
) -> None:
    if isinstance(store, RcPathStore):
        for path in store.paths:
            update_rc_block(path, "", remove=True)
        return
    if added:
        restore_user_path(store, added, separator)


def _launch_command(
    layout: StickLayout,
    cache_dir: Path,
    scratch_dir: Path,
    system: HostSystem,
) -> LaunchCommand:
    command = build_launch(layout, cache_dir, scratch_dir, system)
    separator = separator_for(system)
    path_value = prepend_segments(
        os.environ.get("PATH", ""),
        session_segments(layout, system),
        separator,
    )
    return LaunchCommand(
        argv=command.argv,
        extra_env=command.extra_env + (("PATH", path_value),),
    )


def _prepare_launch(
    layout: StickLayout,
    cache_dir: Path,
    scratch_dir: Path,
    system: HostSystem,
) -> str:
    ensure_data_dirs(layout)
    cache_dir.mkdir(parents=True, exist_ok=True)
    scratch_dir.mkdir(parents=True, exist_ok=True)
    ensure_portable_data(cache_dir)
    pending = cache_dir / ".pending-sync"
    stick_version = read_cursor_version(layout.version_file) or "unknown"
    direction = choose_sync(
        stick_version=stick_version,
        cache_version=_cache_version(cache_dir),
        cache_ready=_cache_ready(cache_dir),
        pending_sync=pending.is_file(),
    )
    LOGGER.info("Cache sync: %s", direction.value)
    launched = _apply_direction(direction, layout, cache_dir)
    pending.write_text("pending\n", encoding="utf-8")
    sync_profile(layout.data, portable_data_dir(cache_dir))
    settings = portable_data_dir(cache_dir) / "user-data" / "User" / "settings.json"
    set_interpreter(settings, interpreter_path(layout, system))
    executable = git_executable(layout.git, system)
    if executable.is_file():
        set_git_path(settings, executable)
    return launched


def _open_session(
    layout: StickLayout,
    host_root: Path,
    cache_dir: Path,
    dot_cursor: Path,
    system: HostSystem,
    hooks: LaunchHooks,
    pid: int,
) -> tuple[SessionRecord, _SessionWatch] | Failure:
    separator = separator_for(system)
    added = prepend_user_path(hooks.store, session_segments(layout, system), separator)
    profile = portable_data_dir(cache_dir) / "dot-cursor"
    home = plan_home_link(
        dot_cursor,
        profile,
        current_target=link_target(dot_cursor),
        link_exists=dot_cursor.exists(),
        backup_exists=Path(str(dot_cursor) + HOME_BACKUP_SUFFIX).exists(),
    )
    home_failure = apply_home_link(home, hooks.linker)
    if home_failure is not None:
        LOGGER.error("%s", home_failure.message)
        notify_headless(home_failure.message)
        _undo_added_path(hooks.store, added, separator)
        return home_failure
    before = with_snapshots(
        hooks.read_shortcuts(),
        host_root / "shortcut-snapshots",
    )
    hooks.ensure_shortcut()
    roots = (str(layout.app), str(cache_dir), str(layout.root))
    record = SessionRecord(
        path_separator=separator,
        path_segments=list(added),
        path_kind=hooks.store.read()[1],
        home_link=str(dot_cursor),
        home_backup=str(home.backup),
        home_target=str(profile),
        cache_dir=str(cache_dir),
        stick_root=str(layout.root),
        pid=str(pid),
    )
    write_session(host_root / SESSION_FILE_NAME, record)
    return record, _SessionWatch(hooks, before, roots, layout, cache_dir)


def _finish_launch(
    layout: StickLayout,
    cache_dir: Path,
    launched: str,
    watch: _SessionWatch,
    record: SessionRecord,
    hooks: LaunchHooks,
    session_path: Path,
) -> None:
    try:
        after = _cache_version(cache_dir)
        if sync_after_exit(launched_version=launched, cache_version=after) is (
            SyncDirection.CACHE_TO_STICK
        ):
            LOGGER.info("Copying in-app update back to the removable drive")
            _apply_direction(SyncDirection.CACHE_TO_STICK, layout, cache_dir)
        try:
            watch.finish()
        except Exception:
            LOGGER.exception("Watcher finish failed")
    finally:
        cleanup_session(record, hooks.store, hooks.linker, delete_cache=False)
        (cache_dir / ".pending-sync").unlink(missing_ok=True)
        session_path.unlink(missing_ok=True)


def run_launch(
    layout: StickLayout,
    host_root: Path,
    cache_dir: Path,
    scratch_dir: Path,
    dot_cursor: Path,
    system: HostSystem,
    hooks: LaunchHooks,
    *,
    pid: int | None = None,
) -> int:
    """Launch Cursor from the local cache and undo host changes afterwards."""
    current_pid = os.getpid() if pid is None else pid
    session_path = host_root / SESSION_FILE_NAME
    busy = _heal_previous(session_path, hooks, current_pid, layout, cache_dir)
    if busy is not None:
        return busy
    launched = _prepare_launch(layout, cache_dir, scratch_dir, system)
    opened = _open_session(
        layout,
        host_root,
        cache_dir,
        dot_cursor,
        system,
        hooks,
        current_pid,
    )
    if isinstance(opened, Failure):
        (cache_dir / ".pending-sync").unlink(missing_ok=True)
        return 1
    record, watch = opened
    code = 1
    try:
        code = hooks.run(
            _launch_command(layout, cache_dir, scratch_dir, system),
            watch.tick,
        )
    finally:
        _finish_launch(
            layout,
            cache_dir,
            launched,
            watch,
            record,
            hooks,
            session_path,
        )
    return code

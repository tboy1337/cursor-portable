"""Run Cursor and invoke a watcher until it exits."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import logging
import subprocess
import time

from cursor_portable.launcher.launch_plan import LaunchCommand

LOGGER = logging.getLogger(__name__)


def run_until_exit(
    command: LaunchCommand,
    environment: Mapping[str, str],
    on_tick: Callable[[], None],
    *,
    watch_seconds: float | None = None,
    poll_seconds: float = 1.0,
) -> int:
    """Start Cursor and tick the watcher until it exits.

    ``watch_seconds`` limits how long ticks run. ``None`` watches the whole
    session. ``0`` ticks once after the process has exited.
    """
    merged = dict(environment)
    for key, value in command.extra_env:
        merged[key] = value
    executable = command.argv[0] if command.argv else ""
    LOGGER.info(
        "Starting %s with NODE_NO_WARNINGS=%s",
        executable,
        merged.get("NODE_NO_WARNINGS", ""),
    )
    process = subprocess.Popen(list(command.argv), env=merged)
    deadline = None if watch_seconds is None else time.monotonic() + watch_seconds
    while process.poll() is None:
        if deadline is None or time.monotonic() < deadline:
            _safe_tick(on_tick)
        time.sleep(poll_seconds)
    _safe_tick(on_tick)
    code = process.returncode
    if code is None:
        return 1
    return code


def _safe_tick(on_tick: Callable[[], None]) -> None:
    try:
        on_tick()
    except Exception:
        LOGGER.exception("Launch watcher tick failed")

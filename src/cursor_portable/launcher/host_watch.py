"""Read and restore host shortcuts, and stop Cursor installer children."""

from __future__ import annotations

import logging
import os
from pathlib import Path
import signal
import subprocess

from cursor_portable.constants import DESKTOP_FILE_NAME
from cursor_portable.launcher.processes import ProcessInfo
from cursor_portable.launcher.reports import (
    parse_ps,
    parse_shortcut_report,
    parse_tasklist_csv,
)
from cursor_portable.launcher.shortcuts import Shortcut
from cursor_portable.runtime import is_windows

LOGGER = logging.getLogger(__name__)

_WINDOWS_SHORTCUTS = r"""
$shell = New-Object -ComObject WScript.Shell
$roots = @(
  (Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu'),
  (Join-Path $env:ProgramData 'Microsoft\Windows\Start Menu'),
  (Join-Path $env:USERPROFILE 'Desktop'),
  (Join-Path $env:PUBLIC 'Desktop')
)
foreach ($root in $roots) {
  if (-not $root -or -not (Test-Path -LiteralPath $root)) { continue }
  Get-ChildItem -LiteralPath $root -Filter *.lnk -Recurse -ErrorAction SilentlyContinue | ForEach-Object {
    $shortcut = $shell.CreateShortcut($_.FullName)
    Write-Output ($_.FullName + '|' + $shortcut.TargetPath)
  }
}
"""


def write_managed_shortcut(launcher: Path, *, windows: bool) -> None:
    """Create the Cursor Portable shortcut. It is never named Cursor."""
    if windows:
        _write_windows_launcher(launcher)
        return
    destination = Path.home() / ".local" / "share" / "applications" / DESKTOP_FILE_NAME
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        "\n".join(
            (
                "[Desktop Entry]",
                "Type=Application",
                "Name=Cursor Portable",
                f"Exec={launcher}",
                "Terminal=false",
                "",
            )
        ),
        encoding="utf-8",
    )
    LOGGER.info("Wrote %s", destination)


def _write_windows_launcher(launcher: Path) -> None:
    appdata = os.environ.get("APPDATA")
    if not appdata:
        LOGGER.warning("APPDATA is unset. No Start Menu shortcut was created.")
        return
    destination = (
        Path(appdata)
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "Cursor Portable.lnk"
    )
    destination.parent.mkdir(parents=True, exist_ok=True)
    location = str(destination).replace("'", "''")
    target = str(launcher).replace("'", "''")
    # 7 opens the console minimized. The launcher closes that window.
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$shortcut = $shell.CreateShortcut('{location}'); "
        f"$shortcut.TargetPath = '{target}'; "
        "$shortcut.WindowStyle = 7; "
        "$shortcut.Save()"
    )
    _run(["powershell", "-NoProfile", "-Command", script])
    LOGGER.info("Wrote %s", destination)


def windows_shortcut_command() -> list[str]:
    """PowerShell command that prints ``path|target`` lines."""
    return ["powershell", "-NoProfile", "-Command", _WINDOWS_SHORTCUTS]


def desktop_exec(text: str) -> str:
    """Return the first command token of an ``Exec=`` line."""
    for line in text.splitlines():
        if line.startswith("Exec="):
            return line[5:].strip().split(" ", 1)[0]
    return ""


def desktop_shortcuts(home: Path) -> tuple[Shortcut, ...]:
    """Read ``.desktop`` files under the home directory."""
    roots = (
        home / "Desktop",
        home / ".local" / "share" / "applications",
    )
    items: list[Shortcut] = []
    for root in roots:
        if not root.is_dir():
            continue
        for path in sorted(root.glob("*.desktop")):
            target = desktop_exec(path.read_text(encoding="utf-8", errors="replace"))
            if target:
                items.append(Shortcut(str(path), target))
    return tuple(items)


def restore_desktop_text(text: str, target: str) -> str:
    """Point the first ``Exec=`` line back at ``target``."""
    lines = text.splitlines()
    updated: list[str] = []
    replaced = False
    for line in lines:
        if not replaced and line.startswith("Exec="):
            updated.append(f"Exec={target}")
            replaced = True
            continue
        updated.append(line)
    suffix = "\n" if text.endswith("\n") else ""
    return "\n".join(updated) + suffix


def read_processes() -> tuple[ProcessInfo, ...]:
    """List processes. A probe failure returns an empty list."""
    if is_windows():
        command = ["tasklist", "/fo", "csv", "/nh"]
        parser = parse_tasklist_csv
    else:
        command = ["ps", "-A", "-o", "pid=,comm="]
        parser = parse_ps
    completed = _run(command)
    if completed is None:
        return ()
    return parser(completed)


def read_shortcuts() -> tuple[Shortcut, ...]:
    """Snapshot Start Menu, desktop, and ``.desktop`` shortcuts."""
    if is_windows():
        completed = _run(windows_shortcut_command())
        if completed is None:
            return ()
        return parse_shortcut_report(completed)
    return desktop_shortcuts(Path.home())


def restore_shortcuts(items: tuple[Shortcut, ...]) -> None:
    """Put shortcut files back. A byte snapshot wins over rewriting the target."""
    for item in items:
        if _restore_snapshot(item):
            continue
        if is_windows():
            _restore_windows_shortcut(item)
            continue
        _restore_desktop_shortcut(item)


def _restore_snapshot(item: Shortcut) -> bool:
    if not item.snapshot:
        return False
    snapshot = Path(item.snapshot)
    if not snapshot.is_file():
        return False
    destination = Path(item.location)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(snapshot.read_bytes())
    LOGGER.info("Restored shortcut bytes %s", destination)
    return True


def stop_processes(pids: tuple[int, ...]) -> None:
    """Stop installer processes. The running Cursor editor is not in this list."""
    for pid in pids:
        LOGGER.warning("Stopping installer pid %s", pid)
        if is_windows():
            _run(["taskkill", "/PID", str(pid), "/T", "/F"])
            continue
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError as exc:
            LOGGER.error("Could not stop pid %s: %s", pid, exc)


def _restore_windows_shortcut(item: Shortcut) -> None:
    location = item.location.replace("'", "''")
    target = item.target.replace("'", "''")
    script = (
        "$shell = New-Object -ComObject WScript.Shell; "
        f"$shortcut = $shell.CreateShortcut('{location}'); "
        f"$shortcut.TargetPath = '{target}'; "
        "$shortcut.Save()"
    )
    _run(["powershell", "-NoProfile", "-Command", script])


def _restore_desktop_shortcut(item: Shortcut) -> None:
    path = Path(item.location)
    if not path.is_file():
        return
    text = path.read_text(encoding="utf-8", errors="replace")
    path.write_text(restore_desktop_text(text, item.target), encoding="utf-8")


def _run(command: list[str]) -> str | None:
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip()
        _warn_probe_once(command[0], detail)
        return None
    return completed.stdout


def _warn_probe_once(program: str, detail: str) -> None:
    warned = getattr(_warn_probe_once, "done", False)
    if not warned:
        LOGGER.warning(
            "A shortcut or process probe failed. "
            "Shortcut restore pauses until the probe works."
        )
        setattr(_warn_probe_once, "done", True)
    LOGGER.error("%s failed: %s", program, detail)

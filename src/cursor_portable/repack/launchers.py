"""Launcher scripts written onto the removable drive."""

from __future__ import annotations

import logging
from pathlib import Path

LOGGER = logging.getLogger(__name__)

# A double-clicked .cmd keeps a console open for the whole Cursor session.
# Closing that window kills the process that restores PATH and ~/.cursor.
# CMDCMDLINE is a cmd dynamic variable, so PowerShell reads the parent command
# line instead. Explorer puts this file there; an open prompt does not. pythonw
# then keeps the session alive with no window. --console stays attached.
WINDOWS_LAUNCHER = """@echo off
rem Purpose: start the portable Cursor session and restore the host when it exits.
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONPATH=%~dp0tool"
set "CURSOR_PORTABLE_HIDDEN=0"
if /I "%~1"=="--console" goto :console
rem CURSOR_PORTABLE_CONSOLE is optional. 1 keeps this console attached.
rem LINT:IGNORE E006
if /I "%CURSOR_PORTABLE_CONSOLE%"=="1" goto :console
set "CURSOR_PORTABLE_PYTHONW=%~dp0python\\pythonw.exe"
set "CURSOR_PORTABLE_ROOT=%~dp0."
rem 3 means pythonw is missing. 2 means this console belongs to the caller.
rem The PowerShell -Command is one line so cmd does not split it.
rem LINT:IGNORE S020
powershell.exe -NoProfile -NonInteractive -Command "__START_HEADLESS__"
if errorlevel 3 goto :hidden
if errorlevel 1 goto :console
exit /b 0

:hidden
set "CURSOR_PORTABLE_HIDDEN=1"
call :HideConsole

:console
"%~dp0python\\python.exe" -m cursor_portable launch --root "%~dp0."
set "EXIT_CODE=%ERRORLEVEL%"
if not "%CURSOR_PORTABLE_HIDDEN%"=="1" goto :done
if "%EXIT_CODE%"=="0" goto :done
call :ShowFailedConsole

:done
rem EXIT_CODE is local. Expanding it on this line happens before endlocal clears it.
endlocal & exit /b %EXIT_CODE%

rem Hide this console. A missing pythonw still reaches the attached launch.
:HideConsole
rem The PowerShell -Command is one line so cmd does not split it.
rem LINT:IGNORE S020
powershell.exe -NoProfile -NonInteractive -Command "__HIDE_CONSOLE__"
goto :eof

rem Show this console when a hidden launch stops with an error.
:ShowFailedConsole
rem The PowerShell -Command is one line so cmd does not split it.
rem LINT:IGNORE S020
powershell.exe -NoProfile -NonInteractive -Command "__SHOW_CONSOLE__"
echo.
echo Cursor Portable stopped with exit code %EXIT_CODE%.
pause
goto :eof
"""

UNIX_LAUNCHER = """#!/bin/bash
set -euo pipefail
ROOT="$(CDPATH='' cd -- "$(dirname -- "$0")" && pwd)"
export PYTHONPATH="${ROOT}/tool"
if [[ -x "${ROOT}/python/bin/python3" ]]; then
    PY="${ROOT}/python/bin/python3"
else
    PY="${ROOT}/python/python.exe"
fi
exec "$PY" -m cursor_portable launch --root "$ROOT"
"""

_HEADLESS_LINES = (
    "$me = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $PID)",
    "$cmd = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $me.ParentProcessId)",
    "if (-not $cmd -or $cmd.CommandLine -notlike '*CursorPortable.cmd*') { exit 2 }",
    "if (-not (Test-Path -LiteralPath $env:CURSOR_PORTABLE_PYTHONW)) { exit 3 }",
    "$env:CURSOR_PORTABLE_HEADLESS='1'",
    (
        "Start-Process -FilePath $env:CURSOR_PORTABLE_PYTHONW "
        "-WorkingDirectory $env:CURSOR_PORTABLE_ROOT -ArgumentList "
        "@('-m','cursor_portable','launch','--root',$env:CURSOR_PORTABLE_ROOT)"
    ),
    "exit 0",
)

_PLACEHOLDERS = {
    "Start-HeadlessCursor.ps1": "__START_HEADLESS__",
    "Hide-ConsoleWindow.ps1": "__HIDE_CONSOLE__",
    "Show-ConsoleWindow.ps1": "__SHOW_CONSOLE__",
}


def _show_window_lines(action: int, type_name: str) -> tuple[str, ...]:
    """PowerShell statements that show or hide this console. No double quotes."""
    signature = (
        "$signature = '[DllImport(' + $q + 'user32.dll' + $q + ')] "
        "public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow); "
        "[DllImport(' + $q + 'kernel32.dll' + $q + ')] "
        "public static extern IntPtr GetConsoleWindow();'"
    )
    return (
        "$q = [char]34",
        signature,
        (
            "$console = Add-Type -MemberDefinition $signature -Name "
            + type_name
            + " -Namespace CursorPortableLauncher -PassThru"
        ),
        "$handle = $console::GetConsoleWindow()",
        "if ($handle -eq [IntPtr]::Zero) { exit 1 }",
        "[void]$console::ShowWindow($handle, " + str(action) + ")",
        "exit 0",
    )


def _powershell_snippets() -> tuple[tuple[str, tuple[str, ...]], ...]:
    return (
        ("Start-HeadlessCursor.ps1", _HEADLESS_LINES),
        ("Hide-ConsoleWindow.ps1", _show_window_lines(0, "Hide")),
        ("Show-ConsoleWindow.ps1", _show_window_lines(9, "Show")),
    )


def _inline_powershell(lines: tuple[str, ...]) -> str:
    return "; ".join(lines)


def windows_launcher_text() -> str:
    """Return the Windows launcher."""
    text = WINDOWS_LAUNCHER
    for name, lines in _powershell_snippets():
        text = text.replace(_PLACEHOLDERS[name], _inline_powershell(lines))
    return text.replace("\n", "\r\n")


def unix_launcher_text() -> str:
    """Return the macOS and Linux launcher."""
    return UNIX_LAUNCHER


def write_launcher_scripts(directory: Path) -> None:
    """Write the generated launchers and the PowerShell snippets they run."""
    directory.mkdir(parents=True, exist_ok=True)
    payloads = {
        "CursorPortable.cmd": windows_launcher_text().encode("utf-8"),
        "cursor-portable": unix_launcher_text().encode("utf-8"),
    }
    for name, lines in _powershell_snippets():
        payloads[name] = ("\n".join(lines) + "\n").encode("utf-8")
    for name, payload in payloads.items():
        path = directory / name
        path.write_bytes(payload)
        LOGGER.info("Wrote launcher script %s (%s bytes)", path, len(payload))

"""Run a disk command, asking Windows for approval only for that command."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from cursor_portable.disk.elevation import elevation_failure, elevation_ready
from cursor_portable.failure import Failure
from cursor_portable.models import HostSystem, host_system_name
from cursor_portable.runtime import is_windows
from cursor_portable.shell import materialize_script

LOGGER = logging.getLogger(__name__)

# Mirrored in tests/shell/Invoke-ElevatedCommand.ps1.
_ELEVATED = r"""#Requires -Version 5.1
param(
    [Parameter(Mandatory = $true)]
    [string]$CommandPath,
    [Parameter(Mandatory = $true)]
    [string]$OutputPath,
    [Parameter(Mandatory = $true)]
    [string]$CodePath
)
$ErrorActionPreference = 'Stop'
# ReadAllText accepts UTF-8 without a BOM. Get-Content -Encoding UTF8 on 5.1 does not.
$payload = [System.IO.File]::ReadAllText($CommandPath) | ConvertFrom-Json
$psi = New-Object System.Diagnostics.ProcessStartInfo
$psi.FileName = [string]$payload.executable
$psi.Arguments = [string]$payload.arguments
$psi.UseShellExecute = $false
$psi.RedirectStandardOutput = $true
$psi.RedirectStandardError = $true
$psi.CreateNoWindow = $true
$proc = New-Object System.Diagnostics.Process
$proc.StartInfo = $psi
[void]$proc.Start()
$outTask = $proc.StandardOutput.ReadToEndAsync()
$errTask = $proc.StandardError.ReadToEndAsync()
$proc.WaitForExit()
Set-Content -LiteralPath $OutputPath -Value $outTask.Result -Encoding utf8
Set-Content -LiteralPath ($OutputPath + '.err') -Value $errTask.Result -Encoding utf8
Set-Content -LiteralPath $CodePath -Value $proc.ExitCode -Encoding ascii
exit $proc.ExitCode
"""

_CANCELLED = 1223
_SEE_MASK_NOCLOSEPROCESS = 0x00000040
_WAIT_INFINITE = 0xFFFFFFFF
_SW_HIDE = 0


class _ShellExecuteInfo(ctypes.Structure):
    """The fields ShellExecuteExW reads. The last handle is the process."""

    _fields_ = (
        ("cbSize", wintypes.DWORD),
        ("fMask", ctypes.c_ulong),
        ("hwnd", wintypes.HWND),
        ("lpVerb", wintypes.LPCWSTR),
        ("lpFile", wintypes.LPCWSTR),
        ("lpParameters", wintypes.LPCWSTR),
        ("lpDirectory", wintypes.LPCWSTR),
        ("nShow", ctypes.c_int),
        ("hInstApp", wintypes.HINSTANCE),
        ("lpIDList", ctypes.c_void_p),
        ("lpClass", wintypes.LPCWSTR),
        ("hkeyClass", wintypes.HKEY),
        ("dwHotKey", wintypes.DWORD),
        ("hMonitor", wintypes.HANDLE),
        ("hProcess", wintypes.HANDLE),
    )


def quote_windows_arg(value: str) -> str:
    """Quote one argument for ``CommandLineToArgvW``."""
    if value == "":
        return '""'
    if not any(char in value for char in ' \t"'):
        return value
    quoted = ['"']
    slashes = 0
    for char in value:
        if char == "\\":
            slashes += 1
            continue
        if char == '"':
            quoted.append("\\" * (slashes * 2 + 1))
            quoted.append('"')
            slashes = 0
            continue
        if slashes:
            quoted.append("\\" * slashes)
            slashes = 0
        quoted.append(char)
    if slashes:
        quoted.append("\\" * (slashes * 2))
    quoted.append('"')
    return "".join(quoted)


def capture_command(
    command: tuple[str, ...],
    *,
    elevate: bool,
) -> tuple[int, str] | Failure:
    """Run ``command``. On Windows, elevation is a UAC prompt for this command."""
    system = host_system_name()
    if elevate and not elevation_ready(system):
        if system is HostSystem.WINDOWS and is_windows():
            LOGGER.info("Requesting approval to run %s", command[0])
            return _windows_elevated(command)
        blocked = elevation_failure(system)
        if blocked is not None:
            return blocked
    return _subprocess_capture(command)


def _subprocess_capture(command: tuple[str, ...]) -> tuple[int, str] | Failure:
    LOGGER.info("Running %s", command)
    try:
        completed = subprocess.run(
            list(command),
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError as exc:
        return Failure("disk", f"{command[0]} could not be started: {exc}")
    output = f"{completed.stdout}\n{completed.stderr}"
    return completed.returncode, output


def _windows_tool(command: tuple[str, ...]) -> tuple[str, ...]:
    name = Path(command[0]).name.casefold()
    if name not in {"format.com", "chkdsk", "chkdsk.exe"}:
        return command
    root = os.environ.get("SystemRoot", "")
    if not root:
        return command
    file_name = "format.com" if name == "format.com" else "chkdsk.exe"
    candidate = Path(root) / "System32" / file_name
    if not candidate.is_file():
        return command
    return (str(candidate), *command[1:])


def _powershell() -> Path:
    root = os.environ.get("SystemRoot", r"C:\Windows")
    return Path(root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"


def _windows_elevated(command: tuple[str, ...]) -> tuple[int, str] | Failure:
    resolved = _windows_tool(command)
    with tempfile.TemporaryDirectory(prefix="cursor-portable-elevate-") as temporary:
        directory = Path(temporary)
        command_file = directory / "command.json"
        payload = {
            "executable": resolved[0],
            "arguments": " ".join(quote_windows_arg(part) for part in resolved[1:]),
        }
        command_file.write_text(json.dumps(payload), encoding="utf-8")
        script = materialize_script("Invoke-ElevatedCommand.ps1", _ELEVATED)
        output = directory / "output.txt"
        code_path = directory / "code.txt"
        parameters = " ".join(
            (
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-File",
                quote_windows_arg(str(script)),
                "-CommandPath",
                quote_windows_arg(str(command_file)),
                "-OutputPath",
                quote_windows_arg(str(output)),
                "-CodePath",
                quote_windows_arg(str(code_path)),
            )
        )
        launched = _shell_execute(
            str(_powershell()),
            parameters,
            directory,
            "runas",
        )
        if isinstance(launched, Failure):
            return launched
        return _read_elevated_result(output, code_path, launched)


def _win_dll(name: str) -> ctypes.CDLL:
    """Load a Windows DLL. ``WinDLL`` is omitted from the non-Windows stubs."""
    load = getattr(ctypes, "WinDLL", None)
    if load is None:
        raise OSError(f"WinDLL is only available on Windows, not {sys.platform}.")
    return load(name, use_last_error=True)  # type: ignore[no-any-return]


def _last_error() -> int:
    """Return the last Windows error. This function exists only on Windows."""
    read = getattr(ctypes, "get_last_error", None)
    if read is None:
        raise OSError(
            f"Windows errors are only available on Windows, not {sys.platform}."
        )
    return read()  # type: ignore[no-any-return]


def _read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _read_elevated_result(
    output: Path,
    code_path: Path,
    exit_code: int,
) -> tuple[int, str] | Failure:
    text = f"{_read_text(output)}\n{_read_text(Path(str(output) + '.err'))}"
    raw = _read_text(code_path).strip()
    if raw.lstrip("-").isdigit():
        return int(raw), text
    if exit_code != 0:
        detail = text.strip() or "The elevated command produced no output."
        return Failure(
            "elevation", f"The elevated command failed ({exit_code}). {detail}"
        )
    return exit_code, text


def _shell_execute(  # pylint: disable=invalid-name,attribute-defined-outside-init
    executable: str,
    parameters: str,
    directory: Path,
    verb: str,
) -> int | Failure:
    """Show the Windows approval prompt and wait for the elevated process."""
    shell32 = _win_dll("shell32")
    kernel32 = _win_dll("kernel32")
    shell32.ShellExecuteExW.argtypes = [ctypes.POINTER(_ShellExecuteInfo)]
    shell32.ShellExecuteExW.restype = wintypes.BOOL
    info = _ShellExecuteInfo()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = _SEE_MASK_NOCLOSEPROCESS
    info.lpVerb = verb
    info.lpFile = executable
    info.lpParameters = parameters
    info.lpDirectory = str(directory)
    info.nShow = _SW_HIDE
    if not shell32.ShellExecuteExW(ctypes.byref(info)):
        error = _last_error()
        if error == _CANCELLED:
            return Failure(
                "elevation",
                "Elevation was cancelled. Nothing was changed.",
            )
        return Failure(
            "elevation",
            f"Windows refused to elevate the disk command ({error}).",
        )
    try:
        kernel32.WaitForSingleObject(info.hProcess, _WAIT_INFINITE)
        code = wintypes.DWORD(0)
        kernel32.GetExitCodeProcess(info.hProcess, ctypes.byref(code))
        return int(code.value)
    finally:
        if info.hProcess:
            kernel32.CloseHandle(info.hProcess)

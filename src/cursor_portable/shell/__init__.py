"""Script text the package runs.

The files under tests/shell and tests/launchers are the copies the linters and tests run.
"""

from __future__ import annotations

import logging
from pathlib import Path
import tempfile

LOGGER = logging.getLogger(__name__)

_SCRIPTS = {
    "Get-VolumeListing.ps1": r"""$busByNumber = @{}
try {
    Get-CimInstance -Namespace root\Microsoft\Windows\Storage -ClassName MSFT_Disk -ErrorAction Stop |
        ForEach-Object {
            $busByNumber[[string]$_.Number] = [int]$_.BusType
        }
} catch {
    [Console]::Error.WriteLine('Bus type probe skipped: ' + $_.Exception.Message)
}
$facts = @{}
Get-CimInstance -ClassName Win32_DiskDrive | ForEach-Object {
    $number = [string]$_.Index
    $bus = $null
    if ($busByNumber.ContainsKey($number)) {
        $bus = $busByNumber[$number]
    }
    $interface = [string]$_.InterfaceType
    $media = [string]$_.MediaType
    Get-CimAssociatedInstance -InputObject $_ -ResultClassName Win32_DiskPartition |
        ForEach-Object {
            Get-CimAssociatedInstance -InputObject $_ -ResultClassName Win32_LogicalDisk |
                ForEach-Object {
                    $facts[$_.DeviceID] = [PSCustomObject]@{
                        interface = $interface
                        media     = $media
                        bus       = $bus
                    }
                }
        }
}
$access = @{}
Get-CimInstance -ClassName Win32_Volume | ForEach-Object {
    $letter = [string]$_.DriveLetter
    if ($letter) {
        if (-not $letter.EndsWith(':')) {
            $letter = $letter + ':'
        }
        $access[$letter] = $_.Access
    }
}
$system = $env:SystemDrive
$disks = @(Get-CimInstance -ClassName Win32_LogicalDisk | ForEach-Object {
        $id = $_.DeviceID
        $fact = $facts[$id]
        $interfaceType = ''
        $mediaType = ''
        $busType = $null
        if ($null -ne $fact) {
            $interfaceType = [string]$fact.interface
            $mediaType = [string]$fact.media
            $busType = $fact.bus
        }
        $rights = $access[$id]
        $writable = ($null -eq $rights) -or ($rights -eq 0) -or ($rights -eq 2) -or ($rights -eq 3)
        [PSCustomObject]@{
            device_id      = $id
            mount_point    = ($id + '\')
            label          = [string]$_.VolumeName
            filesystem     = [string]$_.FileSystem
            size_bytes     = [int64]$_.Size
            drive_type     = [int]$_.DriveType
            interface_type = $interfaceType
            bus_type       = $busType
            media_type     = $mediaType
            writable       = [bool]$writable
            is_system      = ($id -eq $system)
            is_boot        = ($id -eq $system)
        }
    })
$disks | ConvertTo-Json -Compress
""",
    "Get-WindowsShortcuts.ps1": r"""$shell = New-Object -ComObject WScript.Shell
$roots = @(
    (Join-Path -Path $env:APPDATA -ChildPath 'Microsoft\Windows\Start Menu'),
    (Join-Path -Path $env:ProgramData -ChildPath 'Microsoft\Windows\Start Menu'),
    (Join-Path -Path $env:USERPROFILE -ChildPath 'Desktop'),
    (Join-Path -Path $env:PUBLIC -ChildPath 'Desktop')
)
foreach ($root in $roots) {
    if (-not $root -or -not (Test-Path -LiteralPath $root)) {
        continue
    }
    Get-ChildItem -LiteralPath $root -Filter *.lnk -Recurse -ErrorAction SilentlyContinue |
        ForEach-Object {
            $shortcut = $shell.CreateShortcut($_.FullName)
            Write-Output ($_.FullName + '|' + $shortcut.TargetPath)
        }
}
""",
    "Set-WindowsShortcut.ps1": r"""#Requires -Version 5.1
[CmdletBinding(SupportsShouldProcess = $true)]
param(
    [Parameter(Mandatory = $true)]
    [string]$Path,
    [Parameter(Mandatory = $true)]
    [string]$Target,
    [Parameter(Mandatory = $false)]
    [int]$WindowStyle
)
$ErrorActionPreference = 'Stop'
if (-not $PSCmdlet.ShouldProcess($Path, 'Set shortcut target')) {
    return
}
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($Path)
$shortcut.TargetPath = $Target
if ($PSBoundParameters.ContainsKey('WindowStyle')) {
    $shortcut.WindowStyle = $WindowStyle
}
$shortcut.Save()
""",
    "Invoke-ElevatedCommand.ps1": r"""#Requires -Version 5.1
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
""",
    "posix-extractor.sh": r"""#!/usr/bin/env bash
set -euo pipefail
EXTRACTOR_OFFSET=000000000000
EXTRACTOR_SIZE=000000000000
ARCHIVE_OFFSET=000000000000
ARCHIVE_SIZE=000000000000
SELF="$0"
TMP="$(mktemp -d)"
cleanup() { rm -rf "$TMP"; }
trap cleanup EXIT
copy_range() {
    local offset="$1"
    local size="$2"
    local destination="$3"
    set +e
    tail -c "+$((10#$offset + 1))" "$SELF" | head -c "$size" > "$destination"
    # PIPESTATUS is expanded before local runs, so this keeps the pipeline status.
    # shellcheck disable=SC2155
    local saved=("${PIPESTATUS[@]}")
    set -e
    local tail_status="${saved[0]}"
    local head_status="${saved[1]}"
    if [ "$head_status" -ne 0 ]; then
        return "$head_status"
    fi
    if [ "$tail_status" -ne 0 ] && [ "$tail_status" -ne 141 ]; then
        return "$tail_status"
    fi
    return 0
}
copy_range "$EXTRACTOR_OFFSET" "$EXTRACTOR_SIZE" "$TMP/7zz"
copy_range "$ARCHIVE_OFFSET" "$ARCHIVE_SIZE" "$TMP/setup.7z"
chmod +x "$TMP/7zz"
"$TMP/7zz" x "-o${TMP}/root" -y "$TMP/setup.7z"
PY="${TMP}/root/python/bin/python3"
exec "$PY" -m cursor_portable "$@"
""",
    "prepend-path.sh": r"""cursor_portable_bin='__SEGMENT__'
case ":${PATH}:" in
    *":${cursor_portable_bin}:"*) ;;
    *) PATH="${cursor_portable_bin}:${PATH}"; export PATH ;;
esac
unset cursor_portable_bin
""",
}


def shell_text(name: str) -> str:
    """Return one script shipped with this package."""
    try:
        text = _SCRIPTS[name]
    except KeyError as exc:
        LOGGER.error("Shell script is missing: %s", name)
        raise FileNotFoundError(name) from exc
    LOGGER.info("Using shell script %s", name)
    return text


def shell_path(name: str) -> Path:
    """Write one shipped script where PowerShell can run it with -File."""
    text = shell_text(name)
    directory = Path(tempfile.gettempdir()) / "cursor-portable-shell"
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / name
    current = path.read_text(encoding="utf-8") if path.is_file() else None
    if current != text:
        path.write_text(text, encoding="utf-8", newline="\n")
        LOGGER.info("Wrote shell script %s", path)
    return path


def powershell_file_command(name: str) -> list[str]:
    """Return a powershell -File invocation of one shipped script."""
    return [
        "powershell",
        "-NoProfile",
        "-NonInteractive",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        str(shell_path(name)),
    ]

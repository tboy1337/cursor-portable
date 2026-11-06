#Requires -Version 5.1
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Get-LauncherPath {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name
    )
    $root = Join-Path -Path $PSScriptRoot -ChildPath '..\..\scripts\launchers'
    return (Resolve-Path -LiteralPath (Join-Path -Path $root -ChildPath $Name)).Path
}

function Invoke-CapturedProcess {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string]$ArgumentList,
        [switch]$NoWindow
    )
    $start = [System.Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $FilePath
    $start.Arguments = $ArgumentList
    $start.UseShellExecute = $false
    $start.CreateNoWindow = [bool]$NoWindow
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $process = [System.Diagnostics.Process]::Start($start)
    $stdout = $process.StandardOutput.ReadToEnd()
    $stderr = $process.StandardError.ReadToEnd()
    $process.WaitForExit()
    return [pscustomobject]@{
        ExitCode       = $process.ExitCode
        StandardOutput = $stdout
        StandardError  = $stderr
    }
}

function New-LauncherHarness {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Directory,
        [Parameter(Mandatory = $true)]
        [string]$Pythonw,
        [Parameter(Mandatory = $true)]
        [string]$Root,
        [Parameter(Mandatory = $true)]
        [string]$Script,
        [string]$StubLog = ''
    )
    if (-not $PSCmdlet.ShouldProcess($Directory, 'Write CursorPortable.cmd harness')) {
        return
    }
    $launcher = Join-Path -Path $Directory -ChildPath 'CursorPortable.cmd'
    $lines = [System.Collections.Generic.List[string]]::new()
    $lines.Add('@echo off')
    if ($StubLog) {
        $lines.Add("set `"CURSOR_PORTABLE_STUB_LOG=$StubLog`"")
    }
    $lines.Add("set `"CURSOR_PORTABLE_PYTHONW=$Pythonw`"")
    $lines.Add("set `"CURSOR_PORTABLE_ROOT=$Root`"")
    $lines.Add(
        "powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File `"$Script`""
    )
    Set-Content -LiteralPath $launcher -Value $lines.ToArray() -Encoding Ascii
    return $launcher
}

function New-LaunchStub {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Directory
    )
    if (-not $PSCmdlet.ShouldProcess($Directory, 'Compile launch stub')) {
        return
    }
    $exe = Join-Path -Path $Directory -ChildPath 'stub.exe'
    $source = @'
using System;
using System.IO;
using System.Text;
public class LaunchStub {
    public static int Main(string[] args) {
        string path = Environment.GetEnvironmentVariable("CURSOR_PORTABLE_STUB_LOG");
        if (string.IsNullOrEmpty(path)) {
            return 2;
        }
        var builder = new StringBuilder();
        string headless = Environment.GetEnvironmentVariable("CURSOR_PORTABLE_HEADLESS");
        builder.AppendLine(headless == null ? "" : headless);
        foreach (string arg in args) {
            builder.AppendLine(arg);
        }
        File.WriteAllText(path, builder.ToString());
        return 0;
    }
}
'@
    Add-Type -TypeDefinition $source -OutputAssembly $exe -OutputType WindowsApplication | Out-Null
    return $exe
}

function Invoke-Harness {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Launcher
    )
    $arguments = '/d /c "{0}"' -f $Launcher
    return Invoke-CapturedProcess -FilePath $env:ComSpec -ArgumentList $arguments -NoWindow
}

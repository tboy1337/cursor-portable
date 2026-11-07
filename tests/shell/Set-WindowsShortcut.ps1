#Requires -Version 5.1
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

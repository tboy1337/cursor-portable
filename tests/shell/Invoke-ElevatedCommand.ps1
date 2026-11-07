#Requires -Version 5.1
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

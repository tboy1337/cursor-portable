#Requires -Version 5.1
<#
.SYNOPSIS
    Lint and test the generated Cursor Portable PowerShell snippets.
#>
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
$InformationPreference = 'Continue'

function Install-RequiredModule {
    [CmdletBinding(SupportsShouldProcess = $true)]
    param(
        [Parameter(Mandatory = $true)]
        [string]$Name,
        [Parameter(Mandatory = $true)]
        [version]$MinimumVersion
    )
    $present = @(
        Get-Module -ListAvailable -Name $Name |
            Where-Object { $_.Version -ge $MinimumVersion } |
            Sort-Object -Property Version -Descending
    )
    if ($present.Count -gt 0) {
        Write-Output "Using $($present[0].Name) $($present[0].Version)"
        return
    }
    if (-not $PSCmdlet.ShouldProcess($Name, "Install $MinimumVersion or newer")) {
        return
    }
    Write-Output "Installing $Name $MinimumVersion or newer"
    [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
    $nuget = @(
        Get-PackageProvider -Name NuGet -ErrorAction SilentlyContinue |
            Where-Object { $_.Version -ge [version]'2.8.5.201' }
    )
    if ($nuget.Count -eq 0) {
        Install-PackageProvider -Name NuGet -MinimumVersion 2.8.5.201 -Force | Out-Null
    }
    Install-Module -Name $Name -MinimumVersion $MinimumVersion -Scope CurrentUser -Force -AllowClobber -Confirm:$false
}

function Get-AnalyzerIssue {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string[]]$Path
    )
    $issues = [System.Collections.Generic.List[object]]::new()
    foreach ($item in $Path) {
        Write-Information "Analyzing $item"
        $found = @(Invoke-ScriptAnalyzer -Path $item -Severity @('Warning', 'Error'))
        foreach ($issue in $found) {
            $issues.Add($issue)
        }
    }
    return $issues.ToArray()
}

Install-RequiredModule -Name PSScriptAnalyzer -MinimumVersion ([version]'1.21.0')
Install-RequiredModule -Name Pester -MinimumVersion ([version]'5.0.0')
Import-Module -Name PSScriptAnalyzer -MinimumVersion 1.21.0
Import-Module -Name Pester -MinimumVersion 5.0.0

$root = Split-Path -Path $PSScriptRoot -Parent
$issues = @(
    Get-AnalyzerIssue -Path @(
        (Join-Path -Path $root -ChildPath 'tests\launchers'),
        (Join-Path -Path $root -ChildPath 'tests\shell'),
        (Join-Path -Path $root -ChildPath 'tests\pester'),
        $PSCommandPath
    )
)
$failed = $false
if ($issues.Count -gt 0) {
    $failed = $true
    foreach ($issue in $issues) {
        Write-Output ("{0}:{1} {2} {3}" -f $issue.ScriptPath, $issue.Line, $issue.RuleName, $issue.Message)
    }
}

$configuration = New-PesterConfiguration
$configuration.Run.Path = Join-Path -Path $root -ChildPath 'tests\pester'
$configuration.Run.PassThru = $true
$configuration.Output.Verbosity = 'Detailed'
Write-Output "Running Pester from $($configuration.Run.Path.Value)"
$result = Invoke-Pester -Configuration $configuration
if ($null -eq $result -or $result.FailedCount -gt 0 -or $result.TotalCount -eq 0) {
    $failed = $true
}
if ($failed) {
    exit 1
}
exit 0

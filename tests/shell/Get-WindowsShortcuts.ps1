$shell = New-Object -ComObject WScript.Shell
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

#Requires -Version 5.1

Describe 'Start-HeadlessCursor' {
    BeforeAll {
        . (Join-Path -Path $PSScriptRoot -ChildPath 'LauncherTestHelpers.ps1')
    }

    It 'exits 2 when the parent command line is not the launcher' {
        $script = Get-LauncherPath -Name 'Start-HeadlessCursor.ps1'
        $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f $script
        $run = Invoke-CapturedProcess -FilePath 'powershell.exe' -ArgumentList $arguments -NoWindow
        $run.ExitCode | Should -Be 2 -Because $run.StandardError
    }

    It 'exits 3 when pythonw is missing' {
        $script = Get-LauncherPath -Name 'Start-HeadlessCursor.ps1'
        $root = Join-Path -Path $TestDrive -ChildPath 'missing'
        New-Item -ItemType Directory -Path $root | Out-Null
        $launcher = New-LauncherHarness -Directory $root -Pythonw (
            Join-Path -Path $root -ChildPath 'pythonw.exe'
        ) -Root $root -Script $script
        $run = Invoke-Harness -Launcher $launcher
        $run.ExitCode | Should -Be 3 -Because ($run.StandardOutput + $run.StandardError)
    }

    It 'starts pythonw and exits 0 when the launcher parent is present' {
        $script = Get-LauncherPath -Name 'Start-HeadlessCursor.ps1'
        $root = Join-Path -Path $TestDrive -ChildPath 'ready'
        New-Item -ItemType Directory -Path $root | Out-Null
        $stub = New-LaunchStub -Directory $root
        $log = Join-Path -Path $root -ChildPath 'launch.txt'
        $launcher = New-LauncherHarness -Directory $root -Pythonw $stub -Root $root -Script $script -StubLog $log
        $run = Invoke-Harness -Launcher $launcher
        $run.ExitCode | Should -Be 0 -Because ($run.StandardOutput + $run.StandardError)
        $deadline = (Get-Date).AddSeconds(15)
        do {
            if (Test-Path -LiteralPath $log) {
                break
            }
            Start-Sleep -Milliseconds 50
        } while ((Get-Date) -lt $deadline)
        Test-Path -LiteralPath $log | Should -BeTrue
        $recorded = @(Get-Content -LiteralPath $log)
        $recorded[0] | Should -Be '1'
        $recorded | Should -Contain '-m'
        $recorded | Should -Contain 'cursor_portable'
        $recorded | Should -Contain 'launch'
        $recorded | Should -Contain '--root'
        $recorded | Should -Contain $root
    }
}

Describe 'console window scripts' {
    BeforeAll {
        . (Join-Path -Path $PSScriptRoot -ChildPath 'LauncherTestHelpers.ps1')
    }

    It 'hides nothing and exits 1 when the process has no console' {
        $script = Get-LauncherPath -Name 'Hide-ConsoleWindow.ps1'
        $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f $script
        $run = Invoke-CapturedProcess -FilePath 'powershell.exe' -ArgumentList $arguments -NoWindow
        $run.ExitCode | Should -Be 1 -Because $run.StandardError
    }

    It 'shows nothing and exits 1 when the process has no console' {
        $script = Get-LauncherPath -Name 'Show-ConsoleWindow.ps1'
        $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f $script
        $run = Invoke-CapturedProcess -FilePath 'powershell.exe' -ArgumentList $arguments -NoWindow
        $run.ExitCode | Should -Be 1 -Because $run.StandardError
    }

    It 'hides an existing console and exits 0' {
        $script = Get-LauncherPath -Name 'Hide-ConsoleWindow.ps1'
        $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f $script
        $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Wait -PassThru
        $process.ExitCode | Should -Be 0
    }

    It 'restores an existing console and exits 0' {
        $script = Get-LauncherPath -Name 'Show-ConsoleWindow.ps1'
        $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f $script
        $process = Start-Process -FilePath 'powershell.exe' -ArgumentList $arguments -Wait -PassThru
        $process.ExitCode | Should -Be 0
    }
}

Describe 'packaged shell scripts' {
    BeforeAll {
        . (Join-Path -Path $PSScriptRoot -ChildPath 'LauncherTestHelpers.ps1')
    }

    It 'lists volumes as JSON' {
        $script = Get-ShellScriptPath -Name 'Get-VolumeListing.ps1'
        $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f $script
        $run = Invoke-CapturedProcess -FilePath 'powershell.exe' -ArgumentList $arguments -NoWindow
        $run.ExitCode | Should -Be 0 -Because ($run.StandardOutput + $run.StandardError)
        $parsed = $run.StandardOutput | ConvertFrom-Json
        $items = @($parsed)
        $items.Count | Should -BeGreaterThan 0
        $items[0].device_id | Should -Not -BeNullOrEmpty
        $items[0].PSObject.Properties.Name | Should -Contain 'writable'
    }

    It 'reads shortcut targets' {
        $script = Get-ShellScriptPath -Name 'Get-WindowsShortcuts.ps1'
        $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}"' -f $script
        $run = Invoke-CapturedProcess -FilePath 'powershell.exe' -ArgumentList $arguments -NoWindow
        $run.ExitCode | Should -Be 0 -Because $run.StandardError
    }

    It 'creates a shortcut and keeps the window style when it is omitted' {
        $script = Get-ShellScriptPath -Name 'Set-WindowsShortcut.ps1'
        $directory = Join-Path -Path $TestDrive -ChildPath 'shortcut'
        New-Item -ItemType Directory -Path $directory | Out-Null
        $target = Join-Path -Path $directory -ChildPath 'CursorPortable.cmd'
        $link = Join-Path -Path $directory -ChildPath 'Cursor Portable.lnk'
        Set-Content -LiteralPath $target -Value '@echo off' -Encoding Ascii
        $created = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Path "{1}" -Target "{2}" -WindowStyle 7' -f $script, $link, $target
        $first = Invoke-CapturedProcess -FilePath 'powershell.exe' -ArgumentList $created -NoWindow
        $first.ExitCode | Should -Be 0 -Because $first.StandardError
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut($link)
        $shortcut.TargetPath | Should -Be $target
        $shortcut.WindowStyle | Should -Be 7
        $restored = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -Path "{1}" -Target "{2}"' -f $script, $link, $target
        $second = Invoke-CapturedProcess -FilePath 'powershell.exe' -ArgumentList $restored -NoWindow
        $second.ExitCode | Should -Be 0 -Because $second.StandardError
        $again = $shell.CreateShortcut($link)
        $again.TargetPath | Should -Be $target
        $again.WindowStyle | Should -Be 7
    }

    It 'records output and the child exit code' {
        $script = Get-ShellScriptPath -Name 'Invoke-ElevatedCommand.ps1'
        $directory = Join-Path -Path $TestDrive -ChildPath 'elevate'
        New-Item -ItemType Directory -Path $directory | Out-Null
        $command = Join-Path -Path $directory -ChildPath 'command.json'
        $output = Join-Path -Path $directory -ChildPath 'output.txt'
        $code = Join-Path -Path $directory -ChildPath 'code.txt'
        $payload = [ordered]@{
            executable = 'powershell.exe'
            arguments  = '-NoProfile -NonInteractive -Command "Write-Output ''hello''; [Console]::Error.WriteLine(''boom''); exit 4"'
        }
        [System.IO.File]::WriteAllText($command, ($payload | ConvertTo-Json -Compress))
        $arguments = '-NoProfile -NonInteractive -ExecutionPolicy Bypass -File "{0}" -CommandPath "{1}" -OutputPath "{2}" -CodePath "{3}"' -f $script, $command, $output, $code
        $run = Invoke-CapturedProcess -FilePath 'powershell.exe' -ArgumentList $arguments -NoWindow
        $run.ExitCode | Should -Be 4 -Because ($run.StandardOutput + $run.StandardError)
        [System.IO.File]::ReadAllText($output) | Should -Match 'hello'
        [System.IO.File]::ReadAllText($output + '.err') | Should -Match 'boom'
        [System.IO.File]::ReadAllText($code).Trim() | Should -Be '4'
    }
}

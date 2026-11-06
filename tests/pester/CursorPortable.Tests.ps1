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

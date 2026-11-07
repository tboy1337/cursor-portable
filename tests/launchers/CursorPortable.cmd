@echo off
rem Purpose: start the portable Cursor session and restore the host when it exits.
setlocal EnableExtensions
cd /d "%~dp0"
set "PYTHONPATH=%~dp0tool"
set "CURSOR_PORTABLE_HIDDEN=0"
if /I "%~1"=="--console" goto :console
rem CURSOR_PORTABLE_CONSOLE is optional. 1 keeps this console attached.
rem LINT:IGNORE E006
if /I "%CURSOR_PORTABLE_CONSOLE%"=="1" goto :console
set "CURSOR_PORTABLE_PYTHONW=%~dp0python\pythonw.exe"
set "CURSOR_PORTABLE_ROOT=%~dp0."
rem 3 means pythonw is missing. 2 means this console belongs to the caller.
rem The PowerShell -Command is one line so cmd does not split it.
rem LINT:IGNORE S020
powershell.exe -NoProfile -NonInteractive -Command "$me = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $PID); $cmd = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $me.ParentProcessId); if (-not $cmd -or $cmd.CommandLine -notlike '*CursorPortable.cmd*') { exit 2 }; if (-not (Test-Path -LiteralPath $env:CURSOR_PORTABLE_PYTHONW)) { exit 3 }; $env:CURSOR_PORTABLE_HEADLESS='1'; Start-Process -FilePath $env:CURSOR_PORTABLE_PYTHONW -WorkingDirectory $env:CURSOR_PORTABLE_ROOT -ArgumentList @('-m','cursor_portable','launch','--root',$env:CURSOR_PORTABLE_ROOT); exit 0"
if errorlevel 3 goto :hidden
if errorlevel 1 goto :console
exit /b 0

:hidden
set "CURSOR_PORTABLE_HIDDEN=1"
call :HideConsole

:console
"%~dp0python\python.exe" -m cursor_portable launch --root "%~dp0."
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
powershell.exe -NoProfile -NonInteractive -Command "$q = [char]34; $signature = '[DllImport(' + $q + 'user32.dll' + $q + ')] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow); [DllImport(' + $q + 'kernel32.dll' + $q + ')] public static extern IntPtr GetConsoleWindow();'; $console = Add-Type -MemberDefinition $signature -Name Hide -Namespace CursorPortableLauncher -PassThru; $handle = $console::GetConsoleWindow(); if ($handle -eq [IntPtr]::Zero) { exit 1 }; [void]$console::ShowWindow($handle, 0); exit 0"
goto :eof

rem Show this console when a hidden launch stops with an error.
:ShowFailedConsole
rem The PowerShell -Command is one line so cmd does not split it.
rem LINT:IGNORE S020
powershell.exe -NoProfile -NonInteractive -Command "$q = [char]34; $signature = '[DllImport(' + $q + 'user32.dll' + $q + ')] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow); [DllImport(' + $q + 'kernel32.dll' + $q + ')] public static extern IntPtr GetConsoleWindow();'; $console = Add-Type -MemberDefinition $signature -Name Show -Namespace CursorPortableLauncher -PassThru; $handle = $console::GetConsoleWindow(); if ($handle -eq [IntPtr]::Zero) { exit 1 }; [void]$console::ShowWindow($handle, 9); exit 0"
echo.
echo Cursor Portable stopped with exit code %EXIT_CODE%.
pause
goto :eof

$me = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $PID)
$cmd = Get-CimInstance Win32_Process -Filter ('ProcessId=' + $me.ParentProcessId)
if (-not $cmd -or $cmd.CommandLine -notlike '*CursorPortable.cmd*') { exit 2 }
if (-not (Test-Path -LiteralPath $env:CURSOR_PORTABLE_PYTHONW)) { exit 3 }
$env:CURSOR_PORTABLE_HEADLESS='1'
Start-Process -FilePath $env:CURSOR_PORTABLE_PYTHONW -WorkingDirectory $env:CURSOR_PORTABLE_ROOT -ArgumentList @('-m','cursor_portable','launch','--root',$env:CURSOR_PORTABLE_ROOT)
exit 0

$q = [char]34
$signature = '[DllImport(' + $q + 'user32.dll' + $q + ')] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow); [DllImport(' + $q + 'kernel32.dll' + $q + ')] public static extern IntPtr GetConsoleWindow();'
$console = Add-Type -MemberDefinition $signature -Name Show -Namespace CursorPortableLauncher -PassThru
$handle = $console::GetConsoleWindow()
if ($handle -eq [IntPtr]::Zero) { exit 1 }
[void]$console::ShowWindow($handle, 9)
exit 0

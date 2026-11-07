$q = [char]34
$signature = '[DllImport(' + $q + 'user32.dll' + $q + ')] public static extern bool ShowWindow(IntPtr hWnd, int nCmdShow); [DllImport(' + $q + 'kernel32.dll' + $q + ')] public static extern IntPtr GetConsoleWindow();'
$console = Add-Type -MemberDefinition $signature -Name Hide -Namespace CursorPortableLauncher -PassThru
$handle = $console::GetConsoleWindow()
if ($handle -eq [IntPtr]::Zero) { exit 1 }
[void]$console::ShowWindow($handle, 0)
exit 0

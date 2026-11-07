$busByNumber = @{}
try {
    Get-CimInstance -Namespace root\Microsoft\Windows\Storage -ClassName MSFT_Disk -ErrorAction Stop |
        ForEach-Object {
            $busByNumber[[string]$_.Number] = [int]$_.BusType
        }
} catch {
    [Console]::Error.WriteLine('Bus type probe skipped: ' + $_.Exception.Message)
}
$facts = @{}
Get-CimInstance -ClassName Win32_DiskDrive | ForEach-Object {
    $number = [string]$_.Index
    $bus = $null
    if ($busByNumber.ContainsKey($number)) {
        $bus = $busByNumber[$number]
    }
    $interface = [string]$_.InterfaceType
    $media = [string]$_.MediaType
    Get-CimAssociatedInstance -InputObject $_ -ResultClassName Win32_DiskPartition |
        ForEach-Object {
            Get-CimAssociatedInstance -InputObject $_ -ResultClassName Win32_LogicalDisk |
                ForEach-Object {
                    $facts[$_.DeviceID] = [PSCustomObject]@{
                        interface = $interface
                        media     = $media
                        bus       = $bus
                    }
                }
        }
}
$access = @{}
Get-CimInstance -ClassName Win32_Volume | ForEach-Object {
    $letter = [string]$_.DriveLetter
    if ($letter) {
        if (-not $letter.EndsWith(':')) {
            $letter = $letter + ':'
        }
        $access[$letter] = $_.Access
    }
}
$system = $env:SystemDrive
$disks = @(Get-CimInstance -ClassName Win32_LogicalDisk | ForEach-Object {
        $id = $_.DeviceID
        $fact = $facts[$id]
        $interfaceType = ''
        $mediaType = ''
        $busType = $null
        if ($null -ne $fact) {
            $interfaceType = [string]$fact.interface
            $mediaType = [string]$fact.media
            $busType = $fact.bus
        }
        $rights = $access[$id]
        $writable = ($null -eq $rights) -or ($rights -eq 0) -or ($rights -eq 2) -or ($rights -eq 3)
        [PSCustomObject]@{
            device_id      = $id
            mount_point    = ($id + '\')
            label          = [string]$_.VolumeName
            filesystem     = [string]$_.FileSystem
            size_bytes     = [int64]$_.Size
            drive_type     = [int]$_.DriveType
            interface_type = $interfaceType
            bus_type       = $busType
            media_type     = $mediaType
            writable       = [bool]$writable
            is_system      = ($id -eq $system)
            is_boot        = ($id -eq $system)
        }
    })
$disks | ConvertTo-Json -Compress

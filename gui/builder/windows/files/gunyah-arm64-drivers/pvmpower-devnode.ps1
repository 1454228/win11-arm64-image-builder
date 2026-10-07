# pvmpower devnode helper (idempotent, requires admin).
#
# pvmpower.sys binds to a ROOT-ENUMERATED software device (ROOT\PVMPOWER) so it
# sits in the PnP tree and receives system power IRPs. INF staging (pnputil
# /add-driver, DISM /Add-Driver) does NOT create devnodes, so this script:
#   1. creates the ROOT\PVMPOWER devnode via SetupAPI if it does not exist,
#   2. removes the stale UpperFilters value on ACPI\RDMA0000 left behind by the
#      short-lived filter-driver packaging of pvmpower (no-op normally),
#   3. runs pnputil /scan-devices so PnP binds the staged driver to the devnode.
# Used by install-drivers.ps1 and by the image builder's first-boot flow.
$ErrorActionPreference='Continue'

Add-Type -ErrorAction SilentlyContinue @"
using System;
using System.Runtime.InteropServices;
public static class PvmDevNode {
    [StructLayout(LayoutKind.Sequential)]
    public struct SP_DEVINFO_DATA { public uint cbSize; public Guid ClassGuid; public uint DevInst; public IntPtr Reserved; }
    [DllImport("setupapi.dll", SetLastError=true)]
    public static extern IntPtr SetupDiCreateDeviceInfoList(ref Guid ClassGuid, IntPtr hwndParent);
    [DllImport("setupapi.dll", EntryPoint="SetupDiCreateDeviceInfoList", SetLastError=true)]
    public static extern IntPtr SetupDiCreateDeviceInfoListNoClass(IntPtr ClassGuid, IntPtr hwndParent);
    [DllImport("setupapi.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern bool SetupDiCreateDeviceInfoW(IntPtr DeviceInfoSet, string DeviceName, ref Guid ClassGuid, string DeviceDescription, IntPtr hwndParent, uint CreationFlags, ref SP_DEVINFO_DATA DeviceInfoData);
    [DllImport("setupapi.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern bool SetupDiOpenDeviceInfoW(IntPtr DeviceInfoSet, string DeviceInstanceId, IntPtr hwndParent, uint OpenFlags, ref SP_DEVINFO_DATA DeviceInfoData);
    [DllImport("setupapi.dll", CharSet=CharSet.Unicode, SetLastError=true)]
    public static extern bool SetupDiSetDeviceRegistryPropertyW(IntPtr DeviceInfoSet, ref SP_DEVINFO_DATA DeviceInfoData, uint Property, byte[] PropertyBuffer, uint PropertyBufferSize);
    [DllImport("setupapi.dll", SetLastError=true)]
    public static extern bool SetupDiCallClassInstaller(uint InstallFunction, IntPtr DeviceInfoSet, ref SP_DEVINFO_DATA DeviceInfoData);
    [DllImport("setupapi.dll", SetLastError=true)]
    public static extern bool SetupDiDestroyDeviceInfoList(IntPtr DeviceInfoSet);
}
"@
$DICD_GENERATE_ID = 1; $SPDRP_HARDWAREID = 1; $SPDRP_UPPERFILTERS = 0x11; $DIF_REGISTERDEVICE = 0x19

# --- 1. create ROOT\PVMPOWER devnode (persists across boots) ---
$have = Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object { $_.InstanceId -like 'ROOT\PVMPOWER\*' }
if ($have) {
  Write-Host "pvmpower devnode already present: $($have.InstanceId | Select-Object -First 1)"
} else {
  $classSystem = [Guid]'4d36e97d-e325-11ce-bfc1-08002be10318'
  $set = [PvmDevNode]::SetupDiCreateDeviceInfoList([ref]$classSystem, [IntPtr]::Zero)
  if ($set -eq [IntPtr]::Zero -or $set -eq [IntPtr](-1)) {
    Write-Host "devnode: SetupDiCreateDeviceInfoList failed" -ForegroundColor Red
  } else {
    try {
      $dev = New-Object PvmDevNode+SP_DEVINFO_DATA
      $dev.cbSize = [uint32][Runtime.InteropServices.Marshal]::SizeOf([type][PvmDevNode+SP_DEVINFO_DATA])
      if (-not [PvmDevNode]::SetupDiCreateDeviceInfoW($set, 'PVMPOWER', [ref]$classSystem, 'Gunyah pVM Power Manager', [IntPtr]::Zero, $DICD_GENERATE_ID, [ref]$dev)) {
        Write-Host "devnode: SetupDiCreateDeviceInfo failed ($([Runtime.InteropServices.Marshal]::GetLastWin32Error()))" -ForegroundColor Red
      } else {
        # REG_MULTI_SZ "ROOT\PVMPOWER" + double NUL, UTF-16LE
        $hwid = [Text.Encoding]::Unicode.GetBytes("ROOT\PVMPOWER`0`0")
        if (-not [PvmDevNode]::SetupDiSetDeviceRegistryPropertyW($set, [ref]$dev, $SPDRP_HARDWAREID, $hwid, [uint32]$hwid.Length)) {
          Write-Host "devnode: set HardwareID failed ($([Runtime.InteropServices.Marshal]::GetLastWin32Error()))" -ForegroundColor Red
        } elseif (-not [PvmDevNode]::SetupDiCallClassInstaller($DIF_REGISTERDEVICE, $set, [ref]$dev)) {
          Write-Host "devnode: DIF_REGISTERDEVICE failed ($([Runtime.InteropServices.Marshal]::GetLastWin32Error()))" -ForegroundColor Red
        } else {
          Write-Host "created ROOT\PVMPOWER devnode"
        }
      }
    } finally {
      [void][PvmDevNode]::SetupDiDestroyDeviceInfoList($set)
    }
  }
}

# --- 2. drop the stale pvmpower UpperFilters value on ACPI\RDMA0000 (filter-era leftover) ---
Get-PnpDevice -ErrorAction SilentlyContinue | Where-Object { $_.InstanceId -like 'ACPI\RDMA0000\*' } | ForEach-Object {
  $iid = $_.InstanceId
  $uf = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Enum\$iid" -Name UpperFilters -ErrorAction SilentlyContinue).UpperFilters
  if ($uf -and ($uf -contains 'pvmpower')) {
    Write-Host "removing stale UpperFilters=pvmpower from $iid"
    $set2 = [PvmDevNode]::SetupDiCreateDeviceInfoListNoClass([IntPtr]::Zero, [IntPtr]::Zero)
    if ($set2 -ne [IntPtr]::Zero -and $set2 -ne [IntPtr](-1)) {
      try {
        $dev2 = New-Object PvmDevNode+SP_DEVINFO_DATA
        $dev2.cbSize = [uint32][Runtime.InteropServices.Marshal]::SizeOf([type][PvmDevNode+SP_DEVINFO_DATA])
        if ([PvmDevNode]::SetupDiOpenDeviceInfoW($set2, $iid, [IntPtr]::Zero, 0, [ref]$dev2)) {
          if (-not [PvmDevNode]::SetupDiSetDeviceRegistryPropertyW($set2, [ref]$dev2, $SPDRP_UPPERFILTERS, $null, 0)) {
            Write-Host "  delete UpperFilters failed ($([Runtime.InteropServices.Marshal]::GetLastWin32Error()))" -ForegroundColor Red
          }
        }
      } finally {
        [void][PvmDevNode]::SetupDiDestroyDeviceInfoList($set2)
      }
    }
  }
}

# --- 3. let PnP bind whatever is staged in the driver store to the new devnode ---
pnputil /scan-devices | Out-Null

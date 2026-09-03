<#
  debloat.ps1 - Route A's first-logon half of the debloat (runs on the TARGET as the created account).

  build.ps1 already does the offline half on the mounted image: provisioned Appx removal, WinSxS
  ResetBase, hibernate off, Reserved Storage off. What is left mirrors macos/debloat.ps1 steps 2-4
  and 7 -- telemetry / ads, non-essential services, telemetry tasks, and Windows Update off with
  enable_windows_update.bat dropped on this account's desktop (one-time, deliberately not in
  C:\Users\Default so later accounts don't inherit it). Runs before setup-ems-sac.ps1, which
  captures the WU state as its baseline and puts it back after an online FoD pull.
  Failures are not fatal; continue as much as possible.
#>
$ErrorActionPreference = 'SilentlyContinue'
$log = 'C:\DroidVM\debloat.log'
function Log([string]$m) { "{0}  {1}" -f (Get-Date -Format o), $m | Out-File -Append -Encoding utf8 $log }
Log "start (as $(whoami))"

# 1) Disable telemetry / ads / content push
$telemetry = @{
  'HKLM:\SOFTWARE\Policies\Microsoft\Windows\DataCollection' = @{ AllowTelemetry = 0 }
  'HKLM:\SOFTWARE\Policies\Microsoft\Windows\CloudContent'   = @{ DisableWindowsConsumerFeatures = 1; DisableCloudOptimizedContent = 1 }
  'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\AdvertisingInfo' = @{ Enabled = 0 }
}
foreach ($path in $telemetry.Keys) {
  New-Item -Path $path -Force | Out-Null
  foreach ($name in $telemetry[$path].Keys) {
    New-ItemProperty -Path $path -Name $name -Value $telemetry[$path][$name] -PropertyType DWord -Force | Out-Null
  }
}
Log "telemetry / ads policies set"

# 2) Disable non-essential services (conservative: telemetry/diagnostics/Xbox/search indexing)
$svc = 'DiagTrack','dmwappushservice','WSearch','XblAuthManager','XblGameSave','XboxNetApiSvc','XboxGipSvc','MapsBroker','RetailDemo'
foreach ($s in $svc) {
  Set-Service -Name $s -StartupType Disabled
  Stop-Service -Name $s -Force
}
Log "services disabled: $($svc -join ', ')"

# 3) Disable scheduled telemetry tasks
$tasks = '\Microsoft\Windows\Application Experience\Microsoft Compatibility Appraiser',
         '\Microsoft\Windows\Customer Experience Improvement Program\Consolidator',
         '\Microsoft\Windows\Customer Experience Improvement Program\UsbCeip',
         '\Microsoft\Windows\Feedback\Siuf\DmClient',
         '\Microsoft\Windows\Feedback\Siuf\DmClientOnScenarioDownload'
foreach ($t in $tasks) { Disable-ScheduledTask -TaskPath (Split-Path $t) -TaskName (Split-Path $t -Leaf) | Out-Null }
Log "telemetry tasks disabled"

# 4) Disable Windows Update (including WaaSMedicSvc / UsoSvc, which auto-restart wuauserv), and place
#    enable_windows_update.bat on this account's desktop so the user decides when to turn updates back on.
# wuauserv is disabled via sc; WaaSMedicSvc is protected and can only be changed via the registry Start value (4=disabled)
& sc.exe stop wuauserv | Out-Null
& sc.exe config wuauserv start= disabled | Out-Null
reg add "HKLM\SYSTEM\CurrentControlSet\Services\WaaSMedicSvc" /v Start /t REG_DWORD /d 4 /f | Out-Null
reg add "HKLM\SYSTEM\CurrentControlSet\Services\UsoSvc"       /v Start /t REG_DWORD /d 4 /f | Out-Null
New-Item -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -Force | Out-Null
New-ItemProperty -Path 'HKLM:\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate\AU' -Name NoAutoUpdate -Value 1 -PropertyType DWord -Force | Out-Null
Log "Windows Update disabled"

$enableBat = @'
@echo off
>nul 2>&1 net session || (powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs" & exit /b)
echo Re-enabling Windows Update...
reg delete "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" /f >nul 2>&1
reg add "HKLM\SYSTEM\CurrentControlSet\Services\WaaSMedicSvc" /v Start /t REG_DWORD /d 3 /f >nul
reg add "HKLM\SYSTEM\CurrentControlSet\Services\UsoSvc"       /v Start /t REG_DWORD /d 2 /f >nul
sc config wuauserv start= demand >nul
sc start wuauserv >nul 2>&1
echo Done. Windows Update re-enabled (a reboot is recommended).
pause
'@
$desktop = Join-Path $env:USERPROFILE 'Desktop'   # the created account's desktop (FirstLogon runs as that account)
New-Item -ItemType Directory -Force $desktop | Out-Null
Set-Content -Path (Join-Path $desktop 'enable_windows_update.bat') -Value $enableBat -Encoding Ascii
Log "enable_windows_update.bat placed on $desktop"
Log "done"

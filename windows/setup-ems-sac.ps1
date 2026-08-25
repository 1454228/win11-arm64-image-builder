<#
  setup-ems-sac.ps1 - Ensure the "EMS and SAC Toolset" Feature-on-Demand is present.

  The ARM64 LTSC/Pro image ships no SAC runtime (sacdrv.sys / sacsess.exe / sacsvr). The
  preferred way to add it is OFFLINE from a FoD source at build time (Route A build.ps1 step
  5c, FOD_SOURCE). This script covers the ONLINE path, and is self-gating and safe to always
  stage / always run:

    * If the capability is already Installed (offline injection worked) -> no-op.
    * If the arming marker (ems-sac-online.flag) is absent -> no-op (boot-EMS-only build).
    * Otherwise -> temporarily re-enable Windows Update, install the FoD, restore the
      WU-disabled baseline.

  Modes:
    (default)  target first-boot: after a successful install, schedule one reboot so the
               pending FoD applies (sacsvr/sacdrv register on apply). Used by Route A's
               windows/unattend.xml, which runs on the SHIPPED image's first boot.
    -NoReboot  build-time (Route B): runs inside the macOS route's qemu build VM (which has
               NAT internet). Downloads/stages the FoD into the component store but does NOT
               reboot -- the pending operation applies on the TARGET's first boot from the
               local store, so the target needs no network.

  A missing network is NOT fatal: the install is wrapped in try/catch, boot-EMS (BCD, armed
  separately) still works, and first boot proceeds normally. Only the interactive SAC> runtime
  is skipped when the FoD cannot be fetched.
#>
param([switch]$NoReboot)
$ErrorActionPreference = 'SilentlyContinue'
$cap   = 'Windows.Desktop.EMS-SAC.Tools~~~~0.0.1.0'
$stage = 'C:\DroidVM'
$flag  = Join-Path $stage 'ems-sac-online.flag'
$log   = Join-Path $stage 'ems-sac.log'
$task  = 'DroidVM-EMS-SAC-FoD'

function Log([string]$m) { "{0}  {1}" -f (Get-Date -Format o), $m | Out-File -Append -Encoding utf8 $log }

# 1) Already present (offline injection succeeded) -> clear any stale marker and stop.
if ((Get-WindowsCapability -Online -Name $cap).State -eq 'Installed') {
    Log "capability already Installed; nothing to do"
    Remove-Item $flag -Force -ErrorAction SilentlyContinue
    exit 0
}

# 2) Online install only when the builder armed it. No marker = boot-EMS-only ship.
if (-not (Test-Path $flag)) {
    Log "FoD absent and online not armed (no flag) -> boot-EMS only, skipping"
    exit 0
}

function Enable-WU {
    reg delete "HKLM\SOFTWARE\Policies\Microsoft\Windows\WindowsUpdate" /f 2>$null | Out-Null
    reg add "HKLM\SYSTEM\CurrentControlSet\Services\WaaSMedicSvc" /v Start /t REG_DWORD /d 3 /f | Out-Null
    reg add "HKLM\SYSTEM\CurrentControlSet\Services\UsoSvc"       /v Start /t REG_DWORD /d 2 /f | Out-Null
    & sc.exe config wuauserv start= demand | Out-Null
    & sc.exe start  wuauserv | Out-Null
}
function Disable-WU {
    & sc.exe stop   wuauserv | Out-Null
    & sc.exe config wuauserv start= disabled | Out-Null
    reg add "HKLM\SYSTEM\CurrentControlSet\Services\WaaSMedicSvc" /v Start /t REG_DWORD /d 4 /f | Out-Null
    reg add "HKLM\SYSTEM\CurrentControlSet\Services\UsoSvc"       /v Start /t REG_DWORD /d 4 /f | Out-Null
}

# Do the actual servicing. Re-enable WU around the pull, then RESTORE whatever WU state was
# there before -- Route A ships with WU enabled, Route B (debloat.ps1) ships it disabled; we
# must not flip either. Sets $script:installOk.
function Invoke-FodInstall {
    # Capture pre-state: wuauserv Start == 4 means "disabled" (the debloat baseline).
    $startVal = (Get-ItemProperty "HKLM:\SYSTEM\CurrentControlSet\Services\wuauserv" -Name Start -ErrorAction SilentlyContinue).Start
    $wasDisabled = ($startVal -eq 4)
    Log "WU pre-state: wuauserv Start=$startVal (wasDisabled=$wasDisabled)"
    Log "re-enabling Windows Update for the FoD pull"
    Enable-WU
    Log "Add-WindowsCapability -Online $cap"
    $script:installOk = $false
    try { Add-WindowsCapability -Online -Name $cap -ErrorAction Stop | Out-Null; $script:installOk = $true }
    catch { Log "Add-WindowsCapability FAILED: $($_.Exception.Message)" }
    if ($wasDisabled) { Log "restoring WU-disabled baseline"; Disable-WU }
    else { Log "leaving Windows Update enabled (pre-state was enabled)" }
    Log ("post-install capability state: {0} (ok={1})" -f (Get-WindowsCapability -Online -Name $cap).State, $script:installOk)
}

$who = (whoami)
if ($who -eq 'nt authority\system') {
    # Invoked as SYSTEM (fallback path below re-entered us). Do the work and clean up.
    Invoke-FodInstall
    Remove-Item $flag -Force -ErrorAction SilentlyContinue
    schtasks /Delete /TN $task /F 2>$null | Out-Null
    if ($installOk -and -not $NoReboot) { Log "scheduling reboot (+120s) to apply the FoD"; shutdown /r /t 120 /c "DroidVM: applying EMS-SAC toolset" | Out-Null }
    exit 0
}

# FirstLogon runs with an interactive elevated-admin token, which normally CAN drive DISM
# servicing (unlike a network-logon token over SSH). Try directly first.
Invoke-FodInstall

if (-not $installOk) {
    # Access-denied or similar -> re-run as SYSTEM via a one-shot scheduled task, and WAIT for
    # it (build-time ordering must hold: the FoD has to finish before sysprep/generalize).
    Log "direct install did not succeed; retrying as SYSTEM (synchronous scheduled task)"
    $argline = if ($NoReboot) { ' -NoReboot' } else { '' }
    $cmd = "powershell -NoProfile -ExecutionPolicy Bypass -File `"$stage\setup-ems-sac.ps1`"$argline"
    schtasks /Create /TN $task /TR $cmd /SC ONCE /ST 00:00 /RU SYSTEM /RL HIGHEST /F | Out-Null
    schtasks /Run /TN $task | Out-Null
    for ($i = 0; $i -lt 240; $i++) {
        Start-Sleep -Seconds 5
        $q = schtasks /Query /TN $task /FO LIST 2>$null | Out-String
        if ($q -match 'Status:\s+Ready' -or $q -match 'Could not' -or $q -notmatch 'Status:\s+Running') { break }
    }
    Log "SYSTEM fallback task finished (waited)"
    exit 0   # the SYSTEM instance already handled reboot + flag/task cleanup
}

# Direct install succeeded.
Remove-Item $flag -Force -ErrorAction SilentlyContinue
if ($installOk -and -not $NoReboot) {
    Log "scheduling reboot (+120s) to apply the FoD"
    shutdown /r /t 120 /c "DroidVM: applying EMS-SAC toolset" | Out-Null
} else {
    Log "no reboot (build-time -NoReboot): pending FoD applies on the target's first boot"
}

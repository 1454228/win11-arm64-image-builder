# =====================================================================
# windows_build.ps1 — entry point for Route A (x64 Windows). Edit the variables below to your own, then right-click "Run as administrator",
#   Requirements: x64 Windows (administrator), built-in dism/bcdboot/diskpart, qemu-img (QEMU for Windows; when it is
#   missing the build offers to install it with winget -- set QEMU_IMG_INSTALL=1 to skip the question).
#   Usage:  powershell -ExecutionPolicy Bypass -File windows_build.ps1
# =====================================================================
$ErrorActionPreference = 'Stop'
# This process only (env var, nothing persisted, GPO still wins): the .ps1 files this one calls next
# (windows\build.ps1, pack-vmpkg.ps1) would otherwise each prompt again when they carry the
# mark-of-the-web under an Unrestricted policy. This script's own prompt happens before line 1 and
# can only be avoided by launching with -ExecutionPolicy Bypass or by Unblock-File.
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass -Force
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    # Relaunch elevated. UAC starts the child in System32 whatever the caller's directory was, so hand the
    # current directory over on the command line and cd back to it before the script runs.
    $cwd = (Get-Location).ProviderPath.Replace("'", "''")
    $me = $PSCommandPath.Replace("'", "''")
    Start-Process powershell "-NoExit -ExecutionPolicy Bypass -Command `"Set-Location -LiteralPath '$cwd'; & '$me'`"" -Verb RunAs
    exit
}

# Paths may be relative (they count from this file's folder, the repo root, whatever directory you launch from)
# and may use %VAR% or $env:VAR (e.g. "$env:USERPROFILE\Downloads\win11.iso").
$env:SRC_ISO       = "C:\Users\USER\Documents\DroidVMBuild\SW_DVD9_Win_Pro_11_25H2_Arm64_English_Pro_Ent_EDU_N_MLF_X24-13111.ISO"
# $env:IMAGE_INDEX = "1"
$env:OUT_QCOW      = "win11-droidvm-final.qcow2"
$env:OUT_VMPKG     = "win11-droidvm-final.vmpkg"
$env:DRIVERS_DIR   = "https://github.com/HuJK/gunyah-guest-drivers-windows/releases/download/dev/gunyah-arm64-drivers.zip"
$env:OPENSSH_SRC   = "https://github.com/PowerShell/Win32-OpenSSH/releases/download/10.0.0.0p2-Preview/OpenSSH-ARM64-v10.0.0.0.msi"

$env:DVM_USERNAME      = "USER"
$env:DVM_PASSWORD      = "DroidVM"
$env:SSH_PUBKEY        = "ssh-ed25519 AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA root@ReplaceMe"

$env:DISK_SIZE_MB      = "40960" # 40GB
$env:VMPKG_COMPRESSION = "auto"
$env:EMS_SAC_SOURCE    = "online"

$env:DRIVER_DIR     = "ZIP/drivers"
$env:DRIVER_INSTALL = "NetKVM rdmapool pvmpower vioinput viostor vioscsi viosnd viofs"
$env:DRIVER_CERT    = "ZIP/DroidVM_Test.cer"

$env:PATH        = "C:\Program Files\qemu;" + $env:PATH
# $env:QEMU_IMG_INSTALL = "1"   # qemu-img missing -> winget install QEMU for Windows without asking




Write-Host "==== DroidVM Windows builder (DISM offline driver injection) ====" -ForegroundColor Cyan
& "$PSScriptRoot\windows\build.ps1"

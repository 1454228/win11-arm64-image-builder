# =====================================================================
# windows_build.ps1 — entry point for Route A (x64 Windows). Edit the variables below to your own, then right-click "Run as administrator",
#   Requirements: x64 Windows (administrator), built-in dism/bcdboot/diskpart, qemu-img (QEMU for Windows on PATH).
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

$env:SRC_ISO     = "C:\Users\USER\Documents\DroidVMBuild\SW_DVD9_Win_Pro_11_25H2_Arm64_English_Pro_Ent_EDU_N_MLF_X24-13111.ISO"
$env:DRIVERS_DIR = "https://github.com/HuJK/gunyah-guest-drivers-windows/releases/download/dev/gunyah-arm64-drivers.zip"
# $env:IMAGE_INDEX = "1"
$env:OUT_QCOW    = "C:\Users\USER\Documents\DroidVMBuild\win11-droidvm-final.qcow2"

$env:DVM_USERNAME = "USER"        # Name of the local administrator account to create
$env:DVM_PASSWORD = "DroidVM"     # Password (an empty password blocks RDP/SSH network logins)
$env:SSH_PUBKEY  = "ssh-ed25519 AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA root@ReplaceMe"
$env:DISK_SIZE_MB = "40960"
# $env:COMPRESS = "1"   # 1 = ship a qcow2 with zstd-compressed clusters (about half the size; needs the crosvm with qcow2 zstd read support); 0 or unset = off
# $env:OUT_VMPKG = "win11-droidvm.vmpkg"   # also emit a ready-to-import .vmpkg (qcow2 + vms.json baked in; uses built-in tar.exe)
# $env:VMPKG_COMPRESSION = "auto"   # auto (default) = zstd on all cores when tar.exe has libzstd (Win11), else single-threaded gzip; or zstd|gzip|none
# $env:EMS_SAC_SOURCE = "skip"   # interactive SAC> runtime (EMS-SAC FoD): "skip" (default) = boot-EMS only, no SAC>; "online" = pulled from
#                                #   Windows Update on the TARGET's first boot; "E:\" = ARM64 FoD ISO mount/folder -> injected offline (zero network)

$env:DRIVER_DIR     = "ZIP/drivers"                                       # Directory containing each driver subfolder
$env:DRIVER_INSTALL = "NetKVM rdmapool pvmpower vioinput viostor vioscsi viosnd viofs" # Install only these (empty = all); must include the viostor/vioscsi boot drivers. viosnd=virtio-sound, viofs=virtio-fs (non-boot)
$env:DRIVER_CERT    = "ZIP/DroidVM_Test.cer"                              # Specify signing certificate (empty = auto-extract from .cat)

$env:OPENSSH_SRC = "https://github.com/PowerShell/Win32-OpenSSH/releases/download/10.0.0.0p2-Preview/OpenSSH-ARM64-v10.0.0.0.msi"

$env:PATH        = "C:\Program Files\qemu;" + $env:PATH

Write-Host "==== DroidVM Windows builder (DISM offline driver injection) ====" -ForegroundColor Cyan
& "$PSScriptRoot\windows\build.ps1"

# pack-vmpkg.ps1 - Assemble a DroidVM `.vmpkg` from a qcow2 disk + a local VM config (vms.json).
#
# The Windows-route twin of pack-vmpkg.py, with zero dependencies beyond what Windows 10
# 1803+ already ships: the tar/gzip data section comes from the built-in tar.exe (bsdtar),
# everything else is plain PowerShell 5.1. Container format is identical to the .py (which
# stays the reference; see its header comment for the byte layout):
#
#   0        24-byte header  "VMPKG"\0 | u16 ver=1 | u16 app_version_code | u16 manifest_size
#                            | u16 compression (1=gzip) | u16 reserved | i64 data_size  (all LE)
#   0x1000   manifest.json (authoritative)
#   ...      zero pad to alignUpStrict(0x1000 + manifest_size)  (+0x1000 when already aligned)
#   ...      gzip(tar: manifest.json, then the disk by archive_path), data_size bytes
#   ...      zero pad to the next 0x1000 boundary, nothing after
#
# The app's tar reader checks no magic and no checksum (name/size/typeflag only, GNU 'L'
# longnames understood), but bsdtar's default pax format writes extended-header entries it
# has no reason to meet -- so the format is pinned to gnutar.
#
# Compression: `auto` (default) is zstd when this tar.exe carries libzstd (Windows 11's
# bsdtar 3.7+), else gzip. zstd runs on every core (libarchive's zstd:threads), packs a bit
# smaller than gzip, and is what the app's own exporter emits by default, so the reader path
# is the most-travelled one. gzip stays single-threaded here: bsdtar's gzip filter has no
# threads, and PowerShell 5.1's .NET Framework DeflateStream cannot sync-flush, which rules
# out the pigz-style split that pack-vmpkg.py does (multi-member gzip is not an option: the
# app's GZIPInputStream sits on a stream whose available() is 0 and then only detects a
# following member heuristically -- see the .py's comment).
#
# Usage:
#   pack-vmpkg.ps1 -Qcow2 out.qcow2 -Config vms.json -Out win11.vmpkg
#                  [-DiskName win11.qcow2] [-Compression auto|zstd|gzip|none] [-Threads N]
#                  [-AppVersion 0.0] [-AppVersionCode 1]

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Qcow2,
    [Parameter(Mandatory = $true)][string]$Config,
    [Parameter(Mandatory = $true)][string]$Out,
    [string]$DiskName = "",
    [string]$DiskFormat = "qcow2",
    [ValidateSet("auto", "zstd", "gzip", "none")][string]$Compression = "auto",
    [int]$Threads = 0,          # zstd worker threads; 0 = all logical processors
    [string]$AppVersion = "0.0",
    [int]$AppVersionCode = 1
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version 2

$ALIGN = 0x1000
$HEADER_SIZE = 24
$MANIFEST_VERSION = 1
$COMPRESSION_ID = @{ none = 0; gzip = 1; zstd = 3 }      # lib/archive/Compression.java
$COMPRESSION_NAME = @{ none = "NONE"; gzip = "GZIP"; zstd = "ZSTD" }
$ZSTD_LEVEL = 3                                            # the app's own export level

function Align-Up([long]$v, [long]$a = 0x1000) {
    return (($v + $a - 1) -band (-bnot ($a - 1)))
}

function Align-UpStrict([long]$v, [long]$a = 0x1000) {
    $aligned = Align-Up $v $a
    if ($aligned -eq $v) { return $v + $a }
    return $aligned
}

function Write-Zero([IO.Stream]$stream, [long]$n) {
    $zero = New-Object byte[] 65536
    while ($n -gt 0) {
        $step = [Math]::Min([long]$zero.Length, $n)
        $stream.Write($zero, 0, [int]$step)
        $n -= $step
    }
}

foreach ($p in @($Qcow2, $Config)) {
    if (-not (Test-Path -LiteralPath $p -PathType Leaf)) { throw "not found: $p" }
}
# .NET file APIs resolve relative paths against the process CWD, which is not PowerShell's
# location -- pin all three to absolute paths up front so nothing lands somewhere surprising.
$Qcow2 = (Resolve-Path -LiteralPath $Qcow2).ProviderPath
$Config = (Resolve-Path -LiteralPath $Config).ProviderPath
if (-not [IO.Path]::IsPathRooted($Out)) { $Out = Join-Path (Get-Location).ProviderPath $Out }
$tar = Get-Command tar.exe -ErrorAction SilentlyContinue
if (-not $tar) { throw "tar.exe not found (ships with Windows 10 1803+)" }
$tarVersion = (& $tar.Source --version 2>&1 | Out-String).Trim()
$hasZstd = $tarVersion -match "libzstd"
if ($Compression -eq "auto") {
    $Compression = if ($hasZstd) { "zstd" } else { "gzip" }
} elseif ($Compression -eq "zstd" -and -not $hasZstd) {
    throw "this tar.exe has no libzstd ($tarVersion); use -Compression gzip"
}
if ($Threads -le 0) { $Threads = [Math]::Max(1, [Environment]::ProcessorCount) }

# --- manifest ---------------------------------------------------------------------------
$vm = Get-Content -LiteralPath $Config -Raw -Encoding UTF8 | ConvertFrom-Json
# The exporter removes vm.disks (promoted to the top level, rebuilt on import) and vm.id.
foreach ($k in @("disks", "id")) {
    if ($vm.PSObject.Properties[$k]) { $vm.PSObject.Properties.Remove($k) }
}

if (-not $DiskName) { $DiskName = [IO.Path]::GetFileName($Qcow2) }
# Mirror the app's archive_path: the basename, stripped to a safe token.
$archivePath = [regex]::Replace([IO.Path]::GetFileName($DiskName), "[^A-Za-z0-9._-]", "_")
if (-not $archivePath) { $archivePath = "disk.img" }
$diskSize = (Get-Item -LiteralPath $Qcow2).Length

$manifest = [ordered]@{
    manifest_version = $MANIFEST_VERSION
    format           = "vmpkg"
    created_at       = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds()
    app_version      = $AppVersion
    app_version_code = $AppVersionCode
    app_build_type   = "release"
    compression      = $COMPRESSION_NAME[$Compression]
    vm               = $vm
    disks            = @([ordered]@{
        archive_path = $archivePath
        name         = $DiskName
        path         = ""
        format       = $DiskFormat
        size         = $diskSize
        readonly     = $false
        bus          = "virtio"
    })
    boots            = @()
    networks         = @()
}
$manifestJson = $manifest | ConvertTo-Json -Depth 32
$manifestBytes = [Text.Encoding]::UTF8.GetBytes($manifestJson)
if ($manifestBytes.Length -gt 0xFFFF) { throw "manifest too large: $($manifestBytes.Length) bytes (>64KiB)" }
if ($AppVersionCode -lt 0 -or $AppVersionCode -gt 0xFFFF) { throw "app_version_code out of u16 range: $AppVersionCode" }

# --- data section: tar.exe -> temp file -------------------------------------------------
# Staged so the tar holds exactly two members named exactly right; the disk is hardlinked
# (same-volume, instant) rather than copied, with a copy fallback for cross-volume temps.
$stage = Join-Path ([IO.Path]::GetTempPath()) ("vmpkg-" + [IO.Path]::GetRandomFileName())
New-Item -ItemType Directory -Path $stage | Out-Null
$dataTmp = Join-Path $stage "data.blob"
try {
    [IO.File]::WriteAllBytes((Join-Path $stage "manifest.json"), $manifestBytes)
    $diskStaged = Join-Path $stage $archivePath
    $qcow2Full = (Get-Item -LiteralPath $Qcow2).FullName
    try {
        New-Item -ItemType HardLink -Path $diskStaged -Target $qcow2Full -ErrorAction Stop | Out-Null
    } catch {
        Copy-Item -LiteralPath $qcow2Full -Destination $diskStaged
    }

    $tarArgs = @("-c", "--format", "gnutar")
    switch ($Compression) {
        "gzip" { $tarArgs += "-z" }
        "zstd" { $tarArgs += @("--zstd", "--options", "zstd:compression-level=$ZSTD_LEVEL,zstd:threads=$Threads") }
    }
    $tarArgs += @("-f", $dataTmp, "-C", $stage, "manifest.json", $archivePath)
    $sw = [Diagnostics.Stopwatch]::StartNew()
    & $tar.Source @tarArgs
    if ($LASTEXITCODE -ne 0) { throw "tar.exe failed with exit code $LASTEXITCODE" }
    $sw.Stop()
    $dataSize = (Get-Item -LiteralPath $dataTmp).Length

    # --- assemble ------------------------------------------------------------------------
    $f = [IO.File]::Open($Out, [IO.FileMode]::Create, [IO.FileAccess]::Write)
    try {
        Write-Zero $f $HEADER_SIZE                                   # header placeholder
        Write-Zero $f ((Align-Up $f.Position) - $f.Position)         # pad to 0x1000
        if ($f.Position -ne $ALIGN) { throw "layout bug: manifest at $($f.Position)" }
        $f.Write($manifestBytes, 0, $manifestBytes.Length)           # authoritative manifest
        Write-Zero $f ((Align-UpStrict $f.Position) - $f.Position)   # pad to alignUpStrict
        $dataStart = $f.Position
        if ($dataStart % $ALIGN -ne 0) { throw "layout bug: data at $dataStart" }

        $src = [IO.File]::OpenRead($dataTmp)
        try { $src.CopyTo($f, 1MB) } finally { $src.Close() }

        $dataEnd = $f.Position
        if (($dataEnd - $dataStart) -ne $dataSize) { throw "data copy mismatch" }
        Write-Zero $f ((Align-Up $dataEnd) - $dataEnd)               # trailing pad
        $f.SetLength($f.Position)                                    # nothing beyond the padding

        $hdr = New-Object byte[] $HEADER_SIZE
        [Text.Encoding]::ASCII.GetBytes("VMPKG").CopyTo($hdr, 0)     # [5] stays 0
        [BitConverter]::GetBytes([uint16]$MANIFEST_VERSION).CopyTo($hdr, 6)
        [BitConverter]::GetBytes([uint16]$AppVersionCode).CopyTo($hdr, 8)
        [BitConverter]::GetBytes([uint16]$manifestBytes.Length).CopyTo($hdr, 10)
        [BitConverter]::GetBytes([uint16]$COMPRESSION_ID[$Compression]).CopyTo($hdr, 12)
        # [14:16] reserved (volume_count) stays 0
        [BitConverter]::GetBytes([int64]$dataSize).CopyTo($hdr, 16)
        $f.Position = 0
        $f.Write($hdr, 0, $HEADER_SIZE)
    } finally {
        $f.Close()
    }
} finally {
    Remove-Item -LiteralPath $stage -Recurse -Force -ErrorAction SilentlyContinue
}

$total = (Get-Item -LiteralPath $Out).Length
Write-Host ("[vmpkg] wrote {0}" -f $Out)
Write-Host ("[vmpkg]   disk={0} ({1:N2} GiB)  compression={2}  data={3:N2} GiB  package={4:N2} GiB" -f `
    $archivePath, ($diskSize / 1GB), $Compression, ($dataSize / 1GB), ($total / 1GB))
Write-Host ("[vmpkg]   {0:N1}s, {1:N0} MB/s in{2}  ({3})" -f `
    $sw.Elapsed.TotalSeconds, ($diskSize / 1e6 / [Math]::Max($sw.Elapsed.TotalSeconds, 1e-6)), `
    $(if ($Compression -eq "zstd") { ", $Threads threads" } else { "" }), $tarVersion.Split("`n")[0].Trim())
Write-Host ("[vmpkg]   vm: memory_mb={0} cpu_count={1} swiotlb_mb={2}" -f `
    $vm.memory_mb, $vm.cpu_count, $vm.swiotlb_mb)

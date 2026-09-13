# preflight.ps1 - Pre-build cleanup for the DroidVM Windows builder.
#
# Detaches EVERY stale build VHDX (w11.vhdx) left behind by failed/killed runs,
# no matter which path it was mounted from, frees their drive letters, and
# deletes leftover vhdx files so the next "create vdisk" cannot collide.
# This removes the need to reboot after a failed or killed build.
#
# IMPORTANT: keep this file ASCII-only. Windows PowerShell 5.1 reads BOM-less
# .ps1 files as ANSI/GBK, and non-ASCII comments here previously broke parsing.
#
# Runs elevated via run_build.py right before build.ps1.
$ErrorActionPreference = 'SilentlyContinue'

$projectRoot = Split-Path $PSScriptRoot -Parent
$builderTmp  = Join-Path $PSScriptRoot "builder\tmp"
$adhocTmp    = $env:BUILD_TMP

Write-Host "[preflight] scanning for stale DroidVM build virtual disks ..."

# 1) Find every attached VHDX that belongs to our builder
$stalePaths = @()
$disks = @(Get-Disk -ErrorAction SilentlyContinue | Where-Object { $_.Location -like '*.vhdx' })
foreach ($d in $disks) {
    $path = $d.Location
    try {
        $img = $d | Get-DiskImage -ErrorAction SilentlyContinue
        if ($img -and $img.ImagePath) { $path = $img.ImagePath }
    } catch { }
    if (-not $path) { continue }
    # 我们的构建产物固定命名为 w11.vhdx，且一定是虚拟磁盘(.vhdx)。
    # 放宽判定：只要 Location 以 w11.vhdx 结尾即视为我们的残留盘，
    # 不再要求它在项目目录或 BUILD_TMP 内（之前 D:\droidvm_tmp\w11.vhdx
    # 因不在项目路径下被漏掉，导致上一轮失败残留的盘一直占着 W:/V: 盘符）。
    $isOurs = ($path -like '*w11.vhdx') -and ($path -like '*.vhdx')
    if ($isOurs) { $stalePaths += $path }
}

# 2) Detach each stale VHDX (this also releases all of its drive letters),
#    then delete the leftover file so "create vdisk" won't hit "file already exists".
foreach ($p in $stalePaths) {
    Write-Host ("[preflight] detaching stale vhdx: " + $p)
    # Dismount-DiskImage 是卸 VHDX 的规范 API，比 diskpart 脚本更可靠，先用它释放文件锁
    try { Dismount-DiskImage -ImagePath $p -ErrorAction SilentlyContinue } catch { }
    $dp = "select vdisk file=`"$p`"`r`noffline vdisk noerr`r`ndetach vdisk`r`nexit"
    $f = Join-Path $env:TEMP ("droidvm_pf_" + (Get-Random) + ".txt")
    $dp | Set-Content -Encoding ASCII $f
    diskpart /s $f | Out-Null
    Remove-Item $f -Force -ErrorAction SilentlyContinue
    if (Test-Path $p) {
        for ($k = 1; $k -le 12; $k++) {
            Start-Sleep -Milliseconds 500
            try { Remove-Item $p -Force -ErrorAction Stop; break } catch {}
        }
        if (Test-Path $p) { Write-Host ("[warn] could not remove stale vhdx file: " + $p) -ForegroundColor DarkYellow }
        else { Write-Host ("[preflight] removed stale vhdx file: " + $p) }
    }
}

# 3) Sweep any leftover w11.vhdx files that are not attached
$searchRoots = @($builderTmp, $projectRoot)
if ($adhocTmp) { $searchRoots += $adhocTmp }
# Also scan <any-drive>:\droidvm_tmp: the default WORK dir can land on any non-system drive,
# and $env:BUILD_TMP is NOT set yet when preflight runs, so a leftover w11.vhdx there (e.g.
# D:\droidvm_tmp\w11.vhdx) would otherwise be missed and block the next create vdisk.
try {
    foreach ($drv in (Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue)) {
        $searchRoots += (Join-Path ($drv.Name + ":") "droidvm_tmp")
    }
} catch {}
foreach ($root in $searchRoots) {
    if (-not (Test-Path $root)) { continue }
    Get-ChildItem $root -Recurse -Filter "w11*.vhdx" -ErrorAction SilentlyContinue | ForEach-Object {
        $attached = $false
        try { $attached = [bool](Get-DiskImage -ImagePath $_.FullName -ErrorAction SilentlyContinue) } catch { }
        if (-not $attached) {
            Remove-Item $_.FullName -Force -ErrorAction SilentlyContinue
            Write-Host ("[preflight] removed leftover vhdx file (not attached): " + $_.FullName)
        }
    }
}

# 4) Free candidate drive letters still held by any virtual disk
$cands = @('W', 'X', 'Y', 'V', 'Z', 'U', 'T', 'S', 'R', 'Q', 'P', 'N', 'M', 'L', 'K', 'J', 'H', 'G')
foreach ($c in $cands) {
    $vol = Get-Volume -DriveLetter $c -ErrorAction SilentlyContinue
    if (-not $vol) { continue }
    $disk = $null
    try { $disk = ($vol | Get-Partition -ErrorAction SilentlyContinue) | Get-Disk -ErrorAction SilentlyContinue } catch { }
    $isVirtual = $false
    if ($disk) {
        if ($disk.Location -like '*.vhdx') { $isVirtual = $true }
        if ($disk.FriendlyName -like '*Virtual*' -or $disk.FriendlyName -like '*Msft*') { $isVirtual = $true }
    }
    if ($isVirtual) {
        $dp = "select volume $c`r`nremove letter=$c noerr`r`nexit"
        $f = Join-Path $env:TEMP ("droidvm_pf_letter_" + (Get-Random) + ".txt")
        $dp | Set-Content -Encoding ASCII $f
        diskpart /s $f | Out-Null
        Remove-Item $f -Force -ErrorAction SilentlyContinue
        cmd /c "mountvol ${c}: /D" 2>$null | Out-Null
        Write-Host ("[preflight] freed stale drive letter " + $c + " (virtual disk)")
    }
}

Write-Host "[preflight] done"

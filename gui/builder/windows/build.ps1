<#
  build.ps1 - Offline build of a bootable, driver-included qcow2 from a Win11 ARM64 ISO + drivers.
  No Setup, no qemu boot: DISM apply-image + offline driver injection (no signature prompt) + bcdboot + bcdedit.
  First boot runs OOBE via unattend to create USER/autologon (non-interactive).

  Requirements: x64 Windows (Administrator); built-in dism/bcdboot/diskpart; qemu-img (QEMU for Windows, on PATH).

  Entry point: ..\windows_build.ps1 sets $env:SRC_ISO / $DRIVERS_DIR / ... then calls this.
  To run build.ps1 directly, set those $env: vars first, then (as Administrator):
    powershell -ExecutionPolicy Bypass -File build.ps1

  Cross-arch note: x64 host applying/injecting an ARM64 image + bcdboot usually works;
  if not, use ARM64 Windows/WinPE.

  ESD support: If the ISO contains install.esd instead of install.wim, it will be detected
  and used automatically. DISM /Apply-Image supports both formats natively.
#>
[CmdletBinding()]
param()
$ErrorActionPreference = 'Stop'

# --- Command echo helpers: print the command before executing it ---
function Format-CommandArg([AllowNull()][object]$Arg) {
    if ($null -eq $Arg) { return "''" }
    $s = [string]$Arg
    if ($s -eq '') { return "''" }
    if ($s -match '^[A-Za-z0-9_./:\\=-]+$') { return $s }
    return "'" + ($s -replace "'", "''") + "'"
}

function Format-CommandLine([string]$Command, [object[]]$Arguments = @()) {
    $parts = @((Format-CommandArg $Command))
    foreach ($a in $Arguments) { $parts += (Format-CommandArg $a) }
    return ($parts -join ' ')
}

function Show-CommandLine([string]$Command, [object[]]$Arguments = @()) {
    Write-Host ("> " + (Format-CommandLine $Command $Arguments)) -ForegroundColor DarkCyan
}

# --- Requires Administrator (diskpart/dism/bcdboot/mount all need it) ---
if (-not ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
        ).IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Host "Administrator required, relaunching elevated..." -ForegroundColor Yellow
    Show-CommandLine "Start-Process" @("powershell", "-ExecutionPolicy Bypass -File `"$PSCommandPath`"", "-Verb", "RunAs")
    Start-Process powershell "-ExecutionPolicy Bypass -File `"$PSCommandPath`"" -Verb RunAs
    exit
}

$HERE = $PSScriptRoot
$ROOT = Split-Path $HERE -Parent

# --- File-type input resolution: URL -> download into files\ then use; local path -> use directly; zip -> extract into files\ ---
function Resolve-InputFile([string]$Src, [string]$SaveAs = "") {
    if ($Src -match '^https?://') {
        $files = Join-Path $HERE "files"
        New-Item -ItemType Directory -Force $files | Out-Null
        if (-not $SaveAs) { $SaveAs = Split-Path ($Src -replace '\?.*$', '') -Leaf }
        $dst = Join-Path $files $SaveAs
        if (Test-Path $dst) { Write-Host "[files] already exists, skip download: $dst" }
        else {
            Write-Host "[files] download $Src -> $dst"
            Invoke-WebRequest -Uri $Src -OutFile "$dst.part" -UseBasicParsing
            Move-Item "$dst.part" $dst -Force
        }
        return $dst
    }
    if (-not (Test-Path $Src)) { throw "file not found: $Src" }
    return $Src
}

# Driver zip/folder source -> the "root directory" after extraction.
function Resolve-ZipRoot([string]$Src) {
    $p = Resolve-InputFile $Src "gunyah-arm64-drivers.zip"
    if (Test-Path $p -PathType Container) { return $p }
    if ($p -like "*.zip") {
        $dir = Join-Path (Join-Path $HERE "files") ([IO.Path]::GetFileNameWithoutExtension($p))
        if (Test-Path $dir) { Write-Host "[files] already extracted: $dir" }
        else { Write-Host "[files] extract $p -> $dir"; Expand-Archive $p -DestinationPath $dir -Force }
        return $dir
    }
    throw "driver source is neither a folder nor a zip: $p"
}

# Expand a leading ZIP prefix in DRIVER_DIR / DRIVER_CERT -> the zip extraction root
function Expand-ZipToken([string]$Path, [string]$ZipRoot) {
    if (-not $Path) { return $Path }
    if ($Path -eq 'ZIP') { return $ZipRoot }
    if ($Path -match '^ZIP[\\/](.*)$') { return ($ZipRoot.TrimEnd('\', '/') + '\' + ($Matches[1] -replace '/', '\')) }
    return $Path
}

# --- Helpers ---
function Assert-Exit([string]$what) {
    if ($LASTEXITCODE -ne 0) { throw "$what failed (exit code $LASTEXITCODE)" }
}

function Invoke-ExternalCommand {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string]$FilePath,
        [object[]]$ArgumentList = @(),
        [switch]$OutNull,
        [string]$What = ''
    )
    Show-CommandLine $FilePath $ArgumentList
    if ($OutNull) {
        & $FilePath @ArgumentList | Out-Null
    }
    else {
        & $FilePath @ArgumentList
    }
    if ($What) { Assert-Exit $What }
}

function Invoke-DiskPartScript {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string]$Path,
        [switch]$OutNull
    )
    Show-CommandLine "diskpart" @("/s", $Path)
    Write-Host "> diskpart script:" -ForegroundColor DarkCyan
    Get-Content $Path | ForEach-Object { Write-Host ("    " + $_) -ForegroundColor DarkCyan }
    # 捕获 diskpart 输出，便于调用方检测 assign 失败（盘符被占 / 卷离线等）。
    $outLines = @(& diskpart /s $Path 2>&1)
    if ($OutNull) {
        $outLines | Out-Null
    } else {
        $outLines | ForEach-Object { Write-Host ("    " + $_) -ForegroundColor DarkGray }
    }
    return ($outLines -join "`n")
}

# Pick a free drive letter
function Get-FreeDriveLetter([string[]]$Exclude = @()) {
    $used = New-Object System.Collections.Generic.HashSet[string]
    foreach ($l in (Get-Volume -ErrorAction SilentlyContinue).DriveLetter) { if ($l) { [void]$used.Add("$l".ToUpper()) } }
    foreach ($d in (Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue).Name) { if ($d.Length -eq 1) { [void]$used.Add($d.ToUpper()) } }
    try {
        $md = Get-Item 'HKLM:\SYSTEM\MountedDevices' -ErrorAction SilentlyContinue
        if ($md) { foreach ($p in $md.Property) { if ($p -match '^\\DosDevices\\([A-Z]):$') { [void]$used.Add($Matches[1]) } } }
    } catch {}
    foreach ($e in $Exclude) { [void]$used.Add("$e".ToUpper()) }
    foreach ($c in @('W', 'X', 'Y', 'Z', 'V', 'U', 'T', 'S', 'R', 'Q', 'P', 'N', 'M', 'L', 'K', 'J', 'H', 'G')) {
        if (-not $used.Contains($c)) { return $c }
    }
    throw "no free drive letter available"
}

# 选择最适合放构建产物（VHDX / qcow2 / DISM 临时目录）的盘符（不含冒号）。
# 优先非系统盘（C: 除外，因其空间通常紧张），若某非系统盘空闲 >= 20GB 则加权优先；
# 没有任何 >= 20GB 的盘时返回 $null（调用方回退到脚本所在盘）。
function Get-BestDataDrive {
    $cands = @(Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue | Where-Object { $_.Free -gt 0 })
    $best = $null; $bestScore = 0
    foreach ($d in $cands) {
        if ($d.Free -lt 20GB) { continue }
        $score = [double]$d.Free
        if ($d.Name -ne 'C') { $score += 1TB }   # 非系统盘加权优先
        if ($score -gt $bestScore) { $bestScore = $score; $best = $d.Name }
    }
    return $best
}

# Resolve the install image index. Supports both .wim and .esd.
# IMAGE_INDEX <= 0 -> list editions and let the user pick.
# Enumerate the images inside a WIM/ESD.
# The DISM PowerShell cmdlet Get-WindowsImage is fragile with .esd containers (it can
# throw PSArgumentException / require elevation), so dism.exe /Get-WimInfo is tried first
# and the cmdlet is only a fallback. Parses both English and localized output.
function Parse-ImageInfo([string]$text) {
    $list = New-Object System.Collections.ArrayList
    $cur = $null
    foreach ($line in ($text -split "`r?`n")) {
        $s = "$line".Trim()
        if ($s -match '^(Index|索引)\s*[:：]\s*(\d+)\s*$') {
            if ($cur) { [void]$list.Add($cur) }
            $cur = New-Object PSObject -Property @{ ImageIndex = [int]$Matches[2]; ImageName = "(unnamed)"; ImageSize = 0 }
        }
        elseif ($cur -and $s -match '^(Name|名称)\s*[:：]\s*(.+?)\s*$') {
            $cur.ImageName = $Matches[2]
        }
        elseif ($cur -and $s -match '^(Size|大小)\s*[:：]\s*([\d,\.\s]+)') {
            $clean = ($Matches[2] -replace '[^\d]', '')
            $n = [int64]0
            if ([int64]::TryParse($clean, [ref]$n)) { $cur.ImageSize = $n }
        }
    }
    if ($cur) { [void]$list.Add($cur) }
    return @($list)
}

function Get-ImageList([string]$imageFile) {
    # 标记"是否真正读到了映像元数据"。仅靠文件头字节合成的列表不算（说明文件已损坏）。
    $script:ImageEnumerationReliable = $false
    # 用 cmd 重定向拿到 dism 的**原始字节**，避免 PowerShell 按错误的控制台代码页解码
    # （本机实测：dism 必须提权，非提权环境返回 "错误: 740 需要提升权限才能运行 DISM"）。
    $tmpOut = Join-Path $env:TEMP "dvm_wiminfo.txt"
    Remove-Item -LiteralPath $tmpOut -Force -ErrorAction SilentlyContinue
    & cmd.exe /c "`"$env:SystemRoot\System32\dism.exe`" /Get-WimInfo `"/WimFile:$imageFile`" > `"$tmpOut`" 2>&1" | Out-Null
    if (Test-Path -LiteralPath $tmpOut) {
        $bytes = [IO.File]::ReadAllBytes($tmpOut)
        $cands = New-Object System.Collections.ArrayList
        [void]$cands.Add([Text.Encoding]::UTF8.GetString($bytes))
        [void]$cands.Add([Text.Encoding]::Unicode.GetString($bytes))
        try { [void]$cands.Add([Text.Encoding]::GetEncoding(936).GetString($bytes)) } catch { }
        try { [void]$cands.Add([Text.Encoding]::Default.GetString($bytes)) } catch { }
        foreach ($t in $cands) {
            $r = @(Parse-ImageInfo $t)
            if ($r.Count -gt 0) {
                Remove-Item -LiteralPath $tmpOut -Force -ErrorAction SilentlyContinue
                $script:ImageEnumerationReliable = $true
                return $r
            }
        }
        Write-Host "[image] dism 输出无法解析，原始输出前 12 行：" -ForegroundColor DarkYellow
        $i = 0
        foreach ($ln in (([string]$cands[0]) -split "`r?`n")) {
            if ($i -ge 12) { break }
            if ("$ln".Trim()) { Write-Host ("    " + $ln) -ForegroundColor DarkYellow; $i++ }
        }
        Remove-Item -LiteralPath $tmpOut -Force -ErrorAction SilentlyContinue
    }

    try {
        $imgs = @(Get-WindowsImage -ImagePath $imageFile -ErrorAction Stop)
        if ($imgs.Count -gt 0) {
            $script:ImageEnumerationReliable = $true
            return $imgs
        }
    } catch {
        Write-Host ("[image] Get-WindowsImage 不可用: " + $_.Exception.Message) -ForegroundColor DarkYellow
    }
    # 2b) wimlib（独立开源实现，随包附带）：DISM 对个别 esd 容器挑剔时，wimlib 往往仍可读。
    $wimlibExe = Get-WimlibExe
    if ($wimlibExe) {
        try {
            $null = @(& $wimlibExe info $imageFile 2>&1)
            if ($LASTEXITCODE -eq 0) {
                $wc = Get-WimImageCount $imageFile
                if ($wc -lt 1) { $wc = 1 }
                $wl = New-Object System.Collections.ArrayList
                for ($k = 1; $k -le $wc; $k++) {
                    [void]$wl.Add((New-Object PSObject -Property @{ ImageIndex = $k; ImageName = "Image $k (wimlib)"; ImageSize = 0 }))
                }
                Write-Host ("[image] 已用 wimlib 校验镜像，共 " + $wc + " 个映像") -ForegroundColor Cyan
                $script:ImageEnumerationReliable = $true
                return @($wl)
            }
            Write-Host "[image] wimlib 也无法打开该镜像" -ForegroundColor DarkYellow
        } catch {
            Write-Host ("[image] wimlib 执行失败: " + $_.Exception.Message) -ForegroundColor DarkYellow
        }
    }
    # 3) 文件头兜底：直接读取 WIM/ESD 头部的 dwImageCount（偏移 0x2C），
    #    无需提权、无需 DISM。为每个映像合成一条记录，这样无论是单映像还是
    #    多映像（含多版本 ISO 内的 install.esd/wim），都能通过 IMAGE_INDEX 或
    #    报错提示继续，而不是写死 "只有 1 个版本就用 index 1"。
    $cnt = Get-WimImageCount $imageFile
    if ($cnt -gt 0) {
        $syn = New-Object System.Collections.ArrayList
        for ($i = 1; $i -le $cnt; $i++) {
            [void]$syn.Add((New-Object PSObject -Property @{ ImageIndex = $i; ImageName = "Image $i (header)"; ImageSize = 0 }))
        }
        Write-Host "[image] DISM 枚举不可用 - 按文件头 dwImageCount=$cnt 合成映像列表（无版本名，请确认 IMAGE_INDEX 正确）" -ForegroundColor DarkYellow
        return @($syn)
    }
    return @()
}

# Read dwImageCount straight from the WIM/ESD header (offset 0x2C).
# Needs no elevation and no DISM - used as a last-resort fallback.
function Get-WimImageCount([string]$file) {
    try {
        $fs = [IO.File]::OpenRead($file)
        $buf = New-Object byte[] 0x30
        $n = $fs.Read($buf, 0, 0x30)
        $fs.Close()
        if ($n -lt 0x30) { return 0 }
        if ([Text.Encoding]::ASCII.GetString($buf, 0, 5) -ne 'MSWIM') { return 0 }
        return [int][BitConverter]::ToUInt32($buf, 0x2C)
    } catch { return 0 }
}

# Locate wimlib-imagex: PATH first, then the copy shipped next to this script
# (..\tools\wimlib\wimlib-imagex.exe). Returns the path, or $null when unavailable.
# wimlib is an independent open-source WIM/ESD implementation; it can enumerate and
# convert containers that the built-in DISM refuses to open.
function Get-WimlibExe {
    $c = Get-Command wimlib-imagex -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $bundled = Join-Path $HERE "..\tools\wimlib\wimlib-imagex.exe"
    if (Test-Path $bundled) { return $bundled }
    return $null
}

# Quick integrity sanity check on the source image, run BEFORE any disk work.
# A WIM/ESD is a fully packed archive, so a valid file never contains a long run of
# zero bytes. A large all-zero tail means the file was preallocated but never fully
# written (an interrupted download) - a size that looks right but content that is not.
# On this machine that exact case wasted a whole day: the header parsed fine, the disk
# section completed, and only DISM /Apply-Image failed at the very end with
# "invalid data (0x8007000d)". Detect it up front.
# Returns $true when the file looks complete (also when it cannot be judged).
function Test-ImageFileLooksComplete([string]$file) {
    $script:SourceCheckDetail = ""
    try {
        $fs = [IO.File]::OpenRead($file)
        try {
            $len = $fs.Length
            if ($len -lt 1MB) { return $true }

            # ---- (a) 判定性证据：读取文件头，取出它自己声明的元数据位置，然后去那里看 ----
            # 一个合法的 WIM/ESD，头部会写明 offset table 与 XML 元数据存放在文件第几字节。
            # 若那些位置读出来全是 0x00，则文件自相矛盾 —— 大小看着对、内容缺失，
            # 铁证如山，不需要任何外部参照物（DISM/wimlib 也正是死在这里）。
            if ($len -ge 208) {
                $hdr = New-Object byte[] 208
                $fs.Position = 0
                $hn = $fs.Read($hdr, 0, 208)
                if ($hn -ge 208 -and ([Text.Encoding]::ASCII.GetString($hdr, 0, 5) -eq "MSWIM")) {
                    foreach ($pair in @(@("offset table", 0x30), @("XML 元数据", 0x48))) {
                        $rName = [string]$pair[0]
                        $rField = [int]$pair[1]
                        $rOff = [BitConverter]::ToUInt64($hdr, $rField + 8)
                        $rSize = [BitConverter]::ToUInt64($hdr, $rField + 16)
                        if ($rOff -le 0 -or $rOff -ge $len -or $rSize -le 0) { continue }
                        $probeLen = [int][Math]::Min(4096, $len - $rOff)
                        if ($probeLen -le 0) { continue }
                        $pb = New-Object byte[] $probeLen
                        $fs.Position = [int64]$rOff
                        $pn = $fs.Read($pb, 0, $probeLen)
                        $allZero = $true
                        for ($k = 0; $k -lt $pn; $k++) { if ($pb[$k] -ne 0) { $allZero = $false; break } }
                        if ($allZero) {
                            $script:SourceCheckDetail = ("文件头自己声明 " + $rName +
                                " 存放在第 " + $rOff + " 字节（文件总长 " + $len +
                                " 字节），但该位置起全是 0x00 —— 文件自相矛盾，数据只写了一半")
                            return $false
                        }
                    }
                }
            }

            # ---- (b) 兜底：粗粒度全 0 覆盖扫描 ----
            $probe = 64KB
            $buf = New-Object byte[] $probe
            $probes = 24
            $zeroHits = 0
            for ($i = 0; $i -lt $probes; $i++) {
                $pos = [int64]($len * $i / $probes)
                if ($pos + $probe -gt $len) { $pos = $len - $probe }
                if ($pos -lt 0) { $pos = 0 }
                $fs.Position = $pos
                $n = $fs.Read($buf, 0, $probe)
                if ($n -le 0) { continue }
                $allZero = $true
                for ($k = 0; $k -lt $n; $k++) { if ($buf[$k] -ne 0) { $allZero = $false; break } }
                if ($allZero) { $zeroHits++ }
            }
            if ($zeroHits -ge [int]($probes / 3)) {
                $script:SourceCheckDetail = ("$probes 个均匀采样点里有 $zeroHits 个是整块 0x00")
                return $false
            }
            return $true
        } finally { $fs.Close() }
    } catch { return $true }
}

function Resolve-ImageIndex([string]$imageFile, [int]$wanted) {
    Show-CommandLine "dism.exe" @("/Get-WimInfo", "/WimFile:$imageFile")
    $images = @(Get-ImageList $imageFile)
    if ($images.Count -eq 0) {
        # 极度兜底：连文件头都读不到（文件损坏/加密/非 WIM-ESD）
        if ($wanted -gt 0) {
            Write-Host "[image] 无法读取映像列表，直接使用指定的 IMAGE_INDEX=$wanted" -ForegroundColor DarkYellow
            return $wanted
        }
        throw "无法读取映像信息: $imageFile（文件损坏/加密，或宿主 DISM 版本与映像不兼容）"
    }
    $valid = @($images | ForEach-Object { [int]$_.ImageIndex })
    if ($wanted -gt 0) {
        if ($valid -notcontains $wanted) {
            throw ("IMAGE_INDEX=$wanted not in this image. Available: " +
                (($images | ForEach-Object { "$($_.ImageIndex)=$($_.ImageName)" }) -join ', '))
        }
        return $wanted
    }
    if ($images.Count -eq 1) {
        Write-Host "[image] one edition only -> index $($valid[0]) ($($images[0].ImageName))"
        return $valid[0]
    }
    # 多版本：GUI 没有可交互的 stdin，绝不调用 Read-Host（会卡死构建）。
    # 直接报错，让用户显式设置 IMAGE_INDEX（GUI 里有对应字段）。
    $list = ($images | ForEach-Object { "[$($_.ImageIndex)] $($_.ImageName)" }) -join "`n  "
    throw ("该映像含 $($images.Count) 个版本，请在界面上显式设置 IMAGE_INDEX：`n  $list")
}

# =====================================================================
# Resolve config: environment variable (set by windows_build.ps1) > built-in default
# =====================================================================
$SRC_ISO      = if ($env:SRC_ISO)      { $env:SRC_ISO }      else { $null }
$DRIVERS_DIR  = if ($env:DRIVERS_DIR)  { $env:DRIVERS_DIR }   else { "https://github.com/Droid-VM/gunyah-guest-drivers-windows/releases/download/dev/gunyah-arm64-drivers.zip" }
$IMAGE_INDEX  = if ($env:IMAGE_INDEX)  { [int]$env:IMAGE_INDEX } else { 0 }  # 0 = list editions and prompt
$DISK_MB      = if ($env:DISK_SIZE_MB) { [int]$env:DISK_SIZE_MB } else { 40960 }
# 自动选择数据盘：把重负载产物（VHDX/qcow2/DISM 临时目录）放到空闲最大的非系统盘，
# 避免全部落到 C: 导致 DISM "磁盘空间不足" (exit 433)。用户显式指定时仍优先用其指定值。
$bestDrive = Get-BestDataDrive
$OUT_QCOW     = if ($env:OUT_QCOW)     { $env:OUT_QCOW }      else { $null }
if (-not $OUT_QCOW) {
    # 未指定输出路径 -> 放到最佳数据盘的 DroidVM 子目录（优先 D:）
    if ($bestDrive) { $OUT_QCOW = "$bestDrive`:\DroidVM\Windows.qcow2" } else { $OUT_QCOW = Join-Path $ROOT "Windows.qcow2" }
}
elseif (-not [System.IO.Path]::IsPathRooted($OUT_QCOW)) {
    # 用户只填了裸文件名/相对路径 -> 落到最佳数据盘，避免污染 C:
    if ($bestDrive) { $OUT_QCOW = Join-Path "$bestDrive`:\DroidVM" $OUT_QCOW } else { $OUT_QCOW = Join-Path $ROOT $OUT_QCOW }
}
$COMPRESS     = if ($env:COMPRESS)     { $env:COMPRESS }      else { "" }
$LETTER_ESP   = if ($env:LETTER_ESP)   { $env:LETTER_ESP }   else { Get-FreeDriveLetter }
$LETTER_WIN   = if ($env:LETTER_WIN)   { $env:LETTER_WIN }   else { Get-FreeDriveLetter @($LETTER_ESP) }

# --- 确保输出目录存在 ---
$outDir = Split-Path $OUT_QCOW -Parent
if ($outDir -and -not (Test-Path $outDir)) {
    Write-Host "[prep] 创建输出目录: $outDir" -ForegroundColor Cyan
    New-Item -ItemType Directory -Force $outDir | Out-Null
}

# Driver config
$DRIVER_DIR     = if ($env:DRIVER_DIR)     { $env:DRIVER_DIR }     else { "ZIP/drivers" }
$DRIVER_INSTALL = if ($env:DRIVER_INSTALL) { $env:DRIVER_INSTALL } else { "" }
$DRIVER_CERT    = if ($env:DRIVER_CERT)    { $env:DRIVER_CERT }    else { "" }
$DEBLOAT        = if ($env:DEBLOAT)        { $env:DEBLOAT }        else { "" }

# Account config
$USERNAME     = if ($env:DVM_USERNAME) { $env:DVM_USERNAME } else { "USER" }
$PASSWORD     = if ($env:DVM_PASSWORD) { $env:DVM_PASSWORD } elseif ($env:SSH_PASSWORD) { $env:SSH_PASSWORD } else { "DroidVM" }
# Computer name (Windows NetBIOS: ≤15 chars, alphanumeric + hyphen, cannot be all-numeric)
$COMPUTERNAME = if ($env:DVM_COMPUTERNAME) { $env:DVM_COMPUTERNAME } else { "DROIDVM" }
$COMPUTERNAME = ($COMPUTERNAME -replace '[^A-Za-z0-9-]', '').Trim('-')
if (-not $COMPUTERNAME) { $COMPUTERNAME = "DROIDVM" }
if ($COMPUTERNAME -match '^\d+$') { $COMPUTERNAME = "PC-$COMPUTERNAME" }
if ($COMPUTERNAME.Length -gt 15) { $COMPUTERNAME = $COMPUTERNAME.Substring(0, 15).TrimEnd('-') }
if (-not $COMPUTERNAME) { $COMPUTERNAME = "DROIDVM" }
$COMPUTERNAME = $COMPUTERNAME.ToUpper()
$SSH_PUBKEY   = if ($env:SSH_PUBKEY)   { $env:SSH_PUBKEY }   else { "" }
$OPENSSH_SRC  = if ($env:OPENSSH_SRC)  { $env:OPENSSH_SRC }  else { "" }

# Timezone config (auto-detect from host)
# 注意：不再覆盖系统语言/区域，保持镜像原语言
function Resolve-HostTimezone {
    if ($env:TARGET_TIMEZONE -and $env:TARGET_TIMEZONE -ne "") {
        return $env:TARGET_TIMEZONE
    }
    try {
        $tz = [System.TimeZoneInfo]::Local.Id
        return $tz
    } catch {
        return "UTC"
    }
}

$timezone = Resolve-HostTimezone
Write-Host "[locale] Language   : (keep image default, not overridden)"
Write-Host "[locale] TimeZone   : $timezone"

Write-Host "==== DroidVM Windows builder (DISM offline driver injection) ====" -ForegroundColor Cyan
Write-Host "[config] ISO=$SRC_ISO"
Write-Host "[config] Output=$OUT_QCOW"
Write-Host "[config] Username=$USERNAME"
Write-Host "[config] ComputerName=$COMPUTERNAME"
Write-Host "[config] DiskSize=$DISK_MB MB"
Write-Host "[config] Drivers=$(if ($DRIVERS_DIR) { $DRIVERS_DIR } else { '(none)' })"
Write-Host "[config] Compress=$(if ($COMPRESS) { 'yes' } else { 'no' })"
Write-Host "[config] Debloat=$(if ($DEBLOAT) { 'yes' } else { 'no (保留原版)' })"
Write-Host "[disk] drive letters: ESP=$LETTER_ESP Windows=$LETTER_WIN"
Write-Host ""

foreach ($t in @("dism", "bcdboot", "diskpart", "qemu-img")) {
    if (-not (Get-Command $t -ErrorAction SilentlyContinue)) {
        # 自动检测内置 qemu-img
        if ($t -eq "qemu-img") {
            $bundled = "$HERE\..\tools\qemu-img\qemu-img.exe"
            if (Test-Path $bundled) {
                $env:PATH = "$(Split-Path $bundled -Parent);$env:PATH"
                Write-Host "[qemu-img] using bundled: $bundled" -ForegroundColor Cyan
                continue
            }
        }
        throw "$t not found (qemu-img needs QEMU for Windows installed and on PATH)"
    }
}

if (-not $SRC_ISO) { throw "Invalid SRC_ISO: set the Win11 ARM64 ISO (URL or local path) in windows_build.ps1" }

$SRC_ISO = Resolve-InputFile $SRC_ISO "win11-arm64.iso"
# DISM 对本机的正斜杠路径敏感（/WimFile:、/Apply-Image 的 /ImageFile: 用 '/' 会报参数错误），
# 统一成反斜杠本地路径，规避 /Apply-Image exit 87。
$SRC_ISO = $SRC_ISO -replace '/', '\'

# Detect if the source is a raw WIM/ESD file (skip ISO mount)
$isWimEsdDirect = $false
if ($SRC_ISO -match '\.wim$') {
    $isWimEsdDirect = $true
    $imageType = "WIM"
    Write-Host "[source] $SRC_ISO is a raw WIM file - skipping ISO mount" -ForegroundColor Green
} elseif ($SRC_ISO -match '\.esd$') {
    $isWimEsdDirect = $true
    $imageType = "ESD"
    Write-Host "[source] $SRC_ISO is a raw ESD file - skipping ISO mount" -ForegroundColor Green
}

# 临时工作目录：默认放到最佳数据盘（优先 D:）的 droidvm_tmp；若设置了 BUILD_TMP 则优先使用它，
# 以免 C: 空间不足导致 DISM /Apply-Image 失败（exit 433）。
$WORK = if ($env:BUILD_TMP) { $env:BUILD_TMP } else {
    if ($bestDrive) { "$bestDrive`:\droidvm_tmp" } else { Join-Path $ROOT "tmp" }
}
# 空间预检（仅告警，不阻断）：VHDX 是稀疏盘(type=expandable)，只占 OS 真实体积，并非 DISK_MB 整盘。
# 精简版(Tiny11 类)释放后约 6GB；完整 Win11 ARM64 约 12-15GB。下面按保守上限估算峰值占用：
#   峰值 ≈ OS体积(VHDX实际占用) + qcow2(压缩则更小) + DISM scratch(与 VHDX 增长重叠，不简单相加)
try {
    $osFootprint = [math]::Min([int]($DISK_MB / 1024), 15)   # 实际只占 OS 体积；完整版上限按 ~15GB 估
    $qcow2Size   = if ($COMPRESS) { [int]($osFootprint * 0.5) } else { $osFootprint }
    $scratch     = [int]($osFootprint * 0.4)                 # DISM scratch 与写入重叠，远小于独立 5GB
    $needGB = [math]::Round($osFootprint + $qcow2Size + $scratch, 0)
    foreach ($p in @($WORK, (Split-Path $OUT_QCOW -Parent))) {
        if (-not $p) { continue }
        $drv = $p.Substring(0, 1)
        $psd = Get-PSDrive -Name $drv -ErrorAction SilentlyContinue
        if ($psd -and $psd.Free -lt ($needGB * 1GB)) {
            Write-Host ("[warn] drive " + $drv + ": free " + [math]::Round($psd.Free / 1GB, 1) + " GB < estimated need " + $needGB + " GB; DISM may fail with 'not enough disk space' (exit 433). Enable COMPRESS and/or reduce DiskSize.") -ForegroundColor DarkYellow
        }
    }
} catch {}
# BUILD_TMP 可能被设成盘符根目录（如 D:/ 或 D:\）。对根目录调用 New-Item 会抛
# CreateDirectoryArgumentError，因此先规范化路径并只在目录不存在时才创建。
if ($WORK) {
    $WORK = $WORK -replace '[\\/]+$', ''
    if ($WORK.Length -eq 2 -and $WORK[1] -eq ':') { $WORK = $WORK + '\' }
    if ([string]::IsNullOrWhiteSpace($WORK)) { $WORK = Join-Path $ROOT "tmp" }
}
if (-not (Test-Path -LiteralPath $WORK)) {
    Show-CommandLine "New-Item" @("-ItemType", "Directory", "-Force", $WORK)
    New-Item -ItemType Directory -Force -Path $WORK -ErrorAction SilentlyContinue | Out-Null
}
if (-not (Test-Path -LiteralPath $WORK)) {
    throw "工作目录不可用: $WORK (BUILD_TMP 指向不存在的盘符或无法创建)"
}
Write-Host "[work] temp dir = $WORK"
# 唯一文件名：每次构建带时间戳+PID 生成 w11_<ts>_<pid>.vhdx。
# 上一轮失败常残留一个仍 attached/被锁住的 w11.vhdx，导致 create vdisk 报
# "file already exists" 而整轮失败。唯一名彻底绕开锁死的残留文件，保证 create 必成功。
# 清理阶段会卸掉并删除所有旧 w11*.vhdx。qemu-img / Find-VhdxDisk 都引用本变量，无需改其它处。
$timestamp = (Get-Date -Format "yyyyMMdd_HHmmss") + "_" + $PID
$VHDX = Join-Path $WORK ("w11_" + $timestamp + ".vhdx")
$isoMounted = $false; $vhdAttached = $false

function Cleanup {
    if ($script:vhdAttached) {
        foreach ($L in @($script:LETTER_ESP, $script:LETTER_WIN)) {
            if ($L) {
                Show-CommandLine "cmd" @("/c", "mountvol ${L}: /D")
                & cmd /c "mountvol ${L}: /D" 2>$null | Out-Null
            }
        }
        $s = "select vdisk file=`"$VHDX`"`r`noffline vdisk noerr`r`ndetach vdisk`r`nexit"
        $f = Join-Path $WORK "detach.txt"
        $s | Out-File -Encoding ascii $f
        Invoke-DiskPartScript -Path $f -OutNull
    }
    if ($script:isoMounted) {
        Show-CommandLine "Dismount-DiskImage" @("-ImagePath", $SRC_ISO)
        Dismount-DiskImage -ImagePath $SRC_ISO | Out-Null
    }
}

try {
    # =====================================================================
    # === 1) Resolve driver source (URL -> download to files\; local zip/folder; zip -> extract) ===
    # =====================================================================
    if ($DRIVERS_DIR) {
        $zipRoot     = Resolve-ZipRoot $DRIVERS_DIR
        $DRIVER_DIR  = Expand-ZipToken $DRIVER_DIR  $zipRoot
        $DRIVER_CERT = Expand-ZipToken $DRIVER_CERT $zipRoot
        if (-not (Test-Path $DRIVER_DIR -PathType Container)) { throw "driver folder DRIVER_DIR not found: $DRIVER_DIR" }
        if ($DRIVER_CERT -and -not (Test-Path $DRIVER_CERT)) { throw "DRIVER_CERT not found: $DRIVER_CERT" }
        $drvDir = $DRIVER_DIR
        $instShow = if ($DRIVER_INSTALL) { $DRIVER_INSTALL } else { "(all)" }
        $certShow = if ($DRIVER_CERT) { Split-Path $DRIVER_CERT -Leaf } else { "(auto from .cat)" }
        Write-Host "[drivers] dir=$drvDir  install=$instShow  cert=$certShow"
    } else {
        Write-Host "[drivers] skipped (no driver source configured)" -ForegroundColor DarkYellow
        $drvDir = $null
    }

    # =====================================================================
    # === 2) Mount ISO (if needed) or use WIM/ESD directly, resolve image index ===
    # =====================================================================
    if ($isWimEsdDirect) {
        $wim = $SRC_ISO
        Write-Host "[image] using $imageType directly: $wim"
    } else {
        Show-CommandLine "Mount-DiskImage" @("-ImagePath", $SRC_ISO, "-PassThru")
        $mr = Mount-DiskImage -ImagePath $SRC_ISO -PassThru; $isoMounted = $true
        $isoLetter = ($mr | Get-Volume).DriveLetter
        Write-Host "[iso] mounted $SRC_ISO -> ${isoLetter}:"

        # ESD support: detect install.wim or install.esd
        $wim = $null
        $imageType = ""
        $wimPath = "${isoLetter}:\sources\install.wim"
        $esdPath = "${isoLetter}:\sources\install.esd"

        if (Test-Path $wimPath) {
            $wim = $wimPath
            $imageType = "WIM"
            Write-Host "[iso] found install.wim" -ForegroundColor Green
        } elseif (Test-Path $esdPath) {
            $wim = $esdPath
            $imageType = "ESD"
            Write-Host "[iso] found install.esd (ESD format)" -ForegroundColor Green
        } else {
            # Search for any .wim or .esd in sources
            $altWim = Get-ChildItem "${isoLetter}:\sources" -Filter "*.wim" -ErrorAction SilentlyContinue | Select-Object -First 1
            $altEsd = Get-ChildItem "${isoLetter}:\sources" -Filter "*.esd" -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($altWim) {
                $wim = $altWim.FullName
                $imageType = "WIM"
                Write-Host "[iso] found $($altWim.Name)" -ForegroundColor Green
            } elseif ($altEsd) {
                $wim = $altEsd.FullName
                $imageType = "ESD"
                Write-Host "[iso] found $($altEsd.Name)" -ForegroundColor Green
            } else {
                throw "No install.wim or install.esd found in ${isoLetter}:\sources\"
            }
        }
    }

    Write-Host "[iso] image format: $imageType"

    # =====================================================================
    # === 2b) 源镜像预校验：在任何磁盘操作之前就拦掉坏文件 ===
    # =====================================================================
    # 本机曾因一个"预分配但没下完"的 install.esd 浪费了一整天：文件头完全合法、磁盘段
    # 全部跑完，最后才在 DISM /Apply-Image 报 "无效数据 (0x8007000d)"。罪魁是文件后半段
    # 1.5GB 全是 0（下载工具预分配后中断）。这里提前两步校验，直接把问题挑明。
    $srcSizeGB = [math]::Round((Get-Item -LiteralPath $wim).Length / 1GB, 2)
    if ($env:DVM_ALLOW_INCOMPLETE_SRC -eq "1") {
        Write-Host "[image] DVM_ALLOW_INCOMPLETE_SRC=1 -> 跳过源镜像完整性校验（后果自负：DISM 极可能报 无效数据 0x8007000d）" -ForegroundColor DarkYellow
    } elseif (-not (Test-ImageFileLooksComplete $wim)) {
        $detail = if ($script:SourceCheckDetail) { $script:SourceCheckDetail } else { "文件里存在大段整块 0x00" }
        $wimlibExe = Join-Path $PSScriptRoot "..\tools\wimlib\wimlib-imagex.exe"
        throw ("源镜像文件不完整，已在任何磁盘操作之前停止构建。`n" +
               "  文件  : $wim`n" +
               "  大小  : $srcSizeGB GB（属性里的这个大小是下载工具预分配出来的，不代表数据已写满）`n" +
               "  诊断  : $detail`n" +
               "  原因  : 下载工具按完整长度预分配了文件，但只写进了前半部分，其余全是 0x00。`n" +
               "          DISM 与 wimlib 都会在读元数据时就失败，所以继续下去只会浪费时间。`n" +
               "  自检  : 下面这条命令不用管理员，能打印出 Image Count 才是好的文件；`n" +
               "          损坏时会报 Unable to parse the WIM file's XML document：`n" +
               "            $wimlibExe info `"$wim`"`n" +
               "  处理  : 换一份完整的 install.wim / install.esd，或直接提供官方 ARM64 ISO。`n" +
               "  强行继续：设置环境变量 DVM_ALLOW_INCOMPLETE_SRC=1（不推荐）。")
    }

    $IMAGE_INDEX = Resolve-ImageIndex $wim $IMAGE_INDEX

    # 只有真正解析到映像元数据才继续。若 DISM 与 wimlib 都读不出来（只靠文件头 dwImageCount
    # 合成的列表），说明文件已损坏 —— 继续下去必然在 DISM 阶段失败，立即报错避免再烧时间。
    if (-not $script:ImageEnumerationReliable) {
        throw ("无法读取源镜像的映像元数据：DISM 与本机 wimlib 都解析失败。`n" +
               "  文件很可能已损坏（例如下载不完整）。请更换 Windows 11 ARM64 的 install.wim / install.esd 后重试。`n" +
               "  文件: $wim (大小 $srcSizeGB GB)")
    }
    Write-Host "[image] using index $IMAGE_INDEX"

    # =====================================================================
    # === 3) Create + attach VHDX, GPT partition: ESP(FAT32) + MSR + Windows(NTFS) ===
    # =====================================================================
    Write-Host "[disk] creating and partitioning VHDX (max $DISK_MB MB) ..."
    Write-Host ("[disk] planned drive letters: ESP=$LETTER_ESP Windows=$LETTER_WIN") -ForegroundColor Cyan

    # 稳健方案（v3，根因修复）：diskpart 底层驱动，杜绝 PowerShell Set-Partition 在卷离线时静默失败。
    # 根因：通过 attach vdisk 附加的 VHDX，新卷默认因 Windows SAN 策略保持 Offline，必须
    #   先 online volume 才能 assign letter。上一版在 PowerShell 里 Set-Partition -IsOffline
    #   处理的是"分区"离线而非"卷"离线，且顺序错（先 assign 后 online volume），导致永远失败。
    # 本版：脚本 A 只建盘 + GPT 分区 + 格式化；脚本 B 对每个分区显式 online volume 再 assign，
    #   失败时自动重选空闲盘符重试（最多 4 次），彻底规避"盘符被占 / 卷离线"死循环。

    # ---- 脚本 A：建盘 + 分区 + 格式化（不分配盘符）----
    # 唯一文件名策略：本构建的 VHDX 已带时间戳+PID，绝不与上一轮残留文件重名，
    # 因此 create vdisk 不会因 "file already exists" 失败。下面只负责：
    #   (a) 卸掉所有仍附加的旧虚拟磁盘（释放被占的盘符/磁盘号，避免累积）；
    #   (b) 删除 $WORK 里除本次外的旧 w11*.vhdx 文件（仅当未被占用时）。
    # 这样彻底绕开"残留文件被锁死、删不掉、卡死构建"的历史顽疾。

    # (a) 卸掉所有已附加的虚拟磁盘（Dismount-DiskImage 比 diskpart 脚本更可靠）
    foreach ($oldD in @(Get-Disk -ErrorAction SilentlyContinue | Where-Object { $_.Location -ilike '*.vhdx*' })) {
        $oldP = $oldD.Location
        if (-not $oldP) { continue }
        try { Dismount-DiskImage -ImagePath $oldP -ErrorAction SilentlyContinue } catch {}
        Write-Host ("[disk] detached stale attached vhdx: $oldP") -ForegroundColor DarkGray
    }

    # (b) 删除 $WORK 内除本次外的旧 w11*.vhdx（先尝试卸掉再删；删不掉则忽略，不影响本构建）
    foreach ($oldF in @(Get-ChildItem $WORK -Filter "w11*.vhdx" -ErrorAction SilentlyContinue)) {
        if ($oldF.FullName -eq $VHDX) { continue }
        try { Dismount-DiskImage -ImagePath $oldF.FullName -ErrorAction SilentlyContinue } catch {}
        for ($k = 1; $k -le 6; $k++) {
            try { Remove-Item $oldF.FullName -Force -ErrorAction Stop; break } catch { Start-Sleep -Milliseconds 500 }
        }
    }

    # diskpart 只负责创建 + 附加 VHDX（不含 online/convert/分区，那些交给 PowerShell）。
    $dpA = @"
create vdisk file="$VHDX" maximum=$DISK_MB type=expandable
select vdisk file="$VHDX"
attach vdisk
exit
"@
    $dpAFile = Join-Path $WORK "part_create.txt"
    $dpA | Out-File -Encoding ascii $dpAFile
    $dpOut = Invoke-DiskPartScript -Path $dpAFile; $vhdAttached = $true

    # 按文件名定位刚附加的虚拟磁盘。必须在分区/盘符分配等所有调用之前定义，
    # 否则块作用域内函数不会提升（hoist），导致“函数不存在”错误。
    function Find-VhdxDisk {
        $d = @(Get-Disk -ErrorAction SilentlyContinue) | Where-Object { $_.Location -and ($_.Location -ilike "*$([IO.Path]::GetFileName($VHDX))*") } | Select-Object -First 1
        if (-not $d) { $d = @(Get-Disk -ErrorAction SilentlyContinue) | Where-Object { ($_.Location -ilike '*.vhdx*') -or ($_.FriendlyName -like '*Virtual*') } | Select-Object -First 1 }
        return $d
    }

    # attach 后等磁盘出现（慢存储栈），最多 10s
    for ($i = 1; $i -le 10; $i++) {
        if (Find-VhdxDisk) { break }
        Start-Sleep -Seconds 1
    }

    # 用 PowerShell 存储 cmdlet 上线磁盘 + 建 GPT 分区 + 格式化（逐步可验证、失败可重试）。
    # 本机新附加的虚拟磁盘常因 SAN/只读策略导致 diskpart online disk/convert gpt 失败并中止，
    # 改用 Set-Disk -IsOffline $false + Initialize-Disk -GPT + New-Partition + Format-Volume 更可靠。
    function Initialize-VhdxPartitions {
        $d = Find-VhdxDisk
        if (-not $d) { return $null }
        try { $d | Set-Disk -IsOffline $false -ErrorAction Stop } catch {}
        try { $d | Set-Disk -IsReadOnly $false -ErrorAction SilentlyContinue } catch {}
        for ($attempt = 1; $attempt -le 3; $attempt++) {
            try {
                if ($d.PartitionStyle -ne 'GPT') { $d | Initialize-Disk -PartitionStyle GPT -ErrorAction Stop }
                $esp = $d | New-Partition -Size 260MB -GptType '{c12a7328-f81f-11d2-ba4b-00a0c93ec93b}' -ErrorAction Stop
                try { $esp | Set-Partition -IsOffline $false -ErrorAction SilentlyContinue } catch {}
                try { (Get-Volume -Partition $esp -ErrorAction SilentlyContinue) | Set-Volume -Online -ErrorAction SilentlyContinue } catch {}
                $esp | Format-Volume -FileSystem FAT32 -NewFileSystemLabel System -Confirm:$false -ErrorAction Stop | Out-Null
                $d | New-Partition -Size 16MB -GptType '{e3c9e316-0b5c-4db8-817d-f92df00215ae}' -ErrorAction SilentlyContinue | Out-Null
                $win = $d | New-Partition -UseMaximumSize -GptType '{ebd0a0a2-b9e5-4433-87c0-68b6b72699c7}' -ErrorAction Stop
                try { $win | Set-Partition -IsOffline $false -ErrorAction SilentlyContinue } catch {}
                try { (Get-Volume -Partition $win -ErrorAction SilentlyContinue) | Set-Volume -Online -ErrorAction SilentlyContinue } catch {}
                $win | Format-Volume -FileSystem NTFS -NewFileSystemLabel Windows -Confirm:$false -ErrorAction Stop | Out-Null
                return $d
            } catch {
                Write-Host ("[disk] partition attempt $attempt failed: " + $_.Exception.Message) -ForegroundColor Yellow
                try { $d | Clear-Disk -RemoveData -Confirm:$false -ErrorAction SilentlyContinue } catch {}
                Start-Sleep -Seconds 2
            }
        }
        return $null
    }

    $disk = Initialize-VhdxPartitions
    if (-not $disk) { throw ("failed to initialize/partition VHDX after attach (diskpart output:`n" + (($dpOut | Out-String).Trim())) }

    # 用 diskpart 对指定分区 online volume + assign letter；返回 $true 表示成功。
    function Assign-PartitionLetter($disk, $partition, $letter) {
        # 兜底：先用 PowerShell 强制上线该分区与卷（diskpart online volume 的备用路径，
        # 覆盖 SAN 策略导致卷离线、diskpart 未自动上线的极端情况）。
        try { $partition | Set-Partition -IsOffline $false -ErrorAction SilentlyContinue } catch {}
        try { $vol = Get-Volume -Partition $partition -ErrorAction SilentlyContinue; if ($vol) { $vol | Set-Volume -Online -ErrorAction SilentlyContinue } } catch {}

        $script = @"
select vdisk file="$VHDX"
select partition $($partition.PartitionNumber)
remove all noerr
online volume noerr
assign letter=$letter
exit
"@
        $f = Join-Path $WORK ("part_assign_" + $partition.PartitionNumber + ".txt")
        $script | Out-File -Encoding ascii $f
        $out = Invoke-DiskPartScript -Path $f
        # diskpart 失败时会输出包含以下关键词的行（中英文均覆盖）
        $bad = ($out -split "`r?`n") | Where-Object { $_ -match '拒绝|找不到|已在使用|the system cannot|no such|error' }
        if ($bad) { return $false }
        # 复核：盘符确实分配到了该分区、且该分区在本 VHDX 磁盘上
        try {
            $pp = Get-Partition -DriveLetter $letter -ErrorAction SilentlyContinue
            if ($pp -and $pp.PartitionNumber -eq $partition.PartitionNumber -and $pp.DiskNumber -eq $disk.Number) { return $true }
        } catch {}
        return $false
    }

    # ---- 定位分区并分配盘符 ----
    $parts = @($disk | Get-Partition -ErrorAction SilentlyContinue)
    $esp = @($parts | Where-Object { $_.Type -imatch 'System|ESP' }) | Select-Object -First 1
    $win = @($parts | Where-Object { $_.Type -imatch 'Basic|IFS' }) | Select-Object -First 1
    if (-not $win) { $win = @($parts | Where-Object { $_.Size -gt 1GB } | Sort-Object Size -Descending) | Select-Object -First 1 }

    # 给分区挂载/分配盘符（优先 PowerShell Set-Partition -NewDriveLetter，可绕过本机 diskpart
    # 对 ESP 的拒绝；失败再 diskpart assign 兜底）。返回实际盘符或 $null。
    function Mount-Partition($partition, $desired) {
        if ($partition.DriveLetter) { return $partition.DriveLetter }
        $tries = @($desired) + @(Get-FreeDriveLetter)
        foreach ($l in $tries) {
            if (-not $l) { continue }
            try { $partition | Set-Partition -NewDriveLetter $l -ErrorAction Stop; Start-Sleep -Milliseconds 300; return $l } catch {}
            try { $partition | Set-Partition -IsOffline $false -ErrorAction SilentlyContinue } catch {}
            try { (Get-Volume -Partition $partition -ErrorAction SilentlyContinue) | Set-Volume -Online -ErrorAction SilentlyContinue } catch {}
            $script = @"
select vdisk file="$VHDX"
select partition $($partition.PartitionNumber)
remove all noerr
online volume noerr
assign letter=$l
exit
"@
            $f = Join-Path $WORK ("part_assign_" + $partition.PartitionNumber + ".txt")
            $script | Out-File -Encoding ascii $f
            try { Invoke-DiskPartScript -Path $f -OutNull } catch {}
            try {
                $pp = Get-Partition -DriveLetter $l -ErrorAction SilentlyContinue
                if ($pp -and $pp.PartitionNumber -eq $partition.PartitionNumber -and $pp.DiskNumber -eq $disk.Number) { return $l }
            } catch {}
        }
        return $null
    }

    $espLetter = $null; $winLetter = $null
    # Windows 分区通常已自动获得盘符（移除 -NoDefaultDriveLetter 后）；否则显式分配
    if ($win) { $winLetter = Mount-Partition $win (Get-FreeDriveLetter @('W', $LETTER_ESP, $LETTER_WIN)) }
    # ESP：本机 diskpart 拒绝 assign，优先 PowerShell；失败则留空（bcdboot 改为自动识别 ESP）
    if ($esp) { $espLetter = Mount-Partition $esp (Get-FreeDriveLetter @($winLetter, 'W', $LETTER_ESP, $LETTER_WIN)) }

    # ---- 就绪判定 ----
    # Windows(NTFS) 需真正可访问（DISM /Apply-Image 只写它）；ESP 只需盘符已分配（供 bcdboot /s 使用）。
    $mounted = $false
    if ($winLetter -and $espLetter) {
        $winReady = $false
        try {
            $wv = Get-Volume -DriveLetter $winLetter -ErrorAction SilentlyContinue
            if ($wv -and $wv.FileSystemType -eq 'NTFS' -and (Test-Path "${winLetter}:\")) { $winReady = $true }
        } catch {}
        if (-not $winReady) {
            # 兜底：显式上线卷/分区再试一次（应对慢存储栈）
            try { (Get-Volume -DriveLetter $winLetter -ErrorAction SilentlyContinue) | Set-Volume -Online -ErrorAction SilentlyContinue } catch {}
            try { (Get-Partition -DriveLetter $winLetter -ErrorAction SilentlyContinue) | Set-Partition -IsOffline $false -ErrorAction SilentlyContinue } catch {}
            Start-Sleep -Seconds 1
            try {
                $wv = Get-Volume -DriveLetter $winLetter -ErrorAction SilentlyContinue
                if ($wv -and $wv.FileSystemType -eq 'NTFS' -and (Test-Path "${winLetter}:\")) { $winReady = $true }
            } catch {}
        }
        if ($winReady) {
            $script:LETTER_WIN = $winLetter
            $script:LETTER_ESP = $espLetter
            $mounted = $true
        }
    }
    $W = "${LETTER_WIN}:"; $S = "${LETTER_ESP}:"
    if (-not $mounted) {
        $diag = ""
        try {
            $d = Find-VhdxDisk
            if ($d) {
                $diag += "`n[diag] disk: Number=$($d.Number) OperationalStatus=$($d.OperationalStatus) IsOffline=$($d.IsOffline) Location=$($d.Location)"
                foreach ($p in @($d | Get-Partition -ErrorAction SilentlyContinue)) {
                    $diag += "`n[diag]   part Type=$($p.Type) #=$($p.PartitionNumber) Size=$([math]::Round($p.Size/1MB))MB Letter=[$($p.DriveLetter)] IsOffline=$($p.IsOffline)"
                    try { $v = Get-Volume -Partition $p -ErrorAction SilentlyContinue; if ($v) { $diag += " volFS=$($v.FileSystemType) volOffline=$($v.IsOffline)" } } catch {}
                }
            } else { $diag += "`n[diag] VHDX disk not found via Get-Disk" }
        } catch { $diag += "`n[diag] diagnostic error: $($_.Exception.Message)" }
        throw ("diskpart did not bring volumes online (ESP=$LETTER_ESP WIN=$LETTER_WIN). create diskpart output:`n$(( $dpOut | Out-String ).Trim())$diag")
    }
    Write-Host "[disk] mounted: ESP=$LETTER_ESP Windows=$LETTER_WIN"

    # =====================================================================
    # === 4) Apply image (supports both WIM and ESD) ===
    # =====================================================================
    # DISM 对 ESD 固态压缩 / 正斜杠路径较敏感，/Apply-Image 可能报 exit 87。
    # 这里：统一反斜杠路径 -> 捕获真实输出（不再用 -OutNull 把错误吞掉）-> 写 /LogPath；
    # 若直接 Apply 失败，按官方建议先用 /Export-Image 把 ESD 转成 WIM 再 Apply（对 87 有效）。
    $wimPath = ($wim -replace '/', '\')
    Write-Host "[dism] applying $imageType -> $W\ ..."
    $applyLog = Join-Path $env:TEMP "droidvm-dism-apply.log"
    $applyArgs = @("/Apply-Image", "/ImageFile:$wimPath", "/Index:$IMAGE_INDEX", "/ApplyDir:$W\", "/ScratchDir:$WORK", "/LogPath:$applyLog")
    Show-CommandLine "dism" $applyArgs
    $applyOut = @(& dism @applyArgs 2>&1)
    $applyOut | ForEach-Object { Write-Host ("  " + $_) }
    $applyCode = $LASTEXITCODE
    if ($applyCode -ne 0) {
        Write-Host ("[dism] /Apply-Image on $imageType failed (exit $applyCode); converting to WIM and retrying ...") -ForegroundColor Yellow
        $convWim = Join-Path $WORK "install_from_esd.wim"
        $convOk = $false
        Remove-Item -LiteralPath $convWim -Force -ErrorAction SilentlyContinue
        # 优先用 wimlib 转换：它比 DISM 更能容错某些 esd 容器（DISM 的 /Export-Image 可能同样报错）
        $wimlibExe = Get-WimlibExe
        if ($wimlibExe) {
            Show-CommandLine $wimlibExe @("export", $wimPath, "$IMAGE_INDEX", $convWim, "--compress=LZX")
            $wlConv = @(& $wimlibExe export $wimPath "$IMAGE_INDEX" $convWim "--compress=LZX" 2>&1)
            $wlConv | ForEach-Object { Write-Host ("  " + $_) }
            if ($LASTEXITCODE -eq 0 -and (Test-Path $convWim)) { $convOk = $true }
        }
        if (-not $convOk) {
            $convArgs = @("/Export-Image", "/SourceImageFile:$wimPath", "/SourceIndex:$IMAGE_INDEX", "/DestinationImageFile:$convWim", "/Compress:max")
            Show-CommandLine "dism" $convArgs
            $convOut = @(& dism @convArgs 2>&1)
            $convOut | ForEach-Object { Write-Host ("  " + $_) }
            if ($LASTEXITCODE -eq 0 -and (Test-Path $convWim)) { $convOk = $true }
        }
        if ($convOk) {
            $retryArgs = @("/Apply-Image", "/ImageFile:$convWim", "/Index:1", "/ApplyDir:$W\", "/ScratchDir:$WORK", "/LogPath:$applyLog")
            Show-CommandLine "dism" $retryArgs
            $retryOut = @(& dism @retryArgs 2>&1)
            $retryOut | ForEach-Object { Write-Host ("  " + $_) }
            $applyCode = $LASTEXITCODE
        } else {
            Write-Host "[dism] WIM conversion failed; cannot fall back to WIM" -ForegroundColor Red
        }
    }
    if ($applyCode -ne 0) { throw "dism /Apply-Image failed (exit code $applyCode); see $applyLog" }

    # =====================================================================
    # === 5) Offline driver injection (no signature prompt) ===
    # =====================================================================
    if ($drvDir) {
        if ($DRIVER_INSTALL) {
            foreach ($d in ($DRIVER_INSTALL -split '\s+' | Where-Object { $_ })) {
                $sub = Join-Path $drvDir $d
                if (Test-Path $sub -PathType Container) {
                    Write-Host "[dism] inject driver: $d"
                    Invoke-ExternalCommand -FilePath "dism" -ArgumentList @("/Image:$W\", "/Add-Driver", "/Driver:$sub", "/Recurse", "/ForceUnsigned") -OutNull -What "dism /Add-Driver $d"
                } else { Write-Host " [warn] driver $d to install does not exist in $drvDir" -ForegroundColor DarkYellow }
            }
        } else {
            Write-Host "[dism] injecting all drivers offline ..."
            Invoke-ExternalCommand -FilePath "dism" -ArgumentList @("/Image:$W\", "/Add-Driver", "/Driver:$drvDir", "/Recurse", "/ForceUnsigned") -OutNull -What "dism /Add-Driver"
        }

        # === 5b) Extract driver signer certs (offline) -> stage for first-boot trust ===
        Write-Host "[certs] staging driver signer cert(s) offline ..."
        $certDir = "$W\DroidVM\certs"
        New-Item -ItemType Directory -Force $certDir | Out-Null
        if ($DRIVER_CERT) {
            Copy-Item $DRIVER_CERT (Join-Path $certDir (Split-Path $DRIVER_CERT -Leaf)) -Force
            Write-Host "[certs] using the specified DRIVER_CERT: $(Split-Path $DRIVER_CERT -Leaf)"
        } else {
            $seenThumb = @{}
            Get-ChildItem $drvDir -Recurse -Include *.cat, *.sys, *.dll, *.exe -ErrorAction SilentlyContinue | ForEach-Object {
                try {
                    $cert = (Get-AuthenticodeSignature $_.FullName).SignerCertificate
                    if ($cert -and -not $seenThumb.ContainsKey($cert.Thumbprint)) {
                        $seenThumb[$cert.Thumbprint] = $true
                        Export-Certificate -Cert $cert -FilePath (Join-Path $certDir "$($cert.Thumbprint).cer") | Out-Null
                    }
                } catch {}
            }
            Get-ChildItem $drvDir -Recurse -Filter *.cer -ErrorAction SilentlyContinue |
                ForEach-Object { Copy-Item $_.FullName (Join-Path $certDir $_.Name) -Force }
            Write-Host "[certs] auto-extracted $($seenThumb.Count) cert(s) from driver .cat -> $certDir"
        }
    } else {
        Write-Host "[drivers] skipping driver injection (no driver source)" -ForegroundColor DarkYellow
    }

    # =====================================================================
    # === 6) Debloat (offline removal of provisioned Appx) ===
    # =====================================================================
    Write-Host "[debloat] removing extra provisioned Appx offline ..."
    $keep = 'VCLibs|NET\.Native|UI\.Xaml|Store|SecHealth|Photos|Notepad|Terminal|WindowsTerminal'
    try {
        Get-AppxProvisionedPackage -Path "$W\" | Where-Object { $_.DisplayName -notmatch $keep } | ForEach-Object {
            try {
                Show-CommandLine "Remove-AppxProvisionedPackage" @("-Path", "$W\", "-PackageName", $_.PackageName)
                Remove-AppxProvisionedPackage -Path "$W\" -PackageName $_.PackageName | Out-Null
            } catch {}
        }
    } catch { Write-Host " (skipping debloat: $($_.Exception.Message))" -ForegroundColor DarkYellow }

    # === 6b) Offline shrink: WinSxS ResetBase + disable hibernate ===
    Write-Host "[debloat] WinSxS component cleanup (ResetBase, offline) ..."
    try {
        Invoke-ExternalCommand -FilePath "dism" -ArgumentList @("/Image:$W\", "/Cleanup-Image", "/StartComponentCleanup", "/ResetBase") -OutNull -What "dism /Cleanup-Image /ResetBase"
    } catch {
        Write-Host " (skipping ResetBase: $($_.Exception.Message))" -ForegroundColor DarkYellow
    }

    Write-Host "[debloat] disabling hibernate offline (no hiberfil.sys) ..."
    $sysHive = "$W\Windows\System32\config\SYSTEM"
    $hiveLoaded = $false
    try {
        Invoke-ExternalCommand -FilePath "reg" -ArgumentList @("load", "HKLM\DVMOFF", $sysHive) -OutNull -What "reg load SYSTEM hive"
        $hiveLoaded = $true
        foreach ($cs in @("ControlSet001", "ControlSet002")) {
            $pk = "HKLM\DVMOFF\$cs\Control\Power\"
            reg query $pk *> $null
            if ($LASTEXITCODE -eq 0) {
                Show-CommandLine "reg add" @($pk, "/v", "HibernateEnabled", "/t", "REG_DWORD", "/d", "0", "/f")
                reg add $pk /v HibernateEnabled /t REG_DWORD /d 0 /f *> $null
                reg add $pk /v HibernateEnabledDefault /t REG_DWORD /d 0 /f *> $null
            }
            $tk = "HKLM\DVMOFF\$cs\Control\Terminal Server\"
            reg query $tk *> $null
            if ($LASTEXITCODE -eq 0) {
                Show-CommandLine "reg add" @($tk, "/v", "fDenyTSConnections", "/t", "REG_DWORD", "/d", "0", "/f")
                reg add $tk /v fDenyTSConnections /t REG_DWORD /d 0 /f *> $null
            }
            # Set timezone information offline
            $tzInfo = "HKLM\DVMOFF\$cs\Control\TimeZoneInformation\"
            reg query $tzInfo *> $null
            if ($LASTEXITCODE -eq 0) {
                Show-CommandLine "reg add" @($tzInfo, "/v", "TimeZoneKeyName", "/t", "REG_SZ", "/d", "$timezone", "/f")
                reg add $tzInfo /v TimeZoneKeyName /t REG_SZ /d "$timezone" /f *> $null
                reg add $tzInfo /v ActiveTimeBias /t REG_DWORD /d 0 /f *> $null
                reg add $tzInfo /v Bias /t REG_DWORD /d 0 /f *> $null
            }
        }
    } catch {
        Write-Host " (skipping hibernate-off: $($_.Exception.Message))" -ForegroundColor DarkYellow
    } finally {
        if ($hiveLoaded) {
            [gc]::Collect(); [gc]::WaitForPendingFinalizers()
            reg unload HKLM\DVMOFF *> $null
        }
    }

    # === 6c) Disable Reserved Storage (offline) ===
    Write-Host "[debloat] disabling Reserved Storage offline (ShippedWithReserves=0) ..."
    $softHive = "$W\Windows\System32\config\SOFTWARE"
    $softLoaded = $false
    try {
        Invoke-ExternalCommand -FilePath "reg" -ArgumentList @("load", "HKLM\DVMSOFT", $softHive) -OutNull -What "reg load SOFTWARE hive"
        $softLoaded = $true
        $rk = "HKLM\DVMSOFT\Microsoft\Windows\CurrentVersion\ReserveManager\"
        Show-CommandLine "reg add" @($rk, "/v", "ShippedWithReserves", "/t", "REG_DWORD", "/d", "0", "/f")
        reg add $rk /v ShippedWithReserves /t REG_DWORD /d 0 /f *> $null
    } catch {
        Write-Host " (skipping Reserved Storage disable: $($_.Exception.Message))" -ForegroundColor DarkYellow
    } finally {
        if ($softLoaded) {
            [gc]::Collect(); [gc]::WaitForPendingFinalizers()
            reg unload HKLM\DVMSOFT *> $null
        }
    }

    # =====================================================================
    # === 7) Boot files + BCD (bcdboot uses the ARM64 bootmgr from the image) ===
    # =====================================================================
    Write-Host "[boot] bcdboot + BCD ..."
    Invoke-ExternalCommand -FilePath "bcdboot" -ArgumentList @("$W\Windows\", "/s", $S, "/f", "UEFI") -OutNull -What "bcdboot"
    $BCD = "$S\EFI\Microsoft\Boot\BCD"
    Invoke-ExternalCommand -FilePath "bcdedit" -ArgumentList @("/store", $BCD, "/set", "{default}", "testsigning", "on") -OutNull -What "bcdedit testsigning"
    Invoke-ExternalCommand -FilePath "bcdedit" -ArgumentList @("/store", $BCD, "/set", "{default}", "nointegritychecks", "on") -OutNull -What "bcdedit nointegritychecks"

    # =====================================================================
    # === 7b) Debloat (可选，$DEBLOAT 启用时执行) ===
    # =====================================================================
    if ($DEBLOAT) {
        Write-Host ""
        Write-Host "==== 离线 Debloat ====" -ForegroundColor Cyan
        $debloatScript = Join-Path $HERE "debloat.ps1"
        if (Test-Path $debloatScript) {
            & $debloatScript -WinDir "$W\Windows"
        } else {
            Write-Host "[debloat] debloat.ps1 未找到，跳过" -ForegroundColor DarkYellow
        }
        Write-Host ""
    } else {
        Write-Host "[debloat] 未启用，保留原版 Windows (含应用商店/广告)" -ForegroundColor DarkGray
    }

    # =====================================================================
    # === 8) OOBE unattend (create USER / autologon) ===
    # =====================================================================
    Show-CommandLine "New-Item" @("-ItemType", "Directory", "-Force", "$W\Windows\Panther")
    New-Item -ItemType Directory -Force "$W\Windows\Panther" | Out-Null
    $unattendSrc = Join-Path $HERE "unattend.xml"
    $esc = { param($s) $s.Replace('&','&amp;').Replace('<','&lt;').Replace('>','&gt;').Replace('"','&quot;') }
    $unattendXml = (Get-Content $unattendSrc -Raw)
    $unattendXml = $unattendXml.Replace('@@USERNAME@@', (& $esc $USERNAME))
    $unattendXml = $unattendXml.Replace('@@PASSWORD@@', (& $esc $PASSWORD))
    $unattendXml = $unattendXml.Replace('@@COMPUTERNAME@@', (& $esc $COMPUTERNAME))
    $unattendXml = $unattendXml.Replace('@@HASPASSWORD@@', $(if ($PASSWORD) { '1' } else { '0' }))
    $unattendXml = $unattendXml.Replace('@@TIMEZONE@@', $timezone)
    Show-CommandLine "Set-Content" @("$W\Windows\Panther\unattend.xml", "(unattend.xml + locale + password)")
    [System.IO.File]::WriteAllText("$W\Windows\Panther\unattend.xml", $unattendXml, (New-Object System.Text.UTF8Encoding($false)))
    Write-Host "[oobe] unattend.xml placed (first boot creates USER, ComputerName=$COMPUTERNAME, autologon, RDP, language=image default, tz=$timezone)"

    # === 8c) Stage SSH payload into image (offline) ===
    $stage = "$W\DroidVM\"
    New-Item -ItemType Directory -Force $stage | Out-Null
    Copy-Item (Join-Path $HERE "setup-ssh.ps1") "$stage\setup-ssh.ps1" -Force

    if ($drvDir) {
        $pvmDevnode = Join-Path $zipRoot "pvmpower-devnode.ps1"
        if (Test-Path $pvmDevnode) { Copy-Item $pvmDevnode "$stage\pvmpower-devnode.ps1" -Force; Write-Host "[pvmpower] staged pvmpower-devnode.ps1" }
    }

    if ($OPENSSH_SRC) {
        try {
            $sshLocal = Resolve-InputFile $OPENSSH_SRC
            Copy-Item $sshLocal (Join-Path $stage (Split-Path $sshLocal -Leaf)) -Force
            Write-Host "[ssh] staged $(Split-Path $sshLocal -Leaf)"
        } catch {
            Write-Host "[ssh] OpenSSH staging failed -> RDP only: $($_.Exception.Message)" -ForegroundColor DarkYellow
        }
    } else {
        Write-Host "[ssh] `$OPENSSH_SRC empty -> do not install SSH (RDP only)" -ForegroundColor DarkYellow
    }

    if ($SSH_PUBKEY) {
        $akText = ($SSH_PUBKEY -replace "`r`n", "`n" -replace "`r", "`n").TrimEnd("`n") + "`n"
        [System.IO.File]::WriteAllText("$stage\authorized_keys", $akText, (New-Object System.Text.UTF8Encoding($false)))
        Write-Host "[ssh] staged authorized_keys from `$SSH_PUBKEY (key login)"
    } else {
        Write-Host "[ssh] `$SSH_PUBKEY not set -> password login only" -ForegroundColor DarkYellow
    }

    # === 8b) ReTrim so debloat/cleanup actually shrinks the image ===
    Write-Host "[shrink] Optimize-Volume -ReTrim on $W ..."
    try {
        Show-CommandLine "Optimize-Volume" @("-DriveLetter", $LETTER_WIN, "-ReTrim")
        Optimize-Volume -DriveLetter $LETTER_WIN -ReTrim -ErrorAction Stop
    } catch {
        Write-Host " (ReTrim skipped: $($_.Exception.Message))" -ForegroundColor DarkYellow
    }

    # =====================================================================
    # === 9) Detach VHDX -> convert to qcow2 ===
    # =====================================================================
    # 初始化构建成功标识（在脚本最开始已设为 $false）
    $script:BuildSucceeded = $false
    Cleanup; $vhdAttached = $false; $isoMounted = $false
    Write-Host "[qcow2] converting -> $OUT_QCOW ..."
    $convArgs = @("convert", "-p")
    if ($COMPRESS) { $convArgs += "-c" }
    $convArgs += @("-O", "qcow2", $VHDX, $OUT_QCOW)
    Invoke-ExternalCommand -FilePath "qemu-img" -ArgumentList $convArgs -What "qemu-img convert"
    $sz = "{0:N1} GB" -f ((Get-Item $OUT_QCOW).Length / 1GB)
    Write-Host "Done -> $OUT_QCOW ($sz)" -ForegroundColor Green
    # 标记构建成功
    $script:BuildSucceeded = $true

} finally {
    Cleanup
    # --- 卸掉所有仍附加的、属于本工作目录的虚拟磁盘（释放文件锁，便于删除）---
    # 上一轮失败常残留一个仍 attached 的 w11*.vhdx，其文件被锁导致删不掉；
    # 先用 Dismount-DiskImage 释放，再删除。Dismount-DiskImage 比 diskpart 脚本更可靠。
    try {
        foreach ($d in @(Get-Disk -ErrorAction SilentlyContinue | Where-Object { $_.Location -ilike '*.vhdx*' })) {
            $pp = $d.Location
            if ($pp -and $pp -like ($WORK + '*')) {
                try { Dismount-DiskImage -ImagePath $pp -ErrorAction SilentlyContinue } catch {}
            }
        }
    } catch {}
    # --- 清理临时工作目录 ---
    # detach 后系统释放虚拟磁盘文件有延迟，单次 Remove-Item 常因文件仍被占用而静默失败，故循环重试删除。
    if (Test-Path $WORK) {
        Show-CommandLine "Remove-Item" @("-Recurse", "-Force", $WORK, "-ErrorAction", "SilentlyContinue")
        for ($k = 1; $k -le 8; $k++) {
            try { Remove-Item -Recurse -Force $WORK -ErrorAction Stop; break } catch { Start-Sleep -Seconds 1 }
        }
    }
    # --- 构建完成后不再自动删除输出文件（即使构建失败也保留）---
    if (-not (Test-Path $OUT_QCOW)) {
        Write-Host "[cleanup] 输出文件不存在（构建可能失败），跳过" -ForegroundColor DarkGray
    }
    # --- 打开输出文件和相关资源所在文件夹，方便用户查看 ---
    try {
        if (Test-Path $OUT_QCOW) {
            Write-Host "[info] 打开输出文件所在文件夹..." -ForegroundColor Cyan
            & explorer.exe /select,"$OUT_QCOW"
        }
        if (Test-Path $SRC_ISO) {
            Write-Host "[info] 打开 ISO 文件所在文件夹..." -ForegroundColor Cyan
            & explorer.exe /select,"$SRC_ISO"
        }
        if (Test-Path $VHDX) {
            Write-Host "[info] 打开 VHDX 文件所在文件夹..." -ForegroundColor Cyan
            & explorer.exe /select,"$VHDX"
        }
    } catch { }
    # --- 确保 tmp 目录被清理（即使 WORK 变量异常） ---
    $tmpDir = Join-Path $ROOT "tmp"
    if (Test-Path $tmpDir) {
        Remove-Item -Recurse -Force $tmpDir -ErrorAction SilentlyContinue
        Write-Host "[cleanup] 临时目录已清理: $tmpDir" -ForegroundColor DarkGray
    }
}
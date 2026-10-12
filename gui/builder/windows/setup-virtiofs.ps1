# setup-virtiofs.ps1 — 首次登录(USER/管理员)自动配置 VirtIO-FS 共享目录。
# 对应资料库 说明.txt：下载 VirtIO-FS 驱动、services.msc 设「自动(延迟启动)」、net use Z: \\viosfs\a。
# 由 unattend.xml 的 FirstLogonCommands (Order 11) 触发；未暂本脚本时自动跳过（if exist 守卫）。
$ErrorActionPreference = "SilentlyContinue"

# 共享目录标签（资料库说明.txt 默认 a）。如 DroidVM App 里填了别的标签，请同步修改此处。
$tag = "a"
$dest = "Z:"
$share = "\\viosfs\$tag"

# 1) 安装 VirtIO-FS 驱动（构建期若已把驱动包暂存到 C:\DroidVM\virtiofs\）
$pkg = "C:\DroidVM\virtiofs"
if (Test-Path $pkg) {
    $inf = Get-ChildItem $pkg -Recurse -Filter *.inf | Select-Object -First 1
    if ($inf) {
        pnputil.exe /add-driver $inf.FullName /subdirs /install | Out-Null
        Write-Host "[virtiofs] driver installed: $($inf.FullName)"
    } else {
        Write-Host "[virtiofs] no .inf found under $pkg, skip driver install" -ForegroundColor DarkYellow
    }
}

# 2) VirtIO-FS 服务设为「自动(延迟启动)」并拉起
$svc = Get-Service -DisplayName "VirtIO-FS Service" -ErrorAction SilentlyContinue
if (-not $svc) { $svc = Get-Service -Name "VirtioFsSvc" -ErrorAction SilentlyContinue }
if ($svc) {
    sc.exe config $svc.Name start= delayed-auto | Out-Null
    Write-Host "[virtiofs] service '$($svc.Name)' set to delayed-auto"
    # 延迟启动默认 120s；这里主动重试拉起，最多 ~120s
    for ($i = 0; $i -lt 24; $i++) {
        try { Start-Service $svc.Name -ErrorAction Stop; break }
        catch { Start-Sleep -Seconds 5 }
    }
} else {
    Write-Host "[virtiofs] VirtIO-FS Service not found; 请确认驱动已安装" -ForegroundColor DarkYellow
}

# 3) 持久挂载 Z: -> \\viosfs\<tag>（等待服务就绪，最多 ~120s）
for ($i = 0; $i -lt 24; $i++) {
    if (Test-Path $share) { break }
    Start-Sleep -Seconds 5
}
if (Test-Path $share) {
    net.exe use $dest $share /persistent:yes | Out-Null
    Write-Host "[virtiofs] mounted $dest -> $share (persistent)"
} else {
    Write-Host "[virtiofs] share $share not available after wait; 开机约 120s 后通常会自动挂载" -ForegroundColor DarkYellow
}

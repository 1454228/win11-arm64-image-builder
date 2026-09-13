# =====================================================================
# debloat.ps1 — Windows ARM64 离线 Debloat 脚本
#   在构建阶段离线移除预装应用、禁用遥测、清理组件
#   用法: 由 build.ps1 在 DISM Apply-Image 之后调用
#   参数: -WinDir <Windows 目录路径>
# =====================================================================
param(
    [Parameter(Mandatory=$true)]
    [string]$WinDir
)

$ErrorActionPreference = 'SilentlyContinue'

Write-Host "[debloat] 开始离线 Debloat ..." -ForegroundColor Cyan

# =====================================================================
# 1. 移除预装 Appx Provisioned Packages（离线）
#    这些是系统首次开机时自动安装的 UWP 应用
# =====================================================================
$removeApps = @(
    # --- 广告/推广 ---
    "Microsoft.BingNews",
    "Microsoft.BingWeather",
    "Microsoft.BingSearch*",
    # --- 游戏 ---
    "Microsoft.GamingApp",
    "Microsoft.XboxApp",
    "Microsoft.XboxGameOverlay",
    "Microsoft.XboxGamingOverlay",
    "Microsoft.XboxIdentityProvider",
    "Microsoft.XboxSpeechToTextOverlay",
    "Microsoft.Xbox.TCUI",
    # --- 媒体 ---
    "Microsoft.ZuneMusic",
    "Microsoft.ZuneVideo",
    "Microsoft.WindowsMediaPlayer",
    "Microsoft.Media.Player",
    # --- 通讯 ---
    "Microsoft.People",
    "Microsoft.SkypeApp",
    "Microsoft.MicrosoftTeams*",
    # --- 地图/旅行 ---
    "Microsoft.BingMaps",
    "Microsoft.WindowsMaps",
    # --- 其他无用应用 ---
    "Microsoft.3DBuilder",
    "Microsoft.Microsoft3DViewer",
    "Microsoft.MicrosoftOfficeHub",
    "Microsoft.MicrosoftSolitaireCollection",
    "Microsoft.MicrosoftStickyNotes",
    "Microsoft.MixedReality.Portal",
    "Microsoft.OneConnect",
    "Microsoft.Print3D",
    "Microsoft.WindowsFeedbackHub",
    "Microsoft.WindowsAlarms",
    "Microsoft.WindowsCalculator",
    "Microsoft.WindowsCamera",
    "Microsoft.WindowsCommunicationsApps",
    "Microsoft.WindowsSoundRecorder",
    "Microsoft.Windows.Photos",
    "Microsoft.ScreenSketch",
    "Microsoft.GetHelp",
    "Microsoft.Getstarted",
    "Microsoft.Todos",
    "Microsoft.PowerAutomateDesktop",
    "Microsoft.CorinationCheck*",
    "Microsoft.Windows.DevHome",
    "Microsoft.OutlookForWindows*",
    "Microsoft.Copilot*",
    # --- 第三方推广 ---
    "king.com.*",
    "DolbyLaboratories.DolbyAccess*",
    "SpotifyAB.SpotifyMusic*",
    "Disney.37853FC22B2CE*",
    "*EclipseManager*",
    "*ActiproSoftwareLLC*",
    "*AdobeSystemsIncorporated.AdobePhotoshopExpress*",
    "*Duolingo-LearnLanguagesforFree*",
    "*Wunderlist*",
    "*Flipboard*",
    "*Twitter*",
    "*Facebook*",
    "*Instagram*",
    "* McAfee *",
    "*CyberLinkMediaSuite*",
    "*ASUS*InboxApps*"
)

Write-Host "[debloat] 移除预装 Appx 包..." -ForegroundColor Yellow
$removedCount = 0
foreach ($app in $removeApps) {
    try {
        $pkgs = Get-AppxProvisionedPackage -Path $WinDir 2>$null | Where-Object { $_.DisplayName -like $app }
        if ($pkgs) {
            foreach ($pkg in $pkgs) {
                Write-Host "  [-] $($pkg.DisplayName)"
                Remove-AppxProvisionedPackage -Path $WinDir -PackageName $pkg.PackageName -ErrorAction SilentlyContinue | Out-Null
                $removedCount++
            }
        }
    } catch {
        Write-Host "  [!] 跳过: $app ($($_.Exception.Message))" -ForegroundColor DarkYellow
    }
}
Write-Host "[debloat] 已移除 $removedCount 个预装 Appx 包" -ForegroundColor Green

# =====================================================================
# 2. 禁用可选功能（离线）
# =====================================================================
$removeFeatures = @(
    "Internet-Explorer-Optional-amd64",
    "MediaPlayback",
    "WindowsMediaPlayer",
    "WorkFolders-Client",
    "Printing-PrintToPDFServices-Features",
    "Printing-FaxServices-Features",
    "Internet-Explorer-Optional-*"
)

Write-Host "[debloat] 禁用可选功能..." -ForegroundColor Yellow
$featRemovedCount = 0
foreach ($feat in $removeFeatures) {
    try {
        $feats = Get-WindowsOptionalFeature -Path $WinDir -FeatureName $feat 2>$null | Where-Object { $_.State -eq "Enabled" }
        if ($feats) {
            foreach ($f in $feats) {
                Write-Host "  [-] $($f.FeatureName)"
                Disable-WindowsOptionalFeature -Path $WinDir -FeatureName $f.FeatureName -NoRestart -ErrorAction SilentlyContinue | Out-Null
                $featRemovedCount++
            }
        }
    } catch {
        # Some feature names may not match, skip silently
    }
}
Write-Host "[debloat] 已禁用 $featRemovedCount 个可选功能" -ForegroundColor Green

# =====================================================================
# 3. 离线注册表修改：禁用遥测、广告、Cortana 等
# =====================================================================
Write-Host "[debloat] 离线注册表修改..." -ForegroundColor Yellow

$softHive = "$WinDir\System32\config\SOFTWARE"
$softLoaded = $false
try {
    reg load HKLM\DVMSOFT $softHive *> $null
    if ($LASTEXITCODE -eq 0) {
        $softLoaded = $true
    } else {
        Write-Host "  [!] 无法加载 SOFTWARE hive，跳过注册表修改" -ForegroundColor DarkYellow
    }
} catch {
    Write-Host "  [!] 加载 SOFTWARE hive 失败: $($_.Exception.Message)" -ForegroundColor DarkYellow
}

if ($softLoaded) {
    # --- 禁用遥测 ---
    $telemetryKey = "HKLM\DVMSOFT\Microsoft\Windows\CurrentVersion\Policies\DataCollection"
    reg add $telemetryKey /v AllowTelemetry /t REG_DWORD /d 0 /f *> $null
    reg add $telemetryKey /v MaxTelemetryAllowed /t REG_DWORD /d 0 /f *> $null
    Write-Host "  [x] 遥测已禁用 (AllowTelemetry=0)"

    # --- 禁用广告 ID ---
    $adKey = "HKLM\DVMSOFT\Microsoft\Windows\CurrentVersion\AdvertisingInfo"
    reg add $adKey /v Enabled /t REG_DWORD /d 0 /f *> $null
    Write-Host "  [x] 广告 ID 已禁用"

    # --- 禁用 Cortana ---
    $cortanaKey = "HKLM\DVMSOFT\Microsoft\Windows\CurrentVersion\Search"
    reg add $cortanaKey /v CortanaEnabled /t REG_DWORD /d 0 /f *> $null
    reg add $cortanaKey /v CortanaConsent /t REG_DWORD /d 0 /f *> $null
    reg add $cortanaKey /v BingSearchEnabled /t REG_DWORD /d 0 /f *> $null
    Write-Host "  [x] Cortana / Bing Search 已禁用"

    # --- 禁用锁屏广告/提示 ---
    $contentDelivery = "HKLM\DVMSOFT\Microsoft\Windows\CurrentVersion\ContentDeliveryManager"
    $cdmKeys = @(
        "SubscribedContent-310093Enabled",
        "SubscribedContent-310096Enabled",
        "SubscribedContent-314224Enabled",
        "SubscribedContent-314226Enabled",
        "SubscribedContent-314259Enabled",
        "SubscribedContent-338388Enabled",
        "SubscribedContent-338389Enabled",
        "SubscribedContent-338393Enabled",
        "SubscribedContent-353694Enabled",
        "SubscribedContent-353696Enabled",
        "SilentInstalledAppsEnabled",
        "SystemPaneSuggestionsEnabled",
        "SoftLandingEnabled",
        "RotatingLockScreenEnabled",
        "RotatingLockScreenOverlayEnabled"
    )
    foreach ($k in $cdmKeys) {
        reg add $contentDelivery /v $k /t REG_DWORD /d 0 /f *> $null
    }
    Write-Host "  [x] 锁屏广告/提示已禁用 ($($cdmKeys.Count) 项)"

    # --- 禁用云搜索 ---
    $searchKey = "HKLM\DVMSOFT\Microsoft\Windows\CurrentVersion\Search"
    reg add $searchKey /v CloudSearchEnabled /t REG_DWORD /d 0 /f *> $null
    reg add $searchKey /v ConnectedSearchEnabled /t REG_DWORD /d 0 /f *> $null

    # --- 禁用新闻和兴趣 ---
    $newsKey = "HKLM\DVMSOFT\Microsoft\Windows\CurrentVersion\Explorer\Advanced\TaskbarDeveloperSettings\Taskbar\Feeds"
    reg add $newsKey /v FeedsEnabled /t REG_DWORD /d 0 /f *> $null 2>$null

    # --- 卸载 OneDrive ---
    $onedriveKey = "HKLM\DVMSOFT\Microsoft\Windows\CurrentVersion\Uninstall\OneDriveSetup.exe"
    reg add $onedriveKey /v NoRemove /t REG_DWORD /d 0 /f *> $null 2>$null

    # --- 禁用 Windows 精灵/Copilot ---
    $copilotKey = "HKLM\DVMSOFT\Policies\Microsoft\Windows\WindowsCopilot"
    reg add $copilotKey /v TurnOffWindowsCopilot /t REG_DWORD /d 1 /f *> $null 2>$null

    # Unload hive
    [gc]::Collect()
    [gc]::WaitForPendingFinalizers()
    reg unload HKLM\DVMSOFT *> $null
    Write-Host "  [x] SOFTWARE hive 已卸载"
}

# --- 加载 SYSTEM hive 修改服务 ---
$sysHive = "$WinDir\System32\config\SYSTEM"
$sysLoaded = $false
try {
    reg load HKLM\DVBLSYS $sysHive *> $null
    if ($LASTEXITCODE -eq 0) { $sysLoaded = $true }
} catch {}

if ($sysLoaded) {
    # 找到当前控制集
    $cs = "ControlSet001"
    $testKey = "HKLM\DVBLSYS\$cs"
    reg query $testKey *> $null
    if ($LASTEXITCODE -ne 0) {
        $cs = "ControlSet001"
    }

    # --- 禁用诊断跟踪服务 ---
    $svcKey = "HKLM\DVBLSYS\$cs\Services"
    $disableServices = @(
        "DiagTrack",          # 诊断跟踪
        "dmwappushservice",  # WAP 推送消息路由
        "MapsBroker",        # 下载地图管理器
        "RetailDemo",        # 零售演示服务
        "SysMain",           # SysMain/Superfetch（减少磁盘 IO）
        "WbioSrvc",          # Windows 生物识别服务
        "WMPNetworkSvc"      # Windows Media Player 网络共享
    )

    foreach ($svc in $disableServices) {
        $svcPath = "$svcKey\$svc"
        reg query $svcPath *> $null
        if ($LASTEXITCODE -eq 0) {
            reg add $svcPath /v Start /t REG_DWORD /d 4 /f *> $null
            Write-Host "  [x] 服务已禁用: $svc"
        }
    }

    # --- 禁用 Windows 搜索索引（减少磁盘 IO） ---
    $searchSvc = "$svcKey\WSearch"
    reg query $searchSvc *> $null
    if ($LASTEXITCODE -eq 0) {
        reg add $searchSvc /v Start /t REG_DWORD /d 4 /f *> $null
        Write-Host "  [x] Windows 搜索索引已禁用"
    }

    # Unload
    [gc]::Collect()
    [gc]::WaitForPendingFinalizers()
    reg unload HKLM\DVBLSYS *> $null
}

# =====================================================================
# 4. 清理 WinSxS 组件存储
# =====================================================================
Write-Host "[debloat] 清理 WinSxS 组件存储..." -ForegroundColor Yellow
try {
    $dismResult = & dism /image:$WinDir /Cleanup-Image /StartComponentCleanup /ResetBase 2>&1
    Write-Host "  [x] WinSxS 清理完成" -ForegroundColor Green
} catch {
    Write-Host "  [!] WinSxS 清理失败: $($_.Exception.Message)" -ForegroundColor DarkYellow
}

# =====================================================================
# 5. 删除残留 Appx 文件
# =====================================================================
Write-Host "[debloat] 清理残留 Appx 文件..." -ForegroundColor Yellow
$appxDirs = @(
    "$WinDir\SystemApps\Microsoft.BingNews*",
    "$WinDir\SystemApps\Microsoft.BingWeather*",
    "$WinDir\SystemApps\Microsoft.Microsoft3DViewer*",
    "$WinDir\SystemApps\Microsoft.WindowsFeedbackHub*",
    "$WinDir\SystemApps\Microsoft.GetHelp*",
    "$WinDir\SystemApps\Microsoft.Getstarted*",
    "$WinDir\SystemApps\Microsoft.Windows.DevHome*"
)
$cleanedCount = 0
foreach ($dir in $appxDirs) {
    if (Test-Path $dir) {
        Remove-Item -Recurse -Force $dir -ErrorAction SilentlyContinue
        $cleanedCount++
    }
}
if ($cleanedCount -gt 0) {
    Write-Host "  [x] 已清理 $cleanedCount 个残留 Appx 目录" -ForegroundColor Green
}

Write-Host "[debloat] Debloat 完成!" -ForegroundColor Cyan

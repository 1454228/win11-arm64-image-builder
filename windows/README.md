# windows/ — 路線 A(x64 Windows,DISM 離線)

在 **x64 Windows** 上把 **Win11 ARM64 ISO + 驅動**直接做成可開機、已含驅動的 qcow2 —— **不跑 Setup、不開 qemu**,靠 `dism` 套用映像 + 離線注入驅動 + `bcdboot`。入口是根目錄的 `windows_build.ps1`。

## 為什麼用這個(vs 路線 B 的 qemu 流程)

| | 路線 A(DISM 離線) | 路線 B(qemu 跑 Setup) |
|---|---|---|
| 驅動簽章提示 | **完全沒有**(離線注入不經互動 PnP) | 需匯入憑證到 TrustedPublisher |
| 時間 | ~5–10 分(無裝機 reboot) | ~30 分 |
| boot-press / 點擊器 | 不需要 | 需要 |
| 環境 | 要 x64 Windows | Apple Silicon Mac |

> 離線 `dism /Add-Driver /ForceUnsigned` 不會跳「Windows can't verify the publisher」(那是互動式 PnP 才有的)。但自簽驅動要能在**開機時載入**仍需 BCD `testsigning on`(流程第 6 步設)。

> 產物約 **14 GB**(比路線 B 的 ~7 GB 大);想要更小的映像可改走 macOS(路線 B)。

## 需求
- **x64 Windows,系統管理員**(內建 `dism` / `bcdboot` / `diskpart`)
- **`qemu-img`**(QEMU for Windows,需在 PATH)—— 最後 VHDX→qcow2 轉檔用
- Win11 ARM64 ISO

## 用法
```powershell
# 編輯 windows_build.ps1 頂部變數($env:SRC_ISO 必填、$env:DRIVERS_DIR、$env:OUT_QCOW …)
powershell -ExecutionPolicy Bypass -File windows_build.ps1   # 會自動提權
```
變數清單見根目錄 README。**Windows 專屬注意**:帳號名用 `$env:DVM_USERNAME`(**不是** Windows 內建的 `$env:USERNAME`＝目前登入者);密碼 `$env:DVM_PASSWORD`(空密碼會擋 RDP/SSH;相容舊 `$env:SSH_PASSWORD`)。

## 流程(build.ps1,全程離線不開機)
1. 解析驅動來源 → 掛 ISO 取 `install.wim`
2. 建 + 掛 VHDX,GPT 分割 ESP(FAT32)+ MSR + Windows(NTFS)
3. `dism /Apply-Image` 套用映像
4. `dism /Add-Driver /ForceUnsigned` 離線注入 `DRIVER_INSTALL` 指定的驅動;從 `.cat` 萃取簽章憑證 → `C:\DroidVM\certs`
5. debloat:移除多餘 Appx、關 hibernate、開 RDP、停用 Reserved Storage(全部離線改 hive);首次登入再由 `debloat.ps1` 關遙測 / 非必要服務 / 遙測排程 / Windows Update,並在該帳號桌面放 `enable_windows_update.bat`(想開回更新時跑一下,一次性)
6. `bcdboot` + `bcdedit` 開 `testsigning` / `nointegritychecks`
7. 放入 `unattend.xml`(注入帳號)+ staging `setup-ssh.ps1` / OpenSSH / `authorized_keys` / `pvmpower-devnode.ps1` → `C:\DroidVM`
8. 卸載 VHDX → `qemu-img convert` 成 qcow2

產物是 **OOBE-pending**:第一次開機在 **Gunyah 目標機**上跑 —— 建帳號、autologon、匯憑證、裝 SSH、設「永不睡眠 / 永不關螢幕」與「密碼永不過期」,並全新偵測硬體安裝驅動(含把 `rdmapool` 綁到目標機才有的 `ACPI\RDMA0000`),因此驅動能在開機階段正確載入。

## 遠端連線
- **RDP**:離線就開好(`fDenyTSConnections=0`),首次開機只補防火牆;USER 是管理員,預設可連。
- **SSH**:`$env:OPENSSH_SRC` 指安裝檔來源(URL 下載 / 本地路徑),預設抓 arm64 `.msi`(一鍵裝服務 / host key / 防火牆,也接受 `.zip`);設空字串 = 不裝 SSH。
- 匯入 Root/TrustedPublisher 只消掉「日後更新驅動的發行者警告」;自簽驅動開機載入仍靠 `testsigning`,別因此關掉。
- **IPv6**:首次開機關掉隨機 IID(`randomizeidentifiers`)與臨時位址(`privacy`),SLAAC 位址改用 **EUI-64**,所以位址可直接從 VMM 指派的 MAC 推算(`fe80::` / 前綴 + MAC 翻 U/L bit 並插入 `ff:fe`),不必先探測就能 SSH。

## EMS / SAC(序列 out-of-band 主控台,`SAC>`)

讓成品 qcow2 開機即可用 **Emergency Management Services**:在 app 的 SBSA 序列埠上得到 Windows 的 `SAC>` 互動主控台(可在 GUI 無法操作時管理 guest)。三件事缺一不可,builder 已處理前兩件:

1. **BCD EMS(自動烙入)**:`build.ps1` 第 7b 步離線設 `bcdedit /emssettings BIOS`(ARM64 無 legacy I/O COM,EMS 從 ACPI **SPCR** 讀埠/鮑率)+ `/ems {default} on` + `/bootems {bootmgr} on`。無需任何開關,永遠生效 → 開機階段的序列文字(bootmgr / winload)一定有。
2. **EMS-SAC 執行期 FoD(互動式 `SAC>` 需要)**:ARM64 LTSC/Pro 映像**不含** `sacdrv.sys` / `sacsess.exe` / `sacsvr`,要另裝 `Windows.Desktop.EMS-SAC.Tools` 這個 Feature-on-Demand。兩種來源:
   - **`$env:EMS_SAC_SOURCE = "E:\"`(推薦,離線、零連網)**:指向與映像同版的 **ARM64 FoD ISO** 掛載點或解開的資料夾。第 5c 步 `dism /Add-Capability … /Source /LimitAccess` 離線注入,直接烙進映像。
   - **`$env:EMS_SAC_SOURCE = "online"`(目標端線上)**:沒有 FoD ISO 時改用。成品**首次開機**時由 `setup-ems-sac.ps1` 以 SYSTEM 臨時開回 Windows Update 拉 FoD,裝好後自動重開一次套用。→ **目標(app)首次開機要能連網**。沒連網**不會卡死**:腳本 try/catch 略過,只是少了互動 `SAC>`,開機序列文字照常。
   - **`$env:EMS_SAC_SOURCE = "skip"`(預設)** → 只出 boot-EMS(無互動 `SAC>`)。
3. **VM 要把 SBSA 埠設為 guest 主控台(app 側)**:SAC 要真的接上,啟動 VM 的 crosvm 旗標需有一個 SBSA 序列埠且設為「作為 Guest 控制台(SPCR)」(`--serial hardware=sbsa,num=1,console,…`)。crosvm 據此把 stdout-path 指向該 PL011 節點 → edk2 生 SPCR → SAC 綁上去。此為 app / VM 設定,非 image 本身。

> **crosvm 需求**:SBSA UART 裝置與 **PmReset**(ACPI reduced-HW 電源控制器,補上 Windows-on-ARM 無 PSCI 的關機/重啟)都在 crosvm。SBSA UART 在 `0x6000`、PmReset 在 `0x5000`(對齊 edk2 FADT)。用新版 crosvm 才有 `SAC>` 與可用的關機/重啟。

範例(離線 FoD,全程零連網):
```powershell
$env:EMS_SAC_SOURCE = "E:\"   # 掛好的 ARM64 FoD ISO
powershell -ExecutionPolicy Bypass -File windows_build.ps1
```

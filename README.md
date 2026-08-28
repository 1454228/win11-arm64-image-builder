# win11-arm64-image-builder

把 **Win11 ARM64 ISO + 自簽 gunyah/virtio 驅動**全自動建成一顆可直接在 **DroidVM（Gunyah/crosvm）上開機的 qcow2**。

產物預設:本機帳號 `USER`(自動登入、密碼永不過期)、testsigning + nointegritychecks 已開、驅動已裝、SSH/RDP 已開、已 debloat、永不睡眠 / 永不關螢幕、停用 Reserved Storage、IPv6 SLAAC 用 EUI-64(位址可由 MAC 推算)。

## 兩條路線(二選一,產物等價)

| 路線 | 環境 | 入口 | 做法 | 時間 |
|---|---|---|---|---|
| **A. Windows** | 單台 x64 Windows(系統管理員) | `windows_build.ps1` | DISM 離線套用映像 + 離線注入驅動 + bcdboot/bcdedit;不跑 Setup、不開 VM | ~5–10 分 |
| **B. macOS** | 單台 Apple Silicon Mac | `macos_build.sh` | qemu HVF 跑 Setup 裝機 + 裝驅動,再 `sysprep /generalize` 打回可部署狀態 | ~30 分 |

兩條路線都把映像做成 **OOBE-pending(尚未完成首次設定)**:真正的「第一次開機」發生在 **Gunyah 目標機**上,由它全新偵測硬體並安裝驅動 —— 尤其把 `rdmapool` 綁到目標機才有的 `ACPI\RDMA0000`(受保護 VM 的 restricted-DMA pool),讓 `viostor` 的 DMA 走 bounce、不打到 lent 記憶體。這是驅動能在開機階段正確載入的關鍵。

> **產物功能等價,但體積不同**:路線 B(macOS)約 **7 GB**,路線 A(Windows)約 **14 GB**。差在回收可用空間的方式 —— macOS 流程在 qemu 執行期即時 TRIM(`discard=unmap`)並線上 debloat,壓得較實;想更小可走 macOS。開 `-c` 壓縮(`COMPRESS=1`)後兩者都約 **6 GB**,但 crosvm 不能直讀,需 DroidVM 匯入 / pre-flight 解壓。

## 用法

兩邊都是「改入口腳本頂部的變數 → 執行」。

```powershell
# 路線 A(Windows,會自動提權)
powershell -ExecutionPolicy Bypass -File windows_build.ps1
```
```bash
# 路線 B(macOS)
bash macos_build.sh
```

## 設定(入口腳本裡的變數)

檔案類變數(`SRC_ISO` / `DRIVERS_DIR` / `OPENSSH_SRC`)可填 **URL**(自動下載到 `files/`)或**本地路徑**;zip 自動解壓。

| 變數 | 說明 |
|---|---|
| `SRC_ISO` | **必填**。Win11 ARM64 ISO(URL 或路徑) |
| `DRIVERS_DIR` | 驅動來源:GitHub release zip URL / 本地 zip / 本地資料夾。預設抓 `gunyah-guest-drivers-windows` 的 `dev` release |
| `DRIVER_INSTALL` | 只安裝這些驅動子資料夾(預設 `NetKVM rdmapool pvmpower vioinput viostor vioscsi`;必須含開機碟 `viostor`) |
| `DRIVER_CERT` | 簽章憑證(空 = 從驅動 `.cat` 自動萃取) |
| `IMAGE_INDEX` | install.wim 版本索引(空 = 列出讓你選) |
| `USERNAME` / `PASSWORD` | 建立的本機管理員帳號。macOS 用 `USERNAME` / `PASSWORD`;Windows 用 `$env:DVM_USERNAME` / `$env:DVM_PASSWORD` |
| `SSH_PUBKEY` | SSH 公鑰(空 = 只密碼登入) |
| `OUT_QCOW` | 輸出 qcow2 路徑 |
| `FOD_SOURCE` | **僅路線 A**。EMS-SAC 執行期 FoD 的離線來源(ARM64 FoD ISO 掛載點/資料夾)→ 離線 `dism /Add-Capability` 烙進映像,全程零連網。見下方 EMS/SAC |
| `EMS_SAC_ONLINE` | 非空 = 沒有 `FOD_SOURCE` 時改用線上安裝 FoD。路線 A 於**目標端首次開機**連網裝;路線 B 於**構建期** qemu VM 內連網裝並烙入(目標端免連網) |

## EMS / SAC(序列 out-of-band 主控台,`SAC>`)

讓成品開機即可用 **Emergency Management Services**:在 app 的 SBSA 序列埠上得到 Windows `SAC>` 互動主控台。**BCD EMS 一律自動烙入**(`emssettings BIOS` + `ems on` + `bootems on`),所以開機序列文字永遠有;差別只在**互動式 `SAC>` 執行期**(`Windows.Desktop.EMS-SAC.Tools` FoD,LTSC/Pro ARM64 不內含)怎麼來:

| | 路線 A(Windows) | 路線 B(macOS) |
|---|---|---|
| **離線 FoD**(零連網,推薦) | `FOD_SOURCE` → 離線 DISM 烙入 | 無(建置主機非 Windows,無法離線 DISM) |
| **線上 FoD** | `EMS_SAC_ONLINE=1` → **目標端首次開機**連網裝 + 自動重開一次 | `EMS_SAC_ONLINE=1` → **構建期** qemu VM 連網下載並烙入 → 目標端**免連網**、首開機從本機套用 |
| 兩者皆不設 | 只出 boot-EMS,無互動 `SAC>` | 同左 |

沒連網**不會卡死**:安裝腳本 try/catch 略過,boot-EMS 照常,只是少了互動 `SAC>`。

### VM 要有一個 SBSA 埠設為 guest 主控台(SAC 才會綁上)

SAC 走 ACPI SPCR,SPCR 由 edk2 依 crosvm 傳入的 SBSA UART FDT 節點生成,而該節點要被指定為 guest console 才會被 SPCR 指到。兩種取得方式:

- **開箱即用 → 用 vmpkg**:builder 直接輸出**帶 config 的 `.vmpkg`**(本地 `vms.json` 維護,預設 **3 GB RAM + 256 MB swiotlb**),其 `serial_ports` 已含 `{"hardware":"sbsa","num":1,"backend":"app_console","console":true}`(COM2-4 為 sink)。app 匯入整包即帶好設定,免手動、免 import→export 往返。
- **裸 qcow2 → 使用者兩步**:app 裡 **外設 → 序列埠 → 新增 SBSA**,再點該埠的 **「作為 Guest 控制台(SPCR)」** radio。

> **daemon 實際發出的 crosvm 旗標**(SBSA 埠設 app-console + console 時,供對照):
> ```
> --serial type=file,hardware=sbsa,num=1,earlycon,console,path=/proc/self/fd/N,input=/proc/self/fd/M
> ```
> 後端選 pty 則為 `type=pty,hardware=sbsa,num=1,console,earlycon[,path=symlink]`;sink 埠照發 `type=sink,hardware=serial,num=2..4`。`console`/`earlycon` 只掛在被選為 console 的那一個埠上。

並需**新版 crosvm**:含 SBSA UART `@0x6000` + PmReset `@0x5000`(後者補上 Windows-on-ARM 無 PSCI 的關機/重啟,對齊 edk2 FADT)。

## vmpkg 直接輸出(免 app import→export 往返)

設 `OUT_VMPKG` 就會在 qcow2 之外**再吐一顆可直接匯入的 `.vmpkg`**,把 VM 設定(RAM / swiotlb / SBSA 主控台 / boot)一起烙進封裝 —— app 匯入即帶好設定,開箱即用、免手動、免每次跑 app 的 import→export。

- **設定來源 = 本地 `vms.json`**(repo 根目錄,本地維護一份)。改它即可調 RAM / CPU / swiotlb / 序列埠等;packager 會自動把 qcow2 補成頂層 disk 條目。預設已放 `memory_mb=3072`、`swiotlb_mb=256`、`serial_ports` 含 SBSA 主控台(SAC)+ COM sink、UEFI boot。
- **打包器**:兩份等價實作,容器 byte-exact 對齊 app 的 `PackageHeader`/`VMExportTask`(gzip(GNU-tar))。
  - Windows 路線用 **`pack-vmpkg.ps1`**——純 PowerShell + 內建 `tar.exe`(Win10 1803+),**零額外相依,不需要 Python**:
    ```powershell
    .\pack-vmpkg.ps1 -Qcow2 out.qcow2 -Config vms.json -Out win11.vmpkg
    ```
  - macOS 路線用 **`pack-vmpkg.py`**(純 Python 3 stdlib,macOS 內建 python3):
    ```bash
    python3 pack-vmpkg.py --qcow2 out.qcow2 --config vms.json --out win11.vmpkg
    ```
- vmpkg 要用**未壓縮**的 qcow2(勿開 `COMPRESS`,否則 crosvm 解開後讀不了 `-c` 叢集)。

> 相容性:`serial_ports` 是 app 新欄位。新版 app 讀它(自動帶上 SBSA 主控台);舊版 app 匯入會忽略它、靠 ensureDefaults 補 COM 四顆(只是少了 SBSA,不會壞)。

## 需求

- **macOS:** `brew install qemu wimlib xorriso colima docker`;另需約 **50 GB** 可用空間放中間產物(工作 qcow2 / 安裝 ISO / 驅動,位於 `macos/files`)。testsigning 的 BCD patch 用 Colima 容器內的 hivex 代跑,故無需另一台 Linux
- **Windows:** x64 Windows(系統管理員)、內建 `dism` / `bcdboot` / `diskpart`、`qemu-img`(QEMU for Windows,需在 PATH)

## 結構

```
macos_build.sh      路線 B 入口(改變數後 bash 執行)
windows_build.ps1   路線 A 入口(改變數後以系統管理員執行)
macos/              路線 B 實作:build.sh + 00/02/03 階段 + autounattend / gunyah-oobe + Colima BCD patch
windows/            路線 A 實作:build.ps1 + unattend(詳見 windows/README.md)
pack-vmpkg.py       qcow2 + vms.json → .vmpkg 打包器(純 stdlib,兩路線 OUT_VMPKG 共用,也可獨立執行)
vms.json            出廠 VM 設定模板(RAM/swiotlb/序列埠/boot);本地維護,打包時烙入 vmpkg
files/              各路線的中間產物快取(URL 下載 / zip 解壓,已 gitignore)
```

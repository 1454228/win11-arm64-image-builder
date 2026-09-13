# windows/ — 路线 A (x64 Windows, DISM 离线)

在 **x64 Windows** 上把 **Win11 ARM64 ISO + 驱动** 直接做成可开机、已含驱动的 qcow2 —— **不跑 Setup、不开 qemu**，靠 `dism` 套用映像 + 离线注入驱动 + `bcdboot`。入口是根目录的 `windows_build.ps1`。

## 改进内容

相比原版新增以下功能：

| 功能 | 说明 |
|------|------|
| **交互式配置菜单** | 无需手动编辑脚本，运行后通过菜单逐步配置所有参数 |
| **ISO 浏览选择** | 支持输入路径、URL 下载、文件对话框选择三种方式 |
| **ESD 格式支持** | 自动检测 ISO 中的 `install.wim` 或 `install.esd`，DISM 原生支持两种格式 |
| **驱动来源选择** | 支持网络下载、本地 ZIP、本地文件夹、跳过驱动注入四种方式 |
| **自定义用户名/密码** | 交互式输入，密码确认，支持留空密码（不推荐） |
| **磁盘大小 (GB)** | 以 GB 为单位输入，自动转换为 MB，默认 40 GB，范围 20-2048 GB |
| **配置预览确认** | 构建前展示完整配置摘要，支持修改任意配置项 |
| **SSH 公钥配置** | 支持多行输入多个公钥，留空则仅密码登录 |
| **压缩选项** | 可选择是否启用 qcow2 压缩（约 6 GB） |

## 为什么用这个 (vs 路线 B 的 qemu 流程)

| | 路线 A (DISM 离线) | 路线 B (qemu 跑 Setup) |
|---|---|---|
| 驱动签章提示 | **完全没有** (离线注入不经互动 PnP) | 需汇入凭证到 TrustedPublisher |
| 时间 | ~5-10 分 (无装机 reboot) | ~30 分 |
| boot-press / 点击器 | 不需要 | 需要 |
| 环境 | 要 x64 Windows | Apple Silicon Mac |

> 离线 `dism /Add-Driver /ForceUnsigned` 不会跳「Windows can't verify the publisher」(那是互动式 PnP 才有的)。但自签驱动要能在**开机时载入**仍需 BCD `testsigning on`(流程第 7 步设)。
> 产物约 **14 GB**(比路线 B 的 ~7 GB 大)；想要更小的映像可改走 macOS(路线 B)。

## 需求

- **x64 Windows, 系统管理员** (内建 `dism` / `bcdboot` / `diskpart`)
- **`qemu-img`** (QEMU for Windows, 需在 PATH) —— 最后 VHDX -> qcow2 转换用
- **Win11 ARM64 ISO** (支持 `install.wim` 或 `install.esd`)

## 用法

```powershell
# 直接运行交互式菜单（会自动提权）
powershell -ExecutionPolicy Bypass -File windows_build.ps1
```

运行后按提示逐步配置，最终确认后自动开始构建。也可以直接通过环境变量调用 build.ps1：

```powershell
# 设置环境变量后直接调用 build.ps1（适用于脚本/CI）
$env:SRC_ISO = "C:\path\to\win11-arm64.iso"
$env:DVM_USERNAME = "MyUser"
$env:DVM_PASSWORD = "MyPassword"
$env:DISK_SIZE_MB = "81920"  # 80 GB
$env:OUT_QCOW = "C:\output\win11.qcow2"
powershell -ExecutionPolicy Bypass -File windows\build.ps1
```

## 环境变量参考

| 变量 | 说明 |
|------|------|
| `$env:SRC_ISO` | **必填**。Win11 ARM64 ISO (URL 或本地路径) |
| `$env:DRIVERS_DIR` | 驱动来源：URL / 本地 zip / 本地文件夹。设空字符串跳过驱动注入 |
| `$env:DRIVER_DIR` | 驱动物理目录 (ZIP/drivers 或本地路径) |
| `$env:DRIVER_INSTALL` | 只安装这些驱动子文件夹 (默认 `NetKVM rdmapool pvmpower vioinput viostor vioscsi`) |
| `$env:DRIVER_CERT` | 签章凭证 (空 = 从 .cat 自动萃取) |
| `$env:IMAGE_INDEX` | install.wim/esd 版本索引 (空 = 列出让你选) |
| `$env:DVM_USERNAME` | 本机管理员账号名 (默认 `USER`) |
| `$env:DVM_PASSWORD` | 密码 (空密码会阻止 RDP/SSH 网络登录) |
| `$env:SSH_PUBKEY` | SSH 公钥 (空 = 只密码登录) |
| `$env:DISK_SIZE_MB` | 磁盘大小 (MB) (默认 40960 = 40 GB) |
| `$env:OUT_QCOW` | 输出 qcow2 路径 |
| `$env:COMPRESS` | 非空 = 压缩 qcow2 (更小但 crosvm 不能直读) |
| `$env:OPENSSH_SRC` | OpenSSH 安装来源 (URL 或路径，空 = 不装 SSH) |

## 流程 (build.ps1, 全程离线不开机)

1. 解析驱动来源 -> 挂 ISO 取 `install.wim` 或 `install.esd`
2. 建 + 挂 VHDX, GPT 分割 ESP(FAT32 260MB) + MSR(16MB) + Windows(NTFS)
3. `dism /Apply-Image` 套用映像 (支持 WIM 和 ESD)
4. `dism /Add-Driver /ForceUnsigned` 离线注入驱动；从 `.cat` 萃取签章凭证 -> `C:\DroidVM\certs`
5. Debloat: 移除多余 Appx、关 hibernate、开 RDP、停用 Reserved Storage (全部离线改 hive)
6. `bcdboot` + `bcdedit` 开 `testsigning` / `nointegritychecks`
7. 放入 `unattend.xml` (注入账号) + staging `setup-ssh.ps1` / OpenSSH / `authorized_keys` / `pvmpower-devnode.ps1` -> `C:\DroidVM`
8. 卸除 VHDX -> `qemu-img convert` 成 qcow2

产物是 **OOBE-pending**：第一次开机在 **Gunyah 目标机**上跑 —— 建账号、autologon、汇凭证、装 SSH、设「永不睡眠 / 永不关屏幕」，并全新侦测硬体安装驱动 (含把 `rdmapool` 绑到目标机才有的 `ACPI\RDMA0000`)，因此驱动能在开机阶段正确载入。

## 远程连线

- **RDP**: 离线就开好 (`fDenyTSConnections=0`)，首次开机只补防火墙；USER 是管理员，预设可连。
- **SSH**: `$env:OPENSSH_SRC` 指安装档来源 (URL 下载 / 本地路径)，预设抓 arm64 `.msi` (一键装服务 / host key / 防火墙，也接受 `.zip`)；设空字符串 = 不装 SSH。
- 汇入 Root/TrustedPublisher 只消掉「日后更新驱动的发行者警告」；自签驱动开机载入仍靠 `testsigning`，别因此关掉。
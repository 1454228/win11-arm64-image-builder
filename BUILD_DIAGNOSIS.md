# Win11 ARM64 镜像构建 — 诊断结论与交接文档

> 编写时间：2026-09-25
> 适用项目：`W11A64DVMB`（fork 自 `Droid-VM/win11-arm64-image-builder`）
> 目的：本次在 DROIDVM ARM64 guest 上的构建反复失败，根因已定位。本文档供**换环境后继续构建**时参考。

---

## 1. 项目是什么

一个 Win11 ARM64 镜像构建器：把 Win11 ARM64 的 WIM/ISO + Gunyah/VirtIO 驱动，
自动构建成可在 DroidVM（Gunyah / QEMU）里启动的 `qcow2` 镜像。

- **实际运行的构建引擎**：`gui/builder/windows/build.ps1`（GUI 自带、自包含，不依赖仓库根目录）
- **GUI**：`gui/ctk_gui.py`（Python + CustomTkinter），通过 UAC 提权调 `run_build.py` → `build.ps1`
- **另一份** `Windows-ARM64-DroidVM-Builder/` 只是旧 zip 解压出来的**参考副本**，GUI 不调用它

构建关键步骤（与本次故障相关）：
1. 用 `qemu-img` 把中间产物转成 `qcow2`（最终输出）
2. 用 **diskpart** `create vdisk` + `attach vdisk` 创建一个 VHDX 并挂成真实磁盘
3. 对挂上的磁盘做 `convert gpt` / 分区 / `format` / 分配盘符
4. 用 **DISM** 把 WIM 离线 `Apply-Image` 到 Windows 分区、注入驱动、`bcdboot` 写引导

**本次故障卡在第 2 步**——diskpart 创建 VHDX 失败，后续全废。

---

## 2. 故障现象（按时间顺序的 7 轮排查）

| 轮次 | 日志现象 | 当时判断 | 最终结果 |
|---|---|---|---|
| 1 | diskpart 报 `filename syntax is incorrect`，脚本里 `D:\?????` | 中文路径被 ASCII 编码吃成 `?` | 修了编码（误打误撞方向偏了） |
| 2 | 中文路径修好后，`attach` 后 `Get-Disk` 找不到磁盘 | 怀疑 `attach` 挂不上 | 加临时诊断块 |
| 3 | 诊断显示 `VHDX file exists = False`（提权下 create 都没建出文件） | 怀疑 `vhdmp` 没起 | 加启动 vhdmp 逻辑 |
| 4 | vhdmp 起了但仍 `False`，diskpart 只打印 banner | 怀疑脚本**文件路径**含中文 | 改把脚本复制到 ASCII 临时路径执行 |
| 5 | 用户提示"之前能跑、换环境才挂" | 定性为**中文路径**环境差异 | 配置 BUILD_TMP 改 ASCII `D:\tmp`，代码回退 |
| 6 | 路径改 ASCII 后，暴露真错误：`provider not found`(0xc03a0014) | 查服务发现 `FsDepends` Stopped | 加启动 FsDepends 逻辑 |
| 7 | **FsDepends 即使提权也启动失败** → 仍 0xc03a0014 | **决定性结论：本环境 VHD 提供程序内核不可用** | 见第 3 节 |

---

## 3. 决定性根因

**这台 DROIDVM ARM64 guest 的 Windows VHD 提供程序（FsDepends / vhdmp）在内核层无法激活，
diskpart 的 `create vdisk` / `attach vdisk` 在这台机器上是死路。**

铁证（07:42 那次日志 `gui/builder/windows/build_direct.log`）：
```
[disk] starting FsDepends (virtual disk provider), current=Stopped
[disk] WARN: failed to start FsDepends:
        Failed to start service 'File System Dependency Minifilter (FsDepends)'.
DiskPart has encountered an error:
        A virtual disk support provider for the specified file was not found.   (0xc03a0014)
```

辅助证据（`_diag_svc.txt`）：
- `FsDepends` = **Stopped, StartType=Manual**，且 `Start-Service` 失败（minifilter 驱动在本 guest 内核无法加载）
- `vhdmp` = Running，但**依赖 FsDepends**，FsDepends 没起 → 提供程序加载不出
- `vhdsvc` = NOT FOUND（Hyper-V VHD 服务，本机没装，无关）
- `New-VHD` cmdlet = 不可用

**完整故障链**：
- 早期（BUILD_TMP 是中文 `D:\新建文件夹`）→ diskpart 对中文路径**静默失败**（不建文件、不报错、只打印 banner）
- 路径改 ASCII 后 → 表层中文问题消失，但 `FsDepends` 缺位暴露成 **0xc03a0014**
- 两者叠加，根因本质是：**本环境没有可用的 VHD 提供程序**

这完全解释了"之前的版本能正常构建，换到这个环境就出问题"——旧环境 VHD 提供程序正常，本环境没有。

---

## 4. 已做的修复（均可安全带入新环境）

以下改动都在 `gui/builder/windows/build.ps1`（实际运行引擎），已通过 PowerShell 语法校验、保留 UTF-8 BOM：

1. **diskpart 脚本编码**：4 处 `Out-File -Encoding ascii`（中文路径用 ASCII 目录即可，UTF-16 反而可能让 diskpart 把脚本当含 NUL 字节而解析异常）
2. **`Invoke-DiskPartScript` 改进**：执行前把脚本 `Copy-Item` 到 ASCII 临时路径 `%TEMP%\droidvm_dp\` 再跑，并用 `*> rawLog` 全流捕获 diskpart 真实输出（避开 transcript 吞输出）——所有 diskpart 调用自动受益
3. **路径归一化**：`BUILD_TMP` / `DRIVERS_DIR`（仅本地 zip）/ `OUT_QCOW` 增加 `/`→`\` 反斜杠归一化（对齐已有的 `SRC_ISO` 处理），应对 GUI `filedialog` 在 Windows 返回正斜杠路径
4. **启动依赖驱动**（防御性，无害）：`create vdisk` 前尝试 `Start-Service vhdmp` 和 `Start-Service FsDepends`；若已 Running 则跳过，若启动失败仅 WARN 不中断
5. **修清理段 explorer 崩溃**：原 `& explorer.exe /select,"$X"` 的逗号解析 bug 会把下一行 `if` 带崩（日志里 `The term 'if' is not recognized`）；改为 `Start-Process explorer.exe -ArgumentList ('/select,"{0}"' -f $X)`

配置 `gui/build_config.json`：
- `BUILD_TMP` 由 `D:\新建文件夹` → `D:\tmp`（ASCII，同 D: 盘，当时剩余 39.9GB）

> 说明：第 4 点的"启动 FsDepends"在本环境失败（根因），但在**有正常 VHD 提供程序的环境**里它能正常启动、属于有益的健壮性改进，保留无妨。

---

## 5. 带到新环境后的操作建议

### 推荐路径：在 VHD 提供程序可用的普通 Windows 宿主上构建
现有 `build.ps1` + 已修好的配置**可直接用**，无需再改代码。只需注意：

1. **`BUILD_TMP` / `OUT_QCOW` 设成新机器上真实存在、空间充足、且为 ASCII 名的目录**
   （当前配置写的是 `D:\tmp`，仅适用于本机；换机器后按实际盘符调整，避免中文路径）
2. 构建需要**管理员提权**（GUI 会自动 UAC RunAs）
3. 跑之前确认：
   - 目标盘剩余空间足够（WIM apply + 缓冲，建议 ≥ 镜像体积 ×1.5；本机曾报 C: 剩 18.4GB < 估算 36GB，可能 `exit 433`）
   - 物理内存建议 ≥ 4GB 且开页面文件 ≥ 8GB（DISM 离线 apply 大 WIM 易 OOM）
   - 若空间紧张，勾选 `COMPRESS` 或减小 `DISK_SIZE_MB`

### 若仍想在本 DROIDVM guest 构建（不推荐）
本环境 VHD 提供程序不可用，必须**彻底不用 diskpart/vhdmp**：
- 方案 B1：在 **Linux 宿主**用 `qemu-img` + `libguestfs`(guestfish/virt-copy-in) + `wimlib` + `qemu-img convert` 完成建盘/分区/灌系统/转 qcow2（完全不依赖 Windows VHD 提供程序）
- 方案 B2：在本 Windows guest 装**可携式 loopback 驱动**（如 ImDisk，约几 MB）来挂盘——涉及驱动安装 + 下载，需另行批准
- 方案 C（系统层强开）：注册表 `HKLM\SYSTEM\CurrentControlSet\Services\FsDepends` 的 `Start` 设 `0`(boot) 后**重启**本 guest，看 minifilter 能否在启动期加载（ARM64 guest 未必能加载，风险高，优先级最低）

---

## 6. 可删除的临时诊断文件（本次排查产生的残留）

以下文件是诊断过程生成的临时产物，与构建无关，打包前可删：
- `_diag_attach.txt` `_diag_attach2.txt` ... `_diag_attach4.txt`
- `_diag_trans.txt` `_diag_drive.txt` `_diag_svc.txt`
- `_elev_diag.ps1`
- 根目录旧环境的 `_fix*.txt` / `_diag*.txt` / `bench.*` / `diag*.txt` / `wsl*.txt` 等（09/19 旧调试残片，非本次）

> 注：`gui/build.log` 与 `gui/builder/windows/build_direct.log` 是构建日志，建议保留最新一份以便新环境对照。

---

## 7. 一句话结论

**不是代码 bug，是环境缺 VHD 提供程序（FsDepends 起不来）。换一台 VHD 提供程序正常的 Windows 机器，用现有（已修好的）`build.ps1` 即可正常构建；本 DROIDVM guest 因内核限制无法走 diskpart 建 VHDX 这条路。**

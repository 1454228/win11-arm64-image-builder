# W11A64DVMB GUI 更新日志（CHANGELOG）

基于 [Droid-VM/win11-arm64-image-builder](https://github.com/Droid-VM/win11-arm64-image-builder) fork 的 Win11/ARM64 镜像构建工具。
GUI：`gui/fluent_gui.py`（PySide6 + PyQt-Fluent-Widgets）；构建引擎：`gui/builder/windows/build.ps1`；调度：`run_build.py`（UAC 提权）+ `schema.py`（环境变量归一化）。

所有 `.ps1` 文件均为 UTF-8 with BOM。当前最新版本：**v3.10**。

---

## v3.10（2026-10-07）— 热修：VMS_JSON 被路径归一化破坏，vmpkg 打包报「无法识别的转义序列」

**根因**（真机 v3.09 打包截图定位）：`schema.build_env` 对所有非 URL 字段做 `s.replace("/", "\\")` 路径归一化，`VMS_JSON` 也在字段表里被一并处理——JSON 内任何 `/`（如 `gpu_cgroup_path` 默认值 `/dev/cpuset/gpu`、备注里的 URL、共享目录路径）都被换成 `\`，产生 `\d` `\c` 等非法 JSON 转义，`pack-vmpkg.ps1` 的 `ConvertFrom-Json` 在第一个反斜杠处抛「无法识别的转义序列」。

**修复**：`hidden` 类型字段（机器生成的 JSON / 显示用标识符，非路径）一律跳过斜杠归一化；普通路径字段归一化行为不变。

**验证**：build_env 单测——VMS_JSON 含 `/dev/cpuset/gpu`、URL、`D:/tmp` 全部原样透传；`SRC_ISO` 仍归一化为反斜杠；`VMPKG_APP_VERSION` 含 `/` 不再被改。

## v3.09（2026-10-07）— vmpkg 打包修复 + VM 配置对齐上游全量字段

**修复（真机首轮打包暴露）**：
- **vmpkg 打包必失败**：`build.ps1` 用**数组 splat**（`@("-Qcow2", ..., "-Out", ...)`）调 `pack-vmpkg.ps1`，但 PowerShell 数组 splat 里的 `"-Name"` 字符串**不会**被解析成参数名，全部按位置绑定——第 6 位正好是 `-Compression`，吃进了 `.vmpkg` 路径，触发 ValidateSet 报错（`参数"D:\Win.vmpkg"不属于 "auto,zstd,gzip,none"`）。改为**哈希表 splat**（键名显式映射参数名）；回显用的参数列表独立成 `$packDisplay`，日志输出不变。
- `pack-vmpkg.ps1` 的 manifest 是整体透传 `vm` 对象（仅剔除 disks/id、重组 networks），因此以下新字段**无需改 packer**，随 `VMS_JSON` 直达 manifest。

**VM 配置对齐上游全量字段（重读 DroidVM 主仓库 `VMConfig.createWithCustomizeDefaults` + 各子配置类；Linux 开机协议按要求不提供）**：
- 基础：`backend`（crosvm/qemu）、`hypervisor` 扩至 auto/soft/kvm/gunyah/geniezone、`protected_vm` 扩至 4 值（protected_normal/protected_protected/protected_without_firmware/pseudo_unprotected）、`prepare_lend_mthp`（chunked/single/disabled）、`sandbox`、`strace`
- CPU：`cpu_affinity`/`cpu_capacity`/`cpu_clusters`（留空移除，走自动拓扑）、`gpu_cgroup_enabled`/`gpu_cgroup_path`/`gpu_cgroup_cpus`、`gpu_vram_folio_threshold_kb`
- 设备：xHCI `usb2_ports`/`usb3_ports`（0-15，App 默认 8/8）、virtio-snd 声卡开关（endpoints 形状与 `VMPeripheralConfig.createDefaultVirtioSound` 一致）、`vpu_enabled`/`vpu_host_pool_mb`/`vpu_guest_pool_mb`
- 屏幕：simplefb/gpu-0 各自的 `exporter`（native/vnc/none）、`input_enabled`、simplefb `poll_hz` 与 VNC 端口（5909）、gpu-0 `dpi_h`/`dpi_v` 与 VNC 端口（5900）；`display_blit_provider` 扩至 TURNIP/PANVK/SYSTEM/OFF
- 网络：网桥 `stp`、`l2.pseudo_bridge`（Wi-Fi 上游）、NIC `mac_address`（空则移除）
- 启动：UEFI `firmware`/`vars` 路径（空 = 内置 edk2）
- 新增「高级」纵向区块：`extra_options`（crosvm 额外参数，每行一条）、`environment_variables`（每行 KEY=VALUE，无 = 的行丢弃）、`shared_dirs`（每行 标签=路径 → `{tag,path,type:"fs"}`，App 仅实现 virtio-fs）
- 枚举序列化结论（源码级确认）：`DataItem.set(Enum)` 写 `name()`、读取 `Enums.optEnum` 大小写不敏感，故外设 type 用大写（`XHCI_USB`/`VIRTIO_SOUND`），与 App 自身导出一致

**布局同步**：v3.08 的响应式改造已覆盖其余选项卡（Windows/驱动/磁盘），本次新字段全部进入 vmpkg 页对应 FlowLayout 区块。

**校验**：`py_compile` 通过；offscreen 实测 `_build_vms_dict` 默认值 + 修改值共 20+ 项断言全过（含空键移除、JSON 可序列化、`mac_address`/`cpu_affinity` 空值剔除）；两 `.ps1` BOM 正常。

---

## v3.08（2026-10-06）— vmpkg 选项卡响应式重排版 + 备注/包版本/构建类型

**布局改造（用户要求“高效 + 自动适配窗口大小”）**：
- vmpkg 选项卡由「纵向固定卡片」改为 **FlowLayout 响应式卡片网格**：每个字段包成紧凑卡片，随窗口宽度自动换行（宽屏多列、窄屏少列），横向空间利用率更高。
- 新增 `_vfield`（标签+控件卡片）/ `_vsection`（FlowLayout 区块）/ `_vsection_v`（纵向区块，用于占满宽度的备注）/ `_spin_ctrl` / `_toggle_ctrl` / `_combo_ctrl` / `_text_ctrl` / `_edit_ctrl` 等构造器，替换原 `_spin_row`/`_toggle_row`/`_combo_row`/`_text_row`（仅 vmpkg 页使用，已整体替换）。
- 新增「备注与包信息」纵向区块，承载以下三个新字段。

**新增字段（依据 DroidVM 主仓库逆向结论）**：
- **VM 备注（Markdown）**：`vm.notes` 自由文本，导入后在 VM 信息页折叠展示；空则不放进 manifest（与 App `VMConfig.setNotes` 行为一致：空串 `remove("notes")`）。
- **包版本显示名**：manifest `app_version` 自由字符串，导入预览「包版本」直接显示它；空时 App 回退 "DroidVM"。经 `VMPKG_APP_VERSION` 环境变量 → `pack-vmpkg.ps1 -AppVersion`。
- **构建类型**：manifest `app_build_type` 自由字符串（release/debug 或自定义）。`pack-vmpkg.ps1` 新增 `-AppBuildType` 参数替代原硬编码 `"release"`；`build.ps1` 透传。
- `app_version_code` 固定为合法 u16（2650），由 packer 同时写容器头（偏移 8）与 manifest 保证二者一致（`PackageInput` 校验 `header.appVersionCode == manifest.app_version_code`）。

**改动文件**：`fluent_gui.py`（布局 + 三字段 + 数据流）、`i18n.py`（补 VMPKG_SEC_PKG/VMPKG_APP_VERSION/VMPKG_BUILDTYPE/VMPKG_NOTES 中英文）、`schema.py`（新增 `VMPKG_APP_VERSION`/`VMPKG_APP_BUILD_TYPE`/`VMPKG_APP_VERSION_CODE` 三个 hidden 字段）、`build.ps1`（读取并透传）、`pack-vmpkg.ps1`（新增 `-AppBuildType`）。

**校验**：`py_compile` + offscreen 实测——`_build_vms_dict` 10 项断言全过、空备注自动移除、`_collect_fields` 正确注入 `VMPKG_APP_VERSION`/`VMPKG_APP_BUILD_TYPE`/`VMPKG_APP_VERSION_CODE`、6 个 FlowLayout 区块、resize 无异常；两个 `.ps1` BOM 与括号配平（527/527）正常。

---

## v3.07（2026-10-06）— vmpkg VM 配置大幅扩展

**依据**：解析了 DroidVM App（0.0.6.r235.g32def8c）真实导出的空 `test.vmpkg`，并对照上游仓库 vms.json 模式与 README 说明。

**vmpkg 容器格式逆向结论**（`test.vmpkg`，非 tar/zip）：
- 24 字节头：`"VMPKG"\0` + u16 ver=1 + u16 app_version_code + u16 manifest_size + u16 compression + u16 reserved + i64 data_size（均 LE）
- 0x1000 偏移处为明文 `manifest.json`（顶层含 `manifest_version/format/created_at/app_*/compression/vm/disks/boots/networks`）
- 之后补零对齐，接压缩（ZSTD/GZIP）的 GNU tar 数据段（manifest.json + 磁盘镜像）

**vmpkg 选项卡新增分组与字段**（此前仅 内存/CPU/swiotlb/分辨率/刷新率/SBSA/网络名 7 项，现覆盖 App 导出配置的绝大多数可调项）：
- **基础**：VM 名称、hypervisor（auto/gunyah）、保护 VM 模式（protected_without_firmware/pseudo_unprotected）、**vmpkg 压缩方式**（auto/zstd/gzip/none，新 hidden 字段 `VMPKG_COMPRESSION` 透传 `pack-vmpkg.ps1 -Compression`）、VM 随 App 自启
- **CPU 与内存**：SMT、PMU、自动 CPU 拓扑、大页内存、内存 balloon、virtio-rng、Gunyah 动态共享
- **设备**：USB (xHCI)、COM1 作 Guest 控制台、SBSA 作 Guest 控制台（原有）
- **显示与 GPU**：simplefb/gpu-0 屏幕独立开关、GPU 加速 (virglrenderer)、显示 blit provider（可编辑，默认 TURNIP）、原生显示导出、VNC 服务 + VNC 端口（启用时按 App 导出形状在 `screens["gpu-0"]` 内嵌 `vnc:{host,port}`）
- **网络**：网络名（bridge）、上行接口（wlan0）、网络自动启用
- **启动**：UEFI 变量持久化（`boot.uefi.vars_enabled`）

**其他**：
- `schema.py` 新增 `VMPKG_COMPRESSION` hidden 字段；`build.ps1` 读取该环境变量并校验合法值后传给 packer（默认 auto）
- `fluent_gui.py` 新增 `_switch_row` / `_combo_row`（支持 EditableComboBox）/ `_text_row` 通用行构造器；导入 `EditableComboBox`
- `i18n.py` 中英文各补 28 条文案

## v3.06（2026-10-06）— 真实进度条

- 构建页新增 `ProgressBar` + 百分比标签：阶段映射（3% 启动 → preflight/建盘/灌镜像/注入驱动/精简 → qcow2 阶段解析 `qemu-img convert -p` 的 `(xx.xx/100%)` 输出映射到 80-96% → 100% 完成）
- 失败时进度条染红；构建启动归零
- offscreen 实测阶段推进逻辑（`test_progress.py`）

## v3.05（2026-10-06）— 自定义分区名 + 同步上游 vmpkg 功能

- **分区名**：快速分区表新增卷标输入框（分区 1 默认 Windows，其余 Data）；`build.ps1` NTFS 分支按布局 `name` 设置 `NewFileSystemLabel`（清洗 ≤32 字符、剔除 `* : < > " |`，空值回退默认名）
- **同步上游 vmpkg 输出**：侧边栏新增「vmpkg」选项卡（FluentIcon.APPLICATION）；输出格式切换（仅 qcow2 / vmpkg 含 VM 配置）；VM 配置（内存/CPU/swiotlb/分辨率/刷新率/SBSA 串口/网络名）
- 新增 `gui/builder/windows/pack-vmpkg.ps1`（上游原样，纯 PowerShell + 内置 tar.exe，`-Compression auto|zstd|gzip|none`）与 `vms.json` 默认模板（4GB RAM、swiotlb 256MB、SBSA 主控台、UEFI）
- `_collect_fields` 在 vmpkg 模式注入 `OUT_VMPKG`（由 OUT_QCOW 用 `rsplit(".qcow2",1)` 推导，避免 Windows..vmpkg 双点）与 `VMS_JSON`
- `build.ps1` 末尾新增 9b) vmpkg 打包段（写出 VMS_JSON → 调 pack-vmpkg.ps1；失败仅告警不影响 qcow2）
- 修复：`QScrollArea` 未导入 → 改用 qfluentwidgets `ScrollArea`

## v3.04（2026-10-06）— Sort-Object 参数错误

- 修复 `Sort-Object -Ascending`（该参数不存在）→ 删参数用默认升序：`Sort-Object PartitionNumber | Select-Object -First 1`

## v3.03（2026-10-06）— PowerShell 解析错误

- 修复容量提示跨行 `throw ("...")` 缺续行符 → 改 `throw $hint`（反引号续行）

## v3.02（2026-10-06）— 分区尺寸整数溢出

- 修复 "Not enough available capacity"：`_make_layout` 未扣 GPT 末端 16MB 预留，尺寸合计超出磁盘 → 末位分区改 `New-Partition -UseMaximumSize -GptType $gpt`

## v3.01（2026-10-06）— URL 保护 + 路径归一化 + 日志修复

- **SRC_ISO URL 被破坏**：引擎层无条件 `-replace '/','\'` 把 `https://` 改坏 → 加 `^https?://` 守卫（引擎 + `schema.py` 双层）
- **GUI 正斜杠显示**：新增 `_win_path()`（URL 原样保留）套用到 PATH_KEYS 集合（SRC_ISO/DRIVERS_DIR/GPU_DRIVERS_DIR/DRIVER_CERT/OPENSSH_SRC/OUT_QCOW/BUILD_TMP）的浏览/回填/收集全链路
- **日志框空白**：`log_box` 误用 QTextEdit（无 appendPlainText 静默失败）→ 改 `QPlainTextEdit` + 二进制增量读 + 截断自愈；`_on_build` 不再预写 started 避免与 run_build.py 截断竞争

## v3（初始 Fluent 版）

- 由 ctk_gui.py（CustomTkinter）迁移至 PySide6 + PyQt-Fluent-Widgets：FluentWindow 导航窗、卡片式分区、主题/强调色跟随系统注册表、InfoBar 提示
- 字段契约与 ctk_gui.py 等价（schema.py 单一事实来源），UAC 提权构建调度、实时日志轮询

---

## 打包说明

`make_package.py`（会话 artifact 目录）排除：`__pycache__/logs/.git/shots`、`build.log/build.pid/build_config.json/defaults.json`、`core.*`（崩溃转储）、`*.png`（预览截图）、`gpu-arm64-drivers/`（~160MB 按需下载）。典型包体积 10.6-10.7MB / 90+ 条目。

## 待办

- [ ] 真机 Windows 完整构建验证（沙箱无 Windows，diskpart/dism 无法实测）
- [ ] BUILD_DIAGNOSIS.md 内容已过时，待重写
- [ ] Windows PyInstaller .spec 打包未做
- [x] vmpkg：环境变量 / shared_dirs / extra_options 已暴露到 GUI（v3.09，对齐上游 VMConfig）
- [ ] 串口逐端口 backend 编辑（file/unix/pty 等 SerialBackend）未暴露，暂维持模板固定形状

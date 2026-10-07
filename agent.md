# agent.md — 项目简报（给后续会话的自己看）

> 用途：本项目所有上下文、决策、当前状态集中放这里。每次开工先读它。

## 1. 项目目标
把 Win11 ARM64 ISO + Gunyah/VirtIO 驱动，自动构建成可在 **DroidVM（Gunyah/crosvm）** 上启动的 `qcow2` 镜像。
- **真·上游**：`https://github.com/Droid-VM/win11-arm64-image-builder`（Droid-VM 组织仓库）。
- **用户 fork**：`https://github.com/1454228/win11-arm64-image-builder`（**已 fork，显示 forked from Droid-VM**）。
- 用户诉求：① 上传 GitHub 显示 fork 关系；② 改造并添加图形化界面；③ 长期覆盖 Windows / macOS / Linux 三端。

## 2. 工作区关键路径
- 旧 zip 解压：`C:\Users\USER\WorkBuddy\W11A64DVMB\Windows-ARM64-DroidVM-Builder\Windows-ARM64-DroidVM-Builder\`
- 新 fork clone：`C:\Users\USER\WorkBuddy\W11A64DVMB\win11-arm64-image-builder\`（目前为空/仅有上游文件，因为 git 命令行在此环境不可用，靠 API 推送）。
- GUI 代码：`C:\Users\USER\WorkBuddy\W11A64DVMB\gui\`

## 3. 已做决策
- **GUI 技术栈**：选项 B —— 全量用 **Python + CustomTkinter** 重写，弃用 WPF 与旧 Tkinter。
  - 理由：用户认为旧 Tkinter 界面太老气（XP 风格），CustomTkinter 现代、跨平台、改动小。
  - 依赖：`pip install customtkinter pillow`。
- **fork 策略**：已用 GitHub API 从 `Droid-VM/win11-arm64-image-builder` fork 到 `1454228/win11-arm64-image-builder`，徽章已生效。
- **自包含（无仓库根目录）**：用户明确要求“不要仓库根目录，构建文件存在本地，要的是程序本身而非外壳”。
  - 构建引擎（`windows/build.ps1` 全套 + 自带 `tools/qemu-img`）已打包进 `gui/builder/`，GUI 直接调用自家脚本。
  - 已移除 `repo_root` 概念、`_find_repo`、高级区“仓库根目录”输入框、`run_build.py --repo` 参数。
  - 默认输出路径改为 `~/DroidVM/Windows.qcow2`（用户本地、可写）。

## 4. 当前已实现（2026-09-12）
- ✅ 新建 fork：`https://github.com/1454228/win11-arm64-image-builder`
- ✅ 现代 GUI：`gui/ctk_gui.py` + `gui/schema.py` + `gui/i18n.py` + `gui/run_build.py`
  - CustomTkinter 外观，浅色/深色/跟随系统；参数中文标签；配置页 + 实时日志；UAC 提权、日志 tail；中英切换、默认配置存取。
  - **自包含**：`gui/builder/windows/build.ps1` 全套 + `gui/builder/tools/qemu-img/` 随程序打包。
- ✅ 驱动目录自动检测修复：
  - `DRIVER_DIR` 改为 **internal** 字段，GUI 永远强制注入 `ZIP/drivers`，不被用户/保存值覆盖（修复了“裸相对路径 `drivers` 找不到目录”崩溃）。
  - 修复 `_apply_driver_info` 误把探测到的子目录写进 `DRIVER_DIR` 的 bug。
  - `build.ps1` 驱动解析增加**自动回退**：`ZIP/drivers` 不存在时回退到包根目录（兼容 `drivers` 子目录 / 平铺两种布局）。
- ✅ 已通过 GitHub Contents API 把 `gui/` 推送到 fork（含 `builder/` 构建引擎）。
- ✅ `py_compile` 全过。

## 5. 待办
1. **本机验证 GUI**：用户 Windows 上 `pip install -r gui/requirements.txt` 后 `python gui/ctk_gui.py`，确认界面、字段标签、无仓库根目录。
2. **真实构建验证**：Win11 ARM64 ISO + 驱动 zip，跑一次，确认 `DRIVER_DIR` 自动定位成功、UAC 提权、日志实时、退出码捕获正常。
3. **把 zip 里的 Windows 改进并入 fork**：内置 qemu-img、debloat、计算机名处理等（上游原版没有这些）—— 已通过打包 `builder/` 实现。
4. **补齐上游能力**：`vms.json` + `.vmpkg` 打包、EMS/SAC 控制台、zstd 压缩等。
5. **macOS / Linux 路线 GUI**：复用 `ctk_gui.py` 外壳。
6. **PyInstaller 打包成 exe**。

## 6. 环境 / 构建要点
- Windows 路线需 x64 Windows + 管理员；`dism`/`bcdboot`/`diskpart` 内置。
- 驱动包约定：gunyah-arm64-drivers.zip 内含 `drivers/` 子目录（`DRIVER_DIR=ZIP/drivers`）；引擎自动回退到包根兼容平铺布局。
- `gui/builder/windows/files/` 是驱动/ISO 的**解压缓存**，随构建自动生成，**不要**预先塞入陈旧文件夹（会导致“driver folder not found”）。
- 所有 `.ps1`/`.xml` 必须 **UTF-8 with BOM**，否则中文乱码。
- 产物 7–14GB，注意磁盘空间。

## 7. 安全
- GitHub token 已使用，建议用户在 fork+push 完成后到 GitHub Settings → Developer settings → Tokens 里吊销该 PAT。

# DroidVM Win11 ARM64 镜像构建器 — Python 图形界面

> 这是“全量用 Python 重写 GUI、弃用 WPF”（计划选项 B）的落地实现。
> GUI 是**程序本身**：构建引擎（`windows/build.ps1` 及其配套文件、自带 `qemu-img`）已随程序打包在 `gui/builder/`，**无需仓库根目录、无需另行 clone 原仓库**即可运行。

## 运行条件
- Windows（构建本身需要 x64 Windows + 管理员权限；GUI 可普通权限启动，构建时会弹 UAC 提权）
- Python 3.8+（官方 Windows 版）
- 安装依赖：
  ```bat
  pip install -r requirements.txt
  ```
  即 `customtkinter` + `Pillow`。

## 启动
```bat
cd gui
python ctk_gui.py
```
程序会自动使用自带的 `gui/builder/windows/build.ps1` 构建引擎，无需任何外部仓库。

## 界面特性（贴近 WPF 原版）
- **紧凑表单布局**：标签在左、输入在右、底部操作按钮，类似 Win32/WPF 应用。
- **镜像版本自动检测**：选择 ISO/WIM/ESD 后点“检测”，下拉框会列出 `Windows 11 Pro` / `Home` 等版本（WinNTSetup 风格）。
- **驱动包自动探测**：选择驱动 zip/文件夹后，自动探测驱动列表，并以勾选框组呈现；驱动根子目录由构建引擎自动定位（GUI 不展示该细节）。
- **密码明文显示**：方便核对。
- **浅色 / 深色 / 跟随系统** 主题。
- **可折叠构建日志**：默认隐藏，构建时自动展开并实时 tail。
- **中英文切换**、默认配置保存/载入/重置。

## 文件
| 文件 | 作用 |
|------|------|
| `ctk_gui.py` | 主界面（CustomTkinter）。WPF 式紧凑布局、自动探测、提权构建、实时日志 |
| `schema.py` | 字段契约（单一事实来源）：UI 字段 ↔ `build.ps1` 环境变量映射，改这里即可增删字段 |
| `i18n.py` | 多语言（zh-CN 默认 / en-US） |
| `run_build.py` | 被 UAC 提权后实际执行构建的无界面运行器，输出写入 `build.log` 供 GUI 实时读取 |
| `builder/` | **随程序自带构建引擎**（无需仓库根目录） |
| `builder/windows/build.ps1` | 离线 DISM 注入驱动 → 生成 qcow2 的核心脚本 |
| `builder/windows/{debloat,setup-ssh,cleanup}.ps1`、`unattend.xml` | 构建配套脚本 |
| `builder/tools/qemu-img/` | 自带 qemu-img 及依赖 |
| `defaults.json` | 你“保存为默认”的配置（自动生成） |
| `build_config.json` / `build.log` | 每次构建的临时配置与日志（自动生成） |

## 构建流程
1. 填表（ISO 来源必填；用户名/密码/计算机名/磁盘大小等可选，留空用 `build.ps1` 内置默认）。
2. 点“开始构建” → 配置写入 `build_config.json` → 按需 UAC 提权启动 `run_build.py` → 实时 tail `build.log`。
3. 构建完成会在日志末尾打印 `=== EXIT CODE: 0 ===`，产物为 `OUT_QCOW` 指定的 qcow2（默认在 `~/DroidVM/Windows.qcow2`）。

## 设计要点
- **自包含**：构建引擎打包在 `gui/builder/`，GUI 调用自家脚本，没有“仓库根目录”概念，移动整个 `gui/` 文件夹即可使用。
- **最小侵入**：GUI 只生成环境变量 + 调入口脚本，构建逻辑改动都集中在 `builder/windows/build.ps1`。
- **可扩展**：新增配置项只需在 `schema.py` 的 `FIELDS` 里加一行；macOS/Linux 路线后续在此框架上扩展。

## 待办（对应 PLAN 的 Phase 5–9）
- macOS 路线、Linux 路线
- `vms.json` + `.vmpkg` 打包的可视化编辑
- PyInstaller 打包成 exe

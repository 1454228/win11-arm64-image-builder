# DroidVM Win11 ARM64 镜像构建器 — Python 图形界面

> 这是“全量用 Python 重写 GUI、弃用 WPF”（计划选项 B）的落地实现。
> 它**只负责生成配置 + 调用原 `windows/build.ps1`**，不改动构建逻辑（最小侵入）。

## 运行条件
- Windows（构建本身需要 x64 Windows + 管理员权限；GUI 可普通权限启动，构建时会弹 UAC 提权）
- Python 3.8+（官方 Windows 版）
- 安装依赖：
  ```bat
  pip install -r requirements.txt
  ```
  即 `customtkinter` + `Pillow`。
- 仓库根目录需含 `windows/build.ps1`（Fork 原仓库 `Droid-VM/win11-arm64-image-builder` 后放入 `gui/` 即可）

## 启动
```bat
cd <仓库根目录>\gui
python ctk_gui.py
```
GUI 会自动探测仓库根目录（向上找 `windows/build.ps1`），也可手动修改“仓库根目录”框。

## 文件
| 文件 | 作用 |
|------|------|
| `ctk_gui.py` | 主界面（CustomTkinter）。配置表单、平台检测、提权构建、实时日志、中英文切换、默认配置存取 |
| `droidvm_builder_gui.py` | 旧版 Tkinter 界面（已弃用，保留作参考） |
| `schema.py` | 字段契约（单一事实来源）：UI 字段 ↔ `build.ps1` 环境变量映射，改这里即可增删字段 |
| `i18n.py` | 多语言（zh-CN 默认 / en-US） |
| `run_build.py` | 被 UAC 提权后实际执行构建的无界面运行器，输出写入 `build.log` 供 GUI 实时读取 |
| `defaults.json` | 你“保存为默认”的配置（自动生成） |
| `build_config.json` / `build.log` | 每次构建的临时配置与日志（自动生成） |

## 构建流程
1. 填表（ISO 来源必填；用户名/密码/计算机名/磁盘大小等可选，留空用 `build.ps1` 内置默认）。
2. 点“开始构建” → 配置写入 `build_config.json` → 按需 UAC 提权启动 `run_build.py` → 实时 tail `build.log`。
3. 构建完成会在日志末尾打印 `=== EXIT CODE: 0 ===`，产物为 `OUT_QCOW` 指定的 qcow2。

## 设计要点
- **现代 UI**：使用 CustomTkinter，支持浅色 / 深色 / 跟随系统，参数用中文可读标签。
- **最小侵入**：GUI 只生成环境变量 + 调入口脚本，上游 `build.ps1` 一行不改，能随时合上游更新。
- **可扩展**：新增配置项只需在 `schema.py` 的 `FIELDS` 里加一行；macOS/Linux 路线后续在此框架上扩展。

## 待办（对应 PLAN 的 Phase 5–9）
- macOS 路线（调 `macos_build.sh`）、Linux 路线
- `vms.json` + `.vmpkg` 打包的可视化编辑
- PyInstaller 打包成 exe

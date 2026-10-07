# PLAN.md — Windows-ARM64-DroidVM-Builder 改造计划（含图形化界面）

> 配套简报见 `agent.md`。
> 状态：**Phase 0/1 已推进** — fork 已建，现代 GUI 已写并推送。

## 0. 事实校准
- 用户旧的 `1454228/Windows-ARM64-DroidVM-Builder` 已删除。
- 新的 fork 来源为 **`Droid-VM/win11-arm64-image-builder`**，地址：`https://github.com/1454228/win11-arm64-image-builder`。
- GUI 技术栈：**Python + CustomTkinter**（不再使用旧 Tkinter / WPF）。

## 1. 目标
1. 上传 GitHub，显示 **“forked from Droid-VM/win11-arm64-image-builder”** —— ✅ 已完成。
2. 改造 builder，含图形化界面 —— ✅ 第一版已完成，待本机验证。
3. 平台路线：先 Windows；长期 **Windows + macOS + Linux** 三端。

## 2. 分阶段路线
**Phase 0 — 建 fork 关系** ✅
- 已用 GitHub API fork 原仓库到 `1454228/win11-arm64-image-builder`。
- 已用 GitHub Contents API 把 `gui/` 目录推送到 fork。

**Phase 1 — Windows GUI 本机验证**（当前）
- 用户在 Windows 上安装依赖 `pip install -r gui/requirements.txt`。
- 运行 `python gui/ctk_gui.py`，确认：
  - 界面不再“XP 风格”，标签为中文可读名称。
  - **已自包含**：无需仓库根目录，构建引擎在 `gui/builder/` 内；默认输出 `~/DroidVM/Windows.qcow2`。
  - 驱动目录自动检测（`DRIVER_DIR` 内部固定 `ZIP/drivers` + 引擎回退），不展示细节。
  - 保存/载入默认配置正常。

**Phase 2 — 真实构建验证**
- 准备 Win11 ARM64 ISO。
- 在 GUI 里填好 ISO 路径、输出路径，点“开始构建”。
- 确认 UAC 提权窗口弹出、PowerShell 后台运行、`build.log` 实时刷新、退出码正确捕获。

**Phase 3 — 把 zip 里的 Windows 改进并入 fork**
- 内置 qemu-img、debloat、`DVM_COMPUTERNAME`、时区、OpenSSH 等。
- 这些在上游原版 `Droid-VM/win11-arm64-image-builder` 中不存在，需要从 zip 搬运/重写。

**Phase 4 — 补齐上游能力**
- `vms.json` VM 配置模板 + `.vmpkg` 打包。
- zstd 压缩、EMS/SAC 串行控制台等上游新特性。

**Phase 5 — macOS 路线**
- 引入上游 `macos_build.sh` + `macos/`，GUI 平台分支切到它。

**Phase 6 — Linux 路线（长期）**
- 上游无 Linux 构建路线，需新增或复用 macOS 思路。

**Phase 7 — 打包分发**
- Windows：PyInstaller 把 `ctk_gui.py` 打成 exe。
- macOS/Linux：对应打包。

**Phase 8 — 文档**
- 更新根目录 README（fork 关系、GUI 用法、三端状态）、DEVELOPER.md。

## 3. 关键技术注意点
- 新 GUI 基于 **CustomTkinter**，依赖 `customtkinter` + `Pillow`。
- `windows/build.ps1` 需要管理员权限；GUI 通过 `Start-Process -Verb RunAs` 提权启动 `run_build.py`，再用日志文件 tail 收集输出。
- 所有 `.ps1`/`.xml` 必须 **UTF-8 with BOM**（中文不乱码）。
- 最小侵入原则：GUI 只生成配置（环境变量/文件）+ 调入口脚本，不改 `build.ps1` 内部。

## 4. 下一步
1. 用户本机运行 `python gui/ctk_gui.py` 验证界面。
2. 如有 UI 问题，截图反馈，我迭代。
3. 界面 OK 后，用真实 ISO 跑构建验证。

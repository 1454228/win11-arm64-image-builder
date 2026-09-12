# -*- coding: utf-8 -*-
"""
i18n.py — 极简多语言支持（zh-CN 默认，en-US 备选）

用法:
    from i18n import set_lang, T
    set_lang("en-US")
    print(T("BUILD"))
"""

_LANGS = {
    "zh-CN": {
        "APP_TITLE": "DroidVM Win11 ARM64 镜像构建器",
        "SECTION_SOURCE": "来源",
        "SECTION_DRIVERS": "驱动",
        "SECTION_ACCOUNT": "账户",
        "SECTION_OUTPUT": "输出",
        "SECTION_ADVANCED": "高级",
        "BUILD": "开始构建",
        "STOP": "停止",
        "SAVE_DEFAULTS": "保存为默认",
        "LOAD_DEFAULTS": "载入默认",
        "RESET": "重置",
        "LANG": "语言",
        "THEME": "主题",
        "LIGHT": "浅色",
        "DARK": "深色",
        "SYSTEM": "跟随系统",
        "REPO_ROOT": "仓库根目录",
        "REPO_HELP": "包含 windows/build.ps1 的目录（自动探测，可手动改）",
        "TAB_CONFIG": "配置",
        "TAB_LOG": "构建日志",
        "LOG_HINT": "点击“开始构建”后，这里实时显示进度…",
        "BROWSE": "浏览",
        "BROWSE_FILE": "选择文件",
        "BROWSE_DIR": "选择文件夹",
        "ABOUT": "关于",
        "ABOUT_TEXT": (
            "DroidVM Win11 ARM64 镜像构建器\n"
            "fork 自 Droid-VM/win11-arm64-image-builder\n\n"
            "GUI 仅负责生成配置并调用原构建脚本，不改其逻辑。"
        ),
        "NEED_ADMIN": "提示：构建需要管理员权限，将弹出 UAC 提权窗口。",
        "BUILDING": "构建进行中…",
        "BUILD_DONE": "构建完成（退出码 {0}）",
        "BUILD_FAIL": "构建失败（退出码 {0}）",
        "BUILD_STOPPED": "已尝试停止构建进程",
        "NOT_WIN": "当前非 Windows 平台：Windows 路线暂不可用（macOS/Linux 路线开发中）。",
        "SAVED": "已保存默认配置",
        "LOADED": "已载入默认配置",
        "RESET_DONE": "已重置为内置默认",
        "FIELD_REQUIRED": "必填项缺失：{0}",
        "NO_ISO": "请先填写 ISO 来源",
        "EXIT_CODE": "退出码",
        "PLATFORM": "平台",
        "STATUS_READY": "就绪",
        "STATUS": "状态",
        "SELECT_ISO": "选择 ISO 文件",
        "SELECT_FILE": "选择文件",
        "SELECT_FOLDER": "选择文件夹",
        "SAVE_AS": "保存为",
        "ERROR": "错误",
        "MISSING_DEP": "缺少依赖：{0}\n\n请运行：pip install {1}",
    },
    "en-US": {
        "APP_TITLE": "DroidVM Win11 ARM64 Image Builder",
        "SECTION_SOURCE": "Source",
        "SECTION_DRIVERS": "Drivers",
        "SECTION_ACCOUNT": "Account",
        "SECTION_OUTPUT": "Output",
        "SECTION_ADVANCED": "Advanced",
        "BUILD": "Build",
        "STOP": "Stop",
        "SAVE_DEFAULTS": "Save as default",
        "LOAD_DEFAULTS": "Load default",
        "RESET": "Reset",
        "LANG": "Language",
        "THEME": "Theme",
        "LIGHT": "Light",
        "DARK": "Dark",
        "SYSTEM": "System",
        "REPO_ROOT": "Repo root",
        "REPO_HELP": "Directory containing windows/build.ps1 (auto-detected, editable)",
        "TAB_CONFIG": "Config",
        "TAB_LOG": "Build log",
        "LOG_HINT": "Click 'Build' to see live progress here…",
        "BROWSE": "Browse",
        "BROWSE_FILE": "Select file",
        "BROWSE_DIR": "Select folder",
        "ABOUT": "About",
        "ABOUT_TEXT": (
            "DroidVM Win11 ARM64 Image Builder\n"
            "Forked from Droid-VM/win11-arm64-image-builder\n\n"
            "The GUI only generates config and calls the original build script."
        ),
        "NEED_ADMIN": "Note: building requires admin; a UAC prompt will appear.",
        "BUILDING": "Building…",
        "BUILD_DONE": "Build finished (exit code {0})",
        "BUILD_FAIL": "Build failed (exit code {0})",
        "BUILD_STOPPED": "Attempted to stop the build process",
        "NOT_WIN": "Not on Windows: the Windows route is unavailable (macOS/Linux coming later).",
        "SAVED": "Default config saved",
        "LOADED": "Default config loaded",
        "RESET_DONE": "Reset to built-in defaults",
        "FIELD_REQUIRED": "Missing required field: {0}",
        "NO_ISO": "Please fill in the ISO source first",
        "EXIT_CODE": "Exit code",
        "PLATFORM": "Platform",
        "STATUS_READY": "Ready",
        "STATUS": "Status",
        "SELECT_ISO": "Select ISO file",
        "SELECT_FILE": "Select file",
        "SELECT_FOLDER": "Select folder",
        "SAVE_AS": "Save as",
        "ERROR": "Error",
        "MISSING_DEP": "Missing dependency: {0}\n\nPlease run: pip install {1}",
    },
}

_CURRENT = "zh-CN"


def set_lang(lang):
    global _CURRENT
    if lang in _LANGS:
        _CURRENT = lang


def get_lang():
    return _CURRENT


def T(key):
    return _LANGS.get(_CURRENT, _LANGS["zh-CN"]).get(key, key)

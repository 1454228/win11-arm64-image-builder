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
        "FORK_FROM": "Forked from Droid-VM/win11-arm64-image-builder",
        "SECTION_SOURCE": "来源",
        "SECTION_DRIVERS": "驱动",
        "SECTION_ACCOUNT": "账户",
        "SECTION_OUTPUT": "输出",
        "SECTION_ADVANCED": "高级",
        "BUILD": "开始构建",
        "STOP": "停止",
        "SAVE_DEFAULTS": "保存默认",
        "LOAD_DEFAULTS": "载入默认",
        "RESET": "重置",
        "EXIT": "退出",
        "LANG": "语言",
        "THEME": "主题",
        "LIGHT": "浅色",
        "DARK": "深色",
        "SYSTEM": "跟随系统",
        "NO_ENGINE": "未找到内置构建引擎：\n{0}\n\n请确认 gui/builder/windows/build.ps1 随程序一起存在。",
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
        "SELECT_ISO": "选择 ISO/WIM/ESD 文件",
        "SELECT_FILE": "选择文件",
        "SELECT_FOLDER": "选择文件夹",
        "SAVE_AS": "保存为",
        "ERROR": "错误",
        "MISSING_DEP": "缺少依赖：{0}\n\n请运行：pip install {1}",
        # 新版界面新增
        "AUTO": "自动",
        "DETECT": "检测",
        "DETECTING_IMAGE": "正在检测镜像版本…",
        "IMAGE_DETECT_DONE": "已检测到 {0} 个镜像版本",
        "IMAGE_DETECT_FAILED": "未能自动检测镜像版本，可手动输入索引号",
        "DETECTING_DRIVERS": "正在探测驱动包…",
        "DRIVER_DETECT_DONE": "已探测到 {0} 个驱动",
        "DRIVER_DETECT_FAILED": "未能探测驱动列表，使用默认勾选",
        "DRIVERS_TO_INSTALL": "要安装的驱动",
        "SHOW_LOG": "构建日志 ▼",
        "HIDE_LOG": "构建日志 ▲",
        "ADVANCED": "高级选项",
        "SHOW": "展开",
        "HIDE": "收起",
        "AUTO_CONFIG_TITLE": "自动配置项（构建时自动设置）",
        "AUTO_CONFIG_TEXT": (
            "语言：保持镜像默认语言（不覆盖）\n"
            "时区：自动检测本机时区\n"
            "RDP：自动开启｜密码永不过期｜永不睡眠\n"
            "Debloat 关闭（保留原版 Windows，含应用商店/广告）"
        ),
    },
    "en-US": {
        "APP_TITLE": "DroidVM Win11 ARM64 Image Builder",
        "FORK_FROM": "Forked from Droid-VM/win11-arm64-image-builder",
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
        "EXIT": "Exit",
        "LANG": "Language",
        "THEME": "Theme",
        "LIGHT": "Light",
        "DARK": "Dark",
        "SYSTEM": "System",
        "NO_ENGINE": "Bundled build engine not found:\n{0}\n\nEnsure gui/builder/windows/build.ps1 ships with the program.",
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
        "SELECT_ISO": "Select ISO/WIM/ESD file",
        "SELECT_FILE": "Select file",
        "SELECT_FOLDER": "Select folder",
        "SAVE_AS": "Save as",
        "ERROR": "Error",
        "MISSING_DEP": "Missing dependency: {0}\n\nPlease run: pip install {1}",
        # 新版界面新增
        "AUTO": "Auto",
        "DETECT": "Detect",
        "DETECTING_IMAGE": "Detecting image editions…",
        "IMAGE_DETECT_DONE": "Detected {0} image editions",
        "IMAGE_DETECT_FAILED": "Could not auto-detect image editions; enter index manually",
        "DETECTING_DRIVERS": "Detecting driver package…",
        "DRIVER_DETECT_DONE": "Detected {0} drivers",
        "DRIVER_DETECT_FAILED": "Could not detect driver list; using default selection",
        "DRIVERS_TO_INSTALL": "Drivers to install",
        "SHOW_LOG": "Build log ▼",
        "HIDE_LOG": "Build log ▲",
        "ADVANCED": "Advanced options",
        "SHOW": "Show",
        "HIDE": "Hide",
        "AUTO_CONFIG_TITLE": "Auto-config (applied during build)",
        "AUTO_CONFIG_TEXT": (
            "Language: keep image default (do not override)\n"
            "Timezone: auto-detect host timezone\n"
            "RDP: auto-enable | Password never expires | Never sleep\n"
            "Debloat off (keep stock Windows, including Store/ads)"
        ),
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

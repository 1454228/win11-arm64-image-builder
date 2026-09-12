# -*- coding: utf-8 -*-
"""
schema.py — 字段契约（单一事实来源）

UI 表单的每一个字段都映射到 windows/build.ps1 读取的环境变量。
改这里 = 改 GUI 表单，build.ps1 不变（最小侵入原则）。

字段元组格式:
  (section, key, type, default, env_var,
   label_zh, label_en, help_zh, help_en)
  - section   : 分组 key（见 i18n SECTION_*）
  - key       : 内部字段名（也用作配置 json 的 key）
  - type      : text | int | password | bool | textarea | file | save | path_or_url
  - default   : 默认值（bool 用 True/False，其余用字符串）
  - env_var   : 传给 build.ps1 的环境变量名（空字符串表示不传）
  - label_*   : 界面上显示的中文/英文名称
  - help_*    : 中文/英文提示文字
"""
from i18n import T, get_lang

# 字段定义。顺序即界面显示顺序。
FIELDS = [
    # ---------- 来源 ----------
    ("source", "SRC_ISO", "path_or_url", "", "SRC_ISO",
     "Win11 ARM64 ISO 文件或 URL",
     "Win11 ARM64 ISO file or URL",
     "支持 .iso / .wim / .esd，也支持下载链接",
     "Supports .iso / .wim / .esd, or a download URL"),

    ("source", "IMAGE_INDEX", "int", "0", "IMAGE_INDEX",
     "镜像索引号",
     "Image index",
     "0 = 列出可选版本；或填具体索引号",
     "0 = list editions; or enter a specific index"),

    # ---------- 驱动 ----------
    ("drivers", "DRIVERS_DIR", "path_or_url",
     "https://github.com/Droid-VM/gunyah-guest-drivers-windows/releases/download/dev/gunyah-arm64-drivers.zip",
     "DRIVERS_DIR",
     "驱动包（zip / 文件夹 / URL）",
     "Driver package (zip / folder / URL)",
     "Gunyah/VirtIO 驱动包，或下载链接",
     "Gunyah/VirtIO driver package, or a download URL"),

    ("drivers", "DRIVER_DIR", "text", "ZIP/drivers", "DRIVER_DIR",
     "驱动包内子目录",
     "Driver sub-directory",
     "zip 内驱动子目录，默认 ZIP/drivers",
     "Sub-directory inside the zip; default ZIP/drivers"),

    ("drivers", "DRIVER_INSTALL", "text", "", "DRIVER_INSTALL",
     "要安装的驱动",
     "Drivers to install",
     "空格分隔的子目录名（如 NetKVM viostor），留空 = 全部",
     "Space-separated names (e.g. NetKVM viostor); empty = all"),

    ("drivers", "DRIVER_CERT", "file", "", "DRIVER_CERT",
     "驱动签名证书",
     "Driver signature certificate",
     "可选：签名证书路径，留空自动从 .cat 提取",
     "Optional: certificate path; empty = auto-extract from .cat"),

    # ---------- 账户 ----------
    ("account", "DVM_USERNAME", "text", "USER", "DVM_USERNAME",
     "默认用户名",
     "Default username",
     "",
     ""),

    ("account", "DVM_PASSWORD", "password", "DroidVM", "DVM_PASSWORD",
     "默认密码",
     "Default password",
     "",
     ""),

    ("account", "DVM_COMPUTERNAME", "text", "DROIDVM", "DVM_COMPUTERNAME",
     "计算机名",
     "Computer name",
     "≤15 字符，字母数字与连字符；留空默认 DROIDVM",
     "≤15 chars, alphanumeric and hyphens; empty = DROIDVM"),

    ("account", "SSH_PUBKEY", "textarea", "", "SSH_PUBKEY",
     "SSH 公钥",
     "SSH public key",
     "可选：填入公钥启用密钥登录（否则仅密码）",
     "Optional: enable key-based login (otherwise password only)"),

    # ---------- 输出 ----------
    ("output", "OUT_QCOW", "save", "Windows.qcow2", "OUT_QCOW",
     "输出 qcow2 路径",
     "Output qcow2 path",
     "",
     ""),

    ("output", "DISK_SIZE_MB", "int", "40960", "DISK_SIZE_MB",
     "虚拟磁盘大小（MB）",
     "Virtual disk size (MB)",
     "默认 40960 MB（约 40 GB）",
     "Default 40960 MB (~40 GB)"),

    ("output", "COMPRESS", "bool", False, "COMPRESS",
     "压缩 qcow2",
     "Compress qcow2",
     "勾选则 qemu-img 加 -c 压缩（更小但更慢）",
     "Use qemu-img -c (smaller but slower)"),

    ("output", "DEBLOAT", "bool", False, "DEBLOAT",
     "精简系统",
     "Debloat system",
     "勾选则额外移除预装应用、关休眠、关保留存储",
     "Remove preinstalled apps, disable hibernate and reserved storage"),

    # ---------- 高级 ----------
    ("advanced", "TARGET_TIMEZONE", "text", "", "TARGET_TIMEZONE",
     "目标时区",
     "Target timezone",
     "留空 = 自动使用宿主时区",
     "Empty = use host timezone"),

    ("advanced", "OPENSSH_SRC", "file", "", "OPENSSH_SRC",
     "OpenSSH 安装包",
     "OpenSSH package",
     "可选：OpenSSH 压缩包路径；留空仅启用 RDP",
     "Optional: OpenSSH archive path; empty = RDP only"),
]

# 用于按 section 分组
SECTION_ORDER = ["source", "drivers", "account", "output", "advanced"]


def default_values():
    """返回默认字段值的 dict（供初始化/重置使用）。"""
    d = {}
    for _sec, key, _typ, default, _env, _lzh, _len, _hzh, _hen in FIELDS:
        d[key] = default if isinstance(default, bool) else str(default)
    return d


def build_env(fields):
    """把表单字段转成要注入 build.ps1 的环境变量 dict（忽略空值）。"""
    env = {}
    for _sec, key, _typ, _default, env_var, _lzh, _len, _hzh, _hen in FIELDS:
        if not env_var:
            continue
        val = fields.get(key, "")
        if isinstance(val, bool):
            if val:
                env[env_var] = "1"
            continue
        s = str(val).strip()
        if s == "":
            continue
        env[env_var] = s
    return env


def field_meta(key):
    """返回某字段的完整元组。"""
    for tup in FIELDS:
        if tup[1] == key:
            return tup
    return None


def label(key):
    """当前语言下的字段显示名。"""
    tup = field_meta(key)
    if not tup:
        return key
    return tup[5] if get_lang() == "zh-CN" else tup[6]


def help_text(key):
    """当前语言下的字段提示。"""
    tup = field_meta(key)
    if not tup:
        return ""
    return tup[7] if get_lang() == "zh-CN" else tup[8]


def section_title(section):
    return T("SECTION_" + section.upper())


if __name__ == "__main__":
    import json
    print(json.dumps(default_values(), indent=2, ensure_ascii=False))

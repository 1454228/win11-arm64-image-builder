# -*- coding: utf-8 -*-
"""
schema.py — 字段契约（单一事实来源）

UI 表单的每一个字段都映射到 windows/build.ps1 读取的环境变量。
改这里 = 改 GUI 表单，build.ps1 不变（最小侵入原则）。

字段元组格式:
  (section, key, type, default, env_var,
   label_zh, label_en, help_zh, help_en)
  - section   : 分组 key（仅用于代码组织，新版 GUI 不再显式按 section 出标题）
  - key       : 内部字段名（也用作配置 json 的 key）
  - type      : text | int | password | bool | textarea | file | save | path_or_url | hidden | internal
  - default   : 默认值（bool 用 True/False，其余用字符串）
  - env_var   : 传给 build.ps1 的环境变量名（空字符串表示不传）
  - label_*   : 界面上显示的中文/英文名称（hidden/internal 类型可填空）
  - help_*    : 中文/英文提示文字
  - 注:
      hidden   = GUI 内部使用（如 DRIVER_INSTALL 由勾选框生成），可被 collect 读取
      internal = 完全内置、固定值（如 DRIVER_DIR=ZIP/drivers），不被用户/保存值覆盖
"""
from i18n import T, get_lang

# 常见 VirtIO/Gunyah 驱动子目录名（用于默认勾选列表）
# 实际可用驱动会根据用户选择的驱动包自动探测更新。
KNOWN_DRIVERS = [
    "NetKVM",
    "viostor",
    "vioscsi",
    "vioinput",
    "viosnd",
    "viofs",
    "viogpudo",
    "viorng",
    "pvpanic",
    "balloon",
    "fwcfg",
    "rdmapool",
]

# 字段定义。顺序仅影响代码遍历/默认值，新版 GUI 布局由 ctk_gui.py 硬编码。
FIELDS = [
    # ---------- 来源 ----------
    ("source", "SRC_ISO", "path_or_url", "", "SRC_ISO",
     "系统镜像",
     "System image",
     "自动检测: ISO / WIM / ESD",
     "Auto-detect: ISO / WIM / ESD"),

    # 镜像版本/索引：GUI 会把它渲染成下拉框，选择 ISO 后自动列出 Windows 版本。
    ("source", "IMAGE_INDEX", "text", "0", "IMAGE_INDEX",
     "镜像版本",
     "Image edition",
     "0 = 自动选择；也可手动填写索引号",
     "0 = auto; or enter a specific index"),

    # ---------- 驱动 ----------
    ("drivers", "DRIVERS_DIR", "path_or_url",
     "https://github.com/Droid-VM/gunyah-guest-drivers-windows/releases/download/dev/gunyah-arm64-drivers.zip",
     "DRIVERS_DIR",
     "驱动来源",
     "Driver source",
     "Gunyah/VirtIO 驱动包或下载链接",
     "Gunyah/VirtIO driver package or download URL"),

    # 驱动包内子目录：脚本内置约定为 ZIP/drivers（即提取后的驱动包根目录里的 drivers 子目录）
    # 设为 internal：GUI 永远强制注入 "ZIP/drivers"，不被用户或已保存配置覆盖，避免相对路径找不到目录。
    ("drivers", "DRIVER_DIR", "internal", "ZIP/drivers", "DRIVER_DIR",
     "", "", "", ""),

    # 要安装的驱动：GUI 渲染成勾选框组，env 以空格/逗号分隔传递
    ("drivers", "DRIVER_INSTALL", "hidden", "", "DRIVER_INSTALL",
     "", "", "", ""),

    ("drivers", "DRIVER_CERT", "file", "", "DRIVER_CERT",
     "驱动签名证书",
     "Driver certificate",
     "可选：签名证书路径，留空自动从 .cat 提取",
     "Optional: certificate path; empty = auto-extract from .cat"),

    # ---------- 账户 ----------
    ("account", "DVM_USERNAME", "text", "USER", "DVM_USERNAME",
     "用户名",
     "Username",
     "",
     ""),

    ("account", "DVM_PASSWORD", "text", "DroidVM", "DVM_PASSWORD",
     "密码",
     "Password",
     "",
     ""),

    ("account", "DVM_COMPUTERNAME", "text", "DROIDVM", "DVM_COMPUTERNAME",
     "计算机名",
     "Computer name",
     "≤15 字符，字母数字与连字符",
     "≤15 chars, alphanumeric and hyphens"),

    ("account", "SSH_PUBKEY", "textarea", "", "SSH_PUBKEY",
     "SSH 公钥",
     "SSH public key",
     "可选：填入公钥启用密钥登录（否则仅密码）",
     "Optional: enable key-based login (otherwise password only)"),

    # ---------- 输出 ----------
    # 默认留空：build.ps1 会自动把输出放到空闲最大的非系统盘（优先 D: 的 D:\DroidVM\Windows.qcow2）。
    # 用户填了绝对路径则以用户为准（可显式指到 D: 等大盘）。
    ("output", "OUT_QCOW", "save", "", "OUT_QCOW",
     "输出路径",
     "Output path",
     "留空=自动放到空闲最大的盘（优先 D:）；也可手动填 D:\\xxx.qcow2",
     "Empty=auto-place on the drive with most free space (prefer D:); or set e.g. D:\\xxx.qcow2"),

    # 临时工作目录（VHDX 在此生成/挂载）。留空时 build.ps1 自动选最佳数据盘（优先 D:\droidvm_tmp），
    # 彻底规避 C: 空间不足导致的 DISM "磁盘空间不足" (exit 433)。
    ("output", "BUILD_TMP", "text", "", "BUILD_TMP",
     "临时目录(可选)",
     "Temp dir (optional)",
     "留空=自动选最佳盘（优先 D:）；也可手动指到更大的盘，例如 D:\\droidvm_tmp",
     "Empty=auto-pick best drive (prefer D:); or set e.g. D:\\droidvm_tmp"),

    # GUI 以 GB 显示，但存到该字段时仍转回 MB 注入 build.ps1
    ("output", "DISK_SIZE_MB", "int", "40960", "DISK_SIZE_MB",
     "磁盘大小",
     "Disk size",
     "默认 40 GB",
     "Default 40 GB"),

    ("output", "COMPRESS", "bool", False, "COMPRESS",
     "压缩",
     "Compress",
     "qemu-img 加 -c 压缩（更小但更慢）",
     "Use qemu-img -c (smaller but slower)"),

    ("output", "DEBLOAT", "bool", False, "DEBLOAT",
     "精简系统",
     "Debloat",
     "移除预装应用、关休眠、关保留存储",
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

# 用于按 section 分组（旧版兼容）
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
    for _sec, key, typ, default, env_var, _lzh, _len, _hzh, _hen in FIELDS:
        if not env_var:
            continue
        # internal 字段：固定为默认值，绝不被用户/保存配置覆盖（如 DRIVER_DIR=ZIP/drivers）
        if typ == "internal":
            env[env_var] = str(default)
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


if __name__ == "__main__":
    import json
    print(json.dumps(default_values(), indent=2, ensure_ascii=False))

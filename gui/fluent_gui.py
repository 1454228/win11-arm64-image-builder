# -*- coding: utf-8 -*-
"""
fluent_gui.py — PyQt-Fluent-Widgets 版 DroidVM Builder GUI（WinUI 3 / Fluent 风格）

这是方案 B：用 qfluentwidgets 实现接近真实 WinUI 3 观感（FluentWindow 无边框导航窗、
Acrylic 亚克力材质、SwitchButton 药丸开关、卡片式分区、InfoBar 提示、丝滑主题切换）。

逻辑层（字段契约 / UAC 构建调度 / 实时日志）与 ctk_gui.py 完全等价，只换 UI 外壳。
两版并存：本文件为方案 B 入口，运行 `python3.11 gui/fluent_gui.py` 即可。

依赖：
    pip install "PySide6-Fluent-Widgets[full]"
"""

import json
import locale
import os
import re
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QApplication, QFileDialog, QFrame, QGridLayout, QHBoxLayout, QLabel,
    QPlainTextEdit, QSpinBox, QVBoxLayout, QWidget,
)
from qfluentwidgets import (
    BodyLabel, CaptionLabel, CardWidget, ComboBox, EditableComboBox, FlowLayout,
    FluentIcon, FluentWindow, InfoBar, InfoBarPosition, LineEdit, NavigationItemPosition,
    PrimaryPushButton, ProgressBar, PushButton, RadioButton, ScrollArea, setTheme,
    setThemeColor, SubtitleLabel, SwitchButton, TextEdit, Theme,
)

# ---- 共享上下文（与 ctk_gui.py 同源，保证契约单一事实来源）----
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from i18n import get_lang, set_lang, T  # noqa: E402
from schema import (  # noqa: E402
    FIELDS, default_values, KNOWN_DRIVERS, build_env, label, help_text,
)

DEFAULTS_FILE = HERE / "defaults.json"
PID_FILE = HERE / "build.pid"
DEFAULT_OUTPUT = Path.home() / "DroidVM" / "Windows.qcow2"

# 这些字段的值是本地文件/目录路径：GUI 层统一归一化成 Windows 反斜杠，
# 让输入框显示、defaults.json、build_config.json 与引擎看到的一致（引擎层另有兜底）。
PATH_KEYS = {"SRC_ISO", "DRIVERS_DIR", "GPU_DRIVERS_DIR", "DRIVER_CERT",
             "OPENSSH_SRC", "OUT_QCOW", "BUILD_TMP"}

ACCENT = "#0a64a5"

# Windows 浅色/深色主题
THEME_MAP = {"Light": Theme.LIGHT, "Dark": Theme.DARK}

# vmpkg 默认 VM 配置模板（镜像上游 DroidVM vms.json；GUI 仅覆盖用户可调项）。
# 字段含义见 https://github.com/Droid-VM/win11-arm64-image-builder 的 vms.json 说明。
DEFAULT_VMS = {
    "name": "Windows 11 ARM64 (DroidVM)",
    "backend": "crosvm",
    "hypervisor": "auto",
    "protected_vm": "protected_without_firmware",
    "memory_mb": 4096,
    "swiotlb_mb": 256,
    "cpu_count": 4,
    "cpu_topology_auto": True,
    "cpu_affinity": "",
    "cpu_capacity": "",
    "cpu_clusters": "",
    "gpu_cgroup_enabled": False,
    "gpu_cgroup_path": "/dev/cpuset/gpuworker",
    "gpu_cgroup_cpus": "",
    "smt": True,
    "pmu": True,
    "hugepages": True,
    "prepare_lend_mthp": "chunked",
    "gunyah_dynamic_share": False,
    "sandbox": False,
    "strace": False,
    "gpu_vram_folio_threshold_kb": 1024,
    "vpu_enabled": False,
    "vpu_host_pool_mb": 256,
    "vpu_guest_pool_mb": 128,
    "usb": True,
    "balloon": False,
    "rng": True,
    "screens": {
        "simplefb": {"enabled": True, "exporter": "native", "input_enabled": True,
                     "width": 1280, "height": 720, "poll_hz": 60,
                     "vnc": {"host": "127.0.0.1", "port": 5909}},
        "gpu-0": {"enabled": False, "exporter": "native", "input_enabled": True,
                  "width": 1280, "height": 720, "refresh_rate": 60, "dpi_h": 160, "dpi_v": 160,
                  "vnc": {"host": "127.0.0.1", "port": 5900}},
    },
    "display_blit_provider": "TURNIP",
    "display_enabled": True,
    "native_display_enabled": True,
    "display_backend": "simplefb",
    "gpu_enabled": False,
    "vnc_enabled": False,
    "auto_up": False,
    "extra_options": [],
    "environment_variables": [],
    "shared_dirs": [],
    # 外设：xHCI 控制器（usb 迁移目标）+ virtio-snd 声卡（新 VM 默认）。枚举按 name() 大写存储
    # （DataItem.set(Enum) -> name()；读取 Enums.optEnum 大小写不敏感），与 App 写出的形状一致。
    "peripherals": [
        {"type": "XHCI_USB", "id": "xhci-0", "usb2_ports": 8, "usb3_ports": 8},
        {"type": "VIRTIO_SOUND", "endpoints": [
            {"mode": "SPEAKER", "host_device": "DEFAULT|system default", "host_label": ""},
            {"mode": "MICROPHONE", "host_device": "DEFAULT|system default", "host_label": ""},
        ]},
    ],
    "xhci_next": 1,
    "serial_ports": [
        {"hardware": "serial", "num": 1, "backend": "app_console", "console": False, "fixed": True},
        {"hardware": "serial", "num": 2, "backend": "sink", "console": False, "fixed": True},
        {"hardware": "serial", "num": 3, "backend": "sink", "console": False, "fixed": True},
        {"hardware": "serial", "num": 4, "backend": "sink", "console": False, "fixed": True},
        {"hardware": "sbsa", "num": 1, "backend": "app_console", "console": True, "usb_slot": 0},
    ],
    "networks": [
        {"pkg_network_ref": "net0",
         "pkg_network": {"schema": 2, "name": "br-wifi", "bridge_name": "br-wifi",
                         "bridge_type": "linux", "uplink_mode": "l2", "stp": False,
                         "auto_up": True, "l2": {"uplink": "wlan0", "pseudo_bridge": True},
                         "l3": {"vlans": []}}},
    ],
    "boot": {"protocol": "uefi", "uefi": {"vars_enabled": True, "vars": "", "firmware": ""}},
}


def detect_system_theme():
    """探测 Windows 当前主题与强调色，用于让 GUI 跟随系统切换。

    返回 (theme, accent)：
      - theme: "Light" | "Dark"（取 HKCU\\...\\Personalize\\AppsUseLightTheme，1=浅色）
      - accent: "#RRGGBB" 或 None（取 HKCU\\...\\Explorer\\Accents\\AccentColorMenu，
                注册表存为 0x00BBGGRR）
    非 Windows 平台或读取失败时回退 (Light, None)。
    """
    theme, accent = "Light", None
    if sys.platform == "win32":
        try:
            import winreg
            try:
                key = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize")
                try:
                    theme = "Light" if winreg.QueryValueEx(key, "AppsUseLightTheme")[0] else "Dark"
                except OSError:
                    pass
                key.Close()
            except OSError:
                pass
            try:
                akey = winreg.OpenKey(
                    winreg.HKEY_CURRENT_USER,
                    r"Software\Microsoft\Windows\CurrentVersion\Explorer\Accents")
                try:
                    cv = winreg.QueryValueEx(akey, "AccentColorMenu")[0]
                    r = cv & 0xFF
                    g = (cv >> 8) & 0xFF
                    b = (cv >> 16) & 0xFF
                    accent = "#%02X%02X%02X" % (r, g, b)
                except OSError:
                    pass
                akey.Close()
            except OSError:
                pass
        except Exception:
            pass
    return theme, accent


def _quote_ps(s):
    return '"' + s.replace('"', '`"') + '"'


class MainWindow(FluentWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(T("APP_TITLE"))
        self.resize(1040, 760)
        self.platform = sys.platform

        self.fields = {}          # key -> LineEdit / TextEdit
        self.switches = {}        # key -> SwitchButton (bool)
        self.driver_vars = {}     # name -> SwitchButton (gunyah 驱动)
        self.partitions = []       # 磁盘分区布局（GUI 磁盘页维护，收集时序列化为 JSON）
        self.status_labels = []   # 所有底部状态标签（双页面同步）
        self.build_buttons = []
        self.stop_buttons = []
        self.log_box = None
        self.progress_bar = None        # 构建进度条（构建页）
        self.progress_label = None      # 进度百分比文字
        self._progress = 0              # 当前进度（0-100，单调推进）

        self.building = False
        self.log_path = None
        self._last_log_pos = 0
        self._saved_values = {}
        self._populating = False
        self.part_count = 2      # NTFS 分区数目（ESP/MSR 之外），分区 1 为 Windows
        self.part_sizes = []     # 各分区大小（GB），长度 = part_count，末位自动占剩余
        self.part_names = []     # 各 NTFS 分区名（卷标），长度 = part_count，可自定义
        self._sizes_customized = False   # 用户手动改过大小后冻结，否则随磁盘大小均分
        self._accent = ACCENT    # 当前强调色（跟随 Windows 时会被覆盖）

        # 主题完全跟随 Windows 深浅色与强调色，不提供手动切换
        self._apply_system_theme()

        self._build_interfaces()
        self._init_navigation()

        self._load_defaults()
        self._populate_fields()

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._poll_log)
        self._timer.start(250)

        # 跟随系统主题：周期性探测 Windows 设置，变化时实时重应用（2 秒一次）
        self._theme_timer = QTimer(self)
        self._theme_timer.timeout.connect(self._check_system_theme)
        self._theme_timer.start(2000)

    # -------------------------- 界面骨架 --------------------------
    def _build_interfaces(self):
        self.windows_if = self._build_windows()
        self.windows_if.setObjectName("windows")
        self.disk_if = self._build_disk()
        self.disk_if.setObjectName("disk")
        self.drivers_if = self._build_drivers()
        self.drivers_if.setObjectName("drivers")
        self.vmpkg_if = self._build_vmpkg()
        self.vmpkg_if.setObjectName("vmpkg")
        self.build_if = self._build_build()
        self.build_if.setObjectName("build")

    def _init_navigation(self):
        self.addSubInterface(self.windows_if, FluentIcon.HOME, T("TAB_WINDOWS"))
        self.addSubInterface(self.disk_if, FluentIcon.FOLDER, T("TAB_DISK"))
        self.addSubInterface(self.drivers_if, FluentIcon.CLOUD, T("TAB_DRIVERS"))
        self.addSubInterface(self.vmpkg_if, FluentIcon.APPLICATION, T("TAB_VMPKG"))
        self.addSubInterface(self.build_if, FluentIcon.PLAY,
                             T("TAB_BUILD"), position=NavigationItemPosition.BOTTOM)

    # -------------------------- 通用组件 --------------------------
    def _card(self):
        card = CardWidget()
        lay = QVBoxLayout(card)
        lay.setContentsMargins(16, 14, 16, 14)
        lay.setSpacing(8)
        return card, lay

    def _card_title(self, text):
        lab = SubtitleLabel(text)
        lab.setStyleSheet(f"color: {ACCENT}; font-weight: bold;")
        return lab

    def _field_row(self, key, browse=None, multiline=False, minw=220):
        """把「标签 + 控件」包成紧凑卡片（标签在上、控件在下），返回卡片。

        与 vmpkg 页的 _vfield 视觉语言一致，可放进 FlowLayout 随窗口宽度自动换行。
        仍写入 self.fields[key]，保证 _collect_fields/_populate_fields 回填逻辑不变。
        minw 用于路径类长字段（如 ISO/输出目录）加宽，避免截断严重。
        """
        card = CardWidget()
        card.setMinimumWidth(minw)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 9, 12, 9)
        v.setSpacing(5)
        v.addWidget(BodyLabel(label(key)))

        if multiline:
            ctrl = TextEdit()
            ctrl.setMaximumHeight(72)
            ctrl.setPlainText(str(self._default(key)))
        else:
            ctrl = LineEdit()
            ctrl.setText(str(self._default(key)))
        self.fields[key] = ctrl

        h = QHBoxLayout()
        h.setSpacing(8)
        h.addWidget(ctrl, 1)
        if browse:
            btn = PushButton(T("BROWSE"))
            btn.clicked.connect(lambda _, k=key, b=browse: self._browse(k, b))
            h.addWidget(btn, 0)
        v.addLayout(h)

        if help_text(key) and not multiline:
            v.addWidget(CaptionLabel(help_text(key)))
        return card

    def _make_switch(self, checked=False):
        sw = SwitchButton()
        sw.setOnText("")          # 隐藏开关旁的 On/Off 文字
        sw.setOffText("")
        sw.setChecked(checked)
        return sw

    def _switch_row(self, key, show_label=True, minw=196):
        """把「开关」包成紧凑卡片，返回卡片；写入 self.switches[key] 保持回填逻辑不变。"""
        card = CardWidget()
        card.setMinimumWidth(minw)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 9, 12, 9)
        v.setSpacing(5)
        if show_label:
            v.addWidget(BodyLabel(label(key)))
        sw = self._make_switch(bool(self._default(key)))
        self.switches[key] = sw
        h = QHBoxLayout()
        h.setSpacing(8)
        h.addStretch(1)
        h.addWidget(sw, 0)
        v.addLayout(h)
        if help_text(key):
            v.addWidget(CaptionLabel(help_text(key)))
        return card

    def _image_index_row(self):
        """镜像版本行（组合框 + 检测按钮）包成紧凑卡片，返回卡片。"""
        card = CardWidget()
        card.setMinimumWidth(320)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 9, 12, 9)
        v.setSpacing(5)
        v.addWidget(BodyLabel(label("IMAGE_INDEX")))
        h = QHBoxLayout()
        h.setSpacing(8)
        self.image_index_combo = ComboBox()
        self.image_index_combo.addItems(["0 - " + T("AUTO")])
        btn = PushButton(T("DETECT"))
        btn.clicked.connect(self._detect_iso_async)
        h.addWidget(self.image_index_combo, 1)
        h.addWidget(btn, 0)
        v.addLayout(h)
        if help_text("IMAGE_INDEX"):
            v.addWidget(CaptionLabel(help_text("IMAGE_INDEX")))
        return card

    @staticmethod
    def _win_path(p):
        """Windows 上把本地路径里的 / 统一成 \\：文件对话框常返回 C:/... 形式的正斜杠，
        直接显示既不符合 Windows 习惯，也容易让人误以为斜杠没处理。
        URL（http/https）原样保留，避免破坏下载链接（与 schema/build.ps1 的保护一致）。"""
        if p and sys.platform == "win32" \
                and not p.lower().startswith(("http://", "https://")):
            return p.replace("/", "\\")
        return p

    def _browse(self, key, kind):
        ctrl = self.fields.get(key)
        if kind in ("iso", "file"):
            p, _ = QFileDialog.getOpenFileName(self, T("SELECT_FILE"))
        elif kind == "save":
            p, _ = QFileDialog.getSaveFileName(self, T("SAVE_AS"), "", "*.qcow2")
        elif kind in ("dir", "path_or_url"):
            p = QFileDialog.getExistingDirectory(self, T("SELECT_FOLDER"))
        else:
            p, _ = QFileDialog.getOpenFileName(self, T("SELECT_FILE"))
        if p and ctrl:
            ctrl.setText(self._win_path(p))

    def _default(self, key):
        return default_values().get(key, "")

    # -------------------------- Windows 选项卡 --------------------------
    def _build_windows(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)

        sa = ScrollArea()
        sa.setWidgetResizable(True)
        c = QWidget()
        cv = QVBoxLayout(c)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(14)

        # 来源（ISO 路径较长，卡片加宽）
        blk, fl = self._vsection(T("SECTION_SOURCE"))
        fl.addWidget(self._field_row("SRC_ISO", browse="iso", minw=360))
        fl.addWidget(self._image_index_row())
        cv.addWidget(blk)

        # 账户
        blk, fl = self._vsection(T("SECTION_ACCOUNT"))
        fl.addWidget(self._field_row("DVM_USERNAME"))
        fl.addWidget(self._field_row("DVM_PASSWORD"))
        fl.addWidget(self._field_row("DVM_COMPUTERNAME"))
        cv.addWidget(blk)

        # 输出（路径字段加宽）
        blk, fl = self._vsection(T("SECTION_OUTPUT"))
        fl.addWidget(self._field_row("OUT_QCOW", browse="save", minw=360))
        fl.addWidget(self._field_row("BUILD_TMP", browse="dir", minw=360))
        fl.addWidget(self._switch_row("COMPRESS"))
        fl.addWidget(self._switch_row("DEBLOAT"))
        cv.addWidget(blk)

        # 高级（SSH 公钥多行，卡片加宽）
        blk, fl = self._vsection(T("SECTION_ADVANCED"))
        fl.addWidget(self._field_row("OPENSSH_SRC", browse="file", minw=360))
        fl.addWidget(self._field_row("TARGET_TIMEZONE"))
        fl.addWidget(self._field_row("SSH_PUBKEY", multiline=True, minw=360))
        cv.addWidget(blk)

        # 自动配置说明（纵向占满宽度）
        blk, vb = self._vsection_v(T("AUTO_CONFIG_TITLE"))
        vb.addWidget(BodyLabel(T("AUTO_CONFIG_TEXT")))
        cv.addWidget(blk)

        sa.setWidget(c)
        v.addWidget(sa, 1)
        v.addWidget(self._bottom_bar(with_build=False), 0)
        return w

    def _driver_card(self, name):
        """单个驱动开关的紧凑卡片（标签在上、开关右对齐），写入 self.driver_vars[name]。"""
        card = CardWidget()
        card.setMinimumWidth(180)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 9, 12, 9)
        v.setSpacing(5)
        v.addWidget(BodyLabel(name))
        sw = self._make_switch(True)
        self.driver_vars[name] = sw
        h = QHBoxLayout()
        h.setSpacing(8)
        h.addStretch(1)
        h.addWidget(sw, 0)
        v.addLayout(h)
        return card

    # -------------------------- 驱动 选项卡 --------------------------
    def _build_drivers(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)

        sa = ScrollArea()
        sa.setWidgetResizable(True)
        c = QWidget()
        cv = QVBoxLayout(c)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(14)

        # 驱动来源（含导入 ZIP 按钮，纵向卡片）
        blk, vb = self._vsection_v(T("SECTION_DRIVERS"))
        vb.addWidget(self._field_row("DRIVERS_DIR", browse="path_or_url", minw=380))
        hz = QHBoxLayout()
        hz.addStretch(1)
        zbtn = PushButton(T("IMPORT_ZIP"))
        zbtn.clicked.connect(lambda: self._browse_zip("DRIVERS_DIR"))
        hz.addWidget(zbtn)
        vb.addLayout(hz)
        cv.addWidget(blk)

        # 要安装的驱动（Gunyah/VirtIO）：响应式卡片网格
        blk, fl = self._vsection(T("DRIVERS_TO_INSTALL"))
        self.driver_vars.clear()
        for name in KNOWN_DRIVERS:
            fl.addWidget(self._driver_card(name))
        cv.addWidget(blk)

        # GPU 驱动（独立开关，与 Gunyah 解耦）：纵向卡片
        blk, vb = self._vsection_v(label("GPU_DRIVERS"))
        vb.addWidget(self._switch_row("GPU_DRIVERS", show_label=False))
        vb.addWidget(self._field_row("GPU_DRIVERS_DIR", browse="path_or_url", minw=380))
        hz = QHBoxLayout()
        hz.addStretch(1)
        zbtn = PushButton(T("IMPORT_ZIP"))
        zbtn.clicked.connect(lambda: self._browse_zip("GPU_DRIVERS_DIR"))
        hz.addWidget(zbtn)
        vb.addLayout(hz)
        cv.addWidget(blk)

        # 驱动签名证书
        blk, fl = self._vsection(label("DRIVER_CERT"))
        fl.addWidget(self._field_row("DRIVER_CERT", browse="file", minw=360))
        cv.addWidget(blk)

        sa.setWidget(c)
        v.addWidget(sa, 1)
        v.addWidget(self._bottom_bar(with_build=False), 0)
        return w

    # -------------------------- vmpkg 选项卡 --------------------------
    # -------------------------- vmpkg 选项卡（响应式布局）--------------------------
    def _vfield(self, key, control):
        """把「标签 + 控件」包成可自动换行的紧凑卡片，返回卡片；控件本身另存到 self.vmpkg_*。"""
        card = CardWidget()
        card.setMinimumWidth(196)
        v = QVBoxLayout(card)
        v.setContentsMargins(12, 9, 12, 9)
        v.setSpacing(5)
        v.addWidget(BodyLabel(T(key)))
        v.addWidget(control)
        return card

    def _vsection(self, title):
        """带标题的区块：内部用 FlowLayout，控件随窗口宽度自动换行排布（高效利用横向空间）。"""
        block = QWidget()
        vb = QVBoxLayout(block)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(8)
        vb.addWidget(self._card_title(title))
        fl = FlowLayout()
        fl.setSpacing(10)
        vb.addLayout(fl)
        return block, fl

    def _vsection_v(self, title):
        """纵向区块：用于备注等需要占满宽度的字段。"""
        block = QWidget()
        vb = QVBoxLayout(block)
        vb.setContentsMargins(0, 0, 0, 0)
        vb.setSpacing(8)
        vb.addWidget(self._card_title(title))
        inner = QVBoxLayout()
        inner.setSpacing(8)
        vb.addLayout(inner)
        return block, inner

    def _spin_ctrl(self, key, lo, hi, default):
        spin = QSpinBox()
        spin.setRange(lo, hi)
        spin.setValue(default)
        return spin

    def _toggle_ctrl(self, key, default=True):
        sw = SwitchButton()
        sw.setChecked(default)
        sw.setOnText("")
        sw.setOffText("")
        return sw

    def _combo_ctrl(self, key, items, default="", editable=False):
        combo = EditableComboBox() if editable else ComboBox()
        combo.addItems(list(items))
        if default:
            idx = combo.findText(default)
            if idx >= 0:
                combo.setCurrentIndex(idx)
            elif editable:
                combo.setText(default)
        return combo

    def _text_ctrl(self, key, default=""):
        edit = LineEdit()
        edit.setText(default)
        return edit

    def _edit_ctrl(self, key, default=""):
        edit = TextEdit()
        edit.setPlainText(default)
        edit.setMaximumHeight(120)
        return edit

    def _build_vmpkg(self):
        self.out_format = "qcow2"
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(16, 16, 16, 16)
        v.setSpacing(12)
        sa = ScrollArea()
        sa.setWidgetResizable(True)
        sa.setFrameShape(QFrame.NoFrame)
        c = QWidget()
        cv = QVBoxLayout(c)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(14)

        # 输出格式：qcow2 仅镜像 / vmpkg（含 VM 配置）
        cv.addWidget(self._card_title(T("VMPKG_FORMAT")))
        card, lay = self._card()
        fmt_row = QHBoxLayout()
        fmt_row.setSpacing(10)
        self.fmt_qcow2 = RadioButton(T("VMPKG_QCOW2"))
        self.fmt_vmpkg = RadioButton(T("VMPKG_BUNDLE"))
        self.fmt_qcow2.setChecked(True)
        self.fmt_qcow2.toggled.connect(lambda on: self._on_fmt_changed("qcow2", on))
        self.fmt_vmpkg.toggled.connect(lambda on: self._on_fmt_changed("vmpkg", on))
        fmt_row.addWidget(self.fmt_qcow2)
        fmt_row.addWidget(self.fmt_vmpkg)
        fmt_row.addStretch(1)
        lay.addLayout(fmt_row)
        lay.addWidget(CaptionLabel(T("VMPKG_HINT")))
        cv.addWidget(card)

        # ---- 基础（仅 vmpkg 模式使用；qcow2 模式忽略）----
        blk, fl = self._vsection(T("VMPKG_SEC_BASE"))
        self.vmpkg_name = self._text_ctrl("VMPKG_NAME", DEFAULT_VMS["name"])
        fl.addWidget(self._vfield("VMPKG_NAME", self.vmpkg_name))
        self.vmpkg_backend = self._combo_ctrl("VMPKG_BACKEND", ["crosvm", "qemu"], DEFAULT_VMS["backend"])
        fl.addWidget(self._vfield("VMPKG_BACKEND", self.vmpkg_backend))
        self.vmpkg_hyp = self._combo_ctrl(
            "VMPKG_HYPERVISOR", ["auto", "soft", "kvm", "gunyah", "geniezone"],
            DEFAULT_VMS["hypervisor"])
        fl.addWidget(self._vfield("VMPKG_HYPERVISOR", self.vmpkg_hyp))
        self.vmpkg_prot = self._combo_ctrl(
            "VMPKG_PROTECTED",
            ["protected_normal", "protected_protected", "protected_without_firmware",
             "pseudo_unprotected"],
            DEFAULT_VMS["protected_vm"])
        fl.addWidget(self._vfield("VMPKG_PROTECTED", self.vmpkg_prot))
        self.vmpkg_mthp = self._combo_ctrl(
            "VMPKG_MTHP", ["chunked", "single", "disabled"], DEFAULT_VMS["prepare_lend_mthp"])
        fl.addWidget(self._vfield("VMPKG_MTHP", self.vmpkg_mthp))
        self.vmpkg_comp = self._combo_ctrl("VMPKG_COMPRESSION", ["auto", "zstd", "gzip", "none"], "auto")
        fl.addWidget(self._vfield("VMPKG_COMPRESSION", self.vmpkg_comp))
        self.vmpkg_autoup = self._toggle_ctrl("VMPKG_AUTO_UP", False)
        fl.addWidget(self._vfield("VMPKG_AUTO_UP", self.vmpkg_autoup))
        self.vmpkg_sandbox = self._toggle_ctrl("VMPKG_SANDBOX", DEFAULT_VMS["sandbox"])
        fl.addWidget(self._vfield("VMPKG_SANDBOX", self.vmpkg_sandbox))
        self.vmpkg_strace = self._toggle_ctrl("VMPKG_STRACE", DEFAULT_VMS["strace"])
        fl.addWidget(self._vfield("VMPKG_STRACE", self.vmpkg_strace))
        cv.addWidget(blk)

        # ---- 备注与包信息（包版本显示名 / 构建类型 / VM 备注，纵向占满宽度）----
        blk, vb = self._vsection_v(T("VMPKG_SEC_PKG"))
        self.vmpkg_appver = self._text_ctrl("VMPKG_APP_VERSION", "W11A64DVMB")
        vb.addWidget(self._vfield("VMPKG_APP_VERSION", self.vmpkg_appver))
        self.vmpkg_buildtype = self._combo_ctrl("VMPKG_BUILDTYPE", ["release", "debug"], "release", editable=True)
        vb.addWidget(self._vfield("VMPKG_BUILDTYPE", self.vmpkg_buildtype))
        self.vmpkg_notes = self._edit_ctrl("VMPKG_NOTES", "")
        vb.addWidget(self._vfield("VMPKG_NOTES", self.vmpkg_notes))
        cv.addWidget(blk)

        # ---- CPU 与内存 ----
        blk, fl = self._vsection(T("VMPKG_SEC_CPU"))
        self.vmpkg_mem = self._spin_ctrl("VMPKG_MEM", 256, 65536, 4096)
        fl.addWidget(self._vfield("VMPKG_MEM", self.vmpkg_mem))
        self.vmpkg_cpu = self._spin_ctrl("VMPKG_CPU", 1, 32, 4)
        fl.addWidget(self._vfield("VMPKG_CPU", self.vmpkg_cpu))
        self.vmpkg_swiotlb = self._spin_ctrl("VMPKG_SWIOTLB", 0, 4096, 256)
        fl.addWidget(self._vfield("VMPKG_SWIOTLB", self.vmpkg_swiotlb))
        self.vmpkg_smt = self._toggle_ctrl("VMPKG_SMT", DEFAULT_VMS["smt"])
        fl.addWidget(self._vfield("VMPKG_SMT", self.vmpkg_smt))
        self.vmpkg_pmu = self._toggle_ctrl("VMPKG_PMU", DEFAULT_VMS["pmu"])
        fl.addWidget(self._vfield("VMPKG_PMU", self.vmpkg_pmu))
        self.vmpkg_topo = self._toggle_ctrl("VMPKG_TOPO_AUTO", DEFAULT_VMS["cpu_topology_auto"])
        fl.addWidget(self._vfield("VMPKG_TOPO_AUTO", self.vmpkg_topo))
        self.vmpkg_huge = self._toggle_ctrl("VMPKG_HUGEPAGES", DEFAULT_VMS["hugepages"])
        fl.addWidget(self._vfield("VMPKG_HUGEPAGES", self.vmpkg_huge))
        self.vmpkg_balloon = self._toggle_ctrl("VMPKG_BALLOON", DEFAULT_VMS["balloon"])
        fl.addWidget(self._vfield("VMPKG_BALLOON", self.vmpkg_balloon))
        self.vmpkg_rng = self._toggle_ctrl("VMPKG_RNG", DEFAULT_VMS["rng"])
        fl.addWidget(self._vfield("VMPKG_RNG", self.vmpkg_rng))
        self.vmpkg_dynshare = self._toggle_ctrl("VMPKG_DYN_SHARE", DEFAULT_VMS["gunyah_dynamic_share"])
        fl.addWidget(self._vfield("VMPKG_DYN_SHARE", self.vmpkg_dynshare))
        # CPU placement（留空 = 跟随自动拓扑；格式见 App 的 CpuPlacementPlan：vcpu=host 集）
        self.vmpkg_affinity = self._text_ctrl("VMPKG_AFFINITY", "")
        fl.addWidget(self._vfield("VMPKG_AFFINITY", self.vmpkg_affinity))
        self.vmpkg_capacity = self._text_ctrl("VMPKG_CAPACITY", "")
        fl.addWidget(self._vfield("VMPKG_CAPACITY", self.vmpkg_capacity))
        self.vmpkg_clusters = self._text_ctrl("VMPKG_CLUSTERS", "")
        fl.addWidget(self._vfield("VMPKG_CLUSTERS", self.vmpkg_clusters))
        self.vmpkg_folio = self._spin_ctrl("VMPKG_FOLIO", 0, 1048576,
                                           DEFAULT_VMS["gpu_vram_folio_threshold_kb"])
        fl.addWidget(self._vfield("VMPKG_FOLIO", self.vmpkg_folio))
        # GPU cgroup 隔离
        self.vmpkg_gpucgroup = self._toggle_ctrl("VMPKG_GPU_CGROUP", False)
        fl.addWidget(self._vfield("VMPKG_GPU_CGROUP", self.vmpkg_gpucgroup))
        self.vmpkg_gpucgpath = self._text_ctrl("VMPKG_GPU_CGROUP_PATH",
                                               DEFAULT_VMS["gpu_cgroup_path"])
        fl.addWidget(self._vfield("VMPKG_GPU_CGROUP_PATH", self.vmpkg_gpucgpath))
        self.vmpkg_gpucgcpus = self._text_ctrl("VMPKG_GPU_CGROUP_CPUS", "")
        fl.addWidget(self._vfield("VMPKG_GPU_CGROUP_CPUS", self.vmpkg_gpucgcpus))
        cv.addWidget(blk)

        # ---- 设备 ----
        blk, fl = self._vsection(T("VMPKG_SEC_DEV"))
        self.vmpkg_usb = self._toggle_ctrl("VMPKG_USB", DEFAULT_VMS["usb"])
        fl.addWidget(self._vfield("VMPKG_USB", self.vmpkg_usb))
        # xHCI 控制器端口数（App 默认 8/8，范围 0-15）
        self.vmpkg_usb2 = self._spin_ctrl("VMPKG_USB2", 0, 15, 8)
        fl.addWidget(self._vfield("VMPKG_USB2", self.vmpkg_usb2))
        self.vmpkg_usb3 = self._spin_ctrl("VMPKG_USB3", 0, 15, 8)
        fl.addWidget(self._vfield("VMPKG_USB3", self.vmpkg_usb3))
        self.vmpkg_sound = self._toggle_ctrl("VMPKG_SOUND", True)
        fl.addWidget(self._vfield("VMPKG_SOUND", self.vmpkg_sound))
        self.vmpkg_vpu = self._toggle_ctrl("VMPKG_VPU", DEFAULT_VMS["vpu_enabled"])
        fl.addWidget(self._vfield("VMPKG_VPU", self.vmpkg_vpu))
        self.vmpkg_vpuhost = self._spin_ctrl("VMPKG_VPU_HOST", 0, 65536,
                                             DEFAULT_VMS["vpu_host_pool_mb"])
        fl.addWidget(self._vfield("VMPKG_VPU_HOST", self.vmpkg_vpuhost))
        self.vmpkg_vpuguest = self._spin_ctrl("VMPKG_VPU_GUEST", 0, 65536,
                                              DEFAULT_VMS["vpu_guest_pool_mb"])
        fl.addWidget(self._vfield("VMPKG_VPU_GUEST", self.vmpkg_vpuguest))
        self.vmpkg_com1 = self._toggle_ctrl("VMPKG_COM1_CONSOLE", False)
        fl.addWidget(self._vfield("VMPKG_COM1_CONSOLE", self.vmpkg_com1))
        self.vmpkg_sbsa = self._toggle_ctrl("VMPKG_SBSA", True)
        fl.addWidget(self._vfield("VMPKG_SBSA", self.vmpkg_sbsa))
        cv.addWidget(blk)

        # ---- 显示与 GPU ----
        blk, fl = self._vsection(T("VMPKG_SEC_GPU"))
        self.vmpkg_simplefb = self._toggle_ctrl("VMPKG_SIMPLEFB", DEFAULT_VMS["screens"]["simplefb"]["enabled"])
        fl.addWidget(self._vfield("VMPKG_SIMPLEFB", self.vmpkg_simplefb))
        self.vmpkg_sfexp = self._combo_ctrl("VMPKG_SF_EXPORTER", ["native", "vnc", "none"],
                                            DEFAULT_VMS["screens"]["simplefb"]["exporter"])
        fl.addWidget(self._vfield("VMPKG_SF_EXPORTER", self.vmpkg_sfexp))
        self.vmpkg_sfinput = self._toggle_ctrl("VMPKG_SF_INPUT",
                                               DEFAULT_VMS["screens"]["simplefb"]["input_enabled"])
        fl.addWidget(self._vfield("VMPKG_SF_INPUT", self.vmpkg_sfinput))
        self.vmpkg_sfpoll = self._spin_ctrl("VMPKG_SF_POLL", 1, 120,
                                            DEFAULT_VMS["screens"]["simplefb"]["poll_hz"])
        fl.addWidget(self._vfield("VMPKG_SF_POLL", self.vmpkg_sfpoll))
        self.vmpkg_sfvncport = self._spin_ctrl("VMPKG_SF_VNC_PORT", 1024, 65535,
                                               DEFAULT_VMS["screens"]["simplefb"]["vnc"]["port"])
        fl.addWidget(self._vfield("VMPKG_SF_VNC_PORT", self.vmpkg_sfvncport))
        self.vmpkg_gpu0 = self._toggle_ctrl("VMPKG_GPU_SCREEN", DEFAULT_VMS["screens"]["gpu-0"]["enabled"])
        fl.addWidget(self._vfield("VMPKG_GPU_SCREEN", self.vmpkg_gpu0))
        self.vmpkg_gpuexp = self._combo_ctrl("VMPKG_GPU_EXPORTER", ["native", "vnc", "none"],
                                             DEFAULT_VMS["screens"]["gpu-0"]["exporter"])
        fl.addWidget(self._vfield("VMPKG_GPU_EXPORTER", self.vmpkg_gpuexp))
        self.vmpkg_gpuinput = self._toggle_ctrl("VMPKG_GPU_INPUT",
                                                DEFAULT_VMS["screens"]["gpu-0"]["input_enabled"])
        fl.addWidget(self._vfield("VMPKG_GPU_INPUT", self.vmpkg_gpuinput))
        self.vmpkg_gpudpih = self._spin_ctrl("VMPKG_GPU_DPI_H", 96, 480,
                                             DEFAULT_VMS["screens"]["gpu-0"]["dpi_h"])
        fl.addWidget(self._vfield("VMPKG_GPU_DPI_H", self.vmpkg_gpudpih))
        self.vmpkg_gpudpiv = self._spin_ctrl("VMPKG_GPU_DPI_V", 96, 480,
                                             DEFAULT_VMS["screens"]["gpu-0"]["dpi_v"])
        fl.addWidget(self._vfield("VMPKG_GPU_DPI_V", self.vmpkg_gpudpiv))
        self.vmpkg_gpu = self._toggle_ctrl("VMPKG_GPU", DEFAULT_VMS["gpu_enabled"])
        fl.addWidget(self._vfield("VMPKG_GPU", self.vmpkg_gpu))
        self.vmpkg_blit = self._combo_ctrl(
            "VMPKG_BLIT", ["TURNIP", "PANVK", "SYSTEM", "OFF"],
            DEFAULT_VMS["display_blit_provider"], editable=True)
        fl.addWidget(self._vfield("VMPKG_BLIT", self.vmpkg_blit))
        self.vmpkg_natdisp = self._toggle_ctrl("VMPKG_NATIVE_DISPLAY", DEFAULT_VMS["native_display_enabled"])
        fl.addWidget(self._vfield("VMPKG_NATIVE_DISPLAY", self.vmpkg_natdisp))
        self.vmpkg_vnc = self._toggle_ctrl("VMPKG_VNC", DEFAULT_VMS["vnc_enabled"])
        fl.addWidget(self._vfield("VMPKG_VNC", self.vmpkg_vnc))
        self.vmpkg_vncport = self._spin_ctrl("VMPKG_VNC_PORT", 1024, 65535, 5900)
        fl.addWidget(self._vfield("VMPKG_VNC_PORT", self.vmpkg_vncport))
        self.vmpkg_res = self._combo_ctrl("VMPKG_SCREEN", ["1280x720", "1920x1080", "2560x1440"], "1280x720")
        fl.addWidget(self._vfield("VMPKG_SCREEN", self.vmpkg_res))
        self.vmpkg_refresh = self._spin_ctrl("VMPKG_REFRESH", 30, 120, 60)
        fl.addWidget(self._vfield("VMPKG_REFRESH", self.vmpkg_refresh))
        cv.addWidget(blk)

        # ---- 网络 ----
        blk, fl = self._vsection(T("VMPKG_SEC_NET"))
        self.vmpkg_net = self._text_ctrl("VMPKG_NET", "br-wifi")
        fl.addWidget(self._vfield("VMPKG_NET", self.vmpkg_net))
        self.vmpkg_uplink = self._text_ctrl("VMPKG_UPLINK", "wlan0")
        fl.addWidget(self._vfield("VMPKG_UPLINK", self.vmpkg_uplink))
        self.vmpkg_netautoup = self._toggle_ctrl("VMPKG_NET_AUTOUP", DEFAULT_VMS["networks"][0]["pkg_network"]["auto_up"])
        fl.addWidget(self._vfield("VMPKG_NET_AUTOUP", self.vmpkg_netautoup))
        self.vmpkg_stp = self._toggle_ctrl("VMPKG_STP", DEFAULT_VMS["networks"][0]["pkg_network"]["stp"])
        fl.addWidget(self._vfield("VMPKG_STP", self.vmpkg_stp))
        self.vmpkg_pbridge = self._toggle_ctrl("VMPKG_PBRIDGE",
                                               DEFAULT_VMS["networks"][0]["pkg_network"]["l2"]["pseudo_bridge"])
        fl.addWidget(self._vfield("VMPKG_PBRIDGE", self.vmpkg_pbridge))
        self.vmpkg_mac = self._text_ctrl("VMPKG_MAC", "")
        fl.addWidget(self._vfield("VMPKG_MAC", self.vmpkg_mac))
        cv.addWidget(blk)

        # ---- 启动 ----
        blk, fl = self._vsection(T("VMPKG_SEC_BOOT"))
        self.vmpkg_uefivars = self._toggle_ctrl("VMPKG_UEFI_VARS", DEFAULT_VMS["boot"]["uefi"]["vars_enabled"])
        fl.addWidget(self._vfield("VMPKG_UEFI_VARS", self.vmpkg_uefivars))
        self.vmpkg_uefifw = self._text_ctrl("VMPKG_UEFI_FW", "")
        fl.addWidget(self._vfield("VMPKG_UEFI_FW", self.vmpkg_uefifw))
        self.vmpkg_uefivarspath = self._text_ctrl("VMPKG_UEFI_VARS_PATH", "")
        fl.addWidget(self._vfield("VMPKG_UEFI_VARS_PATH", self.vmpkg_uefivarspath))
        cv.addWidget(blk)

        # ---- 高级（crosvm 额外参数 / 环境变量 / 共享目录，纵向占满宽度）----
        blk, vb = self._vsection_v(T("VMPKG_SEC_ADV"))
        self.vmpkg_extra = self._edit_ctrl("VMPKG_EXTRA", "")
        vb.addWidget(self._vfield("VMPKG_EXTRA", self.vmpkg_extra))
        self.vmpkg_envv = self._edit_ctrl("VMPKG_ENVVARS", "")
        vb.addWidget(self._vfield("VMPKG_ENVVARS", self.vmpkg_envv))
        self.vmpkg_shared = self._edit_ctrl("VMPKG_SHARED", "")
        vb.addWidget(self._vfield("VMPKG_SHARED", self.vmpkg_shared))
        cv.addWidget(blk)

        sa.setWidget(c)
        v.addWidget(sa, 1)
        v.addWidget(self._bottom_bar(with_build=False), 0)
        return w
    def _on_fmt_changed(self, fmt, on):
        if on:
            self.out_format = fmt

    def _build_vms_dict(self):
        """根据 vmpkg 页的可调项生成 vms.json 配置（其余字段沿用 DEFAULT_VMS 模板）。"""
        import copy
        vm = copy.deepcopy(DEFAULT_VMS)
        # 基础
        vm["name"] = (self.vmpkg_name.text() or "").strip() or DEFAULT_VMS["name"]
        # 备注：空则不放进 manifest（与 App 的 VMConfig.setNotes 行为一致——空串会 remove("notes")）
        notes = (self.vmpkg_notes.toPlainText() or "").strip()
        if notes:
            vm["notes"] = notes
        else:
            vm.pop("notes", None)
        vm["hypervisor"] = self.vmpkg_hyp.currentText() or "auto"
        vm["protected_vm"] = self.vmpkg_prot.currentText() or DEFAULT_VMS["protected_vm"]
        vm["auto_up"] = bool(self.vmpkg_autoup.isChecked())
        # CPU 与内存
        vm["memory_mb"] = int(self.vmpkg_mem.value())
        vm["cpu_count"] = int(self.vmpkg_cpu.value())
        vm["swiotlb_mb"] = int(self.vmpkg_swiotlb.value())
        vm["smt"] = bool(self.vmpkg_smt.isChecked())
        vm["pmu"] = bool(self.vmpkg_pmu.isChecked())
        vm["cpu_topology_auto"] = bool(self.vmpkg_topo.isChecked())
        vm["hugepages"] = bool(self.vmpkg_huge.isChecked())
        vm["balloon"] = bool(self.vmpkg_balloon.isChecked())
        vm["rng"] = bool(self.vmpkg_rng.isChecked())
        vm["gunyah_dynamic_share"] = bool(self.vmpkg_dynshare.isChecked())
        vm["backend"] = self.vmpkg_backend.currentText() or "crosvm"
        vm["prepare_lend_mthp"] = self.vmpkg_mthp.currentText() or "chunked"
        vm["sandbox"] = bool(self.vmpkg_sandbox.isChecked())
        vm["strace"] = bool(self.vmpkg_strace.isChecked())

        def _opt_text(ctrl):
            return (ctrl.text() or "").strip()

        # CPU placement：留空的键从 manifest 移除（App 端按缺省走自动拓扑）。
        # 格式同 App 的 CpuPlacementPlan：affinity/capacity 为 "vcpu=host 集"（; 分隔），
        # clusters 为 "成员集"（; 分隔，如 "0-1;2-3"）。
        for key, ctrl in (("cpu_affinity", self.vmpkg_affinity),
                          ("cpu_capacity", self.vmpkg_capacity),
                          ("cpu_clusters", self.vmpkg_clusters)):
            val = _opt_text(ctrl)
            if val:
                vm[key] = val
            else:
                vm.pop(key, None)
        vm["gpu_vram_folio_threshold_kb"] = int(self.vmpkg_folio.value())
        vm["gpu_cgroup_enabled"] = bool(self.vmpkg_gpucgroup.isChecked())
        cgpath = _opt_text(self.vmpkg_gpucgpath)
        if cgpath:
            vm["gpu_cgroup_path"] = cgpath
        cgcpus = _opt_text(self.vmpkg_gpucgcpus)
        if cgcpus:
            vm["gpu_cgroup_cpus"] = cgcpus
        # VPU（视频编解码）
        vm["vpu_enabled"] = bool(self.vmpkg_vpu.isChecked())
        vm["vpu_host_pool_mb"] = int(self.vmpkg_vpuhost.value())
        vm["vpu_guest_pool_mb"] = int(self.vmpkg_vpuguest.value())

        # 设备：USB（legacy usb 标志 + xHCI 控制器端口数）+ virtio-snd 声卡。
        # 外设枚举按 name() 大写写入，与 App 自身导出一致；读取端大小写不敏感。
        vm["usb"] = bool(self.vmpkg_usb.isChecked())
        peripherals = [{"type": "XHCI_USB", "id": "xhci-0",
                        "usb2_ports": int(self.vmpkg_usb2.value()),
                        "usb3_ports": int(self.vmpkg_usb3.value())}]
        if self.vmpkg_sound.isChecked():
            peripherals.append({"type": "VIRTIO_SOUND", "endpoints": [
                {"mode": "SPEAKER", "host_device": "DEFAULT|system default", "host_label": ""},
                {"mode": "MICROPHONE", "host_device": "DEFAULT|system default", "host_label": ""},
            ]})
        vm["peripherals"] = peripherals
        vm["xhci_next"] = 1
        # COM1（serial num=1，app_console）是否作为 Guest 控制台
        for sp in vm["serial_ports"]:
            if sp.get("hardware") == "serial" and sp.get("num") == 1:
                sp["console"] = bool(self.vmpkg_com1.isChecked())
            elif sp.get("hardware") == "sbsa":
                # SBSA 串口是否作为 Guest 控制台（SPCR -> SAC）
                sp["console"] = bool(self.vmpkg_sbsa.isChecked())

        # 显示与 GPU（vnc {host,port} 始终写入；仅 exporter=vnc 时被 App 使用）
        try:
            w, h = (self.vmpkg_res.currentText().split("x") + ["720", "720"])[:2]
            w, h = int(w), int(h)
        except Exception:
            w, h = 1280, 720
        vm["screens"]["simplefb"]["enabled"] = bool(self.vmpkg_simplefb.isChecked())
        vm["screens"]["simplefb"]["width"] = w
        vm["screens"]["simplefb"]["height"] = h
        vm["screens"]["simplefb"]["exporter"] = self.vmpkg_sfexp.currentText() or "native"
        vm["screens"]["simplefb"]["input_enabled"] = bool(self.vmpkg_sfinput.isChecked())
        vm["screens"]["simplefb"]["poll_hz"] = int(self.vmpkg_sfpoll.value())
        vm["screens"]["simplefb"]["vnc"] = {"host": "127.0.0.1",
                                            "port": int(self.vmpkg_sfvncport.value())}
        vm["screens"]["gpu-0"]["enabled"] = bool(self.vmpkg_gpu0.isChecked())
        vm["screens"]["gpu-0"]["width"] = w
        vm["screens"]["gpu-0"]["height"] = h
        vm["screens"]["gpu-0"]["refresh_rate"] = int(self.vmpkg_refresh.value())
        vm["screens"]["gpu-0"]["exporter"] = self.vmpkg_gpuexp.currentText() or "native"
        vm["screens"]["gpu-0"]["input_enabled"] = bool(self.vmpkg_gpuinput.isChecked())
        vm["screens"]["gpu-0"]["dpi_h"] = int(self.vmpkg_gpudpih.value())
        vm["screens"]["gpu-0"]["dpi_v"] = int(self.vmpkg_gpudpiv.value())
        vm["screens"]["gpu-0"]["vnc"] = {"host": "127.0.0.1",
                                         "port": int(self.vmpkg_vncport.value())}
        vm["gpu_enabled"] = bool(self.vmpkg_gpu.isChecked())
        blit = (self.vmpkg_blit.currentText() or "").strip()
        if blit:
            vm["display_blit_provider"] = blit
        vm["native_display_enabled"] = bool(self.vmpkg_natdisp.isChecked())
        vm["vnc_enabled"] = bool(self.vmpkg_vnc.isChecked())

        # 网络
        net = (self.vmpkg_net.text() or "br-wifi").strip() or "br-wifi"
        uplink = (self.vmpkg_uplink.text() or "wlan0").strip() or "wlan0"
        pn = vm["networks"][0]["pkg_network"]
        pn["name"] = net
        pn["bridge_name"] = net
        pn["auto_up"] = bool(self.vmpkg_netautoup.isChecked())
        pn["stp"] = bool(self.vmpkg_stp.isChecked())
        pn["l2"]["uplink"] = uplink
        pn["l2"]["pseudo_bridge"] = bool(self.vmpkg_pbridge.isChecked())
        mac = _opt_text(self.vmpkg_mac)
        if mac:
            vm["networks"][0]["mac_address"] = mac
        else:
            vm["networks"][0].pop("mac_address", None)

        # 启动（仅 UEFI 协议；Linux 引导协议不提供）
        vm["boot"]["uefi"]["vars_enabled"] = bool(self.vmpkg_uefivars.isChecked())
        vm["boot"]["uefi"]["firmware"] = _opt_text(self.vmpkg_uefifw)
        vm["boot"]["uefi"]["vars"] = _opt_text(self.vmpkg_uefivarspath)

        # 高级：crosvm 额外参数 / 环境变量 / 共享目录（多行文本 -> 数组；空则移除键）
        extra = [ln.strip() for ln in self.vmpkg_extra.toPlainText().splitlines() if ln.strip()]
        if extra:
            vm["extra_options"] = extra
        else:
            vm.pop("extra_options", None)
        envv = [ln.strip() for ln in self.vmpkg_envv.toPlainText().splitlines()
                if ln.strip() and "=" in ln]
        if envv:
            vm["environment_variables"] = envv
        else:
            vm.pop("environment_variables", None)
        shared = []
        for ln in self.vmpkg_shared.toPlainText().splitlines():
            ln = ln.strip()
            if not ln or "=" not in ln:
                continue
            tag, path = ln.split("=", 1)
            tag, path = tag.strip(), path.strip()
            if tag and path:
                shared.append({"tag": tag, "path": path, "type": "fs"})
        if shared:
            vm["shared_dirs"] = shared
        else:
            vm.pop("shared_dirs", None)
        return vm

    # -------------------------- 构建 选项卡 --------------------------
    def _build_build(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(8)

        # 操作行：状态 + 构建/停止 + 配置/全局操作
        bar = QFrame()
        bar.setFrameShape(QFrame.NoFrame)
        h = QHBoxLayout(bar)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(8)

        status = BodyLabel(T("STATUS_READY"))
        status.setStyleSheet(f"color: {ACCENT}; font-weight: bold;")
        self.status_labels.append(status)
        h.addWidget(status, 1)

        save = PushButton(T("SAVE_DEFAULTS"))
        save.clicked.connect(self._save_defaults)
        load = PushButton(T("LOAD_DEFAULTS"))
        load.clicked.connect(self._load_and_populate)
        reset = PushButton(T("RESET"))
        reset.clicked.connect(self._reset_defaults)
        h.addWidget(save)
        h.addWidget(load)
        h.addWidget(reset)

        build = PrimaryPushButton(T("BUILD"))
        build.clicked.connect(self._on_build)
        self.build_buttons.append(build)
        stop = PushButton(T("STOP"))
        stop.clicked.connect(self._on_stop)
        stop.setEnabled(False)
        self.stop_buttons.append(stop)
        h.addWidget(build)
        h.addWidget(stop)

        lang = PushButton(T("LANG"))
        lang.clicked.connect(self._toggle_lang)
        exitb = PushButton(T("EXIT"))
        exitb.clicked.connect(self.close)
        h.addWidget(lang)
        h.addWidget(exitb)
        v.addWidget(bar)

        # 进度条：跟随构建阶段推进（preflight→建盘分区→灌镜像→注入驱动→精简→qcow2→vmpkg），
        # qemu-img convert -p 阶段解析实时百分比做细粒度推进。只单调前进，失败保持当前值。
        prog_row = QHBoxLayout()
        prog_row.setSpacing(8)
        self.progress_bar = ProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setFixedHeight(10)
        prog_row.addWidget(self.progress_bar, 1)
        self.progress_label = BodyLabel("0%")
        self.progress_label.setFixedWidth(48)
        prog_row.addWidget(self.progress_label)
        v.addLayout(prog_row)

        # 日志区
        v.addWidget(self._card_title(T("TAB_LOG")))
        self.log_box = QPlainTextEdit()
        self.log_box.setReadOnly(True)
        self.log_box.setLineWrapMode(QPlainTextEdit.NoWrap)
        v.addWidget(self.log_box, 1)
        return w

    # -------------------------- 磁盘 选项卡（快速分区，仿 DiskGenius）--------------------------
    def _build_disk(self):
        w = QWidget()
        v = QVBoxLayout(w)
        v.setContentsMargins(18, 14, 18, 14)
        v.setSpacing(6)

        sa = ScrollArea()
        sa.setWidgetResizable(True)
        c = QWidget()
        cv = QVBoxLayout(c)
        cv.setContentsMargins(0, 0, 0, 0)
        cv.setSpacing(10)

        # 磁盘大小（单位 GB，GUI 以 GB 显示，收集时转回 MB 注入 build.ps1）
        blk, fl = self._vsection(T("SECTION_DISK"))
        fl.addWidget(self._field_row("DISK_SIZE_MB"))
        # 磁盘大小变化即重算分区布局（populate 期间用 _populating 保护，避免重复渲染）
        self.fields["DISK_SIZE_MB"].textChanged.connect(
            lambda _=None: self._regen_layout())
        cv.addWidget(blk)

        # 快速分区（仿 DiskGenius）：GPT 固定，ESP/MSR 强制创建，
        # 只暴露「分区数目」与「高级设置里的分区大小」两项，杜绝配错
        cv.addWidget(self._card_title(T("QUICK_PART_TITLE")))
        card, lay = self._card()
        lay.addWidget(BodyLabel(T("QUICK_PART_HINT")))

        # 分区表类型：GPT（固定，不可选）
        pt_row = QHBoxLayout()
        pt_row.setSpacing(8)
        pt_row.addWidget(BodyLabel(T("PT_TYPE")))
        pt_gpt = BodyLabel(T("PT_GPT"))
        pt_gpt.setStyleSheet(f"color: {ACCENT}; font-weight: bold;")
        pt_row.addWidget(pt_gpt, 1)
        lay.addLayout(pt_row)
        lay.addWidget(CaptionLabel(T("ESP_MSR_HINT")))

        # 分区数目：1/2/3/4 个分区 + 自定义
        cnt_row = QHBoxLayout()
        cnt_row.setSpacing(8)
        cnt_row.addWidget(BodyLabel(T("PART_COUNT")))
        self.count_radios = {}
        for n in (1, 2, 3, 4):
            rb = RadioButton(T("PART_COUNT_N").format(n))
            rb.toggled.connect(lambda on, v=n: self._on_count_changed(v, on))
            self.count_radios[n] = rb
            cnt_row.addWidget(rb)
        self.custom_radio = RadioButton(T("PART_CUSTOM"))
        self.custom_radio.toggled.connect(
            lambda on: self.custom_combo.setEnabled(on))
        self.custom_combo = ComboBox()
        self.custom_combo.addItems([str(i) for i in range(1, 17)])
        self.custom_combo.setCurrentIndex(1)
        self.custom_combo.setEnabled(False)
        self.custom_combo.currentIndexChanged.connect(
            lambda _=None: self._on_count_changed(
                self.custom_combo.currentIndex() + 1,
                self.custom_radio.isChecked()))
        cnt_row.addWidget(self.custom_radio)
        cnt_row.addWidget(self.custom_combo)
        cnt_row.addStretch(1)
        lay.addLayout(cnt_row)
        lay.addWidget(CaptionLabel(T("PART_SIZE_HINT")))

        # 高级设置：各分区大小（GB），最后一个分区只读占剩余
        adv_title = BodyLabel(T("PART_ADV"))
        adv_title.setStyleSheet("font-weight: bold;")
        lay.addWidget(adv_title)
        self.size_rows = QVBoxLayout()
        self.size_rows.setSpacing(6)
        lay.addLayout(self.size_rows)

        db = PushButton(T("PART_DEFAULT_SIZES"))
        db.clicked.connect(self._reset_sizes)
        bh = QHBoxLayout()
        bh.addStretch(1)
        bh.addWidget(db)
        lay.addLayout(bh)

        cv.addWidget(card)

        # 分区预览（只读可视化条 + 明细）
        cv.addWidget(self._card_title(T("PART_PREVIEW")))
        card, lay = self._card()
        self.part_bar = QHBoxLayout()
        self.part_bar.setSpacing(4)
        lay.addLayout(self.part_bar)
        self.part_list = QVBoxLayout()
        self.part_list.setSpacing(4)
        lay.addLayout(self.part_list)
        cv.addWidget(card)

        sa.setWidget(c)
        v.addWidget(sa, 1)
        v.addWidget(self._bottom_bar(with_build=False), 0)

        # 初始化快速分区状态（之后会被 _populate_fields 用已保存值覆盖）
        self.part_count = 2
        self.part_sizes = []
        self.count_radios[2].setChecked(True)
        self._ensure_sizes()
        self._rebuild_size_rows()
        self._regen_layout()
        return w

    def _clear_layout(self, layout):
        while layout.count():
            item = layout.takeAt(0)
            wgt = item.widget()
            if wgt is not None:
                wgt.setParent(None)   # 立即脱离父窗口，避免重绘瞬间残影
                wgt.deleteLater()
            else:
                sub = item.layout()
                if sub is not None:
                    self._clear_layout(sub)

    # -------------------------- 快速分区辅助 --------------------------
    def _disk_mb(self):
        try:
            return max(int(self.fields["DISK_SIZE_MB"].text()) * 1024, 1024)
        except Exception:
            return 40 * 1024

    def _avail_mb(self):
        return max(self._disk_mb() - 260 - 16, 1024)   # 扣除固定的 ESP + MSR

    def _default_sizes(self, count=None):
        """均分默认大小（GB）：给最后一个分区至少留 1 GB 剩余。"""
        n = count or self.part_count
        avail_gb = max(self._avail_mb() // 1024, 1)
        each = max(avail_gb // max(n, 1) - 1, 1)
        return [each] * n

    def _default_names(self, count=None):
        """默认卷标：分区 1 为 Windows，其余为 Data（可被用户自定义覆盖）。"""
        n = count or self.part_count
        return ["Windows"] + ["Data"] * max(n - 1, 0)

    def _ensure_sizes(self):
        n = self.part_count
        if len(self.part_sizes) != n:
            defaults = self._default_sizes(n)
            sizes = list(self.part_sizes)[:n]
            while len(sizes) < n:
                sizes.append(defaults[len(sizes)])
            self.part_sizes = sizes
        # 同步分区名列表长度（截断/补齐默认名）
        names = list(self.part_names)[:n]
        while len(names) < n:
            names.append(self._default_names(n)[len(names)])
        self.part_names = names

    def _rebuild_size_rows(self):
        """按分区数目重建高级设置里的大小行：末位只读（自动占剩余），每行带可编辑分区名。"""
        self._clear_layout(self.size_rows)
        self.size_spins = []
        self.name_edits = []
        for i in range(self.part_count):
            role = T("PART_ROLE_SYS") if i == 0 else T("PART_ROLE_DATA")
            row = QHBoxLayout()
            row.setSpacing(8)
            row.addWidget(BodyLabel(f"{i + 1}: NTFS（{role}）"))
            name = LineEdit()
            name.setPlaceholderText(T("PART_NAME_PH"))
            name.setText(self.part_names[i] if i < len(self.part_names) else "")
            name.setMaximumWidth(140)
            name.textChanged.connect(lambda txt, idx=i: self._on_name_changed(idx, txt))
            row.addWidget(name)
            spin = QSpinBox()
            spin.setRange(1, 16 * 1024)
            spin.setSuffix(" GB")
            spin.blockSignals(True)   # 重建期间不触发 _on_size_changed
            spin.setValue(int(self.part_sizes[i]))
            spin.blockSignals(False)
            if i < self.part_count - 1:
                spin.valueChanged.connect(
                    lambda v, idx=i: self._on_size_changed(idx, v))
            else:
                spin.setReadOnly(True)
                spin.setToolTip(T("PART_SIZE_HINT"))
            row.addWidget(spin, 1)
            self.size_rows.addLayout(row)
            self.size_spins.append(spin)
            self.name_edits.append(name)

    def _on_count_changed(self, n, on):
        if getattr(self, "_populating", False) or not on:
            return
        self.part_count = max(1, int(n))
        self._ensure_sizes()
        self._rebuild_size_rows()
        self._regen_layout()

    def _on_size_changed(self, idx, v):
        if getattr(self, "_populating", False):
            return
        self._sizes_customized = True
        self.part_sizes[idx] = int(v)
        # 夹紧：给最后一个分区至少留 1 GB 剩余
        others = sum(self.part_sizes[:idx]) + sum(self.part_sizes[idx + 1:-1])
        max_allowed = max(self._avail_mb() // 1024 - others - 1, 1)
        if int(v) > max_allowed:
            self.part_sizes[idx] = max_allowed
            s = self.size_spins[idx]
            s.blockSignals(True)
            s.setValue(max_allowed)
            s.blockSignals(False)
        self._regen_layout()

    def _reset_sizes(self):
        if getattr(self, "_populating", False):
            return
        self._sizes_customized = False
        self.part_sizes = self._default_sizes()
        self.part_names = self._default_names()
        self._rebuild_size_rows()
        self._regen_layout()

    def _on_name_changed(self, idx, txt):
        if getattr(self, "_populating", False):
            return
        self._sizes_customized = True
        self.part_names[idx] = (txt or "").strip()
        self._regen_layout()

    def _make_layout(self, count, sizes_gb, disk_mb):
        """生成布局：ESP+MSR 固定，分区 1 为 Windows，其余为 Data，末位占剩余；各 NTFS 分区带自定义卷标。"""
        efi, msr = 260, 16
        avail = max(disk_mb - efi - msr, 1024)
        parts = [{"type": "EFI", "size_mb": efi}, {"type": "MSR", "size_mb": msr}]
        fixed = sum(max(int(s), 0) for s in sizes_gb[:count - 1]) * 1024
        last = max(avail - fixed, 1024)
        for i in range(count):
            smb = max(int(sizes_gb[i]), 1) * 1024 if i < count - 1 else last
            ptype = "Windows" if i == 0 else "Data"
            nm = self.part_names[i] if i < len(self.part_names) and self.part_names[i] else ptype
            parts.append({"type": ptype, "size_mb": smb, "name": nm})
        return parts

    def _clamp_sizes(self):
        """保证前 n-1 个分区大小之和不超出预算（末位至少留 1 GB）。"""
        fixed = self.part_sizes[:-1]
        budget = max(self._avail_mb() // 1024 - 1, 1)
        total = sum(fixed)
        if not fixed or total <= budget:
            return
        k = budget / total
        scaled = [max(int(s * k), 1) for s in fixed]
        while sum(scaled) > budget and max(scaled) > 1:
            scaled[scaled.index(max(scaled))] -= 1
        self.part_sizes[:len(scaled)] = scaled

    def _regen_layout(self):
        """根据当前分区数目/大小/磁盘大小重算布局并刷新预览。"""
        if getattr(self, "_populating", False):
            return
        self._ensure_sizes()
        if not self._sizes_customized:
            # 用户未手动调整过：始终按当前磁盘大小均分（末位自动占剩余）
            self.part_sizes = self._default_sizes()
        # 磁盘变小/分区变多时按比例收敛，保证总和不超过磁盘容量
        self._clamp_sizes()
        for i, s in enumerate(self.size_spins[:-1]):
            if s.value() != int(self.part_sizes[i]):
                s.blockSignals(True)
                s.setValue(int(self.part_sizes[i]))
                s.blockSignals(False)
        self.partitions = self._make_layout(
            self.part_count, self.part_sizes, self._disk_mb())
        # 同步末位只读的“剩余”大小框
        if getattr(self, "size_spins", None):
            last = self.size_spins[-1]
            last.blockSignals(True)
            last.setValue(max(int(self.partitions[-1]["size_mb"]) // 1024, 1))
            last.blockSignals(False)
        self._render_partitions()

    def _render_partitions(self):
        self._clear_layout(self.part_bar)
        self._clear_layout(self.part_list)
        parts = self.partitions or []
        total = max(sum(max(int(p.get("size_mb", 0)), 0) for p in parts), 1)
        colors = {"EFI": "#0a64a5", "MSR": "#8a8a8a", "Windows": "#107c10", "Data": "#d29200"}
        fs_map = {"EFI": T("FS_FAT32"), "MSR": T("FS_MSR"),
                  "Windows": T("FS_NTFS"), "Data": T("FS_NTFS")}
        for p in parts:
            sz = max(int(p.get("size_mb", 0)), 0)
            frac = (sz / total) if sz > 0 else 0.04
            gb = sz / 1024.0
            label = p.get("name") or p["type"]
            txt = f"{label}\n{(f'{gb:.1f} GB' if gb >= 1 else f'{sz} MB')}"
            block = QLabel(txt)
            block.setAlignment(Qt.AlignCenter)
            block.setFixedWidth(max(64, int(frac * 620)))
            block.setFixedHeight(46)
            block.setStyleSheet(
                f"background:{colors.get(p['type'], '#888')};color:white;"
                f"border-radius:4px;font-size:11px;")
            self.part_bar.addWidget(block)
        self.part_bar.addStretch(1)
        for p in parts:
            sz = max(int(p.get("size_mb", 0)), 0)
            gb = sz / 1024.0
            label = p.get("name") or p["type"]
            line = BodyLabel(
                f"{label}   ·   {p['type']}   ·   {gb:.1f} GB   ·   {fs_map.get(p['type'], T('FS_NTFS'))}")
            self.part_list.addWidget(line)

    def _browse_zip(self, key):
        ctrl = self.fields.get(key)
        p, _ = QFileDialog.getOpenFileName(self, T("IMPORT_ZIP"), "", "ZIP (*.zip)")
        if p and ctrl:
            ctrl.setText(self._win_path(p))

    # -------------------------- 底部操作栏 --------------------------
    def _bottom_bar(self, with_build=True):
        bar = QFrame()
        bar.setFrameShape(QFrame.NoFrame)
        h = QHBoxLayout(bar)
        h.setContentsMargins(8, 10, 8, 10)
        h.setSpacing(8)

        status = BodyLabel(T("STATUS_READY"))
        self.status_labels.append(status)
        h.addWidget(status, 0)

        save = PushButton(T("SAVE_DEFAULTS"))
        save.clicked.connect(self._save_defaults)
        load = PushButton(T("LOAD_DEFAULTS"))
        load.clicked.connect(self._load_and_populate)
        reset = PushButton(T("RESET"))
        reset.clicked.connect(self._reset_defaults)
        h.addWidget(save)
        h.addWidget(load)
        h.addWidget(reset)

        h.addStretch(1)

        if with_build:
            build = PrimaryPushButton(T("BUILD"))
            build.clicked.connect(self._on_build)
            self.build_buttons.append(build)
            stop = PushButton(T("STOP"))
            stop.clicked.connect(self._on_stop)
            stop.setEnabled(False)
            self.stop_buttons.append(stop)
            h.addWidget(build)
            h.addWidget(stop)

        lang = PushButton(T("LANG"))
        lang.clicked.connect(self._toggle_lang)
        exitb = PushButton(T("EXIT"))
        exitb.clicked.connect(self.close)
        h.addWidget(lang)
        h.addWidget(exitb)
        return bar

    # -------------------------- 字段：收集 / 回填 --------------------------
    def _collect_fields(self):
        data = {}
        defaults = default_values()

        for _sec, key, typ, default, _env, _lzh, _len, _hzh, _hen in FIELDS:
            if typ == "internal":
                data[key] = str(default)
                continue
            if typ == "hidden":
                continue
            if typ == "bool":
                data[key] = self.switches.get(key, SwitchButton()).isChecked()
            elif key == "IMAGE_INDEX":
                text = self.image_index_combo.currentText()
                m = re.match(r"(\d+)", text.strip())
                data[key] = m.group(1) if m else text.strip()
            elif key in self.fields:
                data[key] = self.fields[key].text().strip() if hasattr(self.fields[key], "text") \
                    else self.fields[key].toPlainText().strip()
            else:
                data[key] = str(default)

        try:
            gb = int(self.fields["DISK_SIZE_MB"].text())
            data["DISK_SIZE_MB"] = str(gb * 1024)
        except Exception:
            pass

        selected = [name for name, sw in self.driver_vars.items() if sw.isChecked()]
        data["DRIVER_INSTALL"] = " ".join(selected)

        if not data.get("OUT_QCOW") or data.get("OUT_QCOW") == "Windows.qcow2":
            data["OUT_QCOW"] = str(DEFAULT_OUTPUT)
        # 磁盘分区布局：由磁盘页「快速分区」生成，序列化为 JSON 传给 build.ps1
        data["PARTITION_LAYOUT"] = json.dumps(self.partitions, ensure_ascii=False)
        data["PART_COUNT"] = self.part_count
        data["PART_SIZES"] = json.dumps(self.part_sizes)
        data["PART_NAMES"] = json.dumps(self.part_names, ensure_ascii=False)
        data["PART_SIZES_CUSTOM"] = self._sizes_customized
        # 路径字段统一反斜杠：手动输入或旧 defaults.json 里的正斜杠也一并归一，
        # 保证保存/传给引擎的值与界面显示一致。
        for k in PATH_KEYS:
            if isinstance(data.get(k), str):
                data[k] = self._win_path(data[k])
        # vmpkg 模式：在 qcow2 之外再产出可直接导入 DroidVM 的 .vmpkg，
        # 并把 VM 配置（内存/CPU/swiotlb/分辨率/串口/网络）序列化为 VMS_JSON 传给引擎。
        if getattr(self, "out_format", "qcow2") == "vmpkg" and data.get("OUT_QCOW"):
            qcow = self._win_path(data["OUT_QCOW"])
            vmpkg = qcow.rsplit(".qcow2", 1)[0] + ".vmpkg" if qcow.lower().endswith(".qcow2") else qcow + ".vmpkg"
            data["OUT_VMPKG"] = vmpkg
            data["VMS_JSON"] = json.dumps(self._build_vms_dict(), ensure_ascii=False)
            data["VMPKG_COMPRESSION"] = self.vmpkg_comp.currentText() or "auto"
            # 导入界面显示的「包版本」与构建类型（自由字符串，无格式校验）；
            # app_version_code 固定为合法 u16，由 pack-vmpkg.ps1 同时写入头部与 manifest 保证一致。
            data["VMPKG_APP_VERSION"] = (self.vmpkg_appver.text() or "0.0").strip() or "0.0"
            data["VMPKG_APP_BUILD_TYPE"] = (self.vmpkg_buildtype.currentText() or "release").strip() or "release"
            data["VMPKG_APP_VERSION_CODE"] = "2650"
        return data

    def _populate_fields(self):
        defaults = default_values()
        vals = {**defaults, **self._saved_values}
        # 开始回填：屏蔽控件信号触发的重算/预览，统一在末尾重生成一次
        self._populating = True

        for key, val in vals.items():
            if key == "IMAGE_INDEX":
                self.image_index_combo.setCurrentText(str(val))
            elif key in self.fields:
                ctrl = self.fields[key]
                if key in PATH_KEYS:
                    val = self._win_path(str(val))
                if hasattr(ctrl, "text"):
                    ctrl.setText(str(val))
                else:
                    ctrl.setPlainText(str(val))
            if key in self.switches:
                self.switches[key].setChecked(
                    str(val).lower() in ("1", "true", "yes", "on")
                )

        if "DISK_SIZE_MB" in self.fields:
            try:
                mb = int(self.fields["DISK_SIZE_MB"].text())
                self.fields["DISK_SIZE_MB"].setText(str(mb // 1024))
            except Exception:
                pass

        if "OUT_QCOW" in self.fields:
            cur = self.fields["OUT_QCOW"].text().strip()
            if not cur or cur == "Windows.qcow2":
                self.fields["OUT_QCOW"].setText(str(DEFAULT_OUTPUT))

        if vals.get("DRIVER_INSTALL"):
            selected = {x.strip() for x in str(vals["DRIVER_INSTALL"]).replace(",", " ").split()}
            for name, sw in self.driver_vars.items():
                sw.setChecked(name in selected)
        else:
            for sw in self.driver_vars.values():
                sw.setChecked(True)

        # 快速分区回填（磁盘页）：分区数目 + 各分区大小。
        # 兼容旧版 QUICK_SCHEME 配置：standard/minimal → 1 个分区，data → 2 个分区
        count = vals.get("PART_COUNT")
        try:
            count = max(1, int(count))
        except Exception:
            # 缺省 2 个分区；兼容旧版方案配置：standard/minimal → 1，data → 2
            legacy = vals.get("QUICK_SCHEME")
            count = 2 if legacy in (None, "data") else 1
        sizes = []
        try:
            sizes = [max(int(s), 1) for s in json.loads(vals.get("PART_SIZES", "[]"))]
        except Exception:
            pass
        # 旧版配置带来的大小视为已自定义；否则跟随磁盘大小均分
        self._sizes_customized = bool(vals.get("PART_SIZES_CUSTOM")
                                      or vals.get("QUICK_SCHEME"))
        self.part_count = max(1, min(int(count), 16))
        self.part_sizes = []
        names = []
        try:
            names = [str(s) for s in json.loads(vals.get("PART_NAMES", "[]"))]
        except Exception:
            pass
        self.part_names = names
        self._ensure_sizes()
        if sizes:
            for i in range(self.part_count):
                if i < len(sizes):
                    self.part_sizes[i] = sizes[i]
        if hasattr(self, "count_radios"):
            if self.part_count <= 4:
                self.count_radios[self.part_count].setChecked(True)
            else:
                self.custom_combo.setCurrentIndex(self.part_count - 1)
                self.custom_radio.setChecked(True)
            self._rebuild_size_rows()
        # 关闭 populate 保护，用当前磁盘大小重新生成并渲染分区布局
        self._populating = False
        if hasattr(self, "part_list"):
            self._regen_layout()

    # -------------------------- 默认配置 --------------------------
    def _load_defaults(self):
        if DEFAULTS_FILE.exists():
            try:
                with open(DEFAULTS_FILE, "r", encoding="utf-8") as f:
                    self._saved_values = json.load(f).get("fields", {})
            except Exception:
                self._saved_values = {}
        else:
            self._saved_values = {}

    def _save_defaults(self):
        data = self._collect_fields()
        try:
            with open(DEFAULTS_FILE, "w", encoding="utf-8") as f:
                json.dump({"fields": data}, f, ensure_ascii=False, indent=2)
            self._set_status(T("SAVED"))
            InfoBar.success(title=T("SAVED"), content="", parent=self,
                           position=InfoBarPosition.TOP, duration=2000)
        except Exception as e:
            InfoBar.error(title=T("ERROR"), content=str(e), parent=self,
                         position=InfoBarPosition.TOP)

    def _load_and_populate(self):
        self._load_defaults()
        self._populate_fields()
        self._set_status(T("LOADED"))

    def _reset_defaults(self):
        if DEFAULTS_FILE.exists():
            try:
                DEFAULTS_FILE.unlink()
            except Exception:
                pass
        self._saved_values = {}
        self._populate_fields()
        self._set_status(T("RESET_DONE"))

    # -------------------------- 构建 --------------------------
    def _on_build(self):
        # 分区布局校验：至少需一个 EFI 系统分区和一个 Windows 分区，否则 bcdboot 会失败
        try:
            layout = json.loads(self._collect_fields().get("PARTITION_LAYOUT", "[]"))
            types = [str(p.get("type")) for p in layout]
            if not (("EFI" in types) and (("Windows" in types) or ("Data" in types))):
                InfoBar.warning(title=T("ERROR"), content=T("PART_WARN"), parent=self,
                               position=InfoBarPosition.TOP)
                return
        except Exception:
            pass

        if self.platform != "win32":
            InfoBar.info(title=T("ABOUT"), content=T("NOT_WIN"), parent=self,
                        position=InfoBarPosition.TOP, duration=3000)
            return

        fields = self._collect_fields()
        if not fields.get("SRC_ISO"):
            InfoBar.warning(title=T("ERROR"), content=T("NO_ISO"), parent=self,
                           position=InfoBarPosition.TOP)
            return

        build_ps1 = HERE / "builder" / "windows" / "build.ps1"
        if not build_ps1.is_file():
            InfoBar.warning(title=T("ERROR"), content=T("NO_ENGINE").format(str(build_ps1)),
                           parent=self, position=InfoBarPosition.TOP)
            return

        if PID_FILE.exists():
            PID_FILE.unlink()
        cfg_path = HERE / "build_config.json"
        log_path = HERE / "build.log"
        self.log_path = str(log_path)

        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({"fields": fields}, f, ensure_ascii=False, indent=2)
        # 仅清空上一次构建日志；实际内容由 run_build.py 写入，
        # 避免与 run_build 的 open("w") 截断互相竞争，导致日志开头几行被跳过。
        open(log_path, "w", encoding="utf-8").close()

        self._clear_log()
        self._set_status(T("NEED_ADMIN"))
        self._set_progress(0)
        if self.progress_label:
            self.progress_label.setText("0%")
        self.building = True
        for b in self.build_buttons:
            b.setEnabled(False)
        for b in self.stop_buttons:
            b.setEnabled(True)

        arglist = [str(HERE / "run_build.py"), "--config", str(cfg_path), "--log", str(log_path)]
        args_ps = ",".join(_quote_ps(a) for a in arglist)
        ps_cmd = (
            f"Start-Process -FilePath {_quote_ps(sys.executable)} "
            f"-ArgumentList {args_ps} -Verb RunAs -WindowStyle Hidden"
        )
        try:
            subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps_cmd],
                shell=False,
            )
        except Exception as e:
            self._finish_build_ui(False)
            InfoBar.error(title=T("ERROR"), content=f"Failed to start build:\n{e}",
                         parent=self, position=InfoBarPosition.TOP)

    def _on_stop(self):
        if PID_FILE.exists():
            try:
                pid = int(PID_FILE.read_text().strip())
                subprocess.run(["taskkill", "/PID", str(pid), "/F", "/T"], capture_output=True)
            except Exception as e:
                self._log(f"[GUI] stop failed: {e}")
        self._set_status(T("BUILD_STOPPED"))

    def _finish_build_ui(self, done):
        self.building = False
        for b in self.build_buttons:
            b.setEnabled(True)
        for b in self.stop_buttons:
            b.setEnabled(False)
        if done:
            self._set_status(T("STATUS_READY"))

    # -------------------------- 日志 --------------------------
    def _clear_log(self):
        if self.log_box:
            self.log_box.clear()
        self._last_log_pos = 0

    def _log(self, text):
        if self.log_box:
            self.log_box.appendPlainText(text)

    # -------------------------- 进度条 --------------------------
    def _set_progress(self, pct):
        """直接设定进度（用于收尾的 0% / 100%）。"""
        if not self.progress_bar:
            return
        self._progress = max(0, min(100, int(pct)))
        self.progress_bar.setValue(self._progress)
        if self.progress_label:
            self.progress_label.setText(f"{self._progress}%")

    def _advance_progress(self, pct):
        """单调推进：仅当新阶段百分比更大时才更新（避免日志回跳）。"""
        if not self.progress_bar:
            return
        if pct > self._progress:
            self._set_progress(pct)

    def _mark_progress_error(self):
        if self.progress_label:
            self.progress_label.setText("失败")
        if self.progress_bar:
            # 失败：进度条染红提示（值保持当前）
            try:
                self.progress_bar.setCustomBarColor(QColor(0xC4, 0x2B, 0x1C))
            except Exception:
                pass

    def _update_progress(self, text):
        """根据构建日志文本推进进度条；qemu-img 实时百分比优先。"""
        if not self.progress_bar:
            return
        # qemu-img convert -p 输出形如 "    (12.34/100%)" —— 细粒度推进 qcow2 转换阶段
        m = re.search(r"\(([\d.]+)/100%\)", text)
        if m:
            try:
                pct = float(m.group(1))
                self._advance_progress(80 + pct * 0.16)   # 80% -> 96%
            except ValueError:
                pass
            return
        # 阶段标记（按日志中出现的标签单调推进）
        if "preflight" in text or "=== DroidVM build started" in text:
            self._advance_progress(6)
        if "[wimlib]" in text or "[dism]" in text or "Apply-Image" in text or "Apply image" in text:
            self._advance_progress(20)
        if "[drivers]" in text:
            self._advance_progress(55)
        if "[debloat]" in text:
            self._advance_progress(70)
        if "[shrink]" in text or "ReTrim" in text:
            self._advance_progress(80)
        if "[qcow2]" in text:
            self._advance_progress(84)
        if "[vmpkg]" in text:
            self._advance_progress(97)
        if "=== EXIT CODE:" in text:
            try:
                line = [ln for ln in text.splitlines() if "=== EXIT CODE:" in ln][-1]
                code = int(line.split(":")[-1].strip().split()[0])
                if code == 0:
                    self._set_progress(100)
                else:
                    self._mark_progress_error()
            except Exception:
                pass

    def _poll_log(self):
        if not (self.building and self.log_path):
            return
        try:
            if not os.path.exists(self.log_path):
                self._last_log_pos = 0
                return
            # 二进制增量读：字节偏移最可靠，规避文本模式在 UTF-8 多字节 / 文件被截断重写时的 seek 错位。
            # 若 run_build 以 open("w") 截断并重写日志（size < 上次读取位置），则从头读，避免漏掉开头。
            with open(self.log_path, "rb") as f:
                size = f.seek(0, 2)
                if self._last_log_pos > size:
                    self._last_log_pos = 0
                f.seek(self._last_log_pos)
                raw = f.read()
                self._last_log_pos = f.tell()
            if raw:
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    text = raw.decode(locale.getpreferredencoding(False) or "cp936", "replace")
                self._log(text.rstrip("\n"))
                self._update_progress(text)
                if "=== EXIT CODE:" in text:
                    self._finish_build_ui(True)
                    try:
                        line = [ln for ln in text.splitlines()
                                if "=== EXIT CODE:" in ln][-1]
                        code = int(line.split(":")[-1].strip().split()[0])
                        if code == 0:
                            InfoBar.success(title=T("BUILD_DONE").format(code),
                                            content="", parent=self,
                                            position=InfoBarPosition.TOP, duration=4000)
                        else:
                            InfoBar.error(title=T("BUILD_FAIL").format(code),
                                         content="", parent=self,
                                         position=InfoBarPosition.TOP, duration=4000)
                    except Exception:
                        InfoBar.info(title=T("BUILD_DONE").format("?"), content="",
                                    parent=self, position=InfoBarPosition.TOP)
        except FileNotFoundError:
            self._last_log_pos = 0
        except Exception:
            pass

    # -------------------------- 探测（Windows 生效，Linux 占位）--------------------------
    def _ps(self, cmd, timeout=30):
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                capture_output=True, text=True, timeout=timeout,
                encoding="utf-8", errors="replace",
            )
            return res.stdout, res.stderr, res.returncode
        except Exception as e:
            return "", str(e), 1

    def _mount_iso_wim(self, iso):
        """若传入的是 ISO，挂载后返回内部 install.wim/install.esd 的完整路径；失败返回 None。"""
        iso = iso.replace("/", "\\")
        ps = (
            f'$img = Mount-DiskImage -ImagePath "{iso}" -PassThru -ErrorAction Stop; '
            f'$vol = $img | Get-Volume -ErrorAction Stop; '
            f'$dl = $vol.DriveLetter; '
            f'if (-not $dl) {{ throw "ISO mounted but no drive letter" }}; '
            f'$f = Get-ChildItem -Path ($dl + ":\\") -Recurse '
            f'-Include install.wim,install.esd -ErrorAction Stop | '
            f'Select-Object -First 1 -ExpandProperty FullName; '
            f'if (-not $f) {{ throw "no install.wim/install.esd inside ISO" }}; '
            f'Write-Output $f'
        )
        out, _err, rc = self._ps(ps, timeout=120)
        if rc != 0 or not out.strip():
            return None
        # 取最后一行（Write-Output 的输出），去掉可能的引号
        return out.strip().splitlines()[-1].strip().strip('"')

    def _dism_wim_info(self, wim):
        """用 dism.exe /Get-WimInfo 解析 WIM/ESD 内的版本列表（与 build.ps1 一致）。

        dism 原始输出经 cmd 重定向落盘，再用多编码回退解码，规避中文代码页乱码；
        解析同时兼容中英文（Index/索引、Name/名称）。
        """
        wim = wim.replace("/", "\\")
        tmp = os.path.join(tempfile.gettempdir(), "dvm_wiminfo.txt")
        try:
            if os.path.exists(tmp):
                os.remove(tmp)
        except Exception:
            pass
        self._ps(
            f'& cmd.exe /c "`"$env:SystemRoot\\System32\\dism.exe`" '
            f'/Get-WimInfo `"/WimFile:{wim}`" > `"{tmp}`" 2>&1"',
            timeout=120,
        )
        if not os.path.exists(tmp):
            return []
        try:
            raw = open(tmp, "rb").read()
        except Exception:
            return []
        text = None
        # 编码回退顺序：UTF-8 优先；中文 Windows 的 dism 输出多为 GBK(cp936)，需排在 utf-16 之前——
        # 否则 utf-16 会把偶数长度的 GBK 字节“静默”解成乱码而不报错。
        for enc in ("utf-8", "gbk", "cp936", locale.getpreferredencoding(False) or "cp936", "utf-16"):
            try:
                text = raw.decode(enc)
                break
            except Exception:
                continue
        if text is None:
            text = raw.decode("utf-8", "replace")

        res = []          # [[idx, name], ...]
        cur_idx = None
        for line in text.splitlines():
            s = line.strip()
            m = re.match(r'^(?:Index|索引)\s*[:：]\s*(\d+)', s)
            if m:
                cur_idx = int(m.group(1))
                res.append([cur_idx, "(unnamed)"])
                continue
            if cur_idx is not None:
                m2 = re.match(r'^(?:Name|名称)\s*[:：]\s*(.+)', s)
                if m2:
                    res[-1][1] = m2.group(1).strip()
        return [(i, n) for i, n in res]

    def _detect_image_indices(self, path):
        """探测 ISO/WIM/ESD 内的 Windows 版本列表。

        不再用脆弱的 Get-WindowsImage cmdlet（对 ESD 抛异常、需提权），改为与 build.ps1 一致的
        dism.exe 路线；ISO 会先挂载取出内部 install.wim/install.esd（build.ps1 也是这么做的）。
        路径先归一化反斜杠，规避 Windows 下 dism/diskpart 对 "/" 的解析不稳定。
        """
        if not path or not os.path.exists(path):
            return []
        p = path.replace("/", "\\")
        iso_to_dismount = None
        target = p
        if p.lower().endswith(".iso"):
            target = self._mount_iso_wim(p)
            if not target:
                return []
            iso_to_dismount = p
        try:
            return self._dism_wim_info(target)
        finally:
            if iso_to_dismount:
                self._ps(f'Dismount-DiskImage -ImagePath "{iso_to_dismount}" -ErrorAction SilentlyContinue',
                         timeout=60)

    def _detect_iso_async(self):
        path = self.fields["SRC_ISO"].text().strip()
        self._set_status(T("DETECTING_IMAGE"))
        items = self._detect_image_indices(path)
        if items:
            opts = [f"{i} - {n}" for i, n in items]
            self.image_index_combo.clear()
            self.image_index_combo.addItems(["0 - " + T("AUTO")] + opts)
            self._set_status(T("IMAGE_DETECT_DONE").format(len(items)))
        else:
            self._set_status(T("IMAGE_DETECT_FAILED"))

    # -------------------------- 主题 / 语言 --------------------------
    def _set_status(self, text):
        for lab in self.status_labels:
            lab.setText(text)

    def _apply_system_theme(self):
        """按 Windows 当前设置应用深浅色与强调色。"""
        theme, accent = detect_system_theme()
        self._theme = theme
        self._last_theme = theme
        self._last_accent = accent
        setTheme(THEME_MAP.get(theme, Theme.LIGHT))
        if accent:
            try:
                setThemeColor(accent)
                self._accent = accent
            except Exception:
                pass
        # 同步状态标签配色
        for lab in self.status_labels:
            lab.setStyleSheet(f"color: {self._accent}; font-weight: bold;")

    def _check_system_theme(self):
        """轮询系统主题，仅在发生变化时重应用，实现运行时跟随切换。"""
        theme, accent = detect_system_theme()
        if theme == self._last_theme and accent == self._last_accent:
            return
        self._last_theme = theme
        self._last_accent = accent
        self._theme = theme
        setTheme(THEME_MAP.get(theme, Theme.LIGHT))
        if accent:
            try:
                setThemeColor(accent)
                self._accent = accent
            except Exception:
                pass
        for lab in self.status_labels:
            lab.setStyleSheet(f"color: {self._accent}; font-weight: bold;")

    def _toggle_lang(self):
        # 重建窗口以应用新语言（label()/T() 均依赖 i18n 全局状态）
        cur = self._collect_fields()
        set_lang("en-US" if get_lang() == "zh-CN" else "zh-CN")
        self._timer.stop()
        saved = cur
        self.deleteLater()
        new = MainWindow()
        new._saved_values = saved
        new._populate_fields()
        new.show()


def main():
    app = QApplication(sys.argv)
    w = MainWindow()
    w.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

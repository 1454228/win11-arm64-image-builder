# -*- coding: utf-8 -*-
"""
ctk_gui.py — CustomTkinter 版 DroidVM Builder GUI（WPF 式紧凑布局）

运行前请安装依赖：
    pip install -r requirements.txt

特性：
- 贴近 WPF 原版的紧凑表单布局
- 系统镜像选择后自动列出 Windows 版本（WinNTSetup 风格）
- 驱动包自动探测子目录与可用驱动，以勾选框呈现
- 密码明文显示
- 浅色/深色/跟随系统主题
- 构建时实时日志（可折叠），UAC 提权执行
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path

try:
    import customtkinter as ctk
except ImportError as e:
    import tkinter.messagebox as mb
    mb.showerror(
        "Missing dependency",
        "customtkinter is required.\n\nPlease run:\n  pip install customtkinter pillow",
    )
    sys.exit(1)

import tkinter as tk
from tkinter import filedialog, messagebox

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from i18n import set_lang, get_lang, T  # noqa: E402
from schema import (  # noqa: E402
    FIELDS, default_values, build_env, label, help_text, KNOWN_DRIVERS,
)

DEFAULTS_FILE = HERE / "defaults.json"
PID_FILE = HERE / "build.pid"

# 默认输出路径：放在用户主目录下（本地、可写），避免写进程序目录
DEFAULT_OUTPUT = Path.home() / "DroidVM" / "Windows.qcow2"


def _quote_ps(s):
    return '"' + s.replace('"', '`"') + '"'


class DroidVMBuilderApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title(T("APP_TITLE"))
        self.geometry("780x650")
        self.minsize(720, 520)

        self.platform = sys.platform

        # 字段变量 {key: tk.StringVar / tk.BooleanVar / CTkTextbox}
        self.fields = {}
        self.text_widgets = {}
        self.widgets = {}
        self.driver_vars = {}          # {driver_name: BooleanVar}
        self.available_drivers = []    # 从驱动包探测到的子目录

        self.building = False
        self.log_path = None
        self._last_log_pos = 0
        self._saved_values = {}
        self._iso_after = None
        self._driver_after = None

        # 主题与语言
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self._build_header()
        self._build_form()
        self._build_log_section()
        self._build_bottom_bar()
        self._build_statusbar()

        self._load_defaults()
        self._populate_fields()
        self._poll_log()

    # ---------- 工具 / 探测 ----------
    def _set_status(self, text):
        self.status_var.set(text)

    def _ps(self, cmd, timeout=30):
        """执行 PowerShell 命令并返回 (stdout, stderr, returncode)。"""
        try:
            res = subprocess.run(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", cmd],
                capture_output=True,
                text=True,
                timeout=timeout,
                encoding="utf-8",
                errors="replace",
            )
            return res.stdout, res.stderr, res.returncode
        except Exception as e:
            return "", str(e), 1

    def _detect_image_indices(self, path):
        """返回 [(index, display_name), ...]；失败返回空列表。"""
        if not path or not os.path.exists(path):
            return []

        ext = Path(path).suffix.lower()
        wim_path = None
        mounted = False

        try:
            if ext in (".wim", ".esd"):
                wim_path = path
            elif ext == ".iso":
                # 挂载 ISO
                mount_cmd = (
                    f"$img = Mount-DiskImage -ImagePath {_quote_ps(path)} -PassThru; "
                    f"Start-Sleep -Milliseconds 500; "
                    f"$vol = $img | Get-Volume; "
                    f"if ($vol) {{ $vol.DriveLetter }}"
                )
                out, err, rc = self._ps(mount_cmd, timeout=30)
                letter = (out.strip().splitlines()[-1] if out else "").strip()
                if not letter or len(letter) != 1 or rc != 0:
                    return []
                iso_root = f"{letter}:\\"
                candidates = [
                    os.path.join(iso_root, "sources", "install.wim"),
                    os.path.join(iso_root, "sources", "install.esd"),
                ]
                for cand in candidates:
                    if os.path.exists(cand):
                        wim_path = cand
                        break
                mounted = True
            else:
                return []

            if not wim_path or not os.path.exists(wim_path):
                return []

            out, err, rc = self._ps(
                f"dism /Get-WimInfo /WimFile:{_quote_ps(wim_path)}", timeout=60
            )
            if rc != 0:
                return []

            # 解析 DISM 输出：中文为 "索引" / "名称"，英文为 "Index" / "Name"
            indices = []
            cur_idx = None
            cur_name = None
            idx_re = re.compile(r"索引\s*[:：]\s*(\d+)", re.I)
            name_re = re.compile(r"名称\s*[:：]\s*(.+)", re.I)
            idx_re_en = re.compile(r"Index\s*:\s*(\d+)", re.I)
            name_re_en = re.compile(r"Name\s*:\s*(.+)", re.I)
            for raw in out.splitlines():
                line = raw.strip()
                m = idx_re.search(line) or idx_re_en.search(line)
                if m:
                    if cur_idx is not None:
                        indices.append((cur_idx, cur_name or f"Index {cur_idx}"))
                    cur_idx = int(m.group(1))
                    cur_name = None
                    continue
                m = name_re.search(line) or name_re_en.search(line)
                if m and cur_idx is not None:
                    cur_name = m.group(1).strip()
            if cur_idx is not None:
                indices.append((cur_idx, cur_name or f"Index {cur_idx}"))

            return indices
        finally:
            if mounted:
                try:
                    self._ps(f"Dismount-DiskImage -ImagePath {_quote_ps(path)}", timeout=15)
                except Exception:
                    pass

    def _detect_driver_info(self, path):
        """探测驱动包子目录与可用驱动列表；返回 (subdir, [drivers])。"""
        if not path:
            return None, []

        subdir = None
        drivers = []

        try:
            if os.path.isdir(path):
                root = Path(path)
                subdir = self._find_driver_subdir(root)
                base = root / subdir if subdir else root
                drivers = self._list_driver_dirs(base)
            elif path.lower().endswith(".zip") and os.path.exists(path):
                with zipfile.ZipFile(path, "r") as zf:
                    names = zf.namelist()
                    subdir = self._find_driver_subdir_zip(names)
                    prefix = subdir + "/" if subdir else ""
                    drivers = self._list_driver_dirs_zip(names, prefix)
        except Exception:
            pass

        return subdir, drivers

    @staticmethod
    def _find_driver_subdir(root: Path):
        """在本地文件夹里找驱动根目录（包含 drivers 子目录或 KNOWN_DRIVERS 子目录的父目录）。"""
        # 优先找名为 drivers 的目录
        for p in root.rglob("drivers"):
            if p.is_dir():
                try:
                    return str(p.relative_to(root)).replace("\\", "/")
                except Exception:
                    return "drivers"
        # 否则找某个 KNOWN_DRIVERS 子目录的父目录
        for name in KNOWN_DRIVERS:
            for p in root.rglob(name):
                if p.is_dir():
                    parent = p.parent
                    try:
                        rel = parent.relative_to(root)
                        return str(rel).replace("\\", "/") if str(rel) != "." else ""
                    except Exception:
                        return ""
        return ""

    @staticmethod
    def _find_driver_subdir_zip(names):
        """在 zip 文件列表中找驱动根目录。"""
        # 优先找 drivers 目录
        for n in names:
            parts = n.strip("/").split("/")
            if "drivers" in parts:
                idx = parts.index("drivers")
                return "/".join(parts[:idx + 1])
        # 否则找 KNOWN_DRIVERS 子目录的父目录
        for name in KNOWN_DRIVERS:
            for n in names:
                parts = n.strip("/").split("/")
                if name in parts:
                    idx = parts.index(name)
                    if idx > 0:
                        return "/".join(parts[:idx])
        return ""

    @staticmethod
    def _list_driver_dirs(base: Path):
        """列出 base 下直接包含 .inf 的子目录名。"""
        out = []
        if not base.is_dir():
            return out
        for child in base.iterdir():
            if child.is_dir() and any(child.rglob("*.inf")):
                out.append(child.name)
        return sorted(set(out))

    @staticmethod
    def _list_driver_dirs_zip(names, prefix):
        """列出 zip 中 prefix 下直接包含 .inf 的子目录名。"""
        out = []
        prefix = prefix.rstrip("/") + "/" if prefix else ""
        seen = set()
        for n in names:
            if not n.startswith(prefix) or n.endswith("/"):
                continue
            rel = n[len(prefix):]
            parts = rel.split("/")
            if len(parts) >= 2 and parts[0] and parts[-1].lower().endswith(".inf"):
                seen.add(parts[0])
        return sorted(seen)

    # ---------- 界面构建 ----------
    def _build_header(self):
        header = ctk.CTkFrame(self, fg_color="transparent")
        header.pack(fill="x", padx=16, pady=(12, 0))

        title = ctk.CTkLabel(
            header,
            text=T("APP_TITLE"),
            font=("Microsoft YaHei UI", 18, "bold"),
        )
        title.pack(side="left", anchor="w")

        sub = ctk.CTkLabel(
            header,
            text=T("FORK_FROM"),
            font=("Microsoft YaHei UI", 11),
            text_color="gray",
        )
        sub.pack(side="left", anchor="sw", padx=(8, 0), pady=(4, 0))

        # 语言切换
        lang_frame = ctk.CTkFrame(header, fg_color="transparent")
        lang_frame.pack(side="right")
        self.lang_menu = ctk.CTkOptionMenu(
            lang_frame,
            values=["zh-CN", "en-US"],
            command=self._on_lang_change,
            width=100,
        )
        self.lang_menu.set(get_lang())
        self.lang_menu.pack(side="right")
        ctk.CTkLabel(lang_frame, text=T("LANG") + ":").pack(side="right", padx=(0, 6))

    def _build_form(self):
        """WPF 式紧凑表单。"""
        self.form_container = ctk.CTkScrollableFrame(self)
        self.form_container.pack(fill="both", expand=True, padx=12, pady=10)
        self.form_container._parent_frame.columnconfigure(0, weight=1)

        # 仓库根目录（默认隐藏于高级，但调试方便可放在顶部，这里放在底部高级区）
        # 按 WPF 截图，表单从系统镜像开始

        # --- 系统镜像 ---
        self._add_labeled_entry(
            self.form_container,
            "SRC_ISO",
            browse_kind="iso",
            hint=True,
            callback=self._on_iso_changed,
        )

        # --- 镜像版本 ---
        row = ctk.CTkFrame(self.form_container, fg_color="transparent")
        row.pack(fill="x", pady=4)
        row.columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text=label("IMAGE_INDEX") + ":", width=90, anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        self.image_index_combo = ctk.CTkComboBox(
            row,
            values=["0 - " + T("AUTO")],
            width=360,
        )
        self.image_index_combo.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.image_index_combo.set("0 - " + T("AUTO"))
        self.fields["IMAGE_INDEX"] = self.image_index_combo  # 特殊处理
        self.widgets["IMAGE_INDEX"] = self.image_index_combo

        detect_btn = ctk.CTkButton(
            row,
            text=T("DETECT"),
            width=70,
            command=self._on_detect_iso,
        )
        detect_btn.grid(row=0, column=2, padx=(8, 0))
        self.widgets["IMAGE_INDEX_DETECT"] = detect_btn

        # --- 驱动来源 ---
        self._add_labeled_entry(
            self.form_container,
            "DRIVERS_DIR",
            browse_kind="path_or_url",
            hint=False,
            callback=self._on_driver_changed,
        )

        # --- 驱动勾选框组 ---
        drivers_card = ctk.CTkFrame(self.form_container)
        drivers_card.pack(fill="x", pady=(8, 6))
        ctk.CTkLabel(
            drivers_card,
            text=T("DRIVERS_TO_INSTALL"),
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(anchor="w", padx=10, pady=(8, 4))
        self.drivers_frame = ctk.CTkFrame(drivers_card, fg_color="transparent")
        self.drivers_frame.pack(fill="x", padx=10, pady=(0, 10))
        self._render_driver_checkboxes(KNOWN_DRIVERS)

        # --- 用户名 / 密码（同一行）---
        account_row = ctk.CTkFrame(self.form_container, fg_color="transparent")
        account_row.pack(fill="x", pady=4)
        account_row.columnconfigure(1, weight=1)
        account_row.columnconfigure(3, weight=1)

        ctk.CTkLabel(account_row, text=label("DVM_USERNAME") + ":", width=60, anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        user_var = tk.StringVar()
        ctk.CTkEntry(account_row, textvariable=user_var, height=32).grid(
            row=0, column=1, sticky="ew", padx=(6, 16)
        )
        self.fields["DVM_USERNAME"] = user_var
        self.widgets["DVM_USERNAME"] = user_var

        ctk.CTkLabel(account_row, text=label("DVM_PASSWORD") + ":", width=60, anchor="w").grid(
            row=0, column=2, sticky="w"
        )
        pwd_var = tk.StringVar()
        # 明文显示
        ctk.CTkEntry(account_row, textvariable=pwd_var, show="", height=32).grid(
            row=0, column=3, sticky="ew", padx=(6, 0)
        )
        self.fields["DVM_PASSWORD"] = pwd_var
        self.widgets["DVM_PASSWORD"] = pwd_var

        # --- 计算机名 ---
        self._add_labeled_entry(self.form_container, "DVM_COMPUTERNAME", hint=True)

        # --- 磁盘大小 / Debloat / 压缩（同一行）---
        disk_row = ctk.CTkFrame(self.form_container, fg_color="transparent")
        disk_row.pack(fill="x", pady=4)
        ctk.CTkLabel(disk_row, text=label("DISK_SIZE_MB") + ":", width=60, anchor="w").pack(
            side="left"
        )
        disk_var = tk.StringVar()
        ctk.CTkEntry(disk_row, textvariable=disk_var, width=70, height=32).pack(
            side="left", padx=(6, 0)
        )
        ctk.CTkLabel(disk_row, text="GB").pack(side="left", padx=(4, 16))
        self.fields["DISK_SIZE_MB"] = disk_var
        self.widgets["DISK_SIZE_MB"] = disk_var

        debloat_var = tk.BooleanVar()
        ctk.CTkCheckBox(disk_row, text=label("DEBLOAT"), variable=debloat_var).pack(
            side="left", padx=(0, 12)
        )
        self.fields["DEBLOAT"] = debloat_var

        compress_var = tk.BooleanVar()
        ctk.CTkCheckBox(disk_row, text=label("COMPRESS"), variable=compress_var).pack(
            side="left"
        )
        self.fields["COMPRESS"] = compress_var

        # --- 输出路径 ---
        self._add_labeled_entry(self.form_container, "OUT_QCOW", browse_kind="save")
        # --- 临时目录（可选；C: 空间不足时指到更大的盘）---
        self._add_labeled_entry(self.form_container, "BUILD_TMP", browse_kind="dir", hint=True)

        # --- 高级区（SSH 公钥、时区、OpenSSH、证书、仓库根目录）---
        adv_card = ctk.CTkFrame(self.form_container)
        adv_card.pack(fill="x", pady=(10, 6))
        adv_header = ctk.CTkFrame(adv_card, fg_color="transparent")
        adv_header.pack(fill="x", padx=10, pady=(8, 4))
        ctk.CTkLabel(
            adv_header,
            text=T("ADVANCED"),
            font=("Microsoft YaHei UI", 12, "bold"),
        ).pack(side="left")
        self.adv_toggle = ctk.CTkButton(
            adv_header,
            text=T("HIDE") if False else T("SHOW"),
            width=60,
            command=self._toggle_advanced,
        )
        self.adv_toggle.pack(side="right")
        self.advanced_frame = ctk.CTkFrame(adv_card, fg_color="transparent")
        self.advanced_frame.pack(fill="x", padx=10, pady=(0, 10))

        # 证书
        self._add_labeled_entry(self.advanced_frame, "DRIVER_CERT", browse_kind="file")
        # OpenSSH
        self._add_labeled_entry(self.advanced_frame, "OPENSSH_SRC", browse_kind="file")
        # 时区
        self._add_labeled_entry(self.advanced_frame, "TARGET_TIMEZONE")
        # SSH 公钥
        self._add_textarea(self.advanced_frame, "SSH_PUBKEY")

        # 自动配置提示框（类似 WPF 的浅灰色信息区）
        info_card = ctk.CTkFrame(self.form_container, fg_color=("gray90", "gray20"))
        info_card.pack(fill="x", pady=(8, 0))
        ctk.CTkLabel(
            info_card,
            text=T("AUTO_CONFIG_TITLE"),
            font=("Microsoft YaHei UI", 11, "bold"),
        ).pack(anchor="w", padx=10, pady=(8, 2))
        ctk.CTkLabel(
            info_card,
            text=T("AUTO_CONFIG_TEXT"),
            font=("Microsoft YaHei UI", 11),
            justify="left",
            wraplength=700,
        ).pack(anchor="w", padx=10, pady=(0, 8))

    def _add_labeled_entry(self, parent, key, browse_kind=None, hint=False, callback=None):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=4)
        row.columnconfigure(1, weight=1)

        ctk.CTkLabel(row, text=label(key) + ":", width=90, anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        var = tk.StringVar()
        entry = ctk.CTkEntry(row, textvariable=var, height=32)
        entry.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.fields[key] = var
        self.widgets[key] = entry

        if callback:
            var.trace_add("write", lambda *_, k=key, cb=callback: cb())

        if browse_kind:
            if browse_kind == "iso":
                cmd = lambda vv=var: self._browse_iso(vv)
            elif browse_kind == "save":
                cmd = lambda vv=var: self._browse_save(vv)
            elif browse_kind == "file":
                cmd = lambda vv=var: self._browse_file(vv)
            elif browse_kind == "path_or_url":
                cmd = lambda vv=var: self._browse_path_or_url(vv)
            elif browse_kind == "dir":
                cmd = lambda vv=var: self._browse_dir(vv)
            else:
                cmd = lambda vv=var: self._browse_file(vv)
            ctk.CTkButton(row, text=T("BROWSE"), width=70, command=cmd).grid(
                row=0, column=2, padx=(8, 0)
            )

        if hint and help_text(key):
            ctk.CTkLabel(
                row,
                text=help_text(key),
                font=("Microsoft YaHei UI", 11),
                text_color="gray",
                anchor="w",
            ).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(2, 0))

    def _add_textarea(self, parent, key):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", pady=4)
        row.columnconfigure(1, weight=1)
        ctk.CTkLabel(row, text=label(key) + ":", width=90, anchor="nw").grid(
            row=0, column=0, sticky="nw"
        )
        txt = ctk.CTkTextbox(row, height=60, wrap="word")
        txt.grid(row=0, column=1, sticky="ew", padx=(8, 0))
        self.text_widgets[key] = txt
        self.widgets[key] = txt
        if help_text(key):
            ctk.CTkLabel(
                row,
                text=help_text(key),
                font=("Microsoft YaHei UI", 11),
                text_color="gray",
                anchor="w",
                wraplength=640,
            ).grid(row=1, column=1, sticky="w", padx=(8, 0), pady=(2, 0))

    def _render_driver_checkboxes(self, names):
        for w in self.drivers_frame.winfo_children():
            w.destroy()
        self.driver_vars.clear()

        cols = 4
        for i, name in enumerate(names):
            v = tk.BooleanVar(value=True)
            self.driver_vars[name] = v
            cb = ctk.CTkCheckBox(self.drivers_frame, text=name, variable=v)
            cb.grid(row=i // cols, column=i % cols, padx=6, pady=4, sticky="w")

    def _toggle_advanced(self):
        if self.advanced_frame.winfo_viewable():
            self.advanced_frame.pack_forget()
            self.adv_toggle.configure(text=T("SHOW"))
        else:
            self.advanced_frame.pack(fill="x", padx=10, pady=(0, 10))
            self.adv_toggle.configure(text=T("HIDE"))

    # ---------- 浏览对话框 ----------
    def _browse_iso(self, var):
        f = filedialog.askopenfilename(
            title=T("SELECT_ISO"),
            filetypes=[
                ("ISO/WIM/ESD", "*.iso *.wim *.esd"),
                ("All files", "*.*"),
            ],
        )
        if f:
            var.set(f)

    def _browse_file(self, var):
        f = filedialog.askopenfilename(title=T("SELECT_FILE"))
        if f:
            var.set(f)

    def _browse_save(self, var):
        f = filedialog.asksaveasfilename(
            title=T("SAVE_AS"),
            defaultextension=".qcow2",
            filetypes=[("QCOW2 image", "*.qcow2"), ("All files", "*.*")],
        )
        if f:
            var.set(f)

    def _browse_path_or_url(self, var):
        f = filedialog.askopenfilename(title=T("SELECT_FILE"))
        if f:
            var.set(f)

    def _browse_dir(self, var):
        d = filedialog.askdirectory(title=T("SELECT_FOLDER"))
        if d:
            var.set(d)

    # ---------- 自动探测事件 ----------
    def _on_iso_changed(self):
        # 延迟一点执行，避免输入过程中频繁挂载
        if self._iso_after:
            self.after_cancel(self._iso_after)
        self._iso_after = self.after(600, self._detect_iso_async)

    def _on_detect_iso(self):
        self._detect_iso_async()

    def _detect_iso_async(self):
        path = self.fields.get("SRC_ISO", tk.StringVar()).get().strip()
        if not path or path.lower().startswith(("http://", "https://")):
            return
        self._set_status(T("DETECTING_IMAGE"))

        def task():
            indices = self._detect_image_indices(path)
            self.after(0, lambda: self._apply_image_indices(indices))

        threading.Thread(target=task, daemon=True).start()

    def _apply_image_indices(self, indices):
        if not indices:
            self._set_status(T("IMAGE_DETECT_FAILED"))
            self.image_index_combo.configure(values=["0 - " + T("AUTO")])
            self.image_index_combo.set("0 - " + T("AUTO"))
            return

        values = [f"{idx} - {name}" for idx, name in indices]
        self.image_index_combo.configure(values=values)
        # 默认选第一个非 boot 索引（通常是 1）
        self.image_index_combo.set(values[0])
        self._set_status(T("IMAGE_DETECT_DONE").format(len(values)))

    def _on_driver_changed(self):
        if self._driver_after:
            self.after_cancel(self._driver_after)
        self._driver_after = self.after(600, self._detect_driver_async)

    def _detect_driver_async(self):
        path = self.fields.get("DRIVERS_DIR", tk.StringVar()).get().strip()
        if not path or path.lower().startswith(("http://", "https://")):
            return
        self._set_status(T("DETECTING_DRIVERS"))

        def task():
            subdir, drivers = self._detect_driver_info(path)
            self.after(0, lambda: self._apply_driver_info(subdir, drivers))

        threading.Thread(target=task, daemon=True).start()

    def _apply_driver_info(self, subdir, drivers):
        # 注意：DRIVER_DIR 是 internal 字段，固定为 "ZIP/drivers"（脚本约定），
        # 这里只根据探测结果刷新“要安装的驱动”勾选列表，绝不修改 DRIVER_DIR。
        if drivers:
            self.available_drivers = drivers
            self._render_driver_checkboxes(drivers)
            self._set_status(T("DRIVER_DETECT_DONE").format(len(drivers)))
        else:
            self._render_driver_checkboxes(KNOWN_DRIVERS)
            self._set_status(T("DRIVER_DETECT_FAILED"))

    # ---------- 收集/保存字段 ----------
    def _populate_fields(self):
        defaults = default_values()
        vals = {**defaults, **self._saved_values}

        for key, val in vals.items():
            if key in ("IMAGE_INDEX",):
                self.image_index_combo.set(str(val))
            elif key in self.fields:
                target = self.fields[key]
                if isinstance(target, tk.StringVar):
                    target.set(str(val))
                elif isinstance(target, tk.BooleanVar):
                    target.set(str(val).lower() in ("1", "true", "yes", "on"))
            if key in self.text_widgets:
                self.text_widgets[key].delete("0.0", "end")
                self.text_widgets[key].insert("0.0", str(val))

        # 磁盘大小 MB -> GB 显示
        if "DISK_SIZE_MB" in self.fields:
            try:
                mb = int(self.fields["DISK_SIZE_MB"].get())
                self.fields["DISK_SIZE_MB"].set(str(mb // 1024))
            except Exception:
                pass

        # 输出路径：若无有效值，默认放到用户主目录下的 DroidVM/
        if "OUT_QCOW" in self.fields:
            cur = self.fields["OUT_QCOW"].get().strip()
            if not cur or cur == "Windows.qcow2":
                self.fields["OUT_QCOW"].set(str(DEFAULT_OUTPUT))

        # 驱动安装列表：从保存值勾选
        if vals.get("DRIVER_INSTALL"):
            selected = {x.strip() for x in str(vals["DRIVER_INSTALL"]).replace(",", " ").split()}
            for name, var in self.driver_vars.items():
                var.set(name in selected)
        else:
            for var in self.driver_vars.values():
                var.set(True)

    def _collect_fields(self):
        data = {}
        defaults = default_values()

        for _sec, key, typ, default, _env, _lzh, _len, _hzh, _hen in FIELDS:
            if typ == "internal":
                # 内置固定字段（如 DRIVER_DIR=ZIP/drivers），始终用默认值，不被覆盖
                data[key] = str(default)
                continue
            if typ == "bool":
                v = self.fields.get(key, tk.BooleanVar(value=bool(default))).get()
                data[key] = v
            elif typ == "textarea":
                data[key] = self.text_widgets.get(key, ctk.CTkTextbox(self)).get("0.0", "end").strip()
            elif key == "IMAGE_INDEX":
                text = self.image_index_combo.get()
                # 取 "1 - Windows 11 Pro" 中的数字
                m = re.match(r"(\d+)", text.strip())
                data[key] = m.group(1) if m else text.strip()
            elif key in self.fields:
                data[key] = self.fields[key].get().strip()
            else:
                data[key] = str(default)

        # 磁盘大小 GB -> MB
        try:
            gb = int(data.get("DISK_SIZE_MB", "40"))
            data["DISK_SIZE_MB"] = str(gb * 1024)
        except Exception:
            pass

        # 驱动勾选 -> 空格分隔字符串
        selected = [name for name, var in self.driver_vars.items() if var.get()]
        data["DRIVER_INSTALL"] = " ".join(selected)

        # 输出路径：无有效值时默认到用户主目录
        if not data.get("OUT_QCOW") or data.get("OUT_QCOW") == "Windows.qcow2":
            data["OUT_QCOW"] = str(DEFAULT_OUTPUT)

        return data

    def _saved_field_values(self):
        if DEFAULTS_FILE.exists():
            try:
                with open(DEFAULTS_FILE, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                return payload.get("fields", {})
            except Exception:
                return {}
        return {}

    def _load_defaults(self):
        if DEFAULTS_FILE.exists():
            try:
                with open(DEFAULTS_FILE, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                self._saved_values = payload.get("fields", {})
            except Exception:
                self._saved_values = {}
        else:
            self._saved_values = {}

    def _save_defaults(self):
        data = self._collect_fields()
        # 保存时把磁盘大小存回 GB 更直观？但 schema 默认是 MB。统一按 MB 存。
        payload = {"fields": data}
        try:
            with open(DEFAULTS_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            self._set_status(T("SAVED"))
        except Exception as e:
            messagebox.showerror(T("ERROR"), str(e))

    def _reset_defaults(self):
        if DEFAULTS_FILE.exists():
            try:
                DEFAULTS_FILE.unlink()
            except Exception:
                pass
        self._saved_values = {}
        self._populate_fields()
        self._set_status(T("RESET_DONE"))

    # ---------- 构建 ----------
    def _on_build(self):
        if self.platform != "win32":
            messagebox.showinfo(T("ABOUT"), T("NOT_WIN"))
            return

        fields = self._collect_fields()
        if not fields.get("SRC_ISO"):
            messagebox.showwarning(T("ERROR"), T("NO_ISO"))
            return

        # 构建引擎随程序自带的 gui/builder/windows/build.ps1（无需仓库根目录）
        build_ps1 = HERE / "builder" / "windows" / "build.ps1"
        if not build_ps1.is_file():
            messagebox.showwarning(
                T("ERROR"),
                T("NO_ENGINE").format(str(build_ps1)),
            )
            return

        cfg = {"fields": fields}
        cfg_path = HERE / "build_config.json"
        log_path = HERE / "build.log"
        self.log_path = str(log_path)

        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

        with open(log_path, "w", encoding="utf-8") as f:
            f.write("=== DroidVM build started ===\n")

        self._clear_log()
        self._set_status(T("NEED_ADMIN"))
        self.building = True
        self.build_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self._expand_log()

        if PID_FILE.exists():
            PID_FILE.unlink()

        arglist = [
            str(HERE / "run_build.py"),
            "--config", str(cfg_path),
            "--log", str(log_path),
        ]
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
            self.building = False
            self.build_btn.configure(state="normal")
            self.stop_btn.configure(state="disabled")
            messagebox.showerror(T("ERROR"), f"Failed to start build:\n{e}")

    def _on_stop(self):
        if PID_FILE.exists():
            try:
                pid = int(PID_FILE.read_text().strip())
                subprocess.run(
                    ["taskkill", "/PID", str(pid), "/F", "/T"],
                    capture_output=True,
                )
            except Exception as e:
                self._log(f"[GUI] stop failed: {e}")
        self._set_status(T("BUILD_STOPPED"))

    # ---------- 日志 ----------
    def _build_log_section(self):
        self.log_header = ctk.CTkFrame(self, fg_color="transparent")
        self.log_header.pack(fill="x", padx=16, pady=(0, 4))

        self.log_toggle_btn = ctk.CTkButton(
            self.log_header,
            text=T("SHOW_LOG"),
            width=100,
            command=self._toggle_log,
        )
        self.log_toggle_btn.pack(side="left")

        self.log_frame = ctk.CTkFrame(self)
        # 默认隐藏
        self.log_frame.pack_forget()

        self.log_box = ctk.CTkTextbox(
            self.log_frame,
            wrap="word",
            font=("Consolas", 12),
            state="disabled",
        )
        self.log_box.pack(fill="both", expand=True, padx=8, pady=8)

    def _toggle_log(self):
        if self.log_frame.winfo_ismapped():
            self.log_frame.pack_forget()
            self.log_toggle_btn.configure(text=T("SHOW_LOG"))
        else:
            self.log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
            self.log_toggle_btn.configure(text=T("HIDE_LOG"))

    def _expand_log(self):
        if not self.log_frame.winfo_ismapped():
            self.log_frame.pack(fill="both", expand=True, padx=12, pady=(0, 8))
            self.log_toggle_btn.configure(text=T("HIDE_LOG"))

    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("0.0", "end")
        self.log_box.configure(state="disabled")

    def _log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _poll_log(self):
        if self.building and self.log_path and os.path.exists(self.log_path):
            try:
                with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                    f.seek(self._last_log_pos)
                    chunk = f.read()
                    self._last_log_pos = f.tell()
                    if chunk:
                        self.log_box.configure(state="normal")
                        self.log_box.insert("end", chunk)
                        self.log_box.see("end")
                        self.log_box.configure(state="disabled")
                        if "=== EXIT CODE:" in chunk:
                            self.building = False
                            self.build_btn.configure(state="normal")
                            self.stop_btn.configure(state="disabled")
                            try:
                                code_line = [ln for ln in chunk.splitlines() if "=== EXIT CODE:" in ln][-1]
                                code = int(code_line.split(":")[-1].strip().split()[0])
                                self._set_status(
                                    T("BUILD_DONE").format(code) if code == 0 else T("BUILD_FAIL").format(code)
                                )
                            except Exception:
                                self._set_status(T("BUILD_DONE").format("?"))
            except Exception:
                pass
        else:
            self._last_log_pos = 0

        self.after(250, self._poll_log)

    # ---------- 底部按钮 ----------
    def _build_bottom_bar(self):
        bar = ctk.CTkFrame(self, fg_color="transparent")
        bar.pack(fill="x", padx=16, pady=(0, 8))

        self.save_btn = ctk.CTkButton(
            bar,
            text=T("SAVE_DEFAULTS"),
            width=110,
            command=self._save_defaults,
        )
        self.save_btn.pack(side="left", padx=(0, 8))

        self.load_btn = ctk.CTkButton(
            bar,
            text=T("LOAD_DEFAULTS"),
            width=110,
            command=self._load_and_populate,
        )
        self.load_btn.pack(side="left", padx=8)

        self.reset_btn = ctk.CTkButton(
            bar,
            text=T("RESET"),
            width=80,
            command=self._reset_defaults,
        )
        self.reset_btn.pack(side="left", padx=8)

        self.exit_btn = ctk.CTkButton(
            bar,
            text=T("EXIT"),
            width=90,
            command=self.destroy,
        )
        self.exit_btn.pack(side="right", padx=(8, 0))

        self.stop_btn = ctk.CTkButton(
            bar,
            text=T("STOP"),
            width=90,
            command=self._on_stop,
            state="disabled",
            fg_color="transparent",
            border_width=2,
        )
        self.stop_btn.pack(side="right", padx=8)

        self.build_btn = ctk.CTkButton(
            bar,
            text=T("BUILD"),
            width=110,
            command=self._on_build,
        )
        self.build_btn.pack(side="right", padx=8)

    def _load_and_populate(self):
        self._load_defaults()
        self._populate_fields()
        self._set_status(T("LOADED"))

    def _build_statusbar(self):
        self.status_var = tk.StringVar(value=T("STATUS_READY"))
        self.statusbar = ctk.CTkLabel(
            self,
            textvariable=self.status_var,
            height=26,
            anchor="w",
            padx=10,
        )
        self.statusbar.pack(fill="x", side="bottom", padx=12, pady=(0, 10))

    # ---------- 事件 ----------
    def _on_lang_change(self, lang):
        self._saved_values = self._collect_fields()
        set_lang(lang)
        self.title(T("APP_TITLE"))
        self._set_status(T("STATUS_READY"))
        self._rebuild_ui()

    def _rebuild_ui(self):
        # 简单粗暴：销毁所有子控件重建
        for child in list(self.winfo_children()):
            child.destroy()
        self.fields.clear()
        self.widgets.clear()
        self.text_widgets.clear()
        self.driver_vars.clear()
        self._build_header()
        self._build_form()
        self._build_log_section()
        self._build_bottom_bar()
        self._build_statusbar()
        self._populate_fields()

def main():
    app = DroidVMBuilderApp()
    app.mainloop()


if __name__ == "__main__":
    main()

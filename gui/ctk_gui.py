# -*- coding: utf-8 -*-
"""
ctk_gui.py — CustomTkinter 版 DroidVM Builder GUI

运行前请安装依赖：
    pip install customtkinter pillow

特性：
- 现代外观（圆角、浅色/深色/跟随系统）
- 中文/英文标签（参数名不再暴露环境变量名）
- 配置页 + 实时日志页
- UAC 提权构建、日志实时 tail、错误高亮
"""
import json
import os
import subprocess
import sys
import time
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
    FIELDS, SECTION_ORDER, default_values, build_env,
    section_title, label, help_text,
)

DEFAULTS_FILE = HERE / "defaults.json"
PID_FILE = HERE / "build.pid"


class DroidVMBuilderApp(ctk.CTk):
    def __init__(self):
        super().__init__()

        self.title(T("APP_TITLE"))
        self.geometry("900x720")
        self.minsize(800, 600)

        # 平台
        self.platform = sys.platform

        # 变量
        self.repo_var = tk.StringVar(value=str(self._find_repo()))
        self.fields = {}
        self.widgets = {}
        self.text_widgets = {}
        self.building = False
        self.log_path = None
        self._last_log_pos = 0

        # 主题与语言
        ctk.set_appearance_mode("System")
        ctk.set_default_color_theme("blue")

        self._build_toolbar()
        self._build_tabs()
        self._build_statusbar()

        self._load_defaults()
        self.after(100, self._render_config)
        self._poll_log()

    # ---------- 发现/工具 ----------
    def _find_repo(self):
        """向上查找包含 windows/build.ps1 的目录。"""
        p = HERE
        for _ in range(5):
            if (p / "windows" / "build.ps1").is_file():
                return str(p)
            parent = p.parent
            if parent == p:
                break
            p = parent
        return str(HERE)

    @staticmethod
    def _quote_ps(s):
        return '"' + s.replace('"', '`"') + '"'

    def _set_status(self, text):
        self.status_var.set(text)

    # ---------- 顶部工具栏 ----------
    def _build_toolbar(self):
        self.toolbar = ctk.CTkFrame(self, height=40)
        self.toolbar.pack(fill="x", padx=10, pady=(10, 0))
        self.toolbar.pack_propagate(False)

        ctk.CTkLabel(self.toolbar, text=T("LANG") + ":").pack(side="left", padx=(10, 4))
        self.lang_menu = ctk.CTkOptionMenu(
            self.toolbar,
            values=["zh-CN", "en-US"],
            command=self._on_lang_change,
            width=100,
        )
        self.lang_menu.set(get_lang())
        self.lang_menu.pack(side="left", padx=4)

        ctk.CTkLabel(self.toolbar, text=T("THEME") + ":").pack(side="left", padx=(20, 4))
        self.theme_menu = ctk.CTkOptionMenu(
            self.toolbar,
            values=[T("SYSTEM"), T("LIGHT"), T("DARK")],
            command=self._on_theme_change,
            width=100,
        )
        self.theme_menu.set(T("SYSTEM"))
        self.theme_menu.pack(side="left", padx=4)

    # ---------- 标签页 ----------
    def _build_tabs(self):
        self.tabview = ctk.CTkTabview(self)
        self.tabview.pack(fill="both", expand=True, padx=10, pady=10)

        self.tab_config = self.tabview.add(T("TAB_CONFIG"))
        self.tab_log = self.tabview.add(T("TAB_LOG"))

        self._build_config_tab()
        self._build_log_tab()

    def _build_config_tab(self):
        # 仓库根目录
        repo_frame = ctk.CTkFrame(self.tab_config)
        repo_frame.pack(fill="x", padx=10, pady=(10, 0))
        ctk.CTkLabel(repo_frame, text=T("REPO_ROOT") + ":").pack(side="left", padx=10)
        ctk.CTkEntry(repo_frame, textvariable=self.repo_var, height=32).pack(
            side="left", fill="x", expand=True, padx=(0, 8)
        )
        ctk.CTkButton(
            repo_frame,
            text=T("BROWSE"),
            width=80,
            height=32,
            command=self._browse_repo,
        ).pack(side="right", padx=(0, 10))

        self.config_container = ctk.CTkFrame(self.tab_config, fg_color="transparent")
        self.config_container.pack(fill="both", expand=True, padx=10, pady=10)

    def _build_log_tab(self):
        self.log_box = ctk.CTkTextbox(
            self.tab_log,
            wrap="word",
            font=("Consolas", 13),
            state="disabled",
        )
        self.log_box.pack(fill="both", expand=True, padx=10, pady=(10, 0))
        self._log(T("LOG_HINT"))

        controls = ctk.CTkFrame(self.tab_log)
        controls.pack(fill="x", padx=10, pady=10)

        self.build_btn = ctk.CTkButton(
            controls,
            text=T("BUILD"),
            width=120,
            height=36,
            command=self._on_build,
        )
        self.build_btn.pack(side="left", padx=(0, 8))

        self.stop_btn = ctk.CTkButton(
            controls,
            text=T("STOP"),
            width=100,
            height=36,
            command=self._on_stop,
            state="disabled",
            fg_color="transparent",
            border_width=2,
        )
        self.stop_btn.pack(side="left", padx=8)

        self.save_btn = ctk.CTkButton(
            controls,
            text=T("SAVE_DEFAULTS"),
            width=120,
            height=36,
            command=self._save_defaults,
        )
        self.save_btn.pack(side="left", padx=8)

        self.load_btn = ctk.CTkButton(
            controls,
            text=T("LOAD_DEFAULTS"),
            width=120,
            height=36,
            command=self._load_defaults,
        )
        self.load_btn.pack(side="left", padx=8)

        self.reset_btn = ctk.CTkButton(
            controls,
            text=T("RESET"),
            width=80,
            height=36,
            command=self._reset_defaults,
        )
        self.reset_btn.pack(side="left", padx=8)

        self.about_btn = ctk.CTkButton(
            controls,
            text=T("ABOUT"),
            width=80,
            height=36,
            command=self._show_about,
        )
        self.about_btn.pack(side="right", padx=(8, 0))

    def _build_statusbar(self):
        self.status_var = tk.StringVar(value=T("STATUS_READY"))
        self.statusbar = ctk.CTkLabel(
            self,
            textvariable=self.status_var,
            height=28,
            anchor="w",
            padx=10,
        )
        self.statusbar.pack(fill="x", side="bottom", padx=10, pady=(0, 10))

    # ---------- 配置渲染 ----------
    def _render_config(self):
        # 清掉旧控件
        for w in self.config_container.winfo_children():
            w.destroy()
        self.fields.clear()
        self.widgets.clear()
        self.text_widgets.clear()

        saved = self._saved_field_values()
        defaults = default_values()

        scroll = ctk.CTkScrollableFrame(self.config_container)
        scroll.pack(fill="both", expand=True)

        for sec in SECTION_ORDER:
            card = ctk.CTkFrame(scroll)
            card.pack(fill="x", expand=True, pady=8, padx=4)

            ctk.CTkLabel(
                card,
                text=section_title(sec),
                font=("Microsoft YaHei UI", 15, "bold"),
            ).pack(anchor="w", padx=14, pady=(10, 4))

            for _sec, key, typ, default, _env, _lzh, _len, _hzh, _hen in FIELDS:
                if _sec != sec:
                    continue
                val = saved.get(key, defaults.get(key, default))
                self._render_field(card, key, typ, val)

    def _render_field(self, parent, key, typ, val):
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=6)

        # 标签
        ctk.CTkLabel(
            row,
            text=label(key),
            width=180,
            anchor="w",
            font=("Microsoft YaHei UI", 13),
        ).pack(side="left")

        # 输入区
        input_frame = ctk.CTkFrame(row, fg_color="transparent")
        input_frame.pack(side="left", fill="x", expand=True)
        input_frame.columnconfigure(0, weight=1)

        if typ == "bool":
            v = tk.BooleanVar(value=bool(val) if isinstance(val, bool) else str(val).lower() in ("1", "true", "yes", "on"))
            cb = ctk.CTkCheckBox(input_frame, text="", variable=v)
            cb.grid(row=0, column=0, sticky="w")
            self.fields[key] = v
            self.widgets[key] = cb

        elif typ == "textarea":
            txt = ctk.CTkTextbox(input_frame, height=70, wrap="word")
            txt.insert("0.0", str(val))
            txt.grid(row=0, column=0, sticky="ew")
            self.fields[key] = None  # 手动读取
            self.text_widgets[key] = txt
            self.widgets[key] = txt

        elif typ in ("text", "int"):
            v = tk.StringVar(value=str(val))
            e = ctk.CTkEntry(input_frame, textvariable=v, height=32)
            e.grid(row=0, column=0, sticky="ew")
            self.fields[key] = v
            self.widgets[key] = e

        elif typ == "password":
            v = tk.StringVar(value=str(val))
            e = ctk.CTkEntry(input_frame, textvariable=v, show="*", height=32)
            e.grid(row=0, column=0, sticky="ew")
            self.fields[key] = v
            self.widgets[key] = e

        elif typ in ("file", "save", "path_or_url"):
            v = tk.StringVar(value=str(val))
            e = ctk.CTkEntry(input_frame, textvariable=v, height=32)
            e.grid(row=0, column=0, sticky="ew", padx=(0, 8))

            if typ == "save":
                cmd = lambda vv=v: self._browse_save(vv)
                btn_text = T("BROWSE")
            elif typ == "file":
                cmd = lambda vv=v: self._browse_file(vv)
                btn_text = T("BROWSE")
            else:  # path_or_url
                cmd = lambda vv=v: self._browse_path_or_url(vv)
                btn_text = T("BROWSE")

            btn = ctk.CTkButton(
                input_frame,
                text=btn_text,
                width=70,
                height=32,
                command=cmd,
            )
            btn.grid(row=0, column=1)
            self.fields[key] = v
            self.widgets[key] = e

        # 帮助文字
        h = help_text(key)
        if h:
            ctk.CTkLabel(
                input_frame,
                text=h,
                font=("Microsoft YaHei UI", 11),
                text_color="gray",
                anchor="w",
            ).grid(row=1, column=0, sticky="w", pady=(2, 0))

    # ---------- 浏览对话框 ----------
    def _browse_repo(self):
        d = filedialog.askdirectory(title=T("SELECT_FOLDER"))
        if d:
            self.repo_var.set(d)

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
        # 优先文件选择，也可以让用户直接粘贴 URL
        f = filedialog.askopenfilename(title=T("SELECT_ISO"))
        if f:
            var.set(f)

    # ---------- 收集/保存字段 ----------
    def _collect_fields(self):
        data = {}
        defaults = default_values()
        for _sec, key, typ, default, _env, _lzh, _len, _hzh, _hen in FIELDS:
            if typ == "bool":
                data[key] = self.fields[key].get()
            elif typ == "textarea":
                data[key] = self.text_widgets[key].get("0.0", "end").strip()
            else:
                data[key] = self.fields.get(key, tk.StringVar(value=str(default))).get().strip()
        return data

    def _saved_field_values(self):
        if DEFAULTS_FILE.exists():
            try:
                with open(DEFAULTS_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                return {}
        return {}

    def _save_defaults(self):
        data = self._collect_fields()
        payload = {"repo_root": self.repo_var.get(), "fields": data}
        try:
            with open(DEFAULTS_FILE, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            self._set_status(T("SAVED"))
        except Exception as e:
            messagebox.showerror(T("ERROR"), str(e))

    def _load_defaults(self):
        if DEFAULTS_FILE.exists():
            try:
                with open(DEFAULTS_FILE, "r", encoding="utf-8") as f:
                    payload = json.load(f)
                if "repo_root" in payload:
                    self.repo_var.set(payload["repo_root"])
                self._saved_values = payload.get("fields", {})
            except Exception:
                self._saved_values = {}
        else:
            self._saved_values = {}

    def _reset_defaults(self):
        if DEFAULTS_FILE.exists():
            try:
                DEFAULTS_FILE.unlink()
            except Exception:
                pass
        self._saved_values = {}
        self.repo_var.set(str(self._find_repo()))
        self._render_config()
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

        repo = self.repo_var.get().strip() or str(self._find_repo())
        if not (Path(repo) / "windows" / "build.ps1").is_file():
            messagebox.showwarning(
                T("ERROR"),
                f"windows/build.ps1 not found under:\n{repo}",
            )
            return

        # 写配置
        cfg = {"repo_root": repo, "fields": fields}
        cfg_path = HERE / "build_config.json"
        log_path = HERE / "build.log"
        self.log_path = str(log_path)

        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)

        # 清空旧日志
        with open(log_path, "w", encoding="utf-8") as f:
            f.write("=== DroidVM build started ===\n")

        self._clear_log()
        self._set_status(T("NEED_ADMIN"))
        self.building = True
        self.build_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")

        # 清 PID
        if PID_FILE.exists():
            PID_FILE.unlink()

        # 用 PowerShell Start-Process -Verb RunAs 提权启动 run_build.py
        arglist = [
            str(HERE / "run_build.py"),
            "--config", str(cfg_path),
            "--log", str(log_path),
            "--repo", repo,
        ]
        args_ps = ",".join(self._quote_ps(a) for a in arglist)
        ps_cmd = (
            f"Start-Process -FilePath {self._quote_ps(sys.executable)} "
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
    def _clear_log(self):
        self.log_box.configure(state="normal")
        self.log_box.delete("0.0", "end")
        self.log_box.configure(state="disabled")

    def _log(self, text, tag=None):
        self.log_box.configure(state="normal")
        self.log_box.insert("end", text + "\n")
        if tag:
            # CTkTextbox 目前不支持 tag，只能简单高亮颜色
            pass
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _poll_log(self):
        if self.building and self.log_path and os.path.exists(self.log_path):
            try:
                with open(self.log_path, "r", encoding="utf-8", errors="replace") as f:
                    # 只读取增量
                    f.seek(self._last_log_pos)
                    chunk = f.read()
                    self._last_log_pos = f.tell()
                    if chunk:
                        self.log_box.configure(state="normal")
                        self.log_box.insert("end", chunk)
                        # 简单错误高亮：包含 ERROR/FAIL 的行加红（CTkTextbox 无 tag，用文本前缀）
                        self.log_box.see("end")
                        self.log_box.configure(state="disabled")
                        if "=== EXIT CODE:" in chunk:
                            # 结束
                            self.building = False
                            self.build_btn.configure(state="normal")
                            self.stop_btn.configure(state="disabled")
                            try:
                                code_line = [ln for ln in chunk.splitlines() if "=== EXIT CODE:" in ln][-1]
                                code = int(code_line.split(":")[-1].strip().split()[0])
                                if code == 0:
                                    self._set_status(T("BUILD_DONE").format(code))
                                else:
                                    self._set_status(T("BUILD_FAIL").format(code))
                            except Exception:
                                self._set_status(T("BUILD_DONE").format("?"))
            except Exception:
                pass
        else:
            self._last_log_pos = 0

        self.after(250, self._poll_log)

    # ---------- 事件 ----------
    def _on_lang_change(self, lang):
        # 切语言前先把当前用户输入暂存，避免重渲染后丢失
        self._saved_values = self._collect_fields()
        set_lang(lang)
        self.title(T("APP_TITLE"))
        self._set_status(T("STATUS_READY"))
        self._render_config()
        self._relabel_ui()

    def _on_theme_change(self, theme_name):
        mapping = {T("SYSTEM"): "System", T("LIGHT"): "Light", T("DARK"): "Dark"}
        ctk.set_appearance_mode(mapping.get(theme_name, "System"))

    def _relabel_ui(self):
        # 标签页名称需要重建 tabview 才能改，这里只更新按钮文字
        self.build_btn.configure(text=T("BUILD"))
        self.stop_btn.configure(text=T("STOP"))
        self.save_btn.configure(text=T("SAVE_DEFAULTS"))
        self.load_btn.configure(text=T("LOAD_DEFAULTS"))
        self.reset_btn.configure(text=T("RESET"))
        self.about_btn.configure(text=T("ABOUT"))

    def _show_about(self):
        messagebox.showinfo(T("ABOUT"), T("ABOUT_TEXT"))


def main():
    app = DroidVMBuilderApp()
    app.mainloop()


if __name__ == "__main__":
    main()

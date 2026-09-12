# -*- coding: utf-8 -*-
"""
droidvm_builder_gui.py — DroidVM Win11 ARM64 镜像构建器 图形界面 (Python + Tkinter)

特性:
  - 零依赖（仅 Python 标准库：tkinter / subprocess / json / ctypes / threading）
  - 表单字段由 schema.py 自动生成，与 windows/build.ps1 的环境变量一一对应
  - 平台检测：Windows → 调 windows/build.ps1；macOS/Linux 路线占位（后续阶段）
  - 构建时按需 UAC 提权（GUI 自身不强制提权），实时 tail 日志文件
  - 中/英文切换、默认配置存取、重置

运行:
    python droidvm_builder_gui.py
"""
import ctypes
import json
import os
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import schema  # noqa: E402
import i18n  # noqa: E402
from i18n import T, set_lang, get_lang  # noqa: E402

DEFAULTS_FILE = os.path.join(HERE, "droidvm_gui_defaults.json")


def is_admin():
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except Exception:
        return False


def auto_detect_repo():
    """从本脚本位置向上找包含 windows/build.ps1 的目录。"""
    d = HERE
    for _ in range(6):
        if os.path.isfile(os.path.join(d, "windows", "build.ps1")):
            return d
        parent = os.path.dirname(d)
        if parent == d:
            break
        d = parent
    # 兜底：看看工作区里解压出来的那份
    cand = os.path.join(os.path.dirname(HERE), "Windows-ARM64-DroidVM-Builder",
                        "Windows-ARM64-DroidVM-Builder")
    if os.path.isfile(os.path.join(cand, "windows", "build.ps1")):
        return cand
    return os.getcwd()


class App:
    def __init__(self, root):
        self.root = root
        self.vars = {}          # key -> tk var
        self.text_widgets = {}  # key -> Text (textarea 类型)
        self.repo_var = tk.StringVar()
        self.lang_var = tk.StringVar(value=get_lang())
        self.build_proc = None
        self.tail_thread = None
        self.tail_stop = threading.Event()
        self.building = False

        self.repo_var.set(auto_detect_repo())
        self._load_defaults_into_vars()
        self._build_ui()
        self._refresh_texts()

    # ---------------- UI 构建 ----------------
    def _build_ui(self):
        self.root.minsize(760, 560)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        # 顶部工具栏
        top = ttk.Frame(self.root)
        top.grid(row=0, column=0, sticky="ew", padx=8, pady=6)
        top.columnconfigure(1, weight=1)

        self.status = ttk.Label(top, text=T("STATUS_READY"), anchor="w")
        self.status.grid(row=0, column=0, sticky="w")

        lang_frame = ttk.Frame(top)
        lang_frame.grid(row=0, column=2, sticky="e")
        ttk.Label(lang_frame, text=T("LANG") + ":").pack(side="left")
        self.lang_cb = ttk.Combobox(lang_frame, textvariable=self.lang_var,
                                    values=["zh-CN", "en-US"], width=8,
                                    state="readonly")
        self.lang_cb.pack(side="left", padx=(4, 0))
        self.lang_cb.bind("<<ComboboxSelected>>", self._on_lang)

        # 笔记本（配置 / 日志）
        self.nb = ttk.Notebook(self.root)
        self.nb.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 6))
        self.root.rowconfigure(1, weight=1)

        self._build_config_tab()
        self._build_log_tab()

        # 底部操作栏
        bar = ttk.Frame(self.root)
        bar.grid(row=2, column=0, sticky="ew", padx=8, pady=(0, 8))
        for i in range(6):
            bar.columnconfigure(i, weight=1 if i == 0 else 0)

        self.build_btn = ttk.Button(bar, text=T("BUILD"), command=self.on_build)
        self.build_btn.grid(row=0, column=0, sticky="ew", padx=2)
        self.stop_btn = ttk.Button(bar, text=T("STOP"), command=self.on_stop,
                                   state="disabled")
        self.stop_btn.grid(row=0, column=1, sticky="ew", padx=2)
        ttk.Button(bar, text=T("SAVE_DEFAULTS"),
                   command=self.on_save_defaults).grid(row=0, column=2, sticky="ew", padx=2)
        ttk.Button(bar, text=T("LOAD_DEFAULTS"),
                   command=self.on_load_defaults).grid(row=0, column=3, sticky="ew", padx=2)
        ttk.Button(bar, text=T("RESET"),
                   command=self.on_reset).grid(row=0, column=4, sticky="ew", padx=2)
        ttk.Button(bar, text=T("ABOUT"),
                   command=self.on_about).grid(row=0, column=5, sticky="ew", padx=2)

    def _build_config_tab(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text=T("TAB_CONFIG"))
        tab.rowconfigure(1, weight=1)
        tab.columnconfigure(0, weight=1)

        # 仓库根目录
        rf = ttk.Frame(tab)
        rf.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        rf.columnconfigure(1, weight=1)
        ttk.Label(rf, text=T("REPO_ROOT") + ":").grid(row=0, column=0, sticky="w")
        ttk.Entry(rf, textvariable=self.repo_var).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(rf, text=T("BROWSE"),
                   command=lambda: self._browse_dir(self.repo_var)).grid(row=0, column=2, padx=4)

        # 滚动区
        canvas = tk.Canvas(tab, highlightthickness=0)
        canvas.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        vsb = ttk.Scrollbar(tab, orient="vertical", command=canvas.yview)
        vsb.grid(row=1, column=1, sticky="ns")
        canvas.configure(yscrollcommand=vsb.set)
        inner = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        self._render_fields(inner)

    def _render_fields(self, parent, override=None):
        """在 parent（单列的 inner frame）里按 section 渲染所有字段。
        每个字段包在独立子 Frame 中，避免列布局冲突。
        override: 可选 {key: value} 覆盖保存值/默认值。"""
        self.vars.clear()
        self.text_widgets.clear()
        parent.columnconfigure(0, weight=1)
        row = 0
        defaults = schema.default_values()
        saved = self._saved_fields() if override is None else {}
        base = dict(defaults)
        base.update(saved)
        if override:
            base.update(override)

        for sec in schema.SECTION_ORDER:
            ttk.Label(parent, text=schema.section_title(sec),
                      font=("Segoe UI", 11, "bold")).grid(
                row=row, column=0, sticky="w", pady=(10, 2))
            row += 1
            for (_s, key, typ, _d, _env, help_) in schema.FIELDS:
                if _s != sec:
                    continue
                val = base.get(key, defaults[key])
                self._render_field(parent, row, key, typ, val, help_, saved)
                row += 1

    def _render_field(self, parent, row, key, typ, val, help_, saved):
        frm = ttk.Frame(parent)
        frm.grid(row=row, column=0, sticky="ew", padx=4, pady=2)
        frm.columnconfigure(1, weight=1)
        ttk.Label(frm, text=key, width=22, anchor="w").grid(row=0, column=0, sticky="w")
        help_ = help_ or ""

        if typ == "bool":
            v = tk.BooleanVar(value=(str(val).lower() in ("1", "true", "yes", "on")))
            ttk.Checkbutton(frm, variable=v).grid(row=0, column=1, sticky="w")
            self.vars[key] = v
            if help_:
                ttk.Label(frm, text=help_, foreground="gray").grid(
                    row=0, column=2, sticky="w", padx=6)
            return

        if typ == "textarea":
            v = tk.StringVar(value=val)
            txt = tk.Text(frm, height=3, width=50, wrap="word")
            txt.insert("1.0", val)
            txt.grid(row=0, column=1, columnspan=2, sticky="ew")
            self.vars[key] = v
            self.text_widgets[key] = txt
            if help_:
                ttk.Label(frm, text=help_, foreground="gray").grid(
                    row=1, column=1, columnspan=2, sticky="w")
            return

        if typ == "int":
            v = tk.StringVar(value=val)
            ttk.Entry(frm, textvariable=v, width=16).grid(row=0, column=1, sticky="w")
        elif typ in ("file", "save", "path_or_url"):
            v = tk.StringVar(value=val)
            ttk.Entry(frm, textvariable=v).grid(row=0, column=1, sticky="ew")
            if typ == "save":
                cmd = lambda vv=v: self._browse_save(vv)
            else:
                cmd = lambda vv=v: self._browse_file(vv)
            ttk.Button(frm, text=T("BROWSE"), command=cmd).grid(
                row=0, column=2, padx=4)
        else:  # text
            v = tk.StringVar(value=val)
            ttk.Entry(frm, textvariable=v).grid(
                row=0, column=1, columnspan=2, sticky="ew")

        self.vars[key] = v
        if help_:
            ttk.Label(frm, text=help_, foreground="gray").grid(
                row=0, column=3, sticky="w", padx=6)

    def _build_log_tab(self):
        tab = ttk.Frame(self.nb)
        self.nb.add(tab, text=T("TAB_LOG"))
        tab.rowconfigure(0, weight=1)
        tab.columnconfigure(0, weight=1)
        self.log_txt = tk.Text(tab, wrap="none", state="disabled",
                               font=("Consolas", 10))
        self.log_txt.grid(row=0, column=0, sticky="nsew", padx=6, pady=6)
        sb = ttk.Scrollbar(tab, orient="vertical", command=self.log_txt.yview)
        sb.grid(row=0, column=1, sticky="ns")
        self.log_txt.configure(yscrollcommand=sb.set)
        self.log_txt.insert("1.0", T("LOG_HINT"))

    # ---------------- 配置读写 ----------------
    def _saved_fields(self):
        try:
            with open(DEFAULTS_FILE, "r", encoding="utf-8") as f:
                d = json.load(f)
            return d.get("fields", {})
        except Exception:
            return {}

    def _load_defaults_into_vars(self):
        # 仅初始化 repo_var 之外的字段在 _render_fields 内完成
        pass

    def collect_fields(self):
        out = {}
        for (_s, key, typ, _d, _env, _h) in schema.FIELDS:
            if typ == "bool":
                out[key] = self.vars[key].get()
            elif typ == "textarea":
                out[key] = self.text_widgets[key].get("1.0", "end-1c").strip()
            else:
                out[key] = self.vars[key].get().strip()
        return out

    def on_save_defaults(self):
        data = {"repo_root": self.repo_var.get(), "fields": self.collect_fields()}
        with open(DEFAULTS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        self.status.config(text=T("SAVED"))

    def on_load_defaults(self):
        data = {}
        try:
            with open(DEFAULTS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            pass
        if data.get("repo_root"):
            self.repo_var.set(data["repo_root"])
        self._rerender_with(data.get("fields", {}))
        self.status.config(text=T("LOADED"))

    def on_reset(self):
        self._rerender_with({})
        self.status.config(text=T("RESET_DONE"))

    def _rerender_with(self, fields):
        """重渲染配置页（语言切换/重置/载入默认时），套用给定字段值。"""
        cfg_tab = self.nb.tabs()[0]
        frame = self.nb.nametowidget(cfg_tab)
        for child in list(frame.children.values()):
            child.destroy()
        frame.rowconfigure(1, weight=1)
        frame.columnconfigure(0, weight=1)

        # 仓库根目录
        rf = ttk.Frame(frame)
        rf.grid(row=0, column=0, sticky="ew", padx=6, pady=6)
        rf.columnconfigure(1, weight=1)
        ttk.Label(rf, text=T("REPO_ROOT") + ":").grid(row=0, column=0, sticky="w")
        ttk.Entry(rf, textvariable=self.repo_var).grid(row=0, column=1, sticky="ew", padx=4)
        ttk.Button(rf, text=T("BROWSE"),
                   command=lambda: self._browse_dir(self.repo_var)).grid(row=0, column=2, padx=4)

        # 滚动区
        canvas = tk.Canvas(frame, highlightthickness=0)
        canvas.grid(row=1, column=0, sticky="nsew", padx=6, pady=(0, 6))
        vsb = ttk.Scrollbar(frame, orient="vertical", command=canvas.yview)
        vsb.grid(row=1, column=1, sticky="ns")
        canvas.configure(yscrollcommand=vsb.set)
        inner = ttk.Frame(canvas)
        canvas.create_window((0, 0), window=inner, anchor="nw")
        inner.bind("<Configure>", lambda e: canvas.configure(scrollregion=canvas.bbox("all")))

        self._render_fields(inner, override=fields)

    # ---------------- 构建 ----------------
    def on_build(self):
        if self.building:
            return
        if sys.platform != "win32":
            messagebox.showinfo(T("APP_TITLE"), T("NOT_WIN"))
            return
        fields = self.collect_fields()
        if not fields.get("SRC_ISO"):
            messagebox.showerror(T("APP_TITLE"), T("NO_ISO"))
            return
        repo = self.repo_var.get().strip()
        if not os.path.isfile(os.path.join(repo, "windows", "build.ps1")):
            messagebox.showerror(T("APP_TITLE"),
                                 "windows/build.ps1 not found under:\n" + repo)
            return

        # 写配置 + 日志路径
        cfg_path = os.path.join(HERE, "build_config.json")
        log_path = os.path.join(HERE, "build.log")
        with open(cfg_path, "w", encoding="utf-8") as f:
            json.dump({"repo_root": repo, "fields": fields}, f,
                      indent=2, ensure_ascii=False)

        self._reset_log()
        self.nb.select(1)  # 切到日志页
        self.log(T("NEED_ADMIN") if not is_admin() else "")
        self.log(">>> python run_build.py --config build_config.json --log build.log")
        self.log("")

        # 启动（按需提权）
        self.building = True
        self.build_btn.config(state="disabled")
        self.stop_btn.config(state="normal")
        self.status.config(text=T("BUILDING"))

        if is_admin():
            self.build_proc = subprocess.Popen(
                [sys.executable, os.path.join(HERE, "run_build.py"),
                 "--config", cfg_path, "--log", log_path, "--repo", repo],
                cwd=HERE)
            self._monitor(self.build_proc)
        else:
            # 提权启动 run_build.py，GUI 仅 tail 日志
            ps = ('powershell -NoProfile -Command "Start-Process -FilePath \\"{0}\\" '
                  '-ArgumentList \\"{1} --config {2} --log {3} --repo {4}\\" -Verb RunAs"'
                  ).format(sys.executable, os.path.join(HERE, "run_build.py"),
                           cfg_path, log_path, repo)
            subprocess.Popen(ps, shell=True)
            # 提权进程不可直接 wait，用轮询日志判断
            self._monitor_elevated(log_path)

        self._start_tail(log_path)

    def _monitor(self, proc):
        def _run():
            proc.wait()
            self._finish(proc.returncode)
        threading.Thread(target=_run, daemon=True).start()

    def _monitor_elevated(self, log_path):
        # 提权模式下，靠日志末尾的 EXIT CODE 行判断结束
        def _run():
            import time
            while not self.tail_stop.is_set():
                try:
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                    if "=== EXIT CODE:" in content:
                        code = content.split("=== EXIT CODE:")[1].split("===")[0].strip()
                        self._finish(int(code))
                        return
                except Exception:
                    pass
                time.sleep(1.0)
        threading.Thread(target=_run, daemon=True).start()

    def _finish(self, code):
        self.building = False
        self.build_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        msg = T("BUILD_DONE").format(code) if code == 0 else T("BUILD_FAIL").format(code)
        self.status.config(text=msg)
        self.log("")
        self.log(msg)

    def on_stop(self):
        if self.build_proc and self.build_proc.poll() is None:
            try:
                self.build_proc.terminate()
            except Exception:
                pass
        self.tail_stop.set()
        self.building = False
        self.build_btn.config(state="normal")
        self.stop_btn.config(state="disabled")
        self.status.config(text=T("STOP"))
        self.log("\n[stop] build interrupted by user")

    # ---------------- 日志 tail ----------------
    def _reset_log(self):
        self.log_txt.config(state="normal")
        self.log_txt.delete("1.0", "end")
        self.log_txt.config(state="disabled")

    def log(self, text):
        self.log_txt.config(state="normal")
        self.log_txt.insert("end", text + "\n")
        self.log_txt.see("end")
        self.log_txt.config(state="disabled")

    def _start_tail(self, log_path):
        self.tail_stop.clear()
        last = 0

        def _run():
            while not self.tail_stop.is_set():
                try:
                    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(last)
                        chunk = f.read()
                        if chunk:
                            last = f.tell()
                            self.root.after(0, self._append_log, chunk)
                except Exception:
                    pass
                import time
                time.sleep(0.3)
        threading.Thread(target=_run, daemon=True).start()

    def _append_log(self, text):
        self.log_txt.config(state="normal")
        for line in text.splitlines(keepends=True):
            lowered = line.lower()
            if any(k in lowered for k in ("error", "fail", "throw", "exception",
                                          "❌", "not found", "denied")):
                self.log_txt.insert("end", line, "err")
            elif any(k in lowered for k in ("done", "finished", "成功", "完成")):
                self.log_txt.insert("end", line, "ok")
            else:
                self.log_txt.insert("end", line)
        self.log_txt.see("end")
        self.log_txt.config(state="disabled")

    # ---------------- 杂项 ----------------
    def _browse_file(self, var):
        p = filedialog.askopenfilename()
        if p:
            var.set(p)

    def _browse_save(self, var):
        p = filedialog.asksaveasfilename(defaultextension=".qcow2",
                                         filetypes=[("qcow2", "*.qcow2")])
        if p:
            var.set(p)

    def _browse_dir(self, var):
        p = filedialog.askdirectory()
        if p:
            var.set(p)

    def _on_lang(self, *_):
        set_lang(self.lang_var.get())
        # 重渲染并保留当前值
        self._rerender_with(self.collect_fields())
        self._refresh_texts()
        # 日志页标签更新
        self.nb.tab(1, text=T("TAB_LOG"))

    def _refresh_texts(self):
        self.root.title(T("APP_TITLE"))
        self.status.config(text=T("STATUS_READY") if not self.building else self.status.cget("text"))
        self.build_btn.config(text=T("BUILD"))
        self.stop_btn.config(text=T("STOP"))
        self.nb.tab(0, text=T("TAB_CONFIG"))
        self.nb.tab(1, text=T("TAB_LOG"))

    def on_about(self):
        messagebox.showinfo(T("ABOUT"), T("ABOUT_TEXT"))


def main():
    root = tk.Tk()
    # 日志配色 tag
    try:
        root.tk.call("ttk::style", "theme", "use", "vista")
    except Exception:
        pass
    app = App(root)
    app.log_txt.tag_config("err", foreground="#c00000")
    app.log_txt.tag_config("ok", foreground="#1a7a1a")
    root.mainloop()


if __name__ == "__main__":
    main()

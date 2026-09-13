# -*- coding: utf-8 -*-
"""
run_build.py — 被提权后执行构建的“无界面运行器”

GUI 把当前配置写成 json，再用 UAC 提权启动本脚本:
    python run_build.py --config cfg.json --log build.log

本脚本:
  1. 读取配置
  2. 把字段注入环境变量
  3. 调用 gui/builder/windows/build.ps1（随程序自带的构建引擎，无需仓库根目录）
  4. 把 stdout/stderr 实时写入 --log 指定的日志文件（GUI 会 tail 这个文件）

退出码透传给日志末尾的 === EXIT CODE: n === 行。
"""
import argparse
import json
import os
import subprocess
import sys

# 让本脚本能 import 同目录的 schema（即便被提权后 cwd 变化）
HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

from schema import build_env  # noqa: E402

PID_FILE = os.path.join(HERE, "build.pid")

# 随程序自带的构建引擎（windows/build.ps1 及其同级文件）
BUILDER_DIR = os.path.join(HERE, "builder")
BUILD_PS1 = os.path.join(BUILDER_DIR, "windows", "build.ps1")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--log", required=True)
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # 写入 PID，方便 GUI 停止
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    if not os.path.isfile(BUILD_PS1):
        with open(args.log, "w", encoding="utf-8") as log:
            log.write("ERROR: bundled build engine not found: %s\n" % BUILD_PS1)
        return 2

    env = dict(os.environ)
    env.update(build_env(cfg.get("fields", {})))
    # 确保子进程输出 UTF-8
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONLEGACYWINDOWSSTDIO"] = "utf-8"

    with open(args.log, "w", encoding="utf-8") as log:
        log.write("=== DroidVM build started ===\n")
        log.write("bundled build engine=%s\n" % BUILD_PS1)
        log.write("env vars injected: %s\n" % ", ".join(sorted(build_env(cfg.get("fields", {})).keys())))
        log.write("===========================\n\n")

        # 开工前预检：清理残留的临时 VHDX 挂载、释放被占用的盘符
        # （只针对 DroidVM 自己的 w11.vhdx，绝不触碰真实磁盘）
        PREFLIGHT_PS1 = os.path.join(HERE, "preflight.ps1")
        if os.path.isfile(PREFLIGHT_PS1):
            try:
                pf = subprocess.run(
                    ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                     "-File", PREFLIGHT_PS1],
                    cwd=HERE, capture_output=True, text=True,
                )
                for line in (pf.stdout or "").splitlines():
                    log.write("[preflight] " + line + "\n")
                if pf.stderr:
                    for line in pf.stderr.splitlines():
                        log.write("[preflight!] " + line + "\n")
                log.flush()
            except Exception as e:
                log.write("[preflight] skipped: %s\n" % e)
                log.flush()

        log.flush()

        try:
            proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", BUILD_PS1],
                env=env,
                cwd=BUILDER_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
            )
        except Exception as e:
            log.write("FAILED to launch build.ps1: %s\n" % e)
            return 3

        # 实时把子进程输出写进日志（GUI 会 tail）
        while True:
            raw = proc.stdout.readline()
            if not raw:
                break
            try:
                s = raw.decode("utf-8", "replace")
            except Exception:
                s = raw.decode("cp936", "replace")
            log.write(s)
            log.flush()

        proc.wait()
        log.write("\n=== EXIT CODE: %d ===\n" % proc.returncode)
        return proc.returncode


if __name__ == "__main__":
    sys.exit(main())

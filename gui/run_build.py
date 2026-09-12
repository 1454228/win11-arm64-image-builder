# -*- coding: utf-8 -*-
"""
run_build.py — 被提权后执行构建的“无界面运行器”

GUI 把当前配置写成 json，再用 UAC 提权启动本脚本:
    python run_build.py --config cfg.json --log build.log --repo <repo_root>

本脚本:
  1. 读取配置
  2. 把字段注入环境变量
  3. 调用 windows/build.ps1（已提权，故 build.ps1 内部的自提权分支不会触发，输出可捕获）
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


def find_build_ps1(repo):
    cand = os.path.join(repo, "windows", "build.ps1")
    if os.path.isfile(cand):
        return cand
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--log", required=True)
    ap.add_argument("--repo", default=None)
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)

    # 写入 PID，方便 GUI 停止
    try:
        with open(PID_FILE, "w", encoding="utf-8") as f:
            f.write(str(os.getpid()))
    except Exception:
        pass

    repo = args.repo or cfg.get("repo_root") or "."
    build_ps1 = find_build_ps1(repo)
    if not build_ps1:
        with open(args.log, "w", encoding="utf-8") as log:
            log.write("ERROR: windows/build.ps1 not found under repo root: %s\n" % repo)
        return 2

    env = dict(os.environ)
    env.update(build_env(cfg.get("fields", {})))
    # 确保子进程输出 UTF-8
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONLEGACYWINDOWSSTDIO"] = "utf-8"

    with open(args.log, "w", encoding="utf-8") as log:
        log.write("=== DroidVM build started ===\n")
        log.write("repo=%s\n" % repo)
        log.write("build.ps1=%s\n" % build_ps1)
        log.write("env vars injected: %s\n" % ", ".join(sorted(build_env(cfg.get("fields", {})).keys())))
        log.write("===========================\n\n")
        log.flush()

        try:
            proc = subprocess.Popen(
                ["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass",
                 "-File", build_ps1],
                env=env,
                cwd=repo,
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

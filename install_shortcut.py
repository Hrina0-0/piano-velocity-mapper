#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
创建桌面快捷方式
================

生成 `桌面/钢琴力度重映射.lnk`，目标是 **pythonw.exe**（无窗口版 Python）
+ app.py，所以双击不会出现黑色终端框。

候选安装位置（按顺序找，找到即用）：
    1. 便携版安装目录  %LOCALAPPDATA%\\Programs\\PianoVelocityMapper
    2. 项目目录下的 .venv（日常开发/就地安装）

用法:
    python install_shortcut.py                # 自动选位置
    python install_shortcut.py --portable     # 强制用便携版目录
    python install_shortcut.py --project      # 强制用项目目录
    python install_shortcut.py --start        # 顺便建开始菜单快捷方式
"""

from __future__ import annotations

import os
import subprocess
import sys

APP_DIR = os.path.dirname(os.path.abspath(__file__))
SHORTCUT_NAME = "钢琴力度重映射"
SOURCES = ("app.py", "gui.py", "engine.py", "velocity_mapper.py",
           "webui.py", "webui.html", "app.ico", "aa.py", "tray.py",
           "midi_ports.py", "paths.py")

PORTABLE_DIR = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs",
                            "PianoVelocityMapper")

PS_TEMPLATE = r"""
$ErrorActionPreference = 'Stop'
$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut([IO.Path]::Combine([Environment]::GetFolderPath('{folder}'), '{name}.lnk'))
$sc.TargetPath = '{target}'
$sc.Arguments = '"{args}"'
$sc.WorkingDirectory = '{workdir}'
$sc.IconLocation = '{icon},0'
$sc.Description = '钢琴力度重映射：把键盘力度实时重映射到 1-127，经 loopMIDI 输出'
$sc.Save()
Write-Output 'OK'
"""


def _candidate_dirs(force: str | None = None):
    """返回 [(app_dir, pythonw), ...]，按优先级排列。"""
    out = []
    portable_app = os.path.join(PORTABLE_DIR, "app")
    portable_pyw = os.path.join(PORTABLE_DIR, ".venv", "Scripts", "pythonw.exe")
    project_pyw = os.path.join(APP_DIR, ".venv", "Scripts", "pythonw.exe")

    if force != "project" and os.path.exists(portable_pyw) and \
            os.path.isfile(os.path.join(portable_app, "app.py")):
        out.append((portable_app, portable_pyw))
    if force != "portable" and os.path.exists(project_pyw):
        out.append((APP_DIR, project_pyw))
    if force == "portable" and not out:
        out.append((portable_app, portable_pyw))
    return out


def resolve(force: str | None = None) -> tuple:
    for app_dir, pyw in _candidate_dirs(force):
        missing = [s for s in SOURCES if not os.path.exists(os.path.join(app_dir, s))]
        if missing:
            continue
        return app_dir, pyw
    raise SystemExit(
        "找不到可用的安装（缺少 pythonw.exe 或源文件）。\n"
        "请先双击 setup.cmd 完成安装后再运行本脚本。")


def create(folder: str = "desktop", force: str | None = None) -> tuple:
    """folder: 'desktop' | 'startmenu'；返回 (app_dir, pythonw)。"""
    app_dir, pyw = resolve(force)
    if not os.path.exists(os.path.join(app_dir, "app.ico")):
        print("提示: app.ico 不存在，快捷方式将使用默认图标。", file=sys.stderr)

    script = PS_TEMPLATE.format(
        folder={"desktop": "Desktop", "startmenu": "StartMenu"}[folder],
        name=SHORTCUT_NAME,
        target=pyw,
        args=os.path.join(app_dir, "app.py"),
        workdir=app_dir,
        icon=os.path.join(app_dir, "app.ico"),
    )
    proc = subprocess.run(["powershell", "-NoProfile", "-NonInteractive",
                           "-Command", script], capture_output=True, text=True)
    if "OK" not in (proc.stdout or ""):
        raise SystemExit(f"创建快捷方式失败:\n{proc.stdout}\n{proc.stderr}")
    return app_dir, pyw


def shortcut_path(folder: str = "desktop") -> str:
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command",
         f"[Environment]::GetFolderPath('{ {'desktop':'Desktop','startmenu':'StartMenu'}[folder] }')"],
        capture_output=True, text=True)
    return os.path.join((proc.stdout or "").strip(), SHORTCUT_NAME + ".lnk")


if __name__ == "__main__":
    force = ("portable" if "--portable" in sys.argv else
             "project" if "--project" in sys.argv else None)
    app_dir, pyw = create("desktop", force)
    print("已创建桌面快捷方式:", shortcut_path("desktop"))
    print("  指向:", os.path.join(app_dir, "app.py"))
    if "--start" in sys.argv:
        create("startmenu", force)
        print("已创建开始菜单快捷方式:", shortcut_path("startmenu"))

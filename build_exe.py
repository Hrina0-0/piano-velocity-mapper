#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
打包成绿色版单文件 exe
======================

    python build_exe.py

产物：dist/PianoVelocityMapper.exe（双击即用，对方不需要装 Python）

几个要点：
- --onefile：单文件，解压即用
- --windowed：不出黑色控制台窗口
- config.json / ui_prefs.json 写在 **exe 旁边**（见 paths.data_dir），
  所以用户改的力度、端口不会被临时目录吃掉
- app.ico 既做 exe 图标，也作为数据打包进去（运行时给窗口设图标用）
- mido 的后端要显式 hiddenimports，否则打包后找不到 pygame 后端
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
APP_NAME = "PianoVelocityMapper"


def main() -> int:
    if not os.path.isdir(os.path.join(HERE, ".venv", "Scripts")):
        print("没有 .venv，请先跑 setup.cmd 或手动建虚拟环境。")
        return 1

    py = os.path.join(HERE, ".venv", "Scripts", "python.exe")
    icon = os.path.join(HERE, "app.ico")

    for d in ("build", "dist"):
        shutil.rmtree(os.path.join(HERE, d), ignore_errors=True)

    cmd = [
        py, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", APP_NAME,
        "--icon", icon,
        "--distpath", os.path.join(HERE, "dist"),
        "--workpath", os.path.join(HERE, "build"),
        "--specpath", os.path.join(HERE, "build"),
        # mido / pygame 的后端要显式带上
        "--hidden-import", "mido.backends.pygame",
        "--hidden-import", "mido.backends.rtmidi",
        "--hidden-import", "pygame.midi",
        "--hidden-import", "pygame.gfxdraw",
        # 运行时需要的只读资源
        "--add-data", f"{os.path.join(HERE, 'webui.html')}{os.pathsep}.",
        "--add-data", f"{icon}{os.pathsep}.",
        # 用不到的大块头，排除掉能小好几 MB
        "--exclude-module", "numpy",
        "--exclude-module", "PIL",
        "--exclude-module", "pytest",
        "--exclude-module", "setuptools",
        "--exclude-module", "pip",
        os.path.join(HERE, "app.py"),
    ]

    print("打包中（第一次会慢一点）…")
    proc = subprocess.run(cmd, cwd=HERE)
    if proc.returncode != 0:
        print("打包失败。")
        return proc.returncode

    exe = os.path.join(HERE, "dist", APP_NAME + ".exe")
    if not os.path.isfile(exe):
        print("没有生成 exe。")
        return 1
    size = os.path.getsize(exe) / 1024 / 1024
    print(f"完成：{exe}  ({size:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())

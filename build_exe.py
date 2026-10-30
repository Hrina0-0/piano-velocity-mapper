#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
打包成单文件 exe
================

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

# CI runner 的控制台代码页可能是 cp1252，直接 print 中文会抛 UnicodeEncodeError。
# 统一把 stdout 换成「UTF-8 + 出错时替换」，保证任何环境下都能跑完。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - 老 Python 没有 reconfigure
    pass


def _python_exe() -> str:
    """用哪个 Python 打包。

    优先用「当前正在跑的这个解释器」——本地是 .venv\Scripts\python.exe，
    GitHub Actions 上是 runner 自带的 python（依赖已经装在它里面了）。
    不要再强制要求 .venv 存在，否则 CI 一定挂。
    """
    return sys.executable or "python"


def main() -> int:
    py = _python_exe()
    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("当前 Python 里没有 PyInstaller。\n"
              "请先执行：pip install pyinstaller")
        return 1

    icon = os.path.join(HERE, "app.ico")
    if not os.path.isfile(icon):
        print(f"找不到图标：{icon}")
        return 1

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

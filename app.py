#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
桌面版入口（一键启动，无控制台窗口）
===================================

配合桌面快捷方式使用：快捷方式指向 .venv\\Scripts\\pythonw.exe + 本文件，
因此双击不会弹出黑色终端窗口。

本文件只做三件事：
1. 把项目目录放进 sys.path（双击时工作目录可能不是项目目录）
2. 依赖缺失时自动调用 setup.cmd 装一次依赖
3. 兜底错误提示——pythonw 没有控制台，异常必须用弹窗显示出来
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import traceback
from typing import Optional

from paths import data_dir as _data_dir, resource_dir as _resource_dir

# 可写数据（setup.cmd 等）：打包后 = exe 所在目录
APP_DIR = _data_dir()
# 只读资源（图标）：打包后 = exe 解包的临时目录
RES_DIR = _resource_dir()
SETUP_CMD = os.path.join(APP_DIR, "setup.cmd")


def _message(title: str, body: str, kind: str = "error") -> None:
    """pythonw 下唯一的报错出口：弹窗。"""
    import tkinter as tk
    from tkinter import messagebox

    win = tk.Tk()
    win.withdraw()
    try:
        win.iconbitmap(default=os.path.join(RES_DIR, "app.ico"))
    except Exception:
        pass
    fn = messagebox.showerror if kind == "error" else messagebox.showinfo
    fn(title, body)
    win.destroy()


def _ensure_deps() -> bool:
    """确保 mido 可用；缺失则跑一次 setup.cmd（后台线程 + 进度弹窗）。"""
    try:
        import mido  # noqa: F401
        import pygame  # noqa: F401
        return True
    except ImportError:
        pass

    if not os.path.exists(SETUP_CMD):
        _message("缺少依赖", "未找到 setup.cmd，且 Python 依赖（mido / pygame）未安装。\n\n"
                            f"请手动在项目目录执行：\n{SETUP_CMD}")
        return False

    import tkinter as tk

    win = tk.Tk()
    win.withdraw()
    try:
        win.iconbitmap(default=os.path.join(RES_DIR, "app.ico"))
    except Exception:
        pass

    state = {"ok": False}

    def worker() -> None:
        try:
            # setup.cmd 是 .bat，必须经 cmd.exe 跑；不用 CREATE_NO_WINDOW，
            # 因为第一次安装需要让用户看到进度。
            subprocess.run(["cmd", "/c", SETUP_CMD], cwd=APP_DIR, check=True)
        except Exception:
            pass
        try:
            import mido  # noqa: F401
            import pygame  # noqa: F401
            state["ok"] = True
        except Exception:
            state["ok"] = False

    from tkinter import messagebox
    messagebox.showinfo("首次运行", "正在安装运行依赖（仅需一次，需要联网，可能要十几秒）…")
    t = threading.Thread(target=worker, daemon=True)
    t.start()
    while t.is_alive():
        win.update()
        t.join(0.05)
    win.destroy()

    if not state["ok"]:
        _message("安装失败", "依赖安装没有成功。\n\n"
                            "请检查网络后，双击项目目录里的 setup.cmd 手动安装，"
                            "或确认已安装 Python 3.10+。")
    return state["ok"]


def _parse_args(argv: list) -> dict:
    """极简参数解析：--config PATH（便携安装时指向 ProgramData 里的配置）。"""
    opts: dict = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg == "--config" and i + 1 < len(argv):
            opts["config"] = argv[i + 1]
            i += 2
        elif arg.startswith("--config="):
            opts["config"] = arg.split("=", 1)[1]
            i += 1
        else:
            i += 1
    return opts


def main(argv: Optional[list] = None) -> int:
    if RES_DIR not in sys.path:
        sys.path.insert(0, RES_DIR)

    if not _ensure_deps():
        return 1

    opts = _parse_args(list(sys.argv[1:] if argv is None else argv))

    try:
        import gui
        return gui.main(opts.get("config"))
    except SystemExit as exc:  # velocity_mapper._setup_backend 找不到后端时 exit(2)
        code = exc.code if isinstance(exc.code, int) else 1
        _message("无法启动", "没有可用的 MIDI 后端。\n\n"
                            "请双击 setup.cmd 重新安装依赖后再试。")
        return code
    except Exception:
        _message("启动失败",
                 "程序启动时出错：\n\n" + traceback.format_exc(limit=6) +
                 "\n\n可双击 setup.cmd 重新安装依赖。")
        return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
程序路径：区分「只读资源目录」和「可写数据目录」

==========================================================

打包成单个 exe（PyInstaller）以后，这两件事必须分开：

- 只读资源（app.ico、webui.html）在 exe 解包出来的临时目录里；
- 可写数据（config.json、ui_prefs.json）必须放在 **exe 旁边**，
  否则程序一退出，改动就跟着临时目录一起没了。

所以：
    resource_dir()  -> 只读资源（图标、网页）
    data_dir()      -> 可写数据（配置、偏好）
源码运行时两者是同一个目录。
"""

from __future__ import annotations

import os
import sys


def is_frozen() -> bool:
    """是不是被 PyInstaller 打包过了。"""
    return bool(getattr(sys, "frozen", False))


def resource_dir() -> str:
    """只读资源所在目录。"""
    if is_frozen():
        return getattr(sys, "_MEIPASS",
                       os.path.dirname(os.path.abspath(sys.executable)))
    return os.path.dirname(os.path.abspath(__file__))


def data_dir() -> str:
    """可写数据所在目录：打包后 = exe 所在目录。"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.abspath(__file__))

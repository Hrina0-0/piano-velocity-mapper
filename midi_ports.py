#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
虚拟 MIDI 端口管家
==================

为什么需要这个模块
------------------
力度重映射要接在「键盘 → 本工具 → DAW」中间，所以本工具的**输出**必须
是一个**虚拟 MIDI 端口**（硬件端口是独占的，DAW 抢不到）。

Windows 上只有安装了虚拟 MIDI 驱动才有这种端口，最常见的是 loopMIDI
（Tobias Erichsen 版，靠 teVirtualMIDI 驱动工作）。而这个驱动/程序**不能
随便再分发**（作者明确要求书面授权），所以本项目的做法是：

1. 检测系统里有没有可用的虚拟输出端口；
2. 没有的话，**自动把对方自己已经安装的 loopMIDI 拉起来**（只是启动本机
   已安装的程序，不是分发）；
3. 确实没有安装，就给出清楚的指引，而不是让用户对着日志猜。

对外接口：
    ports = list_outputs()              # 当前所有 MIDI 输出端口名
    vm = find_virtual_output()          # 猜一个虚拟输出端口（找不到返回 None）
    exe = find_loopmidi_exe()           # 找本机已安装的 loopMIDI
    ok = start_loopmidi(exe)            # 拉起它，等端口出现
    name, note = ensure_output_port("loopMIDI Port")   # 一站式处理
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import List, Optional, Tuple

# --------------------------------------------------------------------------- #
# 虚拟端口的判定
# --------------------------------------------------------------------------- #

# 端口名里出现这些片段，基本可以断定是「虚拟 MIDI 端口」而不是真实硬件口
_VIRTUAL_HINTS = (
    "loopmidi",
    "bome",
    "vmp",
    "virtual midi",
    "midi yoke",
    "loopbe",
    "ntmidi",
    "midi virtual",
    "midi bus",
    "midi bridge",
    "midi thru",
    "midi splitter",
)

# 这些是 Windows 自带的输出，不是我们要的「虚拟端口」
_EXCLUDE_HINTS = (
    "microsoft midi mapper",
    "microsoft gs wavetable",
    "microsoft gs wavetable synth",
)

# loopMIDI 可能安装在哪
_LOOPMIDI_PATHS = (
    r"C:\Program Files (x86)\Tobias Erichsen\loopMIDI\loopMIDI.exe",
    r"C:\Program Files\Tobias Erichsen\loopMIDI\loopMIDI.exe",
    r"C:\Program Files (x86)\loopMIDI\loopMIDI.exe",
    r"C:\Program Files\loopMIDI\loopMIDI.exe",
)


# --------------------------------------------------------------------------- #
# 端口枚举
# --------------------------------------------------------------------------- #

def _mido():
    """拿到 mido 模块（后端已由 velocity_mapper 设好）。"""
    import mido  # noqa: WPS433 - 运行时导入，避免加载期就要求 mido
    return mido


def list_outputs() -> List[str]:
    """当前系统所有 MIDI 输出端口名。"""
    try:
        return list(_mido().get_output_names())
    except Exception:
        return []


def list_inputs() -> List[str]:
    """当前系统所有 MIDI 输入端口名。"""
    try:
        return list(_mido().get_input_names())
    except Exception:
        return []


def looks_virtual(name: str) -> bool:
    """这个端口名看起来是虚拟 MIDI 端口吗？"""
    low = (name or "").strip().lower()
    if not low:
        return False
    if any(x in low for x in _EXCLUDE_HINTS):
        return False
    return any(x in low for x in _VIRTUAL_HINTS)


def find_virtual_output() -> Optional[str]:
    """挑一个最像样��虚拟输出端口。

    优先级：名字里带 "loopmidi" 的（用户最可能已经装好的那个）> 其它虚拟端口。
    """
    outs = list_outputs()
    virtual = [n for n in outs if looks_virtual(n)]
    if not virtual:
        return None
    for name in virtual:
        if "loopmidi" in name.lower():
            return name
    return virtual[0]


# --------------------------------------------------------------------------- #
# 找 loopMIDI
# --------------------------------------------------------------------------- #

def _bundled_loopmidi() -> Optional[str]:
    """包里自带的 loopMIDI（如果打包时放了的话）。

    注意：只有在你确认过对方的许可条款、决定随包分发时才应该放这个文件；
    正常情况下这里返回 None，走「用对方自己装的」那条路。
    """
    here = os.path.dirname(os.path.abspath(__file__))
    for rel in (r"tools\loopMIDI\loopMIDI.exe", r"loopMIDI\loopMIDI.exe"):
        path = os.path.join(here, rel)
        if os.path.isfile(path):
            return path
    return None


def find_loopmidi_exe() -> Optional[str]:
    """找本机已安装的 loopMIDI 可执行文件。

    顺序：包里自带的 > 卸载注册表记录的安装位置 > 常见安装路径。
    """
    bundled = _bundled_loopmidi()
    if bundled:
        return bundled

    # --- 卸载注册表 ---
    if os.name == "nt":
        try:
            import winreg  # noqa: WPS433
            roots = (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER)
            sub = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"
            for root in roots:
                for view in (0,  # KEY_WOW64_64KEY
                             0x1000):  # KEY_WOW64_32KEY
                    try:
                        key = winreg.OpenKey(root, sub, 0, winreg.KEY_READ | view)
                    except OSError:
                        continue
                    try:
                        i = 0
                        while True:
                            try:
                                name = winreg.EnumKey(key, i)
                            except OSError:
                                break
                            i += 1
                            try:
                                sub_key = winreg.OpenKey(key, name)
                            except OSError:
                                continue
                            try:
                                try:
                                    disp = str(winreg.QueryValueEx(
                                        sub_key, "DisplayName")[0])
                                except OSError:
                                    continue
                                if "loopmidi" not in disp.lower():
                                    continue
                                loc = ""
                                try:
                                    loc = str(winreg.QueryValueEx(
                                        sub_key, "InstallLocation")[0])
                                except OSError:
                                    loc = ""
                                candidates = []
                                if loc:
                                    candidates.append(os.path.join(
                                        loc, "loopMIDI.exe"))
                                candidates.append(os.path.join(
                                    loc or os.path.dirname(loc),
                                    "loopMIDI.exe"))
                                for path in candidates:
                                    if path and os.path.isfile(path):
                                        return path
                            finally:
                                sub_key.Close()
                    finally:
                        key.Close()
        except Exception:
            pass

    # --- 常见路径 ---
    for path in _LOOPMIDI_PATHS:
        if os.path.isfile(path):
            return path
    return None


def start_loopmidi(exe: Optional[str] = None) -> bool:
    """拉起 loopMIDI，等它的虚拟端口出现。

    返回 True 表示端口已经出现。失败只写日志、不抛异常。
    """
    exe = exe or find_loopmidi_exe()
    if not exe or not os.path.isfile(exe):
        return False

    before = set(list_outputs())
    try:
        flags = 0
        if os.name == "nt":
            flags = getattr(subprocess, "DETACHED_PROCESS", 0)
        subprocess.Popen([exe], close_fds=True, creationflags=flags)
    except Exception:
        return False

    # loopMIDI 起来后端口要等一会儿才出现，最多等 10 秒
    for _ in range(25):
        time.sleep(0.4)
        now = set(list_outputs())
        if now - before:
            return True
        if find_virtual_output():
            return True
    return False


# --------------------------------------------------------------------------- #
# 一站式处理
# --------------------------------------------------------------------------- #

def ensure_output_port(preferred: str = "") -> Tuple[Optional[str], str]:
    """确保有一个能用的输出端口。

    返回 (端口名, 提示信息)。端口名为 None 表示没搞定，提示信息说明原因。

    流程：
      1. 配置里的端口还在 -> 直接用；
      2. 配置里的端口没了，但系统里有别的虚拟端口 -> 改用那个；
      3. 都没有，但对方装了 loopMIDI -> 自动拉起它，等端口出现；
      4. 还没有 -> 返回 None，由调用方弹窗指引。
    """
    preferred = (preferred or "").strip()

    # 1. 配置的端口还在
    if preferred:
        outs = list_outputs()
        if preferred in outs:
            return preferred, ""
        low = preferred.lower()
        for name in outs:
            if low in name.lower():
                return name, ""

    # 2. 换用别的虚拟端口
    vm = find_virtual_output()
    if vm:
        return vm, f"原来的输出端口不存在，已自动改用「{vm}」。"

    # 3. 自动拉起 loopMIDI
    exe = find_loopmidi_exe()
    if exe and start_loopmidi(exe):
        vm = find_virtual_output()
        if vm:
            return vm, "已自动启动 loopMIDI。"

    # 4. 没搞定
    if exe:
        return None, ("检测不到虚拟 MIDI 输出端口。已找到本机的 loopMIDI"
                      f"（{exe}），但没能自动启动，请手动打开它并确认已创建端口。")
    return None, ("检测不到虚拟 MIDI 输出端口。本工具需要借助一个虚拟 MIDI 端口"
                  "把重映射后的力度转发给 DAW（常见做法是装 loopMIDI）。"
                  "本机没有找到 loopMIDI，请先安装并打开它。")


if __name__ == "__main__":
    # 自测：python midi_ports.py
    print("outputs:", list_outputs())
    print("virtual:", find_virtual_output())
    print("loopmidi:", find_loopmidi_exe())
    name, note = ensure_output_port(sys.argv[1] if len(sys.argv) > 1 else "")
    print("result:", name, "|", note)

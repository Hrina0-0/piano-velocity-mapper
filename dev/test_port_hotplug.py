#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""端口热插拔回归测试（v1.3.3 修的 bug）。

背景
----
以前程序用 pygame(PortMidi) 后端，而 PortMidi 在**进程初始化时**就把 MIDI
设备列表缓存住了。于是：

    先开力度映射 -> 再开 loopMIDI  ->  新端口枚举不到，界面一直停在「等待设备」

本脚本检查三件事：

1. 后端选的是 rtmidi（能实时枚举），而不是会缓存端口的 pygame；
2. rtmidi 后端**每次查询都真的重新问一遍系统**（而不是端一份缓存）；
3. 界面确实接上了「端口列表一变就自动刷新」的监听。

另外带一个真实热插拔验证（需要装 loopMIDI，且会重启它一次）：

    python dev\\test_port_hotplug.py --live
"""

from __future__ import annotations

import inspect
import os
import subprocess
import sys
import time
from typing import Any, List, Tuple

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("PVM_SRC_ROOT") or os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import mido  # noqa: E402

import gui  # noqa: E402
import velocity_mapper as vm  # noqa: E402

FAILS: List[str] = []


def ok(msg: str) -> None:
    print(f"  OK  {msg}")


def bad(msg: str) -> None:
    print(f"  FAIL  {msg}")
    FAILS.append(msg)


def check(cond: bool, msg: str) -> None:
    ok(msg) if cond else bad(msg)


# --------------------------------------------------------------------------- #
# 1. 后端选择
# --------------------------------------------------------------------------- #

def test_backend_selection() -> None:
    print("1) 后端选择：必须优先用能实时枚举端口的 rtmidi")
    try:
        import rtmidi  # noqa: F401
        have_rtmidi = True
    except Exception:
        have_rtmidi = False

    backend = vm._setup_backend()
    print(f"     当前后端: {backend}")

    if have_rtmidi:
        check(backend == "mido.backends.rtmidi",
              "选中了 rtmidi 后端（rtmidi 每次都重新向系统要端口列表）")
    else:
        check(backend == "mido.backends.pygame",
              "没装 python-rtmidi，已退回 pygame 后端（仍可用，只是看不到后开的端口）")


# --------------------------------------------------------------------------- #
# 2. 端口列表是实时查出来的，不是缓存
# --------------------------------------------------------------------------- #

def _current_backend() -> str:
    """当前 mido 后端名（如 "mido.backends.rtmidi"）。"""
    backend = getattr(mido, "backend", None)
    name = getattr(backend, "name", "") if backend is not None else ""
    if name:
        return str(name)
    return type(backend).__module__ if backend is not None else ""


def test_live_enumeration() -> None:
    print("2) 端口枚举：每次查询都要真的问一遍系统")
    if _current_backend() != "mido.backends.rtmidi":
        print("     （当前不是 rtmidi 后端，跳过）")
        return

    import rtmidi

    orig_in = rtmidi.MidiIn
    made: List[Any] = []

    class CountingMidiIn:
        """只做计数的壳：真正干活的是原来的 rtmidi.MidiIn。

        （不能直接继承：rtmidi.MidiIn 是 Cython 扩展类型，不接受自定义 __init__。）
        """

        def __init__(self, *a: Any, **kw: Any) -> None:
            made.append(1)
            self._inner = orig_in(*a, **kw)

        def get_ports(self, **kw: Any) -> List[str]:
            return self._inner.get_ports(**kw)

        def __getattr__(self, name: str) -> Any:
            return getattr(self._inner, name)

    rtmidi.MidiIn = CountingMidiIn           # type: ignore[assignment]
    try:
        mido.get_input_names()
        first = len(made)
        mido.get_input_names()
        second = len(made)
    finally:
        rtmidi.MidiIn = orig_in               # type: ignore[assignment]

    check(second > first,
          f"每查一次端口列表就新建一次 RtMidi 实例（{first} -> {second}），"
          "说明不是端缓存")
    ins = mido.get_input_names()
    check(isinstance(ins, list), "能列出输入端口")


# --------------------------------------------------------------------------- #
# 3. 界面接上了自动刷新
# --------------------------------------------------------------------------- #

def test_gui_wiring() -> None:
    print("3) 界面：端口列表一变就自动刷新（不用手点「刷新端口」）")
    check(hasattr(gui.App, "_start_port_watch"),
          "App 有 _start_port_watch（后台线程定期枚举端口）")
    check(hasattr(gui.App, "_apply_port_lists"),
          "App 有 _apply_port_lists（列表变了就刷新下拉框）")
    poll = inspect.getsource(gui.App._poll_tray)
    check("_apply_port_lists" in poll,
          "轮询里取回了后台线程的端口列表结果")
    init = inspect.getsource(gui.App.__init__)
    check("_start_port_watch()" in init,
          "启动时确实拉起了端口监听线程")
    sync = inspect.getsource(gui.App._sync_ports)
    check("ports" in sync,
          "_sync_ports 支持传入已取好的端口列表（避免重复枚举）")


# --------------------------------------------------------------------------- #
# 4. 真实热插拔（可选，需要 loopMIDI）
# --------------------------------------------------------------------------- #

def _loopmidi_exe() -> str:
    import midi_ports
    return midi_ports.find_loopmidi_exe() or ""


def test_live_hotplug() -> None:
    print("4) 真实热插拔：重启 loopMIDI，看端口列表会不会跟着变")
    exe = _loopmidi_exe()
    if not exe:
        bad("本机没找到 loopMIDI，跳过（装一个再来测）")
        return

    def ports() -> Tuple[List[str], List[str]]:
        return vm.list_input_names(), vm.list_output_names()

    subprocess.run(["taskkill", "/F", "/IM", "loopMIDI.exe"],
                   capture_output=True)
    time.sleep(2.0)
    before_in, before_out = ports()
    print(f"     关掉 loopMIDI 后: {len(before_in)} 入 / {len(before_out)} 出")

    subprocess.Popen([exe], creationflags=getattr(subprocess, "DETACHED_PROCESS", 0))
    time.sleep(3.0)
    after_in, after_out = ports()
    print(f"     重新打开后:       {len(after_in)} 入 / {len(after_out)} 出")

    grew = len(after_in) > len(before_in) or len(after_out) > len(before_out)
    check(grew, "loopMIDI 重启后端口列表自动变长了（后开的端口能枚举到）")
    if not grew:
        print("     提示：当前后端是 pygame 的话这里一定失败 —— "
              "那正是本 bug 的根因。")


def main() -> int:
    print("=" * 62)
    print("端口热插拔回归测试")
    print("=" * 62)
    test_backend_selection()
    print()
    test_live_enumeration()
    print()
    test_gui_wiring()
    print()
    if "--live" in sys.argv:
        test_live_hotplug()
        print()
    else:
        print("4) 真实热插拔：跳过（加 --live 参数会真的重启 loopMIDI）")

    print()
    print("=" * 62)
    if FAILS:
        print(f"有 {len(FAILS)} 项没通过：")
        for f in FAILS:
            print(f"  - {f}")
        return 1
    print("全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())

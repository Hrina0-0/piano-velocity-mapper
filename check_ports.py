#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""环境体检：把 MIDI 端口情况打印出来（给 setup.cmd 用）。

刻意只输出 ASCII —— setup.cmd 是 .bat，cmd.exe 按控制台代码页解析，
中文会破坏行解析，所以这里全用英文 + 纯 ASCII。
"""
from __future__ import annotations

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)


def main() -> int:
    print("")
    print("---- MIDI ports ----")
    try:
        import velocity_mapper as vm
        vm._setup_backend()
        import midi_ports as mp
    except Exception as exc:
        print(f"  (cannot read MIDI ports: {exc})")
        return 0

    try:
        outs = mp.list_outputs()
        ins = mp.list_inputs()
    except Exception as exc:
        print(f"  (cannot read MIDI ports: {exc})")
        return 0

    print(f"  outputs ({len(outs)}):")
    for name in outs:
        tag = "  <== virtual" if mp.looks_virtual(name) else ""
        print(f"    - {name}{tag}")
    print(f"  inputs ({len(ins)}):")
    for name in ins:
        print(f"    - {name}")

    virtual = mp.find_virtual_output()
    if virtual:
        print("")
        print(f"  OK: virtual output port found -> '{virtual}'")
        print("       Pick this one as the OUTPUT port in the app,")
        print("       and pick the SAME one as the DAW's MIDI input.")
    else:
        print("")
        print("  !! No virtual MIDI output port found.")
        print("     This app forwards remapped velocity to your DAW through a")
        print("     VIRTUAL MIDI port (loopMIDI is the usual choice).")
        print("     1) Install loopMIDI (free for personal use):")
        print("        https://www.tobias-erichsen.de/software/loopmidi.html")
        print("     2) Open loopMIDI, right-click its tray icon -> Add Port")
        print("     3) Start this app; it will find the port automatically.")
        print("     (The app also starts loopMIDI by itself if it is installed.)")

    exe = mp.find_loopmidi_exe()
    if exe:
        print("")
        print(f"  loopMIDI found at: {exe}")
    print("")
    return 0


if __name__ == "__main__":
    sys.exit(main())

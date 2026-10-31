#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""下拉框在各种「端口不存在」场景下的显示测试。

模拟三种情况：
  1) 这台机器一个 MIDI 输入口都没有
  2) 配置里记着一个不存在的端口（朋友那台就是这种：Digital Piano）
  3) 一切正常
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.environ.get("PVM_SRC_ROOT") or os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import tkinter as tk  # noqa: E402

import gui  # noqa: E402


def main() -> None:
    root = tk.Tk()
    root.title("Dropdown state test")
    root.geometry("620x420+180+120")
    root.configure(bg=gui.CARD)

    def show(label, values, value):
        tk.Label(root, text=label, bg=gui.CARD, fg=gui.TEXT_DIM,
                 font=gui.F(9), anchor="w").pack(fill="x", padx=24, pady=(14, 2))
        dd = gui.Dropdown(root, values, value, lambda v: print("picked:", v))
        dd.pack(fill="x", padx=24)
        return dd

    show("① 一台机器没有任何 MIDI 输入端口",
         [], "")
    show("② 配置里记着的端口在这台机器上不存在",
         ["Microsoft MIDI Mapper", "Microsoft GS Wavetable Synth"], "Digital Piano")
    show("③ 输出端口没找到（没装 loopMIDI）",
         ["Microsoft MIDI Mapper", "Microsoft GS Wavetable Synth"], "loopMIDI Port")
    show("④ 正常",
         ["Digital Piano", "Microsoft MIDI Mapper"], "Digital Piano")

    root.mainloop()


if __name__ == "__main__":
    main()

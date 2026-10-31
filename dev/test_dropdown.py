#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Dropdown 下拉列表的滚轮/滚动复现台。

用法：python dev/test_dropdown.py
它会用 gui.py 里真实的 Dropdown 类，灌入 20 个假端口，
方便直接用鼠标滚轮测试「能不能滚」。
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
    root.title("Dropdown scroll test")
    root.geometry("560x460+160+120")
    root.configure(bg=gui.CARD)

    vals = [f"测试端口 {i:02d} · MIDI Device" for i in range(20)]
    dd = gui.Dropdown(root, vals, vals[0], lambda v: print("picked:", v))
    dd.pack(padx=24, pady=70)

    hint = tk.Label(root, text="滚轮测试：展开后滚动鼠标滚轮", bg=gui.CARD,
                    fg=gui.TEXT_DIM, font=gui.F(9))
    hint.pack()

    def report(_e=None):
        print(f"offset={dd._offset} rows_shown={dd._rows_shown} "
              f"values={len(dd._values)} popup={dd._popup is not None}")

    root.after(1200, report)
    root.mainloop()


if __name__ == "__main__":
    main()

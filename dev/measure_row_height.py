#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测量 Dropdown 里那一行的真实高度，和代码里硬编码的 ROW_H=30 对比。

背景：Dropdown 用 ROW_H=30 这个「估计值」去算浮层高度和能显示几行，
但行高其实由字体 + pady 决定。两边对不上，浮层就会比实际内容矮，
最后一行被裁掉（用户看到的「loopMIDI 藏在最下面」）。
"""
import tkinter as tk

root = tk.Tk()
root.withdraw()

dpi = root.winfo_fpixels("1i")
scale = dpi / 96.0
print(f"屏幕 DPI = {dpi:.1f}   缩放系数 _S = {scale:.3f}")

UI_FONT = "Microsoft YaHei UI"


def px(n):
    return max(1, int(round(n * scale)))


for size in (9, 10):
    f = (UI_FONT, size)
    lbl = tk.Label(root, text="测试一行 Xy", font=f,
                   padx=px(10), pady=px(6), anchor="w")
    lbl.pack()
    root.update_idletasks()
    h = lbl.winfo_reqheight()
    pady = px(6)
    print(f"字体 {size} 号  pady={pady}  实际行高 reqheight={h}px"
          f"   代码里 ROW_H 假设 = {px(30)}px"
          f"   差 {h - px(30):+d}px/行")

root.destroy()

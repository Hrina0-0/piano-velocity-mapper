"""临时预览用：把「缺少虚拟 MIDI 端口」的引导窗口显示出来。

根窗口要保持可见（真实场景里 master 是主窗口），否则 transient 窗口
在 withdrawn master 下可能不显示。
"""
import tkinter as tk

import gui

root = tk.Tk()
root.title("预览用主窗口")
root.geometry("400x120+400+300")
tk.Label(root, text="这是主窗口（预览用）").pack(pady=40)
root.update()

gui.PortGuideDialog(
    root,
    "检测不到虚拟 MIDI 输出端口。本工具需要借助一个虚拟 MIDI 端口"
    "把重映射后的力度转发给 DAW（常见做法是装 loopMIDI）。"
    "本机没有找到 loopMIDI，请先安装并打开它。",
)
root.mainloop()

#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
钢琴力度重映射 · 桌面界面
===========================

tkinter 自绘的深色扁平界面，取代浏览器控制台：
- 无控制台窗口（用 pythonw.exe 启动），双击桌面快捷方式即用
- 所有功能都在界面里：最大力度、曲线、端口、实时力度、日志
- 滑块改动即时生效并自动保存到 config.json，不需要「应用」按钮
- UI 只读取 engine 的只读快照并按 ~40fps 轮询，MIDI 在后台线程处理，互不阻塞
- 曲线/力度条只在「真正变化」时重绘，静止时零 CPU 开销

依赖：仅 Python 标准库（tkinter / threading / ctypes）。
"""

from __future__ import annotations

import contextlib
import ctypes
import io
import json
import os
import queue
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
from ctypes import wintypes
from typing import Any, Callable, Dict, List, Optional, Tuple

import aa

# --------------------------------------------------------------------------- #
# DPI 感知：必须在创建 Tk 窗口之前设置，否则界面会糊
# --------------------------------------------------------------------------- #

def _enable_dpi_awareness() -> None:
    if os.name != "nt":
        return
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # SYSTEM_DPI_AWARE
        return
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


_enable_dpi_awareness()

import engine as eng_mod  # noqa: E402
import velocity_mapper as vm  # noqa: E402

from paths import data_dir as _data_dir, resource_dir as _resource_dir
from i18n import get_lang, set_lang, tr, trf

# 只读资源（图标等）：打包后 = exe 解包的临时目录
SCRIPT_DIR = _resource_dir()
# 可写数据（config.json / ui_prefs.json）：打包后放 exe 旁边，改动才不会丢
DATA_DIR = _data_dir()

# --------------------------------------------------------------------------- #
# 主题
# --------------------------------------------------------------------------- #

BG = "#eef1f5"          # 窗口底色
CARD = "#ffffff"        # 卡片
CARD_HI = "#f1f4f8"     # 按钮 / 输入框
CARD_DOWN = "#e2e7ee"   # 悬停
BORDER = "#e3e8ef"      # 卡片描边
BORDER_HI = "#c2cad6"   # 坐标轴 / 强描边
TRACK = "#dfe4ec"       # 滑轨 / 进度条

TEXT = "#1d2129"        # 主文字
TEXT_DIM = "#5b6472"    # 次要文字
TEXT_FADE = "#98a1ae"   # 辅助文字 / 单位

ACCENT = "#2b7cf0"      # 主色
ACCENT_DIM = "#dbe8fc"  # 下拉选中行底色
ACCENT_DOWN = "#1f66d0"
GOOD = "#0f9d58"
WARN = "#c67c00"
BAD = "#dc3d3d"

UI_FONT = "Microsoft YaHei UI"
MONO_FONT = "Consolas"

NOTE_NAMES = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")

_S = 1.0  # 像素缩放系数（DPI > 96 时放大）


def px(n: float) -> int:
    """按 DPI 缩放的像素值。"""
    return max(1, int(round(n * _S)))


def F(size: int = 9, weight: str = "normal", mono: bool = False) -> tuple:
    fam = MONO_FONT if mono else UI_FONT
    return (fam, size, weight) if weight != "normal" else (fam, size)


def note_name(n: int) -> str:
    return f"{NOTE_NAMES[n % 12]}{n // 12 - 1}"


def _first_real_port(names: List[str]) -> str:
    """从端口列表里挑第一个「真实设备」（跳过虚拟 MIDI 口）。

    首次运行时用来自动选输入口：虚拟口（例如 loopMIDI Port）不该被
    当成键盘接进来。
    """
    if not names:
        return ""
    try:
        import midi_ports
    except Exception:
        return names[0]
    for n in names:
        try:
            if not midi_ports.looks_virtual(n):
                return n
        except Exception:
            return names[0]
    return names[0]


# --------------------------------------------------------------------------- #
# 窗口外观（深色标题栏 / 圆角 / 应用图标）
# --------------------------------------------------------------------------- #

def _hwnd_of(win: tk.Misc) -> int:
    """取 Tk 窗口对应的顶层 HWND（必须用 c_void_p 声明，否则 64 位下会截断）。"""
    user32 = ctypes.windll.user32
    user32.GetParent.argtypes = [ctypes.c_void_p]
    user32.GetParent.restype = ctypes.c_void_p
    child = ctypes.c_void_p(win.winfo_id())
    parent = user32.GetParent(child)
    return int(parent) if parent else int(child)


def _apply_window_chrome(win: tk.Misc) -> None:
    """Win11 圆角。必须在窗口显示之后调用才生效。

    界面是亮色主题，所以标题栏保持系统默认的浅色外观（不做任何覆盖）。
    """
    if os.name != "nt":
        return
    try:
        dwm = ctypes.windll.dwmapi
        dwm.DwmSetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_int,
                                              ctypes.c_void_p, ctypes.c_uint]
        dwm.DwmSetWindowAttribute.restype = ctypes.c_long
        corner = ctypes.c_int(2)      # DWMWA_WINDOW_CORNER_PREFERENCE -> 圆角
        dwm.DwmSetWindowAttribute(_hwnd_of(win), 33, ctypes.byref(corner), 4)
    except Exception:
        pass


def _apply_icon(win: tk.Misc, ico_path: str) -> None:
    """用 app.ico 设置窗口图标（任务栏 / Alt-Tab / 弹窗）。失败无副作用。"""
    if os.name != "nt" or not os.path.exists(ico_path):
        return
    try:
        user32 = ctypes.windll.user32
        user32.LoadImageW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_uint,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_uint]
        user32.LoadImageW.restype = ctypes.c_void_p
        user32.SendMessageW.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.c_size_t,
                                         ctypes.c_void_p]
        user32.SendMessageW.restype = ctypes.c_void_p
        hwnd = ctypes.c_void_p(_hwnd_of(win))
        hinst = ctypes.windll.kernel32.GetModuleHandleW(None)
        LR_LOADFROMFILE, IMAGE_ICON, WM_SETICON = 0x00000010, 1, 0x0080
        for size, icon_id in ((32, 1), (16, 0), (48, 2)):
            hicon = user32.LoadImageW(hinst, ico_path, IMAGE_ICON, size, size,
                                      LR_LOADFROMFILE)
            if hicon:
                user32.SendMessageW(hwnd, WM_SETICON, icon_id, ctypes.c_void_p(hicon))
    except Exception:
        pass


# --------------------------------------------------------------------------- #
# 基础控件
# --------------------------------------------------------------------------- #

class Card(tk.Frame):
    """1px 边框卡片：外层画边框色，内层填内容色。

    注意：标题与 body 各自装在独立容器里，body 初始为空，
    因此调用方可以在 body 内自由地统一使用 grid 或 pack。
    """

    def __init__(self, master: tk.Misc, title: str = "", pad: int = 16, **kw: Any) -> None:
        super().__init__(master, bg=BORDER, **kw)
        inner = tk.Frame(self, bg=CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        if title:
            head = tk.Frame(inner, bg=CARD)
            head.pack(fill="x", padx=px(pad), pady=(px(pad), 0))
            tk.Label(head, text=title, bg=CARD, fg=TEXT_DIM,
                     font=F(9, "bold"), anchor="w").pack(anchor="w")
            self.body = tk.Frame(inner, bg=CARD)
            self.body.pack(fill="both", expand=True, padx=px(pad),
                           pady=(px(12), px(pad)))
        else:
            self.body = tk.Frame(inner, bg=CARD)
            self.body.pack(fill="both", expand=True, padx=px(pad), pady=px(pad))


class FlatButton(tk.Label):
    """无边框扁平按钮（用 Label 手工配色，比 ttk 更好控制）。"""

    _PALETTE = {
        "normal": (CARD_HI, CARD_DOWN, TEXT),
        "accent": (ACCENT, ACCENT_DOWN, "#ffffff"),
        "ghost": (CARD, CARD_HI, TEXT_DIM),
    }

    def __init__(self, master: tk.Misc, text: str,
                 command: Optional[Callable[[], None]] = None,
                 kind: str = "normal", font: Optional[tuple] = None, **kw: Any) -> None:
        bg, active, fg = self._PALETTE.get(kind, self._PALETTE["normal"])
        self._bg, self._active, self._fg = bg, active, fg
        self._disabled = False
        super().__init__(master, text=text, bg=bg, fg=fg, font=font or F(9),
                         cursor="hand2", padx=px(12), pady=px(6), **kw)
        self._command = command
        self.bind("<Button-1>", self._on_press)
        self.bind("<ButtonRelease-1>", self._on_release)
        self.bind("<Leave>", lambda _e: self.configure(bg=self._bg) if not self._disabled else None)
        self.bind("<Enter>", lambda _e: self.configure(bg=self._active, fg=self._fg)
                  if not self._disabled else None)

    def _on_press(self, _e: Any) -> None:
        if not self._disabled and self._command:
            self.configure(bg=self._active)

    def _on_release(self, e: Any) -> None:
        if self._disabled or not self._command:
            return
        inside = 0 <= e.x <= self.winfo_width() and 0 <= e.y <= self.winfo_height()
        self.configure(bg=self._bg)
        if inside:
            self._command()

    def set_enabled(self, on: bool) -> None:
        self._disabled = not on
        self.configure(bg=self._bg if on else CARD,
                       fg=self._fg if on else TEXT_FADE,
                       cursor="hand2" if on else "arrow")


class FlatCheck(tk.Frame):
    """自绘勾选框。

    原生 tk.Checkbutton 在浅色卡片上会露出系统灰底和方形边框，和界面
    其余部分的扁平风格对不上，所以这里用 Canvas 画方框 + 对勾。
    """

    def __init__(self, master: tk.Misc, text: str,
                 command: Optional[Callable[[], None]] = None, **kw: Any) -> None:
        super().__init__(master, bg=CARD, cursor="hand2", **kw)
        self._command = command
        self._value = False
        self._size = px(16)

        self._box = tk.Canvas(self, width=self._size, height=self._size,
                              bg=CARD, highlightthickness=0, bd=0,
                              cursor="hand2")
        self._box.pack(side="left", padx=(0, px(8)))
        self._label = tk.Label(self, text=text, bg=CARD, fg=TEXT_DIM, font=F(9),
                               cursor="hand2")
        self._label.pack(side="left")

        for widget in (self, self._box, self._label):
            widget.bind("<Button-1>", self._toggle)
            widget.bind("<Enter>", self._on_enter)
            widget.bind("<Leave>", self._on_leave)

        self._draw()

    # -- 状态 --------------------------------------------------------------- #

    def get(self) -> bool:
        """当前是否勾选。"""
        return self._value

    def set(self, value: bool) -> None:
        """设置勾选状态（不触发 command）。"""
        self._value = bool(value)
        self._draw()

    # -- 交互 --------------------------------------------------------------- #

    def _toggle(self, _e: Any = None) -> None:
        self._value = not self._value
        self._draw()
        if self._command:
            try:
                self._command()
            except Exception:
                pass

    def _on_enter(self, _e: Any = None) -> None:
        try:
            self._label.configure(fg=TEXT)
        except Exception:
            pass

    def _on_leave(self, _e: Any = None) -> None:
        try:
            self._label.configure(fg=TEXT_DIM)
        except Exception:
            pass

    # -- 绘制 --------------------------------------------------------------- #

    def _draw(self) -> None:
        c = self._box
        s = self._size
        c.delete("all")
        if self._value:
            c.create_rectangle(1, 1, s - 1, s - 1, fill=ACCENT, outline=ACCENT)
            c.create_line(s * 0.26, s * 0.52, s * 0.43, s * 0.70, s * 0.75, s * 0.32,
                          fill="#ffffff", width=max(1, px(2)),
                          capstyle="round", joinstyle="round")
        else:
            c.create_rectangle(1, 1, s - 1, s - 1, fill=CARD, outline=BORDER_HI,
                               width=max(1, px(1)))


class Slider(tk.Canvas):
    """自绘滑块：细轨道 + 圆钮，支持鼠标拖动与方向键微调。

    step 是刻度步长：值会被量化到 step 的整数倍（拖动时不会出现 1.237 这种值）。
    integer=True 时 step 固定为 1；否则可以给 0.1 这种小数步长。
    """

    def __init__(self, master: tk.Misc, lo: float, hi: float, value: float,
                 on_change: Callable[[float], None], width: int = 250, height: int = 26,
                 integer: bool = True, step: Optional[float] = None, **kw: Any) -> None:
        super().__init__(master, width=px(width), height=px(height), bg=CARD,
                         highlightthickness=0, bd=0, takefocus=1, cursor="hand2", **kw)
        self.lo, self.hi = lo, hi
        self.integer = integer
        self.step = float(step) if step else (1.0 if integer else 0.01)
        self._value = self._clamp(value)
        self._on_change = on_change
        self._dragging = False
        self.pad = px(10)
        self.bind("<Configure>", lambda _e: self._redraw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<ButtonRelease-1>", lambda _e: setattr(self, "_dragging", False))
        self.bind("<FocusIn>", lambda _e: self._redraw())
        self.bind("<FocusOut>", lambda _e: self._redraw())
        self.bind("<Left>", lambda _e: self._nudge(-1))
        self.bind("<Right>", lambda _e: self._nudge(1))
        self.bind("<Home>", lambda _e: self._step_to(self.lo))
        self.bind("<End>", lambda _e: self._step_to(self.hi))

    # ---- 值 ----
    def _clamp(self, v: float) -> float:
        if self.integer:
            v = int(round(v))
        else:
            # 量化到 step 的整数倍；+1e-9 避免 round() 的“银行家舍入”把 x.5 向下取
            v = round(round(v / self.step + 1e-9) * self.step, 6)
        return min(self.hi, max(self.lo, v))

    @property
    def value(self) -> float:
        return self._value

    def set(self, v: float, notify: bool = False) -> None:
        v = self._clamp(v)
        changed = v != self._value
        self._value = v
        self._redraw()
        if notify and changed:
            self._on_change(v)

    # ---- 交互 ----
    def _x_to_val(self, x: float) -> float:
        span = max(1, self.winfo_width() - 2 * self.pad)
        return self._clamp(self.lo + (x - self.pad) / span * (self.hi - self.lo))

    def _on_press(self, e: Any) -> None:
        self.focus_set()
        self._dragging = True
        self.set(self._x_to_val(e.x), notify=True)

    def _on_drag(self, e: Any) -> None:
        if self._dragging:
            self.set(self._x_to_val(e.x), notify=True)

    def _step_to(self, v: float) -> None:
        self.set(v, notify=True)

    def _nudge(self, direction: float) -> str:
        """方向键微调：一次一个 step。"""
        step = self.step * (1 if direction > 0 else -1)
        self.set(self._value + step, notify=True)
        return "break"

    # ---- 绘制 ----
    def _redraw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2:
            return
        cy = h // 2
        x0, x1 = self.pad, w - self.pad
        frac = 0.0 if self.hi == self.lo else (self._value - self.lo) / (self.hi - self.lo)
        cx = int(x0 + frac * (x1 - x0))

        th = max(4, px(5))
        self.create_line(x0, cy, x1, cy, fill=TRACK, width=th, capstyle="round")
        if cx > x0:
            self.create_line(x0, cy, cx, cy, fill=ACCENT, width=th, capstyle="round")
        r = px(9)
        focused = self.focus_get() is self
        # 手柄是圆：用抗锯齿位图画，否则边缘是阶梯状
        knob = aa.circle_photo(r, CARD, ACCENT if focused else BORDER_HI, px(2))
        self.create_image(cx, cy, image=knob)


class Dropdown(tk.Frame):
    """深色下拉选择器：点击弹出浮层列表（比 ttk.Combobox 好看且可完全控制配色）。

    MIDI 端口一多，列表就会比屏幕能放下的高度还长，靠后的几项直接跑到屏幕
    外面去，既看不见也点不着。这里做了四件事：
    1. 屏幕下方放不下就改为向上弹出；
    2. 还是放不下就只显示放得下的那几行；
    3. 剩下的行用滚轮 / 上下键 / PageUp·PageDown·Home·End / 右侧滚动条翻动；
    4. 行高按字体实测、行控件复用不重建 —— 滚动跟手、不闪、也不丢滚轮事件。
    """

    MAX_ROWS = 14        # 最多同时显示多少行（再按屏幕实际空间往下收缩）
    ROW_PAD_Y = 6        # 每行上下内边距
    SB_W = 7
    MIN_W = 150          # 浮层最小宽
    MAX_W = 460          # 浮层最大宽（端口名太长时也不至于看不全）

    def __init__(self, master: tk.Misc, values: List[str], value: str,
                 on_change: Callable[[str], None], width: int = 240,
                 height: int = 30, **kw: Any) -> None:
        super().__init__(master, bg=BORDER, width=px(width), height=px(height), **kw)
        self.pack_propagate(False)          # 尺寸由上面的 width/height 决定（像素）
        self._inner = tk.Frame(self, bg=CARD_HI)
        self._inner.pack(fill="both", expand=True)
        self._btn = tk.Label(self._inner, text="", bg=CARD_HI, fg=TEXT, font=F(9),
                             anchor="w", cursor="hand2", padx=px(10))
        self._btn.pack(side="left", fill="both", expand=True)
        self._arrow = tk.Label(self._inner, text="▾", bg=CARD_HI, fg=TEXT_FADE,
                               font=F(9), padx=px(9))
        self._arrow.pack(side="right", fill="y")

        self._values = list(values)
        self._value = value
        self._on_change = on_change
        self._popup: Optional[tk.Toplevel] = None
        self._offset = 0
        self._rows_shown = 0
        self._hover = -1
        self._body: Optional[tk.Frame] = None
        self._rows: List[tk.Label] = []     # 复用的行控件（滚动时只改内容，不重建）
        self._row_h = 0                     # 实测出来的行高
        self._wheel_acc = 0                 # 滚轮 delta 累积器
        self._sb: Optional[tk.Canvas] = None
        self._sb_needed = False
        self._sb_drag_from = 0
        self._sb_off_from = 0
        self._busy = False
        self._render()
        for w in (self, self._inner, self._btn, self._arrow):
            w.bind("<Button-1>", self._toggle)

    def _render(self) -> None:
        if not self._value:
            text, fg = tr("（无可用端口）", "(no ports available)"), TEXT_FADE
        elif self._values and self._value not in self._values:
            # 配置里记的端口在这台机器上根本不存在（换电脑 / 换键盘 /
            # 以前版本把作者自己的设备名写成了默认值）。
            # 别把它显示成一个正常选项 —— 标出来，用户才知道要重选。
            text = trf("⚠ {name} · 未找到", "⚠ {name} · not found", name=self._value)
            fg = WARN
        else:
            text, fg = self._value, TEXT
        self._btn.configure(text=text, fg=fg)

    def set_values(self, values: List[str], value: str) -> None:
        self._values = list(values)
        self._value = value
        self._render()

    def _toggle(self, _e: Any = None) -> None:
        # self / _inner / _btn / _arrow 都绑了同一个回调，而 Tk 不会把事件冒泡
        # 上去——但一次点击仍可能触发其中 2~3 个。用 _busy 只让第一次生效。
        if self._busy:
            return
        self._busy = True
        try:
            self._close() if self._popup else self._open()
        finally:
            self.after_idle(self._unbusy)

    def _unbusy(self) -> None:
        self._busy = False

    def _close(self) -> None:
        popup, self._popup = self._popup, None
        self._rows_shown = 0
        self._hover = -1
        self._body = None
        self._rows = []
        self._sb = None
        self._sb_needed = False
        if popup is not None:
            try:
                popup.grab_release()
                popup.destroy()
            except Exception:
                pass

    # ------------------------------------------------------------------ #
    # 浮层
    # ------------------------------------------------------------------ #

    def _measure_row_h(self) -> int:
        """量出这一行真实有多高。

        之前这里写死 30px，可 9 号字体的行 + 上下各 6px 内边距实际是 33px，
        于是浮层比内容矮 3px/行，9 行下来最后一行会被裁掉半截。
        """
        probe = tk.Label(self, text="Mg", font=F(9), anchor="w",
                         padx=px(10), pady=px(self.ROW_PAD_Y))
        try:
            h = probe.winfo_reqheight()
        finally:
            probe.destroy()
        return max(px(20), h)

    def _popup_width(self) -> int:
        """浮层宽度：至少跟控件一样宽，长端口名也尽量放得下。"""
        w = max(self.winfo_width(), px(self.MIN_W))
        try:
            font = tkfont.Font(font=F(9))
            need = max((font.measure(v) for v in self._values), default=0) + px(34)
            w = max(w, need)
        except Exception:
            pass
        return min(w, px(self.MAX_W))

    def _open(self) -> None:
        if not self._values:
            return
        popup = tk.Toplevel(self)
        popup.wm_overrideredirect(True)
        self._popup = popup

        row_h = self._measure_row_h()
        self._row_h = row_h
        self._wheel_acc = 0
        w = self._popup_width()
        x = self.winfo_rootx()
        screen_h = self.winfo_screenheight()

        # 先算「最多 MAX_ROWS 行」需要多高，再看屏幕下方放不放得下
        want_h = row_h * min(self.MAX_ROWS, len(self._values)) + px(4)
        below_y = self.winfo_rooty() + self.winfo_height() + px(2)
        space_below = screen_h - below_y
        space_above = self.winfo_rooty() - px(2)
        if space_below >= want_h or space_below >= space_above:
            y, avail = below_y, space_below       # 下方放得下就往下弹
        else:
            y = max(0, self.winfo_rooty() - px(2) - want_h)
            avail = space_above                     # 放不下就往上弹

        rows = max(1, min(self.MAX_ROWS, len(self._values)))
        rows = max(1, min(rows, int((avail - px(8)) // row_h)))
        h = row_h * rows + px(4)
        if y + h > screen_h:
            y = max(0, screen_h - h)
        popup.geometry(f"{w}x{h}+{x}+{y}")
        popup.configure(bg=BORDER)

        inner = tk.Frame(popup, bg=CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        self._rows_shown = rows
        # 打开时让当前选中的那一项落在可见范围内
        sel = self._values.index(self._value) if self._value in self._values else -1
        if sel >= 0:
            if sel < self._offset:
                self._offset = sel
            elif sel >= self._offset + rows:
                self._offset = sel - rows + 1
        self._clamp_offset()

        body = tk.Frame(inner, bg=CARD)
        body.pack(side="left", fill="both", expand=True)
        body.grid_columnconfigure(0, weight=1)
        self._body = body

        # 行控件一次建好、以后只改文字和颜色。
        # 之前每次滚动都 destroy 重建，正在接收滚轮事件的那一行被销毁，
        # 同一次滚动的后续事件就全丢了 —— 表现成「滚一下只动一行 / 滚不动」。
        self._rows = []
        for i in range(rows):
            row = tk.Label(body, text="", bg=CARD, fg=TEXT_DIM, font=F(9),
                           anchor="w", cursor="hand2",
                           padx=px(10), pady=px(self.ROW_PAD_Y))
            row.grid(row=i, column=0, sticky="ew")
            row.bind("<Button-1>", lambda _e, r=row: self._pick_row(r))
            row.bind("<Enter>", lambda _e, r=row: self._hover_row(r))
            row.bind("<MouseWheel>", self._wheel)
            self._rows.append(row)

        self._sb = tk.Canvas(inner, width=px(self.SB_W), bg=CARD_HI,
                             highlightthickness=0, bd=0)
        self._sb_needed = len(self._values) > rows
        if self._sb_needed:
            self._sb.pack(side="right", fill="y")
            self._sb.bind("<Configure>", lambda _e: self._draw_scrollbar())
            self._sb.bind("<Button-1>", self._sb_press)
            self._sb.bind("<B1-Motion>", self._sb_drag)
            self._sb.bind("<MouseWheel>", self._wheel)

        self._fill_rows()
        try:
            popup.update_idletasks()
            self._draw_scrollbar()
        except Exception:
            pass

        popup.bind("<Button-1>", lambda _e: self._close())
        popup.bind("<Escape>", lambda _e: self._close())
        popup.bind("<MouseWheel>", self._wheel)
        popup.bind("<Up>", lambda _e: self._move(-1))
        popup.bind("<Down>", lambda _e: self._move(1))
        popup.bind("<Prior>", lambda _e: self._page(-1))     # PageUp
        popup.bind("<Next>", lambda _e: self._page(1))       # PageDown
        popup.bind("<Home>", lambda _e: self._jump(0))
        popup.bind("<End>", lambda _e: self._jump(len(self._values) - 1))
        popup.bind("<Return>", self._enter)
        try:
            popup.grab_set()   # grab 之后，点击浮层外部的事件也被重定向到浮层 -> 触发关闭
        except Exception:
            pass
        popup.focus_force()

    def _fill_rows(self) -> None:
        """按当前偏移刷新可见行的内容（控件复用，不重建）。"""
        rows = self._rows
        if not rows:
            return
        for i, row in enumerate(rows):
            idx = self._offset + i
            if idx >= len(self._values):
                row.grid_remove()
                continue
            row.grid(row=i, column=0, sticky="ew")
            name = self._values[idx]
            sel = name == self._value
            hov = idx == self._hover
            row._vidx = idx
            row.configure(text=("✓  " if sel else "    ") + name,
                          bg=ACCENT_DIM if sel else (CARD_HI if hov else CARD),
                          fg=TEXT if (sel or hov) else TEXT_DIM)

    def _pick_row(self, row: tk.Label) -> str:
        idx = getattr(row, "_vidx", -1)
        if 0 <= idx < len(self._values):
            return self._pick(self._values[idx])
        return "break"

    def _hover_row(self, row: tk.Label) -> None:
        idx = getattr(row, "_vidx", -1)
        if idx < 0:
            return
        self._hover = idx
        sel = self._values[idx] == self._value
        row.configure(bg=ACCENT_DIM if sel else CARD_HI)

    # ------------------------------------------------------------------ #
    # 滚动
    # ------------------------------------------------------------------ #

    def _max_offset(self) -> int:
        return max(0, len(self._values) - self._rows_shown)

    def _clamp_offset(self) -> None:
        self._offset = max(0, min(self._offset, self._max_offset()))

    def _scroll_by(self, step: int) -> None:
        self._offset += step
        self._clamp_offset()
        self._fill_rows()
        self._draw_scrollbar()

    def _wheel(self, e: Any) -> str:
        """滚轮：一格滚一行。

        delta 先累积再取整整倍，触控板那种细滚动（delta 小）也能连续滚，
        而普通鼠标一格（120）刚好滚一行。
        """
        if len(self._values) <= self._rows_shown:
            self._wheel_acc = 0
            return "break"
        delta = int(getattr(e, "delta", 0) or 0)
        if delta == 0:
            # X11 风格的 Button-4 / Button-5
            num = int(getattr(e, "num", 0) or 0)
            if num not in (4, 5):
                return "break"
            self._scroll_by(-1 if num == 4 else 1)
            return "break"
        self._wheel_acc += delta
        steps = int(self._wheel_acc / 120)
        if steps:
            self._wheel_acc -= steps * 120
            # delta > 0 是往上滚（看更前面的项），offset 要减小，所以取反
            self._scroll_by(-steps)
        return "break"

    def _move(self, delta: int) -> str:
        n = len(self._values)
        if n == 0:
            return "break"
        cur = self._hover if self._hover >= 0 else self._current_index()
        self._hover = max(0, min(n - 1, cur + delta))
        self._reveal(self._hover)
        return "break"

    def _page(self, direction: int) -> str:
        """PageUp / PageDown：整屏翻。"""
        n = len(self._values)
        if n == 0:
            return "break"
        cur = self._hover if self._hover >= 0 else self._current_index()
        self._hover = max(0, min(n - 1, cur + direction * max(1, self._rows_shown - 1)))
        self._reveal(self._hover)
        return "break"

    def _jump(self, index: int) -> str:
        """Home / End：跳到第一项 / 最后一项。"""
        n = len(self._values)
        if n == 0:
            return "break"
        self._hover = max(0, min(n - 1, index))
        self._reveal(self._hover)
        return "break"

    def _reveal(self, index: int) -> None:
        """把第 index 项滚进可见区域。"""
        if index < self._offset:
            self._offset = index
        elif index >= self._offset + self._rows_shown:
            self._offset = index - self._rows_shown + 1
        self._clamp_offset()
        self._fill_rows()
        self._draw_scrollbar()

    def _current_index(self) -> int:
        if self._value in self._values:
            return self._values.index(self._value)
        return 0

    def _enter(self, _e: Any = None) -> str:
        if 0 <= self._hover < len(self._values):
            self._pick(self._values[self._hover])
        return "break"

    # ------------------------------------------------------------------ #
    # 滚动条
    # ------------------------------------------------------------------ #

    def _thumb_h(self) -> int:
        sb = self._sb
        h = sb.winfo_height() if sb is not None else 0
        total = max(1, len(self._values))
        shown = max(1, self._rows_shown)
        return max(px(20), int(h * shown / total)) if h > 1 else px(20)

    def _draw_scrollbar(self) -> None:
        sb = self._sb
        if sb is None or not self._sb_needed:
            return
        sb.delete("all")
        h = sb.winfo_height()
        if h <= 1:
            return
        thumb_h = self._thumb_h()
        travel = max(1, h - thumb_h)
        max_off = max(1, self._max_offset())
        y = int(travel * self._offset / max_off)
        sb.create_rectangle(0, y, px(self.SB_W), y + thumb_h, fill=ACCENT, outline="")

    def _sb_press(self, e: Any) -> str:
        if not self._sb_needed or self._sb is None:
            return "break"
        h = self._sb.winfo_height()
        travel = max(1, h - self._thumb_h())
        max_off = max(1, self._max_offset())
        frac = min(1.0, max(0.0, (e.y - self._thumb_h() / 2) / travel))
        self._offset = int(frac * max_off)
        self._clamp_offset()
        self._fill_rows()
        self._draw_scrollbar()
        self._sb_drag_from = e.y
        self._sb_off_from = self._offset
        return "break"

    def _sb_drag(self, e: Any) -> str:
        if not self._sb_needed or self._sb is None:
            return "break"
        h = self._sb.winfo_height()
        travel = max(1, h - self._thumb_h())
        max_off = max(1, self._max_offset())
        delta = (e.y - self._sb_drag_from) / travel * max_off
        self._offset = int(self._sb_off_from + delta)
        self._clamp_offset()
        self._fill_rows()
        self._draw_scrollbar()
        return "break"

    def _pick(self, name: str) -> str:
        changed = name != self._value
        self._value = name
        self._render()
        self._close()
        if changed:
            self._on_change(name)
        return "break"


# --------------------------------------------------------------------------- #
# 力度曲线 / 力度条 / 状态药丸
# --------------------------------------------------------------------------- #

class CurveView(tk.Canvas):
    """力度映射曲线：横轴 原始力度 1..max_in，纵轴 映射后 1..127。

    曲线本身只在 (max_in, exp) 变化时重画；弹奏时只移动落点圆点，因此非常省。
    """

    def __init__(self, master: tk.Misc, height: int = 190, **kw: Any) -> None:
        super().__init__(master, height=px(height), bg=CARD, highlightthickness=0,
                         bd=0, **kw)
        self._sig: Optional[tuple] = None
        self._last: Optional[Dict[str, int]] = None
        self._img: Optional[tk.PhotoImage] = None
        self._max_in = 100
        self._exp = 1.0
        self.bind("<Configure>", lambda _e: (setattr(self, "_sig", None), self._redraw()))

    def update(self, max_in: int, exp: float, last: Optional[Dict[str, int]]) -> None:
        sig = (max_in, round(exp, 3))
        self._last = last
        if sig != self._sig:
            self._sig = sig
            self._max_in = max(2, max_in)
            self._exp = exp
        self._redraw()

    # ---- 坐标映射 ----
    def _box(self):
        w, h = self.winfo_width(), self.winfo_height()
        l, r, t, b = px(34), px(12), px(12), px(22)
        gw, gh = w - l - r, h - t - b
        return (l, t, gw, gh) if gw > 20 and gh > 20 else None

    @staticmethod
    def _x(l, gw, raw, max_in):
        return l + (raw - 1) / (max_in - 1) * gw

    @staticmethod
    def _y(t, gh, mapped):
        return t + gh - (mapped - 1) / 126.0 * gh

    def _redraw(self) -> None:
        box = self._box()
        if box is None:
            return
        l, t, gw, gh = box
        mi, ex = self._max_in, self._exp
        X = lambda raw: self._x(l, gw, raw, mi)      # noqa: E731
        Y = lambda mapped: self._y(t, gh, mapped)    # noqa: E731

        self.delete("all")

        # ---- 曲线区：整块用抗锯齿位图绘制（曲线/圆点不再有锯齿）----
        steps = max(2, min(160, int(gw)))
        pts: List[Tuple[float, float]] = []
        for i in range(steps + 1):
            raw = 1 + (mi - 1) * i / steps
            pts.append((X(raw) - l, Y(vm.map_velocity(raw, mi, ex)) - t))

        painter = aa.Painter(gw, gh, scale=3)
        painter.polygon(pts + [(gw - 1, gh - 1), (0, gh - 1)], "#e8f1fe")
        for gv in (0, 32, 64, 96, 127):
            painter.line([(0, Y(gv) - t), (gw, Y(gv) - t)], BORDER)
        painter.line([(0, Y(1) - t), (gw, Y(1) - t)], BORDER_HI)
        painter.line([(0, 0), (0, gh)], BORDER_HI)
        # 线性参考线（虚线）
        painter.dashed_line(pts[0], pts[-1], BORDER_HI, dash=px(3))
        # 当前曲线
        painter.line(pts, ACCENT, width=px(2))

        # 最近一次弹奏落点
        last = self._last
        marker_c = None
        if last and 1 <= last["raw"] <= mi:
            cx, cy = X(last["raw"]) - l, Y(last["mapped"]) - t
            painter.circle(cx, cy, px(8), fill=CARD, outline=GOOD, width=px(2))
            marker_c = (cx + l, cy + t)

        self._img = painter.to_photo()
        self.create_image(l, t, anchor="nw", image=self._img)

        # ---- 文字刻度用 Tk 画（字体比位图好看）----
        for gv in (0, 32, 64, 96, 127):
            self.create_text(l - px(7), Y(gv), text=str(gv), anchor="e",
                             fill=TEXT_FADE, font=F(8, mono=True))
        for gv in (1, mi // 2):
            if gv < mi:
                self.create_text(X(gv), t + gh + px(9), text=str(gv), anchor="n",
                                 fill=TEXT_FADE, font=F(8, mono=True))
        self.create_text(l + gw, t + gh + px(9), text=tr("原始力度 →", "Raw velocity →"), anchor="ne",
                         fill=TEXT_FADE, font=F(8))
        if marker_c and last:
            self.create_text(marker_c[0], marker_c[1] - px(8) - px(5),
                             text=f"{last['raw']}→{last['mapped']}",
                             anchor="s", fill=GOOD, font=F(8, mono=True))


class Meter(tk.Canvas):
    """横向力度条：标签 + 进度条 + 数值；可显示「映射到 127」的刻度线。"""

    def __init__(self, master: tk.Misc, label: str, color: str, height: int = 34,
                 **kw: Any) -> None:
        super().__init__(master, height=px(height), bg=CARD, highlightthickness=0,
                         bd=0, **kw)
        self.label, self.color = label, color
        self._value: Optional[int] = None
        self._total = 127
        self._marker: Optional[float] = None
        self.bind("<Configure>", lambda _e: self._redraw())

    def set(self, value: Optional[int], total: int = 127,
            marker: Optional[float] = None) -> None:
        self._value, self._total, self._marker = value, max(1, total), marker
        self._redraw()

    def _redraw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2:
            return
        bx0, bx1 = px(46), w - px(46)
        bw = max(1, bx1 - bx0)
        by = h // 2
        th = max(5, px(7))
        self.create_text(0, by, text=self.label, anchor="w", fill=TEXT_DIM, font=F(8))
        self.create_rectangle(bx0, by - th // 2, bx1, by + th // 2, fill=TRACK, outline="")

        v = self._value
        if v is not None:
            frac = min(1.0, max(0.0, v / self._total))
            if frac > 0:
                self.create_rectangle(bx0, by - th // 2, bx0 + bw * frac,
                                      by + th // 2, fill=self.color, outline="")
            self.create_text(w, by, text=str(v), anchor="e", fill=TEXT,
                             font=F(11, "bold", mono=True))
        else:
            self.create_text(w, by, text="--", anchor="e", fill=TEXT_FADE,
                             font=F(11, mono=True))

        if self._marker is not None and 0 < self._marker < self._total:
            mx = bx0 + bw * (self._marker / self._total)
            self.create_line(mx, by - th // 2 - px(3), mx, by + th // 2 + px(3),
                             fill=WARN, width=px(2))
            # 刻线太靠右时把说明放到左侧，避免压住数值
            left_side = (self._marker / self._total) > 0.82
            self.create_text(mx - px(4) if left_side else mx + px(4), by, text="→127",
                             anchor="e" if left_side else "w", fill=WARN,
                             font=F(7, mono=True))


class CloseDialog(tk.Toplevel):
    """点关闭时问一句：是完全退出，还是留在后台（托盘）继续跑。

    对应用户的「是 / 否」：
        是 -> 完全退出（力度转发停止）
        否 -> 留在后台运行（托盘图标常驻，转发不受影响）

    带一个「下次不再提示」复选框；勾上以后这次选择会记到 ui_prefs.json，
    以后直接按记住的方式执行。想改回来：托盘图标右键 -> 「关闭时总是询问」。
    """

    def __init__(self, master: tk.Misc) -> None:
        super().__init__(master)
        self.title(tr("力度重映射", "Velocity Remapper"))
        self.configure(bg=CARD)
        self.resizable(False, False)
        self.transient(master)
        self.action = "tray"          # "tray" | "exit"
        self.remember = False
        self._done = False

        wrap = tk.Frame(self, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=px(22), pady=px(18))

        tk.Label(wrap, text=tr("要完全退出这个工具吗？", "Quit this tool completely?"), bg=CARD, fg=TEXT,
                 font=F(12, "bold")).pack(anchor="w")
        tk.Label(wrap,
                 text=tr("是（完全退出）：力度转发立即停止。\n"
                         "否（留在后台运行）：托盘图标常驻，转发不受影响。",
                         "Yes (quit completely): velocity forwarding stops immediately.\n"
                         "No (keep it running in the background): the tray icon stays, forwarding is unaffected."),
                 bg=CARD, fg=TEXT_DIM, font=F(9), justify="left").pack(
            anchor="w", pady=(px(8), px(12)))

        self._check = FlatCheck(wrap, tr("下次不再提示（记住这次的选择）", "Don't ask again (remember my choice)"),
                                command=self._on_check)
        self._check.pack(anchor="w")

        self._hint = tk.Label(wrap, text="", bg=CARD, fg=TEXT_FADE, font=F(8),
                              justify="left")
        self._hint.pack(anchor="w", pady=(px(4), 0))

        row = tk.Frame(wrap, bg=CARD)
        row.pack(anchor="e", pady=(px(18), 0))
        FlatButton(row, tr("留在后台运行", "Keep running in background"), command=self._on_tray,
                   kind="accent").pack(side="right")
        FlatButton(row, tr("完全退出", "Quit completely"), command=self._on_exit,
                   kind="normal").pack(side="right", padx=(px(8), 0))

        # 回车 = 默认按钮（蓝色那颗「留在后台运行」），Esc 也是「取消」，
        # 两者都不该让程序意外退出；「完全退出」要点它自己
        self.bind("<Escape>", lambda _e: self._on_tray())
        self.bind("<Return>", lambda _e: self._on_tray())
        self.protocol("WM_DELETE_WINDOW", self._on_tray)
        self.update_idletasks()
        self._center_on(master)
        self.grab_set()
        self.focus_set()

    def _center_on(self, master: tk.Misc) -> None:
        try:
            x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
            y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 2
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass

    def _on_check(self) -> None:
        self.remember = bool(self._check.get())
        # 勾上就说明白「记住了去哪改」，免得用户以为永久关不掉了
        try:
            self._hint.configure(
                text=tr("记住了：下次点关闭直接执行。\n"
                        "想改回来：托盘图标右键 ->「关闭时总是询问」。",
                        "Remembered — next time, closing will do this directly.\n"
                        "To undo: right-click the tray icon -> “Always ask when closing”.")
                if self.remember else "")
        except Exception:
            pass

    def _finish(self, action: str) -> None:
        if self._done:
            return
        self._done = True
        self.action = action
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def _on_tray(self) -> None:
        self._finish("tray")

    def _on_exit(self) -> None:
        self._finish("exit")

    @staticmethod
    def ask(master: tk.Misc) -> tuple:
        """弹出对话框，返回 (action, remember)。"""
        dlg = CloseDialog(master)
        master.wait_window(dlg)
        return dlg.action, dlg.remember


class PortGuideDialog(tk.Toplevel):
    """没找到虚拟 MIDI 输出端口时的引导。

    力度重映射要接在「键盘 -> 本工具 -> DAW」中间，输出端必须是一个
    虚拟 MIDI 端口（硬件端口是独占的，DAW 抢不到）。所以第一次用的人
    很容易卡在这里，给一个说人话的说明比让他们对着日志猜强。
    """

    def __init__(self, master: tk.Misc, message: str,
                 on_retry: Optional[Callable[[], None]] = None) -> None:
        super().__init__(master)
        self.title(tr("需要虚拟 MIDI 端口", "A virtual MIDI port is required"))
        self.configure(bg=CARD)
        self.resizable(False, False)
        self.transient(master)
        self._on_retry = on_retry

        wrap = tk.Frame(self, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=px(22), pady=px(18))

        tk.Label(wrap, text=tr("需要一个虚拟 MIDI 端口", "You need a virtual MIDI port"), bg=CARD, fg=TEXT,
                 font=F(12, "bold")).pack(anchor="w")
        tk.Label(wrap, text=message, bg=CARD, fg=TEXT_DIM, font=F(9),
                 justify="left", wraplength=380).pack(
            anchor="w", pady=(px(8), px(12)))

        tk.Label(wrap,
                 text=tr("本工具要把重映射后的力度转发给 DAW，需要借助虚拟 MIDI 端口。\n"
                         "Windows 上最常见的做法是装 loopMIDI（免费、个人使用免费）：\n\n"
                         "  1. 打开官网下载安装\n"
                         "  2. 打开 loopMIDI，右键托盘图标 ->「添加端口」，名字随意\n"
                         "  3. 回到本工具把「输出」选成那个端口，DAW 里也选同一个\n\n"
                         "装好后本工具会自动找到它，不用再重装本工具。",
                         "This tool forwards the remapped velocity to your DAW through a virtual MIDI port.\n"
                         "On Windows the usual choice is loopMIDI (free for personal use):\n\n"
                         "  1. Download and install it from the official site\n"
                         "  2. Open loopMIDI, right-click its tray icon -> “Add Port”, any name\n"
                         "  3. Back here, set “Output” to that port and pick the same one in your DAW\n\n"
                         "Once installed, this tool finds it by itself — no need to reinstall anything."),
                 bg=CARD, fg=TEXT_DIM, font=F(9), justify="left").pack(anchor="w")

        row = tk.Frame(wrap, bg=CARD)
        row.pack(anchor="e", pady=(px(18), 0))
        FlatButton(row, tr("重新检测", "Check again"), command=self._retry, kind="normal").pack(
            side="right", padx=(px(8), 0))
        FlatButton(row, tr("打开 loopMIDI 官网", "Open loopMIDI site"), command=self._open_site,
                   kind="accent").pack(side="right")
        FlatButton(row, tr("稍后再说", "Not now"), command=self._close, kind="ghost").pack(
            side="right", padx=(px(8), 0))

        self.bind("<Escape>", lambda _e: self._close())
        self.protocol("WM_DELETE_WINDOW", self._close)
        self.update_idletasks()
        try:
            x = master.winfo_rootx() + (master.winfo_width() - self.winfo_width()) // 2
            y = master.winfo_rooty() + (master.winfo_height() - self.winfo_height()) // 2
            self.geometry(f"+{max(0, x)}+{max(0, y)}")
        except Exception:
            pass
        self.grab_set()
        self.focus_set()

    def _close(self) -> None:
        try:
            self.grab_release()
        except Exception:
            pass
        self.destroy()

    def _retry(self) -> None:
        self._close()
        if self._on_retry:
            try:
                self._on_retry()
            except Exception:
                pass

    def _open_site(self) -> None:
        try:
            import webbrowser
            webbrowser.open("https://www.tobias-erichsen.de/software/loopmidi.html")
        except Exception:
            pass
        self._close()


class Pill(tk.Canvas):
    """右上角状态药丸：● 已连接 / ● 等待设备 / ● 端口被占用 / ● 已停止。"""

    # 状态 -> (前景色, 浅底色)
    _COLORS = {"ok": (GOOD, "#dcf3e7"),
               "waiting": (WARN, "#fbeed2"),
               "busy": (WARN, "#fbeed2"),
               "error": (BAD, "#fadcdc"),
               "stopped": (TEXT_FADE, "#e4e8ee")}

    def __init__(self, master: tk.Misc, width: int = 120, height: int = 26,
                 **kw: Any) -> None:
        super().__init__(master, width=px(width), height=px(height), bg=BG,
                         highlightthickness=0, bd=0, cursor="hand2", **kw)
        self._status: Optional[str] = None
        self.bind("<Configure>", lambda _e: self.set(self._status or "waiting"))
        self.bind("<Button-1>", self._on_click)

    def _on_click(self, _e: Any) -> None:  # 最小化后点击可恢复
        top = self.winfo_toplevel()
        if top.state() == "iconic":
            top.deiconify()
            top.lift()

    def set(self, status: str) -> None:
        if status == self._status:
            return
        self._status = status
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w <= 2:
            w, h = px(120), px(26)
        fg, tint = self._COLORS.get(status, (TEXT_DIM, CARD_HI))
        text = eng_mod.status_text(status)
        self.create_rectangle(px(1), px(1), w - px(1), h - px(1), fill=tint,
                              outline="")
        # 小圆点同样走抗锯齿位图
        dot = aa.circle_photo(px(4), fg, None, 0)
        self.create_image(px(14), h // 2, image=dot)
        self.create_text(px(25), h // 2, text=text, anchor="w", fill=fg,
                         font=F(9, "bold"))


class _StdoutBridge:
    """把遗留的 print()（_announce 提示、pygame banner 等）接到界面日志里。

    pythonw.exe 没有 stdout，直接 print 会丢信息；转成日志行反而更好用。
    """

    def __init__(self, sink: Callable[..., None]) -> None:
        self._sink = sink

    def write(self, text: str) -> int:
        for line in text.replace("\r", "\n").split("\n"):
            if line.strip():
                self._sink(line.strip())
        return len(text)

    def flush(self) -> None:  # noqa: D401
        pass


# --------------------------------------------------------------------------- #
# 主窗口
# --------------------------------------------------------------------------- #

class App:
    """桌面应用主窗口。"""

    POLL_MS = 50  # ~20fps 刷新引擎快照（25ms 时每次唤醒要 ~1ms，空闲时白烧 3% CPU）
    LOG_LINES = 200

    def __init__(self, config_path: Optional[str] = None) -> None:
        self.config_path = config_path or os.path.join(DATA_DIR, "config.json")
        # 第一次运行（还没有 config.json）才允许自动挑端口并写回；
        # 已有配置时绝不能因为「设备暂时没插上」就把它改掉
        self._config_preexisting = os.path.exists(self.config_path)
        boot = io.StringIO()
        with contextlib.redirect_stdout(boot):   # pythonw 没有控制台，把提示收进日志
            cfg = vm.load_config(self.config_path)
            vm._setup_backend()               # （pygame 后端会打印 banner，一并收走）

        self.root = tk.Tk()
        self.root.withdraw()
        global _S
        try:
            _S = self.root.winfo_fpixels("1i") / 96.0
        except Exception:
            _S = 1.0

        self.root.title(tr("钢琴力度重映射", "Piano Velocity Remapper"))
        self.root.configure(bg=BG)
        _apply_icon(self.root, os.path.join(SCRIPT_DIR, "app.ico"))
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.root.bind("<Escape>", lambda _e: self.on_close())
        self.root.bind("<Unmap>", self._on_unmap)

        self.engine = eng_mod.Engine(cfg, config_path=self.config_path, console_echo=False)
        # 后续所有 print（_announce 提示等）都汇入界面日志，不再丢失
        sys.stdout = _StdoutBridge(self.engine.log_external)
        for line in boot.getvalue().strip().splitlines():
            if line.strip():
                self.engine.log_external(line.strip(), stamped=False)

        self._last_rev = -1
        self._last_status = ""
        self._last_log_seq = -1
        # 日志合并：连续相同的行只显示一行，后面标「×N」
        self._log_last_text: Optional[str] = None
        self._log_last_count = 0
        self._log_last_line = 0
        self._log_last_tag = ""
        self._log_last_item: Optional[Dict[str, Any]] = None
        self._curve_key = None
        self._save_job: Optional[str] = None

        # 后台运行（托盘）相关
        self._tray_queue: "queue.Queue[str]" = queue.Queue()
        self._tray: Any = None
        self._tray_notified: set = set()   # 已经提示过的场景，避免重复弹气泡
        self._close_pref = self._load_pref()
        self._minimize_to_tray = self._load_minimize_pref()
        self._hidden_to_tray = False
        self._suppress_unmap = False

        # 虚拟 MIDI 端口检查（后台线程 -> 队列 -> Tk 线程）
        self._port_queue: "queue.Queue[tuple]" = queue.Queue()
        self._port_guide_shown = False

        # 端口热插拔监测（后台线程 -> 队列 -> Tk 线程）
        self._port_watch_queue: "queue.Queue[tuple]" = queue.Queue()
        self._port_watch_stop = threading.Event()
        self._last_port_lists: Optional[Tuple[List[str], List[str]]] = None

        # 界面语言（默认中文，按 ui_prefs.json 里的偏好）
        self._apply_lang_pref()

        self._build()
        self._fit_window()
        self.root.deiconify()
        self.root.after_idle(lambda: _apply_window_chrome(self.root))
        self._sync_ports(initial=True)
        self._tick()
        self.engine.start()
        self._start_tray()
        self._poll_tray()
        _start_activate_listener(self._on_activate_event)
        self._start_port_watch()
        self._ensure_output_port()

    # ------------------------------------------------------------------ #
    # 布局
    # ------------------------------------------------------------------ #

    def _fit_window(self) -> None:
        """按内容的自然高度定窗口大小，不同缩放比例/字体下都不会被裁掉。"""
        self.root.update_idletasks()
        need = (self._header.winfo_reqheight() + self._body.winfo_reqheight()
                + self._statusbar.winfo_reqheight() + px(26)) + px(70)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        w = max(px(880), min(px(1020), sw - px(120)))
        h = min(max(need, px(620)), sh - px(120))
        self.root.minsize(px(820), max(px(520), h - px(200)))
        self.root.geometry(f"{w}x{h}")
        self.root.after(80, self._center)

    def _center(self) -> None:
        self.root.update_idletasks()
        w, h = self.root.winfo_width(), self.root.winfo_height()
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        self.root.geometry(f"{w}x{h}+{max(0, (sw - w) // 2)}+{max(0, (sh - h) // 2)}")

    def _build(self) -> None:
        """搭出整个界面。切换语言时会把容器清空再重建一次。"""
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=1)

        self._ui = tk.Frame(self.root, bg=BG)
        self._ui.grid(row=0, column=0, sticky="nsew")
        self._build_into(self._ui)

    def _build_into(self, parent: tk.Misc) -> None:
        cfg = self.engine.config()
        parent.columnconfigure(0, weight=1)
        parent.rowconfigure(1, weight=1)

        # 窗口标题也跟着语言走
        try:
            self.root.title(tr("钢琴力度重映射", "Piano Velocity Remapper"))
        except Exception:
            pass

        self._header = self._build_header(parent)

        body = tk.Frame(parent, bg=BG)
        self._body = body
        body.grid(row=1, column=0, sticky="nsew", padx=px(18), pady=(px(2), px(10)))
        # 两列等宽；上面两行等高（卡片边缘对齐），最后一行是横跨两列的日志条
        for c in (0, 1):
            body.grid_columnconfigure(c, weight=1, uniform="col")
        body.grid_rowconfigure(0, weight=1)
        body.grid_rowconfigure(1, weight=1)

        gap = px(14)
        self._build_mapping_card(body).grid(row=0, column=0, sticky="nsew",
                                            padx=(0, gap), pady=(0, gap))
        self._build_realtime_card(body).grid(row=0, column=1, sticky="nsew",
                                            padx=(gap, 0), pady=(0, gap))
        self._build_curve_card(body).grid(row=1, column=0, sticky="nsew",
                                          padx=(0, gap), pady=(0, gap))
        self._build_ports_card(body).grid(row=1, column=1, sticky="nsew",
                                          padx=(gap, 0), pady=(0, gap))
        self._build_log(body).grid(row=2, column=0, columnspan=2, sticky="ew")
        self._statusbar = self._build_statusbar(parent)

    def _rebuild_ui(self) -> None:
        """清空界面并按当前语言重建（引擎和 MIDI 连接都不受影响）。"""
        try:
            for child in self._ui.winfo_children():
                child.destroy()
            self._build_into(self._ui)
            self._sync_ports()
            self._reload_log()
            self._refresh()
        except Exception:
            pass

    # ---- 顶栏 ----
    def _build_header(self, parent: tk.Misc) -> tk.Frame:
        bar = tk.Frame(parent, bg=BG)
        bar.grid(row=0, column=0, sticky="ew", padx=px(18), pady=(px(16), px(10)))

        box = tk.Frame(bar, bg=BG)
        box.pack(side="left")
        tk.Label(box, text=tr("力度重映射", "Velocity Remapper"), bg=BG, fg=TEXT, font=F(16, "bold")).pack(anchor="w")
        self.subtitle = tk.Label(box, text=tr("正在检测 MIDI 设备…", "Detecting MIDI devices…"), bg=BG, fg=TEXT_DIM,
                                 font=F(9))
        self.subtitle.pack(anchor="w", pady=(px(3), 0))

        self.pill = Pill(bar, width=120)
        self.pill.pack(side="right", anchor="e")

        # 语言切换：按钮上显示「切过去」的那种语言
        self.lang_btn = FlatButton(bar, text="EN" if get_lang() == "zh" else "中",
                                   kind="ghost", font=F(8),
                                   command=self._toggle_lang)
        self.lang_btn.pack(side="right", padx=(0, px(10)))
        return bar

    # ---- 卡片：最大力度 + 曲线指数 ----
    def _build_mapping_card(self, parent: tk.Frame) -> Card:
        cfg = self.engine.config()
        card = Card(parent, tr("力度映射", "Velocity Mapping"))
        card.body.grid_columnconfigure(0, weight=1)

        # 最大力度
        head = tk.Frame(card.body, bg=CARD)
        head.grid(row=0, column=0, sticky="ew")
        tk.Label(head, text=tr("最大力度", "Max velocity"), bg=CARD, fg=TEXT, font=F(9)).pack(side="left")
        self.max_val = tk.Label(head, text=str(int(cfg["max_input_velocity"])), bg=CARD,
                                fg=ACCENT, font=F(13, "bold", mono=True))
        self.max_val.pack(side="right")

        self.max_slider = Slider(card.body, 2, 127, int(cfg["max_input_velocity"]),
                                 self._on_max_changed, width=250)
        self.max_slider.grid(row=1, column=0, sticky="ew", pady=(px(2), px(2)))

        row = tk.Frame(card.body, bg=CARD)
        row.grid(row=2, column=0, sticky="ew", pady=(0, px(16)))
        tk.Label(row, text=tr("键盘用尽全力能达到的力度", "The velocity your keyboard reaches at full force"), bg=CARD, fg=TEXT_FADE,
                 font=F(8)).pack(side="left")
        self.suggest_btn = FlatButton(row, text=tr("采用建议值", "Use suggested"), kind="ghost", font=F(8),
                                      command=self._use_suggested)
        self.suggest_btn.pack(side="right")

        # 曲线指数
        head2 = tk.Frame(card.body, bg=CARD)
        head2.grid(row=3, column=0, sticky="ew")
        tk.Label(head2, text=tr("力度曲线", "Velocity Curve"), bg=CARD, fg=TEXT, font=F(9)).pack(side="left")
        tk.Label(head2, text=tr("0.1/格", "0.1 / step"), bg=CARD, fg=TEXT_FADE, font=F(8)).pack(
            side="right", padx=(0, px(8)))
        self.exp_val = tk.Label(head2, text=f"{float(cfg['curve_exponent']):.1f}", bg=CARD,
                                fg=ACCENT, font=F(13, "bold", mono=True))
        self.exp_val.pack(side="right")

        # 力度曲线：0.1 为一格（拖动/方向键都按 0.1 跳，Shift+方向键 0.01 细调）
        self.exp_slider = Slider(card.body, 0.2, 3.0, float(cfg["curve_exponent"]),
                                 self._on_exp_changed, width=250,
                                 integer=False, step=0.1)
        self.exp_slider.grid(row=4, column=0, sticky="ew", pady=(px(2), px(2)))

        presets = tk.Frame(card.body, bg=CARD)
        presets.grid(row=5, column=0, sticky="ew", pady=(px(6), 0))
        for text, value, tip in ((tr("线性", "Linear"), 1.0, tr("1-max 均匀拉伸到 1-127，手感最自然", "Stretch 1-max evenly to 1-127 — the most natural feel")),
                                 (tr("柔和", "Soft"), 1.7, tr("轻触更轻，用力才出效果，动态范围大", "A light touch plays light, wide dynamic range")),
                                 (tr("爆发", "Punchy"), 0.6, tr("轻触就出声，层次集中在下段", "Even the lightest touch sounds; detail lives in the lower range"))):
            FlatButton(presets, text=text, kind="ghost", font=F(8),
                       command=lambda v=value, t=tip: self._set_preset(v, t)).pack(
                side="left", padx=(0, px(8)))

        foot = tk.Frame(card.body, bg=CARD)
        foot.grid(row=7, column=0, sticky="ew", pady=(px(10), 0))
        # 多余的竖向空间给第 6 行（空行），底部提示贴在卡片下边缘
        card.body.grid_rowconfigure(6, weight=1)
        self.exp_hint = tk.Label(foot, text=tr("1-max 均匀拉伸到 1-127，手感最自然", "Stretch 1-max evenly to 1-127 — the most natural feel"),
                                 bg=CARD, fg=TEXT_FADE, font=F(8), anchor="w")
        self.exp_hint.pack(side="left")
        self.save_hint = tk.Label(foot, text="", bg=CARD, fg=GOOD, font=F(8))
        self.save_hint.pack(side="right")
        return card

    # ---- 卡片：曲线图 ----
    def _build_curve_card(self, parent: tk.Frame) -> Card:
        card = Card(parent, tr("力度曲线", "Velocity Curve"))
        self.curve = CurveView(card.body, height=150)
        self.curve.pack(fill="both", expand=True)
        return card

    # ---- 卡片：实时力度 ----
    def _build_realtime_card(self, parent: tk.Frame) -> Card:
        card = Card(parent, tr("实时力度", "Live Velocity"))

        head = tk.Frame(card.body, bg=CARD)
        head.pack(fill="x")
        self.note_lbl = tk.Label(head, text="♪ —", bg=CARD, fg=TEXT, font=F(13, "bold"))
        self.note_lbl.pack(side="left")
        FlatButton(head, text=tr("重置", "Reset"), kind="ghost", font=F(8),
                   command=self._reset_stats).pack(side="right")
        self.notes_lbl = tk.Label(head, text=tr("已弹 0 个音符", "Played 0 notes"), bg=CARD, fg=TEXT_FADE,
                                  font=F(8))
        self.notes_lbl.pack(side="right", padx=(0, px(10)))

        big = tk.Frame(card.body, bg=CARD)
        big.pack(fill="x", pady=(px(4), px(10)))
        self.raw_big = tk.Label(big, text="--", bg=CARD, fg=TEXT_DIM,
                                font=F(26, "bold", mono=True))
        self.raw_big.pack(side="left")
        tk.Label(big, text="  →  ", bg=CARD, fg=TEXT_FADE, font=F(16)).pack(side="left")
        self.map_big = tk.Label(big, text="--", bg=CARD, fg=ACCENT,
                                font=F(26, "bold", mono=True))
        self.map_big.pack(side="left")

        self.raw_meter = Meter(card.body, tr("原始", "Raw"), ACCENT)
        self.raw_meter.pack(fill="x")
        self.map_meter = Meter(card.body, tr("映射", "Mapped"), GOOD)
        self.map_meter.pack(fill="x", pady=(px(4), px(4)))

        self.range_lbl = tk.Label(card.body, text="", bg=CARD, fg=TEXT_FADE, font=F(8))
        self.range_lbl.pack(anchor="w")

        # 弹性占位：把“建议最大力度 / 采用”这一行顶到卡片下边缘
        tk.Frame(card.body, bg=CARD).pack(fill="both", expand=True)

        sug = tk.Frame(card.body, bg=CARD)
        sug.pack(fill="x", pady=(px(10), 0))
        self.sug_lbl = tk.Label(sug, text=tr("建议最大力度：弹几下后给出", "Suggested max velocity: appears after a few notes"), bg=CARD,
                                fg=TEXT_DIM, font=F(8))
        self.sug_lbl.pack(side="left")
        self.suggest_btn2 = FlatButton(sug, text=tr("采用", "Use"), kind="accent", font=F(8),
                                       command=self._use_suggested)
        self.suggest_btn2.pack(side="right")
        return card

    # ---- 卡片：端口 ----
    def _build_ports_card(self, parent: tk.Frame) -> Card:
        card = Card(parent, tr("端口", "Ports"))
        card.body.grid_columnconfigure(1, weight=1)

        tk.Label(card.body, text=tr("输入（键盘）", "Input (keyboard)"), bg=CARD, fg=TEXT_DIM,
                 font=F(9)).grid(row=0, column=0, sticky="w", padx=(0, px(12)))
        self.in_dd = Dropdown(card.body, [], "", self._on_port_changed, width=230)
        self.in_dd.grid(row=0, column=1, sticky="ew")

        tk.Label(card.body, text=tr("输出（虚拟端口）", "Output (virtual port)"), bg=CARD, fg=TEXT_DIM,
                 font=F(9)).grid(row=1, column=0, sticky="w", padx=(0, px(12)),
                                 pady=(px(10), 0))
        self.out_dd = Dropdown(card.body, [], "", self._on_port_changed, width=230)
        self.out_dd.grid(row=1, column=1, sticky="ew", pady=(px(10), 0))

        self.port_hint = tk.Label(card.body, text="", bg=CARD, fg=TEXT_FADE,
                                  font=F(8))
        self.port_hint.grid(row=2, column=0, columnspan=2, sticky="w",
                            pady=(px(6), 0))

        row = tk.Frame(card.body, bg=CARD)
        row.grid(row=3, column=0, columnspan=2, sticky="ew", pady=(px(12), 0))
        # 多余的竖向空间给第 2 行（空行），这样两个下拉框不会被撑开
        card.body.grid_rowconfigure(2, weight=1)
        FlatButton(row, text=tr("刷新端口", "Refresh ports"), kind="ghost", font=F(8),
                   command=self._sync_ports).pack(side="left")
        tk.Label(row, text=tr("切换后自动重连", "Reconnects automatically"), bg=CARD, fg=TEXT_FADE,
                 font=F(8)).pack(side="right")
        return card

    # ---- 日志 ----
    def _build_log(self, parent: tk.Frame) -> Card:
        card = Card(parent, "", pad=10)
        head = tk.Frame(card.body, bg=CARD)
        head.pack(fill="x", pady=(0, px(4)))
        tk.Label(head, text=tr("运行日志", "Log"), bg=CARD, fg=TEXT_DIM,
                 font=F(9, "bold")).pack(side="left")
        self.log_hint = tk.Label(head, text="", bg=CARD, fg=TEXT_FADE, font=F(8))
        self.log_hint.pack(side="right")

        wrap = tk.Frame(card.body, bg=CARD)
        wrap.pack(fill="both", expand=True)
        # pady=0：内边距会让可视高度差一点点，最后一行会被裁掉半个
        self.log = tk.Text(wrap, bg=CARD, fg=TEXT_DIM, relief="flat", bd=0,
                           highlightthickness=0, font=F(8, mono=True), wrap="word",
                           height=5, padx=px(2), pady=0, state="disabled")
        sb = tk.Scrollbar(wrap, orient="vertical", command=self.log.yview,
                          bg=TRACK, troughcolor=CARD, activebackground=BORDER_HI,
                          bd=0, highlightthickness=0, width=px(10),
                          elementborderwidth=0)
        self.log.configure(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        self.log.pack(side="left", fill="both", expand=True)
        for tag, col in (("ok", GOOD), ("warn", WARN), ("bad", BAD),
                         ("cfg", ACCENT), ("time", TEXT_FADE)):
            self.log.tag_configure(tag, foreground=col)
        return card

    # ---- 底部状态条 ----
    def _build_statusbar(self, parent: tk.Misc) -> tk.Frame:
        bar = tk.Frame(parent, bg=BG)
        bar.grid(row=2, column=0, sticky="ew", padx=px(18), pady=(0, px(12)))
        self.status = tk.Label(bar, text="", bg=BG, fg=TEXT_FADE, font=F(8), anchor="w")
        self.status.pack(side="left")
        tk.Label(bar, text=tr("最小化 / 关闭会收进托盘 · 改动自动保存", "Minimizing / closing hides to the tray · changes save automatically"),
                 bg=BG, fg=TEXT_FADE, font=F(8)).pack(side="right")
        return bar

    # ------------------------------------------------------------------ #
    # 交互回调
    # ------------------------------------------------------------------ #

    def _on_max_changed(self, v: float) -> None:
        self.max_val.configure(text=str(int(v)))
        self.engine.update({"max_input_velocity": int(v)})
        self._flash_saved()

    def _on_exp_changed(self, v: float) -> None:
        self.exp_val.configure(text=f"{v:.1f}")
        self.engine.update({"curve_exponent": round(float(v), 2)})
        self._flash_saved()

    def _set_preset(self, value: float, tip: str) -> None:
        self.exp_slider.set(value, notify=True)
        self.exp_hint.configure(text=tip)

    def _use_suggested(self) -> None:
        sug = self.engine.summary().get("suggested")
        if sug:
            self.max_slider.set(sug, notify=True)

    def _reset_stats(self) -> None:
        self.engine.reset_stats()
        self._last_rev = -1

    def _on_port_changed(self, _name: str) -> None:
        self.engine.update({
            "input_port_substring": self.in_dd._value,
            "output_port": self.out_dd._value,
        })
        self._flash_saved(tr("端口已更新，正在重连…", "Ports updated, reconnecting…"))
        self._last_rev = -1

    def _start_port_watch(self) -> None:
        """后台线程定期枚举 MIDI 端口，列表一变就通知界面。

        为什么需要它:
            端口列表以前只在「程序启动」和「手点刷新端口」时读一次。
            而 loopMIDI 这类虚拟端口通常恰恰是**程序开着之后**才建出来的，
            于是用户看到的现象就是「一直停在等待设备的界面」。

            这里每秒读一次（读一次只要一两毫秒）。列表变了才重画下拉框，
            引擎那边本来就会每 2 秒自动重连，端口一出现就能自动接上。
        """

        def worker() -> None:
            while not self._port_watch_stop.is_set():
                try:
                    ins = vm.list_input_names()
                    outs = vm.list_output_names()
                except Exception:
                    ins, outs = [], []
                try:
                    self._port_watch_queue.put((ins, outs))
                except Exception:
                    pass
                self._port_watch_stop.wait(1.0)

        threading.Thread(target=worker, name="port-watch", daemon=True).start()

    def _apply_port_lists(self, inputs: List[str], outputs: List[str]) -> None:
        """端口列表有变化时刷新下拉框（已经在 Tk 线程）。"""
        try:
            prev = self._last_port_lists
            cur = (list(inputs), list(outputs))
            if prev is not None and cur == prev:
                return
            self._last_port_lists = cur
            self._sync_ports(ports=cur)
            if prev is None:
                return
            # 有设备出现了，说一声，省得用户以为程序卡住了
            new_names = [n for n in inputs if n not in prev[0]]
            new_names += [n for n in outputs if n not in prev[1]]
            if new_names:
                self.engine.log_external(
                    trf(f"[端口] 检测到新的 MIDI 端口: {', '.join(new_names)}",
                        f"[Ports] New MIDI port(s) detected: {', '.join(new_names)}"))
        except Exception:
            pass

    def _sync_ports(self, initial: bool = False,
                    ports: Optional[Tuple[List[str], List[str]]] = None) -> None:
        if ports is None:
            try:
                ports = self.engine.ports()
            except Exception as exc:
                self.status.configure(text=trf(f"读取 MIDI 端口失败: {exc}", f"Failed to read MIDI ports: {exc}"))
                return
        inputs, outputs = ports
        cfg = self.engine.config()
        cur_in = cfg["input_port_substring"]
        # 输入端按「子串匹配」语义解析：优先显示实际连上的那个端口。
        # 配置为空 = 自动模式：挑一个最像键盘的（不能直接用
        # 「空串 in 端口名」去匹配，那样永远只会拿到列表里的第一个）。
        if cur_in:
            match = next((n for n in inputs if cur_in.lower() in n.lower()), "")
        else:
            match = vm.auto_pick_input_port(inputs) or ""
        cur_out = cfg["output_port"]
        out_match = cur_out if cur_out in outputs else next(
            (n for n in outputs if cur_out.lower() in n.lower()), "")

        if not match or not out_match:
            if self._config_preexisting:
                # 已有配置：设备可能只是暂时没插上。保留用户原来的设置，
                # 千万别用列表里第一个把它覆盖掉——否则键盘插上之后
                # 工具反而连错了口。引擎会每 2 秒自己重试。
                # （下拉框会把找不到的端口标成「⚠ 未找到」，不会假装它有效。）
                match = match or cur_in
                out_match = out_match or cur_out
            else:
                # 第一次运行（还没有 config.json）：帮用户挑一个。
                # 输入端挑最像键盘的那个；挑不出来就留空，
                # 界面上会显示「（无可用端口）」，而不是编一个设备名上去。
                if not match:
                    match = vm.auto_pick_input_port(inputs) or ""
                if not out_match:
                    out_match = cur_out

        self.in_dd.set_values(inputs, match)
        self.out_dd.set_values(outputs, out_match)
        if initial and not self._config_preexisting and \
                (match != cur_in or out_match != cur_out):
            self.engine.update({"input_port_substring": match, "output_port": out_match})
        self._update_port_hint()

    def _flash_saved(self, text: str = tr("已保存", "Saved")) -> None:
        self.save_hint.configure(text=text)
        if self._save_job:
            try:
                self.root.after_cancel(self._save_job)
            except Exception:
                pass
        self._save_job = self.root.after(1400, lambda: self.save_hint.configure(text=""))

    # ------------------------------------------------------------------ #
    # 轮询刷新
    # ------------------------------------------------------------------ #

    def _tick(self) -> None:
        try:
            self._refresh()
        except Exception:
            pass
        self.root.after(self.POLL_MS, self._tick)

    def _refresh(self) -> None:
        eng = self.engine
        st = eng.state
        status = st.get("status", "waiting")
        if not eng.alive:            # 后台线程挂了：别再装作“已连接”
            status = "stopped"
            st = dict(st, hint=tr("引擎线程已停止，请看日志并重启工具", "The engine thread stopped — check the log and restart the tool"))
        # 注意：要连 hint / input_name / output_name 一起比对。只比 status 会漏
        # 掉“端口换了我还显示旧的”这种情况（status 一直是 busy）。
        sub = st.get("hint") or ""
        bar = (trf(f"输入 {st.get('input_name') or '—'}    →    ", f"Input {st.get('input_name') or '—'}    →    ")
               + trf(f"输出 {st.get('output_name') or '—'}", f"Output {st.get('output_name') or '—'}"))
        sig = (status, sub, bar)
        if sig != self._last_status:
            self._last_status = sig
            self.pill.set(status)
            self.subtitle.configure(text=sub)
            self.status.configure(text=bar)

        if eng.rev == self._last_rev:
            return
        self._last_rev = eng.rev

        s = eng.summary()
        last = s.get("last")
        cfg = eng.config()
        max_in = int(cfg["max_input_velocity"])

        # 曲线：参数和“最后音符”都没变就别重画（Canvas 重画 ~500 点很贵，
        # 而心跳日志每 2 秒就会把 rev 顶一下，不加判重会白白重画）
        curve_key = (max_in, float(cfg["curve_exponent"]), s["notes"])
        if curve_key != self._curve_key:
            self._curve_key = curve_key
            self.curve.update(max_in, float(cfg["curve_exponent"]), last)

        # 实时力度
        raw = last["raw"] if last else None
        mapped = last["mapped"] if last else None
        self.note_lbl.configure(text=f"♪ {note_name(last['note'])}" if last else "♪ —")
        self.raw_big.configure(text="--" if raw is None else str(raw))
        self.map_big.configure(text="--" if mapped is None else str(mapped))
        self.notes_lbl.configure(text=trf(f"已弹 {s['notes']} 个音符", f"Played {s['notes']} notes"))
        self.raw_meter.set(raw, 127, max_in)
        self.map_meter.set(mapped, 127)
        self.range_lbl.configure(text=self._range_text(s))

        sug = s.get("suggested")
        if sug:
            same = sug == max_in
            self.sug_lbl.configure(text=trf(f"建议最大力度 {sug}", f"Suggested max velocity {sug}"))
            self.suggest_btn2.configure(text=tr("已是建议值", "Already the suggested value") if same else trf(f"采用 {sug}", f"Use {sug}"))
            self.suggest_btn2.set_enabled(not same)
            self.suggest_btn.configure(text=tr("已是建议值", "Already the suggested value") if same else trf(f"采用建议值 {sug}", f"Use suggested {sug}"))
            self.suggest_btn.set_enabled(not same)
        else:
            self.sug_lbl.configure(text=tr("建议最大力度：弹几下后给出", "Suggested max velocity: appears after a few notes"))
            self.suggest_btn.configure(text=tr("采用建议值", "Use suggested"))
            self.suggest_btn.set_enabled(False)
            self.suggest_btn2.set_enabled(False)

        self._refresh_log()

    @staticmethod
    def _range_text(s: Dict[str, Any]) -> str:
        if not s["notes"]:
            return tr("还没有收到音符，弹一下试试", "No notes yet — play something")
        return (trf(f"最近范围：原始 {s['raw_min']}–{s['raw_max']}", f"Recent range: raw {s['raw_min']}–{s['raw_max']}")
                + trf(f"  →  映射 {s['map_min']}–{s['map_max']}", f"  →  mapped {s['map_min']}–{s['map_max']}"))

    @staticmethod
    def _log_tag(text: str) -> str:
        """日志行按前缀着色（中英文前缀都认）。"""
        if text.startswith(("[连接]", "[Connected]", "[已", "转发模式", "Forwarding", "Done")):
            return "ok"
        if text.startswith(("[等待]", "[Waiting]", "提示", "可用")):
            return "warn"
        if text.startswith(("[断开]", "[Disconnected]", "[错误]", "警告")):
            return "bad"
        if text.startswith(("[配置]", "[Config]")):
            return "cfg"
        return ""

    def _redraw_last_log_line(self) -> None:
        """把日志框最后一行重画成「原文（×N）」。"""
        no = self._log_last_line
        item = self._log_last_item
        if no <= 0 or item is None:
            return
        try:
            # 整行删掉（含换行），再按「正文 -> 时间戳」的顺序插回去：
            # Tk 的 insert 是「插在位置前」，所以后插的时间戳才会排在前面。
            self.log.delete(f"{no}.0", f"{no + 1}.0")
            suffix = f"（×{self._log_last_count}）" if self._log_last_count > 1 else ""
            self.log.insert(f"{no}.0", item["text"] + suffix + "\n", self._log_last_tag)
            if item.get("t"):
                self.log.insert(f"{no}.0", item["t"] + "  ", "time")
        except Exception:
            pass

    def _insert_log_items(self, items: List[Dict[str, Any]]) -> None:
        """把日志条目写进日志框。

        连续出现的**同一句话**会合并成一行并标「×N」——不然一条
        「已最小化到后台」之类的提示能刷出几十行，把其它日志全淹掉。
        """
        for item in items:
            text = item.get("text") or ""
            if not text:
                continue
            if self._log_last_text is not None and text == self._log_last_text:
                self._log_last_count += 1
                self._redraw_last_log_line()
                continue
            tag = self._log_tag(text)
            if item.get("t"):
                self.log.insert("end", item["t"] + "  ", "time")
            self.log.insert("end", text + "\n", tag)
            self._log_last_text = text
            self._log_last_count = 1
            self._log_last_tag = tag
            self._log_last_item = item
            # Text 末尾永远多两行（一个空行 + 一个隐含的空行），
            # 所以刚插入的这行 = end 的行号 - 2。
            self._log_last_line = int(self.log.index("end").split(".")[0]) - 2

    def _reload_log(self) -> None:
        """重建界面后把历史日志重新灌回日志框。"""
        try:
            lines = [ln for ln in self.engine.log_tail(self.LOG_LINES)
                     if ln.get("text")]
            self.log.configure(state="normal")
            self.log.delete("1.0", "end")
            self._log_last_text = None
            self._log_last_count = 0
            self._log_last_line = 0
            self._insert_log_items(lines)
            self.log.configure(state="disabled")
            self.log.see("end")
            self._last_log_seq = self.engine.log_seq
        except Exception:
            pass

    def _refresh_log(self) -> None:
        """只追加新增行（之前是整块删+重建，每条心跳日志都要重画 200 行）。"""
        seq = self.engine.log_seq
        if seq == self._last_log_seq:
            return
        first = self._last_log_seq < 0          # 首次：整块填充
        if first:
            lines = self.engine.log_tail(self.LOG_LINES)
        else:
            lines = self.engine.log_tail(min(seq - self._last_log_seq,
                                             self.LOG_LINES))
        self._last_log_seq = seq
        if not lines:
            return
        self.log.configure(state="normal")
        if first:
            self.log.delete("1.0", "end")
            self._log_last_text = None
            self._log_last_count = 0
            self._log_last_line = 0
        lines = [ln for ln in lines if ln.get("text")]
        if not lines:
            self.log.configure(state="disabled")
            return
        self._insert_log_items(lines)
        self.log.configure(state="disabled")
        self.log.see("end")
        # 引擎的 deque(maxlen=100) 会丢旧行，这里自己按窗口容量裁剪
        shown = int(self.log.index("end-1c").split(".")[0])
        if shown > self.LOG_LINES + 8:
            cut = shown - self.LOG_LINES
            self.log.configure(state="normal")
            self.log.delete("1.0", f"{cut}.0")
            self.log.configure(state="disabled")
            self._log_last_line -= cut       # 裁掉前面的行，行号要跟着挪
        self.log_hint.configure(text=trf(f"共 {min(shown, self.LOG_LINES)} 条", f"{min(shown, self.LOG_LINES)} lines"))

    # ------------------------------------------------------------------ #
    # 托盘图标 + 关闭行为
    # ------------------------------------------------------------------ #

    def _pref_path(self) -> str:
        return os.path.join(DATA_DIR, "ui_prefs.json")

    def _load_prefs(self) -> Dict[str, Any]:
        """读 ui_prefs.json（不存在或坏了就当没设置过）。

        用 utf-8-sig：有些编辑器（记事本等）保存时会加 BOM，
        普通 utf-8 读会直接抛错。
        """
        try:
            with open(self._pref_path(), "r", encoding="utf-8-sig") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_prefs(self) -> None:
        """写 ui_prefs.json。写失败也不影响使用（只是下次还会再问一遍）。"""
        try:
            with open(self._pref_path(), "w", encoding="utf-8") as f:
                json.dump({"close_action": self._close_pref,
                           "minimize_to_tray": bool(self._minimize_to_tray),
                           "lang": get_lang()},
                          f, ensure_ascii=False, indent=2)
        except Exception:
            pass

    def _apply_lang_pref(self) -> None:
        """按偏好设置界面语言（默认中文）。"""
        try:
            lang = str(self._load_prefs().get("lang", "zh"))
        except Exception:
            lang = "zh"
        set_lang(lang)

    def _toggle_lang(self) -> None:
        """切换界面语言（中文 <-> English），并记住选择。

        只重建界面，引擎和 MIDI 连接都不受影响，弹奏不会断。
        """
        new = "en" if get_lang() == "zh" else "zh"
        set_lang(new)
        self._save_prefs()
        self._rebuild_ui()
        self._apply_tray_labels()
        self._sync_tray_state()
        try:
            if new == "en":
                self.engine.log_external(
                    "界面语言已切换为英文。"
                    if new == "zh" else "Interface language switched to English.")
            else:
                self.engine.log_external(
                    "界面语言已切换为中文。"
                    if new == "zh" else "Interface language switched to Chinese.")
        except Exception:
            pass

    def _load_pref(self) -> str:
        """读上次的关闭偏好: ask / exit / tray。"""
        val = str(self._load_prefs().get("close_action", "ask"))
        return val if val in ("ask", "exit", "tray") else "ask"

    def _save_pref(self, action: str) -> None:
        self._close_pref = action
        self._save_prefs()

    def _load_minimize_pref(self) -> bool:
        """最小化时要不要收进托盘（默认收）。"""
        return bool(self._load_prefs().get("minimize_to_tray", True))

    def _tray_menu(self) -> List[Tuple[str, Optional[str], Optional[Dict[str, bool]]]]:
        """托盘右键菜单（按当前语言生成）。"""
        return [(tr("显示主窗口", "Show main window"), "show", None),
                (tr("最小化到后台", "Minimize to background"), "hide", None),
                ("---", None, None),
                (tr("关闭时总是询问", "Always ask when closing"), "toggle_ask",
                 {"checked": self._close_pref == "ask"}),
                (tr("最小化时收进托盘", "Minimize to tray"), "toggle_min_tray",
                 {"checked": bool(self._minimize_to_tray)}),
                ("---", None, None),
                (tr("完全退出", "Quit completely"), "quit", None)]

    def _apply_tray_labels(self) -> None:
        """语言切换后更新托盘菜单文字（托盘菜单不在界面里，不会自动重建）。"""
        if self._tray is None:
            return
        for label, entry_id, _opts in self._tray_menu():
            if not entry_id:
                continue
            try:
                self._tray.set_menu_label(entry_id, label)
            except Exception:
                pass

    def _start_tray(self) -> None:
        """启动托盘图标（失败也不影响主功能）。"""
        icon_path = os.path.join(SCRIPT_DIR, "app.ico")
        if not os.path.exists(icon_path):
            return
        try:
            import tray
            self._tray = tray.TrayIcon(
                title=tr("钢琴力度重映射", "Piano Velocity Remapper"),
                icon_path=icon_path,
                menu=[(label, entry_id, opts)
                      for label, entry_id, opts in self._tray_menu()],
                on_event=self._on_tray_event)
            self._tray.start()
        except Exception as exc:  # noqa: BLE001 - 托盘失败不能影响主功能
            self._tray = None
            self.engine.log_external(trf(f"托盘图标启动失败（不影响使用）: {exc}", f"The tray icon failed to start (everything else still works): {exc}"),
                                     stamped=False)

    def _on_tray_event(self, event: str) -> None:
        """托盘线程回调：只往队列里丢事件，真正的动作在 Tk 线程做。"""
        self._tray_queue.put(event)

    def _poll_tray(self) -> None:
        try:
            while True:
                self._handle_tray_event(self._tray_queue.get_nowait())
        except queue.Empty:
            pass
        except Exception:
            pass
        # 后台线程做的虚拟端口检查，结果也在这里取回（统一切回 Tk 线程）
        try:
            while True:
                self._handle_port_result(*self._port_queue.get_nowait())
        except queue.Empty:
            pass
        except Exception:
            pass
        # 端口热插拔监测的结果也在这里取回
        try:
            while True:
                self._apply_port_lists(*self._port_watch_queue.get_nowait())
        except queue.Empty:
            pass
        except Exception:
            pass
        try:
            self.root.after(150, self._poll_tray)
        except Exception:
            pass

    def _handle_tray_event(self, event: str) -> None:
        if event in ("show", "left_click", "double_click"):
            self._show_window()
        elif event == "hide":
            self._hide_to_tray("close")
        elif event == "quit":
            self._quit()
        elif event == "toggle_ask":
            self._toggle_always_ask()
        elif event == "toggle_min_tray":
            self._toggle_minimize_to_tray()

    def _show_window(self) -> None:
        """把主窗口叫回前台（托盘左键/双击、再次启动都走这里）。"""
        try:
            self._hidden_to_tray = False
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
        except Exception:
            pass
        self._sync_tray_state()

    def _hide_to_tray(self, reason: str = "close") -> None:
        """收到「留在后台」。没有托盘图标就不能藏，否则用户找不回来。"""
        if self._hidden_to_tray:
            # 已经在后台了。窗口最小化时 <Unmap> 会连着触发好几次，
            # 这里直接返回，别重复 withdraw、也别重复刷日志。
            return
        if self._tray is None or not getattr(self._tray, "visible", False):
            # 托盘不可用：点关闭只能真退出；最小化的话就别管，保持最小化状态
            if reason == "close":
                self._quit()
            return
        try:
            self._suppress_unmap = True
            self.root.withdraw()
        except Exception:
            self._suppress_unmap = False
            self._quit()
            return
        self._suppress_unmap = False
        self._hidden_to_tray = True
        self._sync_tray_state()
        self.engine.log_external(tr("已最小化到后台：托盘图标常驻，力度转发继续运行。", "Minimized to the background: the tray icon stays and velocity forwarding keeps running."))
        tip = (tr("已最小化到托盘，双击托盘图标可以重新打开窗口。", "Minimized to the tray — click the tray icon to reopen the window.")
               if reason == "minimize"
               else tr("仍在后台运行，双击托盘图标可以重新打开窗口。", "Still running in the background — click the tray icon to reopen the window."))
        if reason not in self._tray_notified:
            self._tray_notified.add(reason)
            self._tray_notify(tr("力度重映射", "Velocity Remapper"), tip)

    def _on_unmap(self, _e: Any = None) -> None:
        """窗口被最小化 -> 收进托盘（可在托盘菜单里关掉这个行为）。"""
        if self._suppress_unmap or not self._minimize_to_tray:
            return
        try:
            if self.root.state() != "iconic":     # 只有最小化才收，withdraw 不算
                return
        except Exception:
            return
        self.root.after_idle(self._hide_to_tray, "minimize")

    def _toggle_always_ask(self) -> None:
        """托盘菜单：恢复「关闭时总是询问」。"""
        self._close_pref = "ask"
        self._save_prefs()
        self._sync_tray_menu()
        self.engine.log_external(tr("已恢复「关闭时总是询问」：下次点关闭会再次提示。", "Restored “Always ask when closing”: closing will ask again next time."))
        self._tray_notify(tr("力度重映射", "Velocity Remapper"), tr("已恢复关闭时询问。", "Restored the close prompt."))

    def _toggle_minimize_to_tray(self) -> None:
        """托盘菜单：切换「最小化时收进托盘」。"""
        self._minimize_to_tray = not self._minimize_to_tray
        self._save_prefs()
        self._sync_tray_menu()
        self.engine.log_external(
            tr("最小化时收进托盘：", "Minimize to tray:") + (tr("已开启", "on") if self._minimize_to_tray else tr("已关闭", "off")))

    def _sync_tray_menu(self) -> None:
        """把当前状态同步到托盘菜单的勾选态。"""
        if self._tray is None:
            return
        try:
            self._tray.set_menu_check("toggle_ask", self._close_pref == "ask")
            self._tray.set_menu_check("toggle_min_tray",
                                       bool(self._minimize_to_tray))
        except Exception:
            pass

    def _sync_tray_state(self) -> None:
        """把当前状态同步到托盘图标的悬停提示。"""
        if self._tray is None:
            return
        try:
            self._tray.set_tooltip(
                tr("钢琴力度重映射 · 后台运行中（双击打开）", "Piano Velocity Remapper · running in the background (click to open)")
                if self._hidden_to_tray else tr("钢琴力度重映射", "Piano Velocity Remapper"))
        except Exception:
            pass

    def _tray_notify(self, title: str, text: str) -> None:
        try:
            if self._tray is not None:
                self._tray.notify(title, text)
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 虚拟 MIDI 端口（loopMIDI 之类）
    # ------------------------------------------------------------------ #

    def _ensure_output_port(self) -> None:
        """启动后异步检查输出端口。

        端口没了就换用系统里其它虚拟端口；再没有就自动把对方自己装的
        loopMIDI 拉起来（只是启动本机已安装的程序）。整个检查放到后台线程，
        不卡界面。
        """
        def worker() -> None:
            try:
                import midi_ports
                cfg = self.engine.config()
                name, note = midi_ports.ensure_output_port(
                    cfg.get("output_port", ""))
            except Exception as exc:      # noqa: BLE001
                name, note = None, trf(f"检查 MIDI 端口时出错：{exc}", f"Error while checking MIDI ports: {exc}")
            self._port_queue.put((name, note))

        threading.Thread(target=worker, name="port-check", daemon=True).start()

    def _handle_port_result(self, name: Optional[str], note: str) -> None:
        """端口检查的结果（已经在 Tk 线程）。"""
        if note:
            self.engine.log_external(trf(f"[端口] {note}", f"[Ports] {note}"))
        if name:
            try:
                self.engine.update({"output_port": name})
            except Exception:
                pass
            self._sync_ports()
        elif not self._port_guide_shown:
            self._port_guide_shown = True
            self.root.after(600, lambda: self._show_port_guide(note))

    def _show_port_guide(self, message: str) -> None:
        """弹出「需要一个虚拟 MIDI 端口」的引导。"""
        try:
            PortGuideDialog(self.root, message or tr("没有找到可用的输出端口。", "No usable output port found."),
                             on_retry=self._ensure_output_port)
        except Exception:
            pass

    def _update_port_hint(self) -> None:
        """端口卡片下面那行提示：输入端接上了没、输出端是不是虚拟端口。"""
        try:
            import midi_ports
            outs = midi_ports.list_outputs()
            ins = midi_ports.list_inputs()
            cfg = self.engine.config()
            cur = cfg.get("output_port", "")
            cur_in = cfg.get("input_port_substring", "")
        except Exception:
            return
        out_matched = next((n for n in outs
                            if cur and cur.lower() in n.lower()), "")
        # 输入端：配置为空 = 自动模式，按自动匹配的结果判断
        if cur_in:
            in_matched = next((n for n in ins
                               if cur_in.lower() in n.lower()), "")
        else:
            in_matched = vm.auto_pick_input_port(ins) or ""
        # 输入端优先：没键盘的话，下面的输出端说什么都没意义
        if not in_matched:
            if cur_in:
                in_text = trf(f"没找到输入端口「{cur_in}」，请重新选择",
                              f"Input port “{cur_in}” not found — please pick another")
            else:
                in_text = tr("没有输入端口，请连接键盘后点「刷新端口」",
                             "No input port — connect a keyboard and hit “Refresh ports”")
        else:
            in_text = ""
        try:
            if not out_matched:
                out_text = tr("没找到输出端口，请确认虚拟 MIDI 端口已打开", "Output port not found — make sure your virtual MIDI port is running")
                fg = WARN
            elif midi_ports.looks_virtual(out_matched):
                out_text = trf(f"虚拟端口「{out_matched}」· DAW 里请选同一个", f"Virtual port “{out_matched}” · pick the same one in your DAW")
                fg = GOOD
            else:
                out_text = trf(f"「{out_matched}」是硬件端口，DAW 收不到重映射后的力度", f"“{out_matched}” is a hardware port — your DAW won't get the remapped velocity")
                fg = WARN
            if in_text:
                out_text = f"{in_text} · {out_text}"
                fg = WARN
            self.port_hint.configure(text=out_text, fg=fg)
        except Exception:
            pass

    def _on_activate_event(self) -> None:
        """收到「再次启动」的唤醒信号（监听线程上触发，只丢队列）。"""
        self._tray_queue.put("show")

    def _quit(self) -> None:
        """真正退出：收掉托盘图标、停引擎、关窗口。"""
        try:
            if self._tray is not None:
                self._tray.stop()
                self._tray = None
        except Exception:
            pass
        try:
            self.engine.stop(timeout=1.5)
        except Exception:
            pass
        try:
            self.root.destroy()
        except Exception:
            pass

    # ------------------------------------------------------------------ #
    # 运行 / 退出
    # ------------------------------------------------------------------ #

    def run(self) -> None:
        try:
            self.root.mainloop()
        finally:
            try:
                if self._tray is not None:
                    self._tray.stop()
            except Exception:
                pass
            try:
                self.engine.stop(timeout=1.0)
            except Exception:
                pass

    def on_close(self) -> None:
        """点关闭：问一句是完全退出还是留在后台（除非用户记住了偏好）。"""
        pref = self._close_pref
        if pref == "exit":
            self._quit()
            return
        if pref == "tray":
            self._hide_to_tray("close")
            return
        try:
            action, remember = CloseDialog.ask(self.root)
        except Exception:
            action, remember = "exit", False
        if remember:
            self._close_pref = action
            self._save_prefs()
            self._sync_tray_menu()
            self.engine.log_external(
                tr("已记住关闭方式：", "Close behaviour remembered:")
                + (tr("下次点关闭直接完全退出。", "closing will now quit completely.") if action == "exit"
                   else tr("下次点关闭直接收进后台。", "closing will now hide to the background."))
                + tr("（可在托盘图标右键菜单里恢复）", "(you can undo this from the tray icon menu)"))
        if action == "exit":
            self._quit()
        else:
            self._hide_to_tray("close")


# --------------------------------------------------------------------------- #
# 「再次启动 -> 唤醒后台实例」
#
# 用了托盘之后，用户很容易再点一次桌面快捷方式（窗口明明在后台跑着）。
# 主流做法是：第二次启动不做任何事，只把已有实例的窗口叫回前台。
# 这里用一个 Windows 命名事件做通知：
#   - 第一个实例 CreateEventW 独占它，并起一个线程等这个事件；
#   - 后续实例 OpenEventW + SetEvent，把第一个实例叫醒。
# 事件句柄随进程退出自动销毁，所以不会留下「幽灵实例」。
# --------------------------------------------------------------------------- #

_ACTIVATE_EVENT_NAME = "Local\\PianoVelocityMapperActivate"
_EVENT_MODIFY_STATE = 0x0002
_ERROR_ALREADY_EXISTS = 183
_WAIT_OBJECT_0 = 0x00000000
_INFINITE = 0xFFFFFFFF


def _kernel32() -> Any:
    try:
        return ctypes.WinDLL("kernel32", use_last_error=True)
    except Exception:
        return None


def _start_activate_listener(on_activate: Callable[[], None]) -> None:
    """第一个实例：占住命名事件并监听「唤醒」信号。

    事件已被别人占着（说明另一个实例先起来了）就直接放弃监听。
    """
    if os.name != "nt":
        return
    kernel32 = _kernel32()
    if kernel32 is None:
        return
    try:
        kernel32.CreateEventW.restype = wintypes.HANDLE
        kernel32.CreateEventW.argtypes = [ctypes.c_void_p, wintypes.BOOL,
                                          wintypes.BOOL, wintypes.LPCWSTR]
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        kernel32.ResetEvent.restype = wintypes.BOOL
        kernel32.ResetEvent.argtypes = [wintypes.HANDLE]

        handle = kernel32.CreateEventW(None, True, False, _ACTIVATE_EVENT_NAME)
        if not handle:
            return
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return

        def waiter() -> None:
            while True:
                if kernel32.WaitForSingleObject(handle, _INFINITE) != _WAIT_OBJECT_0:
                    return
                kernel32.ResetEvent(handle)
                try:
                    on_activate()
                except Exception:
                    pass

        threading.Thread(target=waiter, name="activate-listener",
                         daemon=True).start()
    except Exception:
        pass


def _activate_running_instance(retries: int = 12, delay: float = 0.1) -> bool:
    """通知已在运行的实例把窗口叫回前台。成功返回 True。

    带重试：第一个实例可能刚起来还没建好事件，等一下再来。
    """
    if os.name != "nt":
        return False
    kernel32 = _kernel32()
    if kernel32 is None:
        return False
    try:
        kernel32.OpenEventW.restype = wintypes.HANDLE
        kernel32.OpenEventW.argtypes = [wintypes.DWORD, wintypes.BOOL,
                                        wintypes.LPCWSTR]
        kernel32.SetEvent.restype = wintypes.BOOL
        kernel32.SetEvent.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    except Exception:
        return False

    for _ in range(max(1, retries)):
        handle = kernel32.OpenEventW(_EVENT_MODIFY_STATE, False,
                                     _ACTIVATE_EVENT_NAME)
        if handle:
            try:
                kernel32.SetEvent(handle)
                return True
            except Exception:
                return False
            finally:
                kernel32.CloseHandle(handle)
        time.sleep(delay)
    return False


def _already_running_dialog() -> None:
    win = tk.Tk()
    win.withdraw()
    _apply_icon(win, os.path.join(SCRIPT_DIR, "app.ico"))
    from tkinter import messagebox
    messagebox.showinfo(
        tr("力度重映射", "Velocity Remapper"),
        tr("工具已经在运行了。\n\n"
           "如果它已最小化到后台，请双击任务栏右下角\n"
           "托盘里的图标把窗口叫回来；\n"
           "实在找不到，可在任务管理器结束 "
           "pythonw.exe 后再启动。",
           "The tool is already running.\n\n"
           "If it is minimized to the background, click the icon in the\n"
           "system tray (bottom right) to bring the window back;\n"
           "if you cannot find it, end "
           "pythonw.exe in Task Manager and start again."))
    win.destroy()


def main(config_path: Optional[str] = None) -> int:
    if not vm._check_single_instance():
        # 已经有实例在跑（多半是收在后台托盘里）：把它的窗口叫回来，
        # 而不是再弹一次「已经在运行了」
        if _activate_running_instance():
            return 0
        _already_running_dialog()
        return 0
    App(config_path).run()
    return 0


if __name__ == "__main__":
    sys.exit(main())

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
    """深色下拉选择器：点击弹出浮层列表（比 ttk.Combobox 好看且可完全控制配色）。"""

    MAX_ROWS = 9
    ROW_H = 30

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
        self._busy = False
        self._render()
        for w in (self, self._inner, self._btn, self._arrow):
            w.bind("<Button-1>", self._toggle)

    def _render(self) -> None:
        self._btn.configure(text=self._value or "（无可用端口）",
                            fg=TEXT if self._value else TEXT_FADE)

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
        if popup is not None:
            try:
                popup.grab_release()
                popup.destroy()
            except Exception:
                pass

    def _open(self) -> None:
        if not self._values:
            return
        popup = tk.Toplevel(self)
        popup.wm_overrideredirect(True)
        self._popup = popup
        rows = min(self.MAX_ROWS, len(self._values))
        w = self.winfo_width()
        x = self.winfo_rootx()
        y = self.winfo_rooty() + self.winfo_height() + px(2)
        popup.geometry(f"{w}x{px(self.ROW_H) * rows + px(4)}+{x}+{y}")
        popup.configure(bg=BORDER)

        inner = tk.Frame(popup, bg=CARD)
        inner.pack(fill="both", expand=True, padx=1, pady=1)

        for i in range(rows):
            idx = self._offset + i
            if idx >= len(self._values):
                break
            name = self._values[idx]
            sel = name == self._value
            row = tk.Label(inner, text=("✓  " if sel else "    ") + name,
                           bg=ACCENT_DIM if sel else CARD,
                           fg=TEXT if sel else TEXT_DIM,
                           font=F(9), anchor="w", cursor="hand2",
                           padx=px(10), pady=px(6))
            row.pack(fill="x")
            row.bind("<Button-1>", lambda _e, n=name: self._pick(n))
            row.bind("<Enter>", lambda _e, r=row, s=sel:
                     r.configure(bg=ACCENT_DIM if s else CARD_HI))

        popup.bind("<Button-1>", lambda _e: self._close())
        popup.bind("<Escape>", lambda _e: self._close())
        popup.bind("<MouseWheel>", self._wheel)
        popup.grab_set()      # grab 之后，点击浮层外部的事件也被重定向到浮层 -> 触发关闭
        popup.focus_force()

    def _wheel(self, e: Any) -> None:
        if len(self._values) <= self.MAX_ROWS:
            return
        self._offset = max(0, min(len(self._values) - self.MAX_ROWS,
                                  self._offset - (1 if e.delta > 0 else -1)))
        self._close()
        self._open()

    def _pick(self, name: str) -> None:
        changed = name != self._value
        self._value = name
        self._render()
        self._close()
        if changed:
            self._on_change(name)


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
        self.create_text(l + gw, t + gh + px(9), text="原始力度 →", anchor="ne",
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
        self.title("力度重映射")
        self.configure(bg=CARD)
        self.resizable(False, False)
        self.transient(master)
        self.action = "tray"          # "tray" | "exit"
        self.remember = False
        self._done = False

        wrap = tk.Frame(self, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=px(22), pady=px(18))

        tk.Label(wrap, text="要完全退出这个工具吗？", bg=CARD, fg=TEXT,
                 font=F(12, "bold")).pack(anchor="w")
        tk.Label(wrap,
                 text="是（完全退出）：力度转发立即停止。\n"
                      "否（留在后台运行）：托盘图标常驻，转发不受影响。",
                 bg=CARD, fg=TEXT_DIM, font=F(9), justify="left").pack(
            anchor="w", pady=(px(8), px(12)))

        self._check = FlatCheck(wrap, "下次不再提示（记住这次的选择）",
                                command=self._on_check)
        self._check.pack(anchor="w")

        self._hint = tk.Label(wrap, text="", bg=CARD, fg=TEXT_FADE, font=F(8),
                              justify="left")
        self._hint.pack(anchor="w", pady=(px(4), 0))

        row = tk.Frame(wrap, bg=CARD)
        row.pack(anchor="e", pady=(px(18), 0))
        FlatButton(row, "留在后台运行", command=self._on_tray,
                   kind="accent").pack(side="right")
        FlatButton(row, "完全退出", command=self._on_exit,
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
                text="记住了：下次点关闭直接执行。\n"
                     "想改回来：托盘图标右键 ->「关闭时总是询问」。"
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
        self.title("需要虚拟 MIDI 端口")
        self.configure(bg=CARD)
        self.resizable(False, False)
        self.transient(master)
        self._on_retry = on_retry

        wrap = tk.Frame(self, bg=CARD)
        wrap.pack(fill="both", expand=True, padx=px(22), pady=px(18))

        tk.Label(wrap, text="需要一个虚拟 MIDI 端口", bg=CARD, fg=TEXT,
                 font=F(12, "bold")).pack(anchor="w")
        tk.Label(wrap, text=message, bg=CARD, fg=TEXT_DIM, font=F(9),
                 justify="left", wraplength=380).pack(
            anchor="w", pady=(px(8), px(12)))

        tk.Label(wrap,
                 text="本工具要把重映射后的力度转发给 DAW，需要借助虚拟 MIDI 端口。\n"
                      "Windows 上最常见的做法是装 loopMIDI（免费、个人使用免费）：\n\n"
                      "  1. 打开官网下载安装\n"
                      "  2. 打开 loopMIDI，右键托盘图标 ->「添加端口」，名字随意\n"
                      "  3. 回到本工具把「输出」选成那个端口，DAW 里也选同一个\n\n"
                      "装好后本工具会自动找到它，不用再重装本工具。",
                 bg=CARD, fg=TEXT_DIM, font=F(9), justify="left").pack(anchor="w")

        row = tk.Frame(wrap, bg=CARD)
        row.pack(anchor="e", pady=(px(18), 0))
        FlatButton(row, "重新检测", command=self._retry, kind="normal").pack(
            side="right", padx=(px(8), 0))
        FlatButton(row, "打开 loopMIDI 官网", command=self._open_site,
                   kind="accent").pack(side="right")
        FlatButton(row, "稍后再说", command=self._close, kind="ghost").pack(
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
        text = eng_mod.STATUS_TEXT.get(status, status)
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

        self.root.title("钢琴力度重映射")
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
        cfg = self.engine.config()
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)

        self._header = self._build_header()

        body = tk.Frame(self.root, bg=BG)
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
        self._statusbar = self._build_statusbar()

    # ---- 顶栏 ----
    def _build_header(self) -> tk.Frame:
        bar = tk.Frame(self.root, bg=BG)
        bar.grid(row=0, column=0, sticky="ew", padx=px(18), pady=(px(16), px(10)))

        box = tk.Frame(bar, bg=BG)
        box.pack(side="left")
        tk.Label(box, text="力度重映射", bg=BG, fg=TEXT, font=F(16, "bold")).pack(anchor="w")
        self.subtitle = tk.Label(box, text="正在检测 MIDI 设备…", bg=BG, fg=TEXT_DIM,
                                 font=F(9))
        self.subtitle.pack(anchor="w", pady=(px(3), 0))

        self.pill = Pill(bar, width=120)
        self.pill.pack(side="right", anchor="e")
        return bar

    # ---- 卡片：最大力度 + 曲线指数 ----
    def _build_mapping_card(self, parent: tk.Frame) -> Card:
        cfg = self.engine.config()
        card = Card(parent, "力度映射")
        card.body.grid_columnconfigure(0, weight=1)

        # 最大力度
        head = tk.Frame(card.body, bg=CARD)
        head.grid(row=0, column=0, sticky="ew")
        tk.Label(head, text="最大力度", bg=CARD, fg=TEXT, font=F(9)).pack(side="left")
        self.max_val = tk.Label(head, text=str(int(cfg["max_input_velocity"])), bg=CARD,
                                fg=ACCENT, font=F(13, "bold", mono=True))
        self.max_val.pack(side="right")

        self.max_slider = Slider(card.body, 2, 127, int(cfg["max_input_velocity"]),
                                 self._on_max_changed, width=250)
        self.max_slider.grid(row=1, column=0, sticky="ew", pady=(px(2), px(2)))

        row = tk.Frame(card.body, bg=CARD)
        row.grid(row=2, column=0, sticky="ew", pady=(0, px(16)))
        tk.Label(row, text="键盘用尽全力能达到的力度", bg=CARD, fg=TEXT_FADE,
                 font=F(8)).pack(side="left")
        self.suggest_btn = FlatButton(row, text="采用建议值", kind="ghost", font=F(8),
                                      command=self._use_suggested)
        self.suggest_btn.pack(side="right")

        # 曲线指数
        head2 = tk.Frame(card.body, bg=CARD)
        head2.grid(row=3, column=0, sticky="ew")
        tk.Label(head2, text="力度曲线", bg=CARD, fg=TEXT, font=F(9)).pack(side="left")
        tk.Label(head2, text="0.1/格", bg=CARD, fg=TEXT_FADE, font=F(8)).pack(
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
        for text, value, tip in (("线性", 1.0, "1-max 均匀拉伸到 1-127，手感最自然"),
                                 ("柔和", 1.7, "轻触更轻，用力才出效果，动态范围大"),
                                 ("爆发", 0.6, "轻触就出声，层次集中在下段")):
            FlatButton(presets, text=text, kind="ghost", font=F(8),
                       command=lambda v=value, t=tip: self._set_preset(v, t)).pack(
                side="left", padx=(0, px(8)))

        foot = tk.Frame(card.body, bg=CARD)
        foot.grid(row=7, column=0, sticky="ew", pady=(px(10), 0))
        # 多余的竖向空间给第 6 行（空行），底部提示贴在卡片下边缘
        card.body.grid_rowconfigure(6, weight=1)
        self.exp_hint = tk.Label(foot, text="1-max 均匀拉伸到 1-127，手感最自然",
                                 bg=CARD, fg=TEXT_FADE, font=F(8), anchor="w")
        self.exp_hint.pack(side="left")
        self.save_hint = tk.Label(foot, text="", bg=CARD, fg=GOOD, font=F(8))
        self.save_hint.pack(side="right")
        return card

    # ---- 卡片：曲线图 ----
    def _build_curve_card(self, parent: tk.Frame) -> Card:
        card = Card(parent, "力度曲线")
        self.curve = CurveView(card.body, height=150)
        self.curve.pack(fill="both", expand=True)
        return card

    # ---- 卡片：实时力度 ----
    def _build_realtime_card(self, parent: tk.Frame) -> Card:
        card = Card(parent, "实时力度")

        head = tk.Frame(card.body, bg=CARD)
        head.pack(fill="x")
        self.note_lbl = tk.Label(head, text="♪ —", bg=CARD, fg=TEXT, font=F(13, "bold"))
        self.note_lbl.pack(side="left")
        FlatButton(head, text="重置", kind="ghost", font=F(8),
                   command=self._reset_stats).pack(side="right")
        self.notes_lbl = tk.Label(head, text="已弹 0 个音符", bg=CARD, fg=TEXT_FADE,
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

        self.raw_meter = Meter(card.body, "原始", ACCENT)
        self.raw_meter.pack(fill="x")
        self.map_meter = Meter(card.body, "映射", GOOD)
        self.map_meter.pack(fill="x", pady=(px(4), px(4)))

        self.range_lbl = tk.Label(card.body, text="", bg=CARD, fg=TEXT_FADE, font=F(8))
        self.range_lbl.pack(anchor="w")

        # 弹性占位：把“建议最大力度 / 采用”这一行顶到卡片下边缘
        tk.Frame(card.body, bg=CARD).pack(fill="both", expand=True)

        sug = tk.Frame(card.body, bg=CARD)
        sug.pack(fill="x", pady=(px(10), 0))
        self.sug_lbl = tk.Label(sug, text="建议最大力度：弹几下后给出", bg=CARD,
                                fg=TEXT_DIM, font=F(8))
        self.sug_lbl.pack(side="left")
        self.suggest_btn2 = FlatButton(sug, text="采用", kind="accent", font=F(8),
                                       command=self._use_suggested)
        self.suggest_btn2.pack(side="right")
        return card

    # ---- 卡片：端口 ----
    def _build_ports_card(self, parent: tk.Frame) -> Card:
        card = Card(parent, "端口")
        card.body.grid_columnconfigure(1, weight=1)

        tk.Label(card.body, text="输入（键盘）", bg=CARD, fg=TEXT_DIM,
                 font=F(9)).grid(row=0, column=0, sticky="w", padx=(0, px(12)))
        self.in_dd = Dropdown(card.body, [], "", self._on_port_changed, width=230)
        self.in_dd.grid(row=0, column=1, sticky="ew")

        tk.Label(card.body, text="输出（虚拟端口）", bg=CARD, fg=TEXT_DIM,
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
        FlatButton(row, text="刷新端口", kind="ghost", font=F(8),
                   command=self._sync_ports).pack(side="left")
        tk.Label(row, text="切换后自动重连", bg=CARD, fg=TEXT_FADE,
                 font=F(8)).pack(side="right")
        return card

    # ---- 日志 ----
    def _build_log(self, parent: tk.Frame) -> Card:
        card = Card(parent, "", pad=10)
        head = tk.Frame(card.body, bg=CARD)
        head.pack(fill="x", pady=(0, px(4)))
        tk.Label(head, text="运行日志", bg=CARD, fg=TEXT_DIM,
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
    def _build_statusbar(self) -> tk.Frame:
        bar = tk.Frame(self.root, bg=BG)
        bar.grid(row=2, column=0, sticky="ew", padx=px(18), pady=(0, px(12)))
        self.status = tk.Label(bar, text="", bg=BG, fg=TEXT_FADE, font=F(8), anchor="w")
        self.status.pack(side="left")
        tk.Label(bar, text="最小化 / 关闭会收进托盘 · 改动自动保存",
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
        self._flash_saved("端口已更新，正在重连…")
        self._last_rev = -1

    def _sync_ports(self, initial: bool = False) -> None:
        try:
            inputs, outputs = self.engine.ports()
        except Exception as exc:
            self.status.configure(text=f"读取 MIDI 端口失败: {exc}")
            return
        cfg = self.engine.config()
        cur_in = cfg["input_port_substring"]
        # 输入端按「子串匹配」语义解析：优先显示实际连上的那个端口
        match = next((n for n in inputs if cur_in.lower() in n.lower()), "")
        if not match:
            match = inputs[0] if inputs else cur_in
        cur_out = cfg["output_port"]
        out_match = cur_out if cur_out in outputs else next(
            (n for n in outputs if cur_out.lower() in n.lower()),
            outputs[0] if outputs else cur_out)

        self.in_dd.set_values(inputs, match)
        self.out_dd.set_values(outputs, out_match)
        if initial and (match != cur_in or out_match != cur_out):
            self.engine.update({"input_port_substring": match, "output_port": out_match})
        self._update_port_hint()

    def _flash_saved(self, text: str = "已保存") -> None:
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
            st = dict(st, hint="引擎线程已停止，请看日志并重启工具")
        # 注意：要连 hint / input_name / output_name 一起比对。只比 status 会漏
        # 掉“端口换了我还显示旧的”这种情况（status 一直是 busy）。
        sub = st.get("hint") or ""
        bar = (f"输入 {st.get('input_name') or '—'}    →    "
               f"输出 {st.get('output_name') or '—'}")
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
        self.notes_lbl.configure(text=f"已弹 {s['notes']} 个音符")
        self.raw_meter.set(raw, 127, max_in)
        self.map_meter.set(mapped, 127)
        self.range_lbl.configure(text=self._range_text(s))

        sug = s.get("suggested")
        if sug:
            same = sug == max_in
            self.sug_lbl.configure(text=f"建议最大力度 {sug}")
            self.suggest_btn2.configure(text="已是建议值" if same else f"采用 {sug}")
            self.suggest_btn2.set_enabled(not same)
            self.suggest_btn.configure(text="已是建议值" if same else f"采用建议值 {sug}")
            self.suggest_btn.set_enabled(not same)
        else:
            self.sug_lbl.configure(text="建议最大力度：弹几下后给出")
            self.suggest_btn.configure(text="采用建议值")
            self.suggest_btn.set_enabled(False)
            self.suggest_btn2.set_enabled(False)

        self._refresh_log()

    @staticmethod
    def _range_text(s: Dict[str, Any]) -> str:
        if not s["notes"]:
            return "还没有收到音符，弹一下试试"
        return (f"最近范围：原始 {s['raw_min']}–{s['raw_max']}"
                f"  →  映射 {s['map_min']}–{s['map_max']}")

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
        lines = [ln for ln in lines if ln.get("text")]
        if not lines:
            self.log.configure(state="disabled")
            return
        for item in lines:
            text = item["text"]
            if text.startswith(("[连接]", "[已", "转发模式", "Done")):
                tag = "ok"
            elif text.startswith(("[等待]", "提示", "可用")):
                tag = "warn"
            elif text.startswith(("[断开]", "[错误]", "警告")):
                tag = "bad"
            elif text.startswith("[配置]"):
                tag = "cfg"
            else:
                tag = ""
            if item.get("t"):
                self.log.insert("end", item["t"] + "  ", "time")
            self.log.insert("end", text + "\n", tag)
        self.log.configure(state="disabled")
        self.log.see("end")
        # 引擎的 deque(maxlen=100) 会丢旧行，这里自己按窗口容量裁剪
        shown = int(self.log.index("end-1c").split(".")[0])
        if shown > self.LOG_LINES + 8:
            self.log.configure(state="normal")
            self.log.delete("1.0", f"{shown - self.LOG_LINES}.0")
            self.log.configure(state="disabled")
        self.log_hint.configure(text=f"共 {min(shown, self.LOG_LINES)} 条")

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
                           "minimize_to_tray": bool(self._minimize_to_tray)},
                          f, ensure_ascii=False, indent=2)
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

    def _start_tray(self) -> None:
        """启动托盘图标（失败也不影响主功能）。"""
        icon_path = os.path.join(SCRIPT_DIR, "app.ico")
        if not os.path.exists(icon_path):
            return
        try:
            import tray
            self._tray = tray.TrayIcon(
                title="钢琴力度重映射",
                icon_path=icon_path,
                menu=[("显示主窗口", "show"),
                      ("最小化到后台", "hide"),
                      ("---", None),
                      ("关闭时总是询问", "toggle_ask",
                       {"checked": self._close_pref == "ask"}),
                      ("最小化时收进托盘", "toggle_min_tray",
                       {"checked": bool(self._minimize_to_tray)}),
                      ("---", None),
                      ("完全退出", "quit")],
                on_event=self._on_tray_event)
            self._tray.start()
        except Exception as exc:  # noqa: BLE001 - 托盘失败不能影响主功能
            self._tray = None
            self.engine.log_external(f"托盘图标启动失败（不影响使用）: {exc}",
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
        self.engine.log_external("已最小化到后台：托盘图标常驻，力度转发继续运行。")
        tip = ("已最小化到托盘，双击托盘图标可以重新打开窗口。"
               if reason == "minimize"
               else "仍在后台运行，双击托盘图标可以重新打开窗口。")
        if reason not in self._tray_notified:
            self._tray_notified.add(reason)
            self._tray_notify("力度重映射", tip)

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
        self.engine.log_external("已恢复「关闭时总是询问」：下次点关闭会再次提示。")
        self._tray_notify("力度重映射", "已恢复关闭时询问。")

    def _toggle_minimize_to_tray(self) -> None:
        """托盘菜单：切换「最小化时收进托盘」。"""
        self._minimize_to_tray = not self._minimize_to_tray
        self._save_prefs()
        self._sync_tray_menu()
        self.engine.log_external(
            "最小化时收进托盘：" + ("已开启" if self._minimize_to_tray else "已关闭"))

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
                "钢琴力度重映射 · 后台运行中（双击打开）"
                if self._hidden_to_tray else "钢琴力度重映射")
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
                name, note = None, f"检查 MIDI 端口时出错：{exc}"
            self._port_queue.put((name, note))

        threading.Thread(target=worker, name="port-check", daemon=True).start()

    def _handle_port_result(self, name: Optional[str], note: str) -> None:
        """端口检查的结果（已经在 Tk 线程）。"""
        if note:
            self.engine.log_external(f"[端口] {note}")
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
            PortGuideDialog(self.root, message or "没有找到可用的输出端口。",
                             on_retry=self._ensure_output_port)
        except Exception:
            pass

    def _update_port_hint(self) -> None:
        """端口卡片下面那行提示：当前输出是不是虚拟端口。"""
        try:
            import midi_ports
            outs = midi_ports.list_outputs()
            cur = self.engine.config().get("output_port", "")
        except Exception:
            return
        matched = next((n for n in outs
                        if cur and cur.lower() in n.lower()), "")
        try:
            if not matched:
                self.port_hint.configure(
                    text="没找到输出端口，请确认虚拟 MIDI 端口已打开", fg=WARN)
            elif midi_ports.looks_virtual(matched):
                self.port_hint.configure(
                    text=f"虚拟端口「{matched}」· DAW 里请选同一个", fg=GOOD)
            else:
                self.port_hint.configure(
                    text=f"「{matched}」是硬件端口，DAW 收不到重映射后的力度",
                    fg=WARN)
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
                "已记住关闭方式："
                + ("下次点关闭直接完全退出。" if action == "exit"
                   else "下次点关闭直接收进后台。")
                + "（可在托盘图标右键菜单里恢复）")
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
    messagebox.showinfo("力度重映射", "工具已经在运行了。\n\n"
                                      "如果它已最小化到后台，请双击任务栏右下角\n"
                                      "托盘里的图标把窗口叫回来；\n"
                                      "实在找不到，可在任务管理器结束 "
                                      "pythonw.exe 后再启动。")
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

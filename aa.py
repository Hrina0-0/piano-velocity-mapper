"""抗锯齿绘图工具。

Tk 的 Canvas 只有硬边像素：画曲线和圆点会有明显锯齿（阶梯状），
在高分屏上尤其难看。这里借用 pygame（本项目本来就依赖它）在 2~4 倍
分辨率下画好，再用 smoothscale 平滑缩小、导出 PNG 贴回 Tk，
得到真正的抗锯齿效果。

对外接口只有一个 Painter：在 1 倍坐标系里作画，内部自动放大。
"""
from __future__ import annotations

import base64
import functools
import io
import math
import os
from typing import Any, List, Optional, Sequence, Tuple

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pygame  # noqa: E402
import pygame.gfxdraw  # noqa: E402
import tkinter as tk  # noqa: E402

RGBA = Tuple[int, int, int, int]


def hex_rgba(color: str, alpha: int = 255) -> RGBA:
    """#rgb / #rrggbb -> (r, g, b, a)。"""
    c = color.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return (int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16), alpha)


class Painter:
    """在 1 倍坐标系里作画的画布；底层是 N 倍分辨率，导出时平滑缩小。"""

    def __init__(self, width: int, height: int, scale: int = 3,
                 bg: Optional[str] = None) -> None:
        self.scale = scale
        self.width = max(1, int(width))
        self.height = max(1, int(height))
        self.surf = pygame.Surface((self.width * scale, self.height * scale),
                                   pygame.SRCALPHA)
        if bg:
            self.surf.fill(hex_rgba(bg))

    # ---- 基本图元（坐标都是 1 倍系）----
    def polygon(self, points: Sequence[Tuple[float, float]], fill: str) -> None:
        pts = [(x * self.scale, y * self.scale) for x, y in points]
        if len(pts) >= 3:
            pygame.draw.polygon(self.surf, hex_rgba(fill), pts)

    def line(self, points: Sequence[Tuple[float, float]], color: str,
             width: float = 1.0) -> None:
        """折线。宽度 > 1 时拆成若干条平行的抗锯齿线。"""
        if len(points) < 2:
            return
        if width <= 1.01:
            pts = [(x * self.scale, y * self.scale) for x, y in points]
            pygame.draw.aalines(self.surf, hex_rgba(color), False, pts)
            return
        self.stroke(points, color, width)

    def stroke(self, points: Sequence[Tuple[float, float]], color: str,
               width: float = 2.0) -> None:
        """画一条有宽度的抗锯齿折线。

        pygame 没有“粗的抗锯齿线”，这里的做法是：算出折线各点的法线，
        在宽度方向上均匀地画若干条 1px 的 aalines 并行线，叠加后就是一条
        边缘平滑的粗线（比画多边形再缩放好得多，多边形缩放的边缘仍是硬阶梯）。
        """
        if len(points) < 2:
            return
        normals: List[Tuple[float, float]] = []
        count = len(points)
        for i, (x, y) in enumerate(points):
            if i == 0:
                dx, dy = points[1][0] - x, points[1][1] - y
            elif i == count - 1:
                dx, dy = x - points[-2][0], y - points[-2][1]
            else:
                dx = points[i + 1][0] - points[i - 1][0]
                dy = points[i + 1][1] - points[i - 1][1]
            length = math.hypot(dx, dy) or 1.0
            normals.append((-dy / length, dx / length))

        device_w = max(1, int(round(width * self.scale)))
        half = (device_w - 1) / 2.0
        for k in range(device_w):
            off = (-half + k) / self.scale
            pts = [(x * self.scale + nx * off * self.scale,
                    y * self.scale + ny * off * self.scale)
                   for (x, y), (nx, ny) in zip(points, normals)]
            pygame.draw.aalines(self.surf, hex_rgba(color), False, pts)

    def dashed_line(self, p0: Tuple[float, float], p1: Tuple[float, float],
                    color: str, dash: float = 3.0, width: float = 1.0) -> None:
        """虚线参考线：按 dash 长度拆成一段段实线。"""
        x0, y0 = p0
        x1, y1 = p1
        dx, dy = x1 - x0, y1 - y0
        length = (dx * dx + dy * dy) ** 0.5
        if length <= 0:
            return
        ux, uy = dx / length, dy / length
        pos = 0.0
        while pos < length:
            end = min(length, pos + dash)
            self.line([(x0 + ux * pos, y0 + uy * pos),
                       (x0 + ux * end, y0 + uy * end)], color, width)
            pos = end + dash

    def circle(self, cx: float, cy: float, r: float, fill: Optional[str] = None,
               outline: Optional[str] = None, width: float = 1.0) -> None:
        """圆/圆环：填充用 filled_circle，轮廓用 gfxdraw.aacircle（真抗锯齿）。"""
        x, y = int(round(cx * self.scale)), int(round(cy * self.scale))
        rr = int(round(r * self.scale))
        if rr <= 0:
            return
        if outline:
            pygame.draw.circle(self.surf, hex_rgba(outline), (x, y), rr)
            inner = rr - max(1, int(round(width * self.scale)))
            if fill:
                if inner > 0:
                    pygame.draw.circle(self.surf, hex_rgba(fill), (x, y), inner)
                    pygame.gfxdraw.aacircle(self.surf, x, y, inner,
                                            hex_rgba(fill))
            pygame.gfxdraw.aacircle(self.surf, x, y, rr, hex_rgba(outline))
        elif fill:
            pygame.draw.circle(self.surf, hex_rgba(fill), (x, y), rr)
            pygame.gfxdraw.aacircle(self.surf, x, y, rr, hex_rgba(fill))

    def rect(self, x0: float, y0: float, x1: float, y1: float,
             fill: Optional[str] = None) -> None:
        s = self.scale
        pygame.draw.rect(self.surf, hex_rgba(fill or "#000000"),
                         pygame.Rect(int(x0 * s), int(y0 * s),
                                     int((x1 - x0) * s), int((y1 - y0) * s)))

    # ---- 导出 ----
    def to_photo(self) -> tk.PhotoImage:
        """平滑缩小成 1 倍并变成 Tk 图片。"""
        small = pygame.transform.smoothscale(
            self.surf, (self.surf.get_width() // self.scale,
                        self.surf.get_height() // self.scale))
        buf = io.BytesIO()
        pygame.image.save(small, buf, "PNG")
        data = base64.b64encode(buf.getvalue()).decode("ascii")
        return tk.PhotoImage(data=data, format="png")


@functools.lru_cache(maxsize=32)
def circle_photo(radius: int, fill: Optional[str], outline: Optional[str],
                 width: int, scale: int = 4) -> tk.PhotoImage:
    """圆点图片（带缓存）：滑块的手柄、曲线上的落点圈都用它。"""
    pad = width // 2 + 1
    size = 2 * (radius + pad) + 2
    p = Painter(size, size, scale=scale)
    p.circle(size / 2.0, size / 2.0, radius, fill=fill, outline=outline,
             width=width)
    return p.to_photo()

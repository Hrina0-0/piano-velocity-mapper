"""抓一张应用窗口的截图，存成 PNG（给 README 用）。"""
import ctypes
import os
import sys

os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")
import pygame  # noqa: E402

SRCCOPY = 0x00CC0020
CAPTUREBLT = 0x40000000

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", ctypes.c_uint32), ("biWidth", ctypes.c_int32),
        ("biHeight", ctypes.c_int32), ("biPlanes", ctypes.c_uint16),
        ("biBitCount", ctypes.c_uint16), ("biCompression", ctypes.c_uint32),
        ("biSizeImage", ctypes.c_uint32), ("biXPelsPerMeter", ctypes.c_int32),
        ("biYPelsPerMeter", ctypes.c_int32), ("biClrUsed", ctypes.c_uint32),
        ("biClrImportant", ctypes.c_uint32),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER), ("bmiColors", ctypes.c_uint32 * 3)]


def grab(x0, y0, x1, y1):
    w, h = x1 - x0, y1 - y0
    hdc = user32.GetDC(None)
    mem_dc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    gdi32.SelectObject(mem_dc, bmp)
    gdi32.BitBlt(mem_dc, 0, 0, w, h, hdc, x0, y0, SRCCOPY | CAPTUREBLT)

    bi = BITMAPINFO()
    bi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bi.bmiHeader.biWidth = w
    bi.bmiHeader.biHeight = -h
    bi.bmiHeader.biPlanes = 1
    bi.bmiHeader.biBitCount = 32
    bi.bmiHeader.biCompression = 0
    buf = ctypes.create_string_buffer(w * h * 4)
    gdi32.GetDIBits(mem_dc, bmp, 0, h, buf, ctypes.byref(bi), 0)

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem_dc)
    user32.ReleaseDC(None, hdc)

    raw = bytes(buf)
    rows = []
    for y in range(h):
        base = y * w * 4
        row = bytearray(w * 3)
        for i in range(w):
            p = base + i * 4
            row[i * 3] = raw[p + 2]
            row[i * 3 + 1] = raw[p + 1]
            row[i * 3 + 2] = raw[p]
        rows.append(bytes(row))
    return pygame.image.frombuffer(b"".join(rows), (w, h), "RGB")


def main() -> int:
    x, y, w, h = (int(v) for v in sys.argv[1:5])
    out = sys.argv[5]
    pygame.display.init()
    pygame.display.set_mode((8, 8))
    img = grab(x, y, x + w, y + h)
    pygame.image.save(img, out)
    print("saved:", out, img.get_size())
    return 0


if __name__ == "__main__":
    sys.exit(main())

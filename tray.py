#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Windows 系统托盘（通知区域）图标
=================================

纯标准库实现（ctypes + Win32 API），不引入 pystray 之类的第三方依赖。

设计要点：
- 托盘图标跑在独立的工作线程里：该线程拥有一个隐藏的顶层窗口
  (CreateWindowExW，父窗口传 None，加 WS_EX_TOOLWINDOW 不进任务栏)
  和 Shell_NotifyIcon 的消息循环，因此完全不碰 Tk，不会和主线程的
  Tk 事件循环抢消息。
- 那个隐藏窗口同时是右键菜单的 owner：弹出菜单需要 owner 在前台，
  否则菜单点完不消失、方向键也选不了项。
- 事件回调 on_event 在【托盘线程】上触发。调用方（gui.py）需要自己用队列
  把事件转回 Tk 主线程再更新界面。
- 任何一步失败（图标加载失败、Shell_NotifyIcon 返回 False、窗口创建失败……）
  都只写日志、不抛异常，绝不把 GUI 弄崩；失败时 visible() 返回 False。

对外接口：
    tray = TrayIcon("标题", "app.ico", [("显示窗口", "show"), ("退出", "quit")],
                    on_event=cb)
    tray.start()      # 立刻返回
    tray.notify("提示", "内容")   # 气球提示
    tray.visible      # 图标是否在托盘里
    tray.stop()       # 移除图标并结束线程（可重复调用）
    tray.set_menu_check("toggle_ask", True)   # 运行时勾选/取消某个菜单项
    tray.set_tooltip("新的悬停提示")            # 运行时改悬停提示

菜单项写法（每项一个元组）：
    ("文字", "事件 id")                     普通项
    ("文字", "事件 id", {"checked": True})  带勾选态的项（勾会打在文字左边）
    ("文字", "事件 id", {"enabled": False})  灰色不可点的项
    ("---", None)                           分隔线

事件 id：
    "left_click"    —— 托盘图标左键单击（WM_LBUTTONUP）
    "double_click"  —— 托盘图标左键双击（WM_LBUTTONDBLCLK）
    以及 menu 里逐条给出的条目 id（原样透传）
"""

from __future__ import annotations

import ctypes
import logging
import os
import threading
from ctypes import wintypes
from typing import Callable, Dict, List, Optional, Sequence, Tuple

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# Win32 常量
# --------------------------------------------------------------------------- #

WM_DESTROY = 0x0002
WM_CLOSE = 0x0010
WM_QUIT = 0x0012
WM_NULL = 0x0000
WM_CONTEXTMENU = 0x007D
WM_COMMAND = 0x0111
WM_USER = 0x0400
WM_APP = 0x8000
WM_LBUTTONUP = 0x0202
WM_LBUTTONDBLCLK = 0x0203
WM_RBUTTONUP = 0x0205

# 托盘回调消息：鼠标事件由 Shell 转发到这个自定义消息
WM_TRAY_CALLBACK = WM_APP + 20

# Shell_NotifyIcon
NIM_ADD = 0x00
NIM_MODIFY = 0x01
NIM_DELETE = 0x02
NIM_SETFOCUS = 0x03
NIM_SETVERSION = 0x04

NIF_MESSAGE = 0x01
NIF_ICON = 0x02
NIF_TIP = 0x04
NIF_INFO = 0x10
NIF_SHOWTIP = 0x80   # NOTIFYICON_VERSION_4 下默认不显示系统气泡式提示，需显式开启

NOTIFYICON_VERSION_4 = 4
NIIF_INFO = 0x01
NIIF_WARNING = 0x02
NIIF_ERROR = 0x03

# 菜单 / 窗口
MF_STRING = 0x00000000
MF_SEPARATOR = 0x00000800
MF_CHECKED = 0x00000008
MF_DISABLED = 0x00000002
MF_GRAYED = 0x00000001
TPM_RIGHTBUTTON = 0x0002
TPM_NONOTIFY = 0x0080
TPM_RETURNCMD = 0x0100

WS_OVERLAPPED = 0x00000000
WS_EX_TOOLWINDOW = 0x00000080   # 不出现在任务栏 / Alt+Tab 里
CW_USEDEFAULT = -2147483648  # 0x80000000
PM_NOREMOVE = 0x0000
IDI_APPLICATION = 32512

# 资源加载
IMAGE_ICON = 1
LR_LOADFROMFILE = 0x0010
LR_DEFAULTSIZE = 0x0040

SM_CXSMICON = 49
SM_CYSMICON = 50

# 菜单命令 id 起始值（避开控件 id 与 WM_ 消息）
_MENU_CMD_BASE = 1000

# --------------------------------------------------------------------------- #
# ctypes 结构体（显式声明字段，兼容 64 位指针宽度）
# --------------------------------------------------------------------------- #

# LRESULT = LONG_PTR（64 位下是 64 位有符号整型）
LRESULT = ctypes.c_ssize_t
# WNDPROC = LRESULT (HWND, UINT, WPARAM, LPARAM)
# 非 Windows 平台没有 WINFUNCTYPE，退化成 CFUNCTYPE，保证 import 不会炸
_PROC_FACTORY = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)
WNDPROC = _PROC_FACTORY(LRESULT, wintypes.HWND, wintypes.UINT,
                        wintypes.WPARAM, wintypes.LPARAM)


class GUID(ctypes.Structure):
    """GUID（NOTIFYICONDATA 里用到，wintypes 里没有现成的）。"""

    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_ubyte * 8),
    ]


class WNDCLASSW(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class NOTIFYICONDATAW(ctypes.Structure):
    """NOTIFYICONDATAW（对应 C 的 NOTIFYICONDATAW，字段顺序必须一字不差）。"""

    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateMask", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("ItemGuid", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", wintypes.POINT),
    ]


# --------------------------------------------------------------------------- #
# Win32 API 声明（argtypes/restype 必须显式写，64 位下靠默认会截断指针）
# --------------------------------------------------------------------------- #

def _load_api() -> Tuple[ctypes.WinDLL, ctypes.WinDLL, ctypes.WinDLL]:
    """加载并声明 user32 / shell32 / kernel32 的原型。"""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    shell32 = ctypes.WinDLL("shell32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    # --- 窗口类 / 窗口 ---
    user32.RegisterClassW.argtypes = [ctypes.POINTER(WNDCLASSW)]
    user32.RegisterClassW.restype = wintypes.ATOM

    user32.UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
    user32.UnregisterClassW.restype = wintypes.BOOL

    user32.CreateWindowExW.argtypes = [
        wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
        ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
        wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, ctypes.c_void_p,
    ]
    user32.CreateWindowExW.restype = wintypes.HWND

    user32.DestroyWindow.argtypes = [wintypes.HWND]
    user32.DestroyWindow.restype = wintypes.BOOL

    user32.DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT,
                                     wintypes.WPARAM, wintypes.LPARAM]
    user32.DefWindowProcW.restype = LRESULT

    # --- 消息循环 ---
    user32.GetMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND,
                                   wintypes.UINT, wintypes.UINT]
    user32.GetMessageW.restype = ctypes.c_int

    user32.PeekMessageW.argtypes = [ctypes.POINTER(MSG), wintypes.HWND,
                                    wintypes.UINT, wintypes.UINT, wintypes.UINT]
    user32.PeekMessageW.restype = wintypes.BOOL

    user32.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
    user32.TranslateMessage.restype = wintypes.BOOL

    user32.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
    user32.DispatchMessageW.restype = LRESULT

    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT,
                                    wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.restype = wintypes.BOOL

    user32.PostThreadMessageW.argtypes = [wintypes.DWORD, wintypes.UINT,
                                          wintypes.WPARAM, wintypes.LPARAM]
    user32.PostThreadMessageW.restype = wintypes.BOOL

    user32.PostQuitMessage.argtypes = [ctypes.c_int]
    user32.PostQuitMessage.restype = None

    # --- 菜单 ---
    user32.CreatePopupMenu.restype = wintypes.HMENU
    user32.CreatePopupMenu.argtypes = []

    user32.AppendMenuW.argtypes = [wintypes.HMENU, wintypes.UINT,
                                   ctypes.c_size_t, wintypes.LPCWSTR]
    user32.AppendMenuW.restype = wintypes.BOOL

    user32.TrackPopupMenu.argtypes = [wintypes.HMENU, wintypes.UINT,
                                      ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                      wintypes.HWND, ctypes.c_void_p]
    user32.TrackPopupMenu.restype = wintypes.UINT

    user32.DestroyMenu.argtypes = [wintypes.HMENU]
    user32.DestroyMenu.restype = wintypes.BOOL

    user32.SetForegroundWindow.argtypes = [wintypes.HWND]
    user32.SetForegroundWindow.restype = wintypes.BOOL

    user32.GetForegroundWindow.argtypes = []
    user32.GetForegroundWindow.restype = wintypes.HWND

    user32.GetCursorPos.argtypes = [ctypes.POINTER(wintypes.POINT)]
    user32.GetCursorPos.restype = wintypes.BOOL

    user32.GetWindowThreadProcessId.argtypes = [wintypes.HWND,
                                                ctypes.POINTER(wintypes.DWORD)]
    user32.GetWindowThreadProcessId.restype = wintypes.DWORD

    user32.AttachThreadInput.argtypes = [wintypes.DWORD, wintypes.DWORD,
                                          wintypes.BOOL]
    user32.AttachThreadInput.restype = wintypes.BOOL

    # --- 资源 ---
    user32.LoadImageW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR,
                                  wintypes.UINT, ctypes.c_int, ctypes.c_int,
                                  wintypes.UINT]
    user32.LoadImageW.restype = wintypes.HANDLE

    user32.LoadIconW.argtypes = [wintypes.HINSTANCE, wintypes.LPCWSTR]
    user32.LoadIconW.restype = wintypes.HICON

    user32.DestroyIcon.argtypes = [wintypes.HICON]
    user32.DestroyIcon.restype = wintypes.BOOL

    user32.GetSystemMetrics.argtypes = [ctypes.c_int]
    user32.GetSystemMetrics.restype = ctypes.c_int

    # --- Shell 托盘 ---
    shell32.Shell_NotifyIconW.argtypes = [wintypes.DWORD,
                                          ctypes.POINTER(NOTIFYICONDATAW)]
    shell32.Shell_NotifyIconW.restype = wintypes.BOOL

    # --- kernel32 ---
    kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
    kernel32.GetModuleHandleW.restype = wintypes.HMODULE

    kernel32.GetCurrentThreadId.argtypes = []
    kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    return user32, shell32, kernel32


# --------------------------------------------------------------------------- #
# 托盘图标
# --------------------------------------------------------------------------- #

class TrayIcon:
    """Windows 托盘图标。

    参数
    ----
    title     : 鼠标悬停时显示的提示文字（NOTIFYICONDATA.szTip）
    icon_path : .ico 文件路径；加载失败时退回系统默认图标
    menu      : [(显示文字, 事件 id[, 选项字典]), ...]，右键菜单项；事件 id 原样回传。
                事件 id 为 None 的项按分隔线处理；选项字典支持
                {"checked": bool} 与 {"enabled": bool}。
    on_event  : 回调 Callable[[str], None]，在【托盘线程】上被调用
    """

    def __init__(self, title: str, icon_path: str,
                 menu: Sequence[Tuple[str, str]],
                 on_event: Callable[[str], None]) -> None:
        self._title = title
        self._icon_path = icon_path
        # 菜单项统一存成 (文字, 事件 id, 选项)，事件 id 为 None 表示分隔线
        self._menu: List[Tuple[str, Optional[str], Dict[str, bool]]] = []
        for item in menu:
            label = str(item[0])
            entry_id = item[1] if len(item) > 1 else None
            opts = dict(item[2]) if len(item) > 2 and item[2] else {}
            entry_id = str(entry_id) if entry_id is not None else None
            self._menu.append((label, entry_id, opts))
        self._on_event = on_event

        self._thread: Optional[threading.Thread] = None
        self._thread_id: Optional[int] = None
        self._hwnd: Optional[int] = None
        self._hinst: Optional[int] = None
        self._hicon: Optional[int] = None
        self._hicon_owned = False       # 自己加载的图标需要 DestroyIcon
        self._icon_added = False
        self._version4 = False          # 是否已切到 NOTIFYICON_VERSION_4
        self._visible = False
        self._icon_id = 1               # NOTIFYICONDATA.uID，窗口内唯一即可

        self._ready = threading.Event()     # 窗口/图标就绪
        self._stop_event = threading.Event()
        self._lock = threading.Lock()       # 保护 notify()/stop() 与托盘线程的竞争

        # 命令 id -> 菜单条目 id（分隔线不占命令 id）
        self._cmd_to_id: Dict[int, str] = {}
        for i, (_label, entry_id, _opts) in enumerate(self._menu):
            if entry_id is not None:
                self._cmd_to_id[_MENU_CMD_BASE + i] = entry_id

        self._user32: Optional[ctypes.WinDLL] = None
        self._shell32: Optional[ctypes.WinDLL] = None
        self._kernel32: Optional[ctypes.WinDLL] = None
        self._wndproc = None                     # 必须持有引用，否则被 GC 后窗口回调失效
        self._class_name = "RepianoTrayIcon_%d_%d" % (os.getpid(), id(self))

    # -- 公开接口 ---------------------------------------------------------- #

    @property
    def visible(self) -> bool:
        """图标当前是否还在托盘里。"""
        return self._visible

    @property
    def alive(self) -> bool:
        """托盘线程是否还活着（便于测试/外部判断）。"""
        thread = self._thread
        return bool(thread and thread.is_alive())

    def start(self) -> None:
        """创建窗口并添加托盘图标；立刻返回，工作线程在后台跑消息循环。"""
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop_event.clear()
            self._ready.clear()
            self._thread = threading.Thread(target=self._run,
                                            name="tray-icon",
                                            daemon=True)
            self._thread.start()

    def stop(self) -> None:
        """移除托盘图标并结束线程；可重复调用（幂等）。"""
        with self._lock:
            thread = self._thread

        self._stop_event.set()

        if thread is None:
            return

        # 等窗口建好（最多 3 秒），否则没有 hwnd 可投递关闭消息
        if self._hwnd is None:
            self._ready.wait(3.0)

        hwnd = self._hwnd
        if hwnd and self._user32 is not None:
            try:
                # 唤醒 GetMessageW：窗口收到 WM_CLOSE 后调用 PostQuitMessage
                self._user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
            except Exception:
                logger.exception("投递 WM_CLOSE 失败")

        if thread is not threading.current_thread():
            thread.join(timeout=5.0)

        if thread.is_alive() and self._user32 is not None and self._thread_id:
            # 兜底：直接给线程发 WM_QUIT
            try:
                self._user32.PostThreadMessageW(self._thread_id, WM_QUIT, 0, 0)
            except Exception:
                logger.exception("投递 WM_QUIT 失败")
            thread.join(timeout=2.0)

        self._visible = False

    def notify(self, title: str, message: str) -> None:
        """在托盘图标上弹一个气泡提示（balloon tip）。"""
        with self._lock:
            hwnd = self._hwnd
            if not hwnd or not self._icon_added or self._shell32 is None:
                logger.warning("notify() 被调用，但托盘图标尚未就绪，已忽略。")
                return

            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = hwnd
            nid.uID = self._icon_id
            nid.uFlags = NIF_INFO
            nid.szInfoTitle = (title or "")[:63]
            nid.szInfo = (message or "")[:255]
            nid.dwInfoFlags = NIIF_INFO
            # uTimeoutOrVersion 是联合体：NIM_MODIFY 时解释为 uTimeout（毫秒），
            # NIM_SETVERSION 时解释为 uVersion。Vista+ 已弃用 uTimeout，
            # 气泡时长改由系统辅助功能设置决定，所以这里写 4（=版本 4）
            # 在 Win10/11 上无副作用，仅表示“这是版本 4 的图标”。
            nid.uTimeoutOrVersion = NOTIFYICON_VERSION_4 if self._version4 else 0
            try:
                ok = self._shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid))
            except Exception:
                logger.exception("Shell_NotifyIconW(NIM_MODIFY) 调用失败")
                return
            if not ok:
                logger.error("Shell_NotifyIconW(NIM_MODIFY) 返回 False，错误码 %d",
                             ctypes.get_last_error())

    def set_menu_check(self, entry_id: str, checked: bool) -> None:
        """勾选/取消某个菜单项（如「关闭时总是询问」的开关状态）。

        菜单是每次右键时现建的，所以这里只要改状态即可，下次右键就生效。
        """
        with self._lock:
            for item in self._menu:
                if item[1] == entry_id:
                    item[2]["checked"] = bool(checked)
                    return

    def set_tooltip(self, text: str) -> None:
        """改鼠标悬停时的提示文字（可用来显示当前状态）。"""
        with self._lock:
            hwnd = self._hwnd
            if not hwnd or not self._icon_added or self._shell32 is None:
                return
            nid = NOTIFYICONDATAW()
            nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
            nid.hWnd = hwnd
            nid.uID = self._icon_id
            nid.uFlags = NIF_TIP | NIF_SHOWTIP
            nid.szTip = (text or "")[:127]
            try:
                if not self._shell32.Shell_NotifyIconW(NIM_MODIFY, ctypes.byref(nid)):
                    logger.warning("Shell_NotifyIconW(NIM_MODIFY) 更新提示失败，错误码 %d",
                                   ctypes.get_last_error())
            except Exception:
                logger.exception("Shell_NotifyIconW(NIM_MODIFY) 调用失败")

    # -- 内部实现 ---------------------------------------------------------- #

    def _run(self) -> None:
        """托盘线程主体：建窗口 -> 加图标 -> 消息循环 -> 清理。"""
        self._thread_id = threading.get_ident()
        try:
            self._user32, self._shell32, self._kernel32 = _load_api()
        except Exception:
            logger.exception("加载 Win32 API 失败，托盘图标不可用。")
            self._ready.set()
            return

        try:
            hwnd = self._create_window()
            if not hwnd:
                logger.error("创建托盘窗口失败，托盘图标不可用。")
                self._ready.set()
                return

            self._hwnd = hwnd
            self._ready.set()          # 窗口已就绪，stop() 可以投递 WM_CLOSE

            if not self._add_icon(hwnd):
                logger.error("添加托盘图标失败，托盘图标不可用。")
                return

            self._message_loop()
        except Exception:
            logger.exception("托盘线程异常退出")
        finally:
            self._cleanup()
            self._ready.set()

    # -- 窗口 --------------------------------------------------------------- #

    def _create_window(self) -> Optional[int]:
        """注册窗口类并创建一个隐藏的顶层窗口（同时也是右键菜单的 owner）。"""
        user32 = self._user32
        kernel32 = self._kernel32
        assert user32 is not None and kernel32 is not None

        hinst = kernel32.GetModuleHandleW(None)
        self._hinst = hinst

        self._wndproc = WNDPROC(self._wnd_proc)

        wc = WNDCLASSW()
        wc.style = 0
        wc.lpfnWndProc = self._wndproc
        wc.cbClsExtra = 0
        wc.cbWndExtra = 0
        wc.hInstance = hinst
        wc.hIcon = None
        wc.hCursor = None
        wc.hbrBackground = None
        wc.lpszMenuName = None
        wc.lpszClassName = self._class_name

        atom = user32.RegisterClassW(ctypes.byref(wc))
        if not atom:
            err = ctypes.get_last_error()
            if err != 1410:  # ERROR_CLASS_ALREADY_EXISTS：不影响使用，继续
                logger.error("RegisterClassW 失败，错误码 %d", err)
                return None

        # 托盘窗口：常规的隐藏顶层窗口（不是 HWND_MESSAGE）。
        # 原因：消息专用窗口永远没法被 SetForegroundWindow 置前，而弹出菜单
        # 需要自己的 owner 在前台——否则菜单点完不会消失、方向键也选不了项，
        # 跟主流软件的行为对不上。WS_EX_TOOLWINDOW 保证它不进任务栏、不进 Alt+Tab。
        hwnd = user32.CreateWindowExW(
            WS_EX_TOOLWINDOW,      # dwExStyle
            self._class_name,
            self._title,
            WS_OVERLAPPED,         # dwStyle（从不 ShowWindow，窗口一直隐藏）
            0, 0, 0, 0,            # x, y, w, h（隐藏窗口不需要真实尺寸）
            None,                  # hWndParent = None -> 顶层窗口
            None,                  # hMenu
            hinst,                 # hInstance
            None,                  # lpParam
        )
        if not hwnd:
            logger.error("CreateWindowExW 失败，错误码 %d", ctypes.get_last_error())
            return None
        return hwnd

    # -- 托盘图标 ----------------------------------------------------------- #

    def _load_icon(self) -> Tuple[Optional[int], bool]:
        """加载 .ico；返回 (hicon, 是否需要自己销毁)。失败时退回系统默认图标。"""
        user32 = self._user32
        assert user32 is not None
        path = self._icon_path
        if path and os.path.isfile(path):
            # 优先按通知区域的小图标尺寸（通常 16x16）取图，取不到再退回默认尺寸
            cx = user32.GetSystemMetrics(SM_CXSMICON)
            cy = user32.GetSystemMetrics(SM_CYSMICON)
            for (w, h), flags in (((cx, cy), LR_LOADFROMFILE),
                                  ((0, 0), LR_LOADFROMFILE | LR_DEFAULTSIZE)):
                try:
                    hicon = user32.LoadImageW(None, path, IMAGE_ICON, w, h, flags)
                except Exception:
                    logger.exception("LoadImageW 加载 %s 失败", path)
                    hicon = None
                if hicon:
                    return hicon, True
            logger.error("LoadImageW 加载 %s 失败，改用系统默认图标", path)

        # 兜底：系统默认应用程序图标（共享资源，不能 DestroyIcon）
        try:
            hicon = user32.LoadIconW(None, ctypes.c_wchar_p(IDI_APPLICATION))
        except Exception:
            logger.exception("LoadIconW 失败")
            hicon = None
        return hicon, False

    def _add_icon(self, hwnd: int) -> bool:
        """调用 Shell_NotifyIconW(NIM_ADD) 把图标放进托盘。"""
        shell32 = self._shell32
        assert shell32 is not None

        hicon, owned = self._load_icon()
        self._hicon = hicon
        self._hicon_owned = owned

        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = self._icon_id
        # NIF_SHOWTIP：版本 4 默认不弹系统气泡式提示，不加这句鼠标悬停看不到文字
        nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP | NIF_SHOWTIP
        nid.uCallbackMessage = WM_TRAY_CALLBACK
        nid.hIcon = hicon
        nid.szTip = (self._title or "")[:127]

        try:
            ok = shell32.Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))
        except Exception:
            logger.exception("Shell_NotifyIconW(NIM_ADD) 调用失败")
            ok = False

        if not ok:
            logger.error("Shell_NotifyIconW(NIM_ADD) 返回 False，错误码 %d",
                         ctypes.get_last_error())
            return False

        # 切到 NOTIFYICON_VERSION_4：气泡提示需要，同时改变回调消息的打包方式
        ver = NOTIFYICONDATAW()
        ver.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        ver.hWnd = hwnd
        ver.uID = self._icon_id
        ver.uTimeoutOrVersion = NOTIFYICON_VERSION_4
        try:
            if shell32.Shell_NotifyIconW(NIM_SETVERSION, ctypes.byref(ver)):
                self._version4 = True
        except Exception:
            logger.exception("Shell_NotifyIconW(NIM_SETVERSION) 调用失败")

        self._icon_added = True
        self._visible = True
        return True

    def _delete_icon(self) -> None:
        """调用 Shell_NotifyIconW(NIM_DELETE) 把图标从托盘里摘掉。"""
        with self._lock:
            self._delete_icon_locked()

    def _delete_icon_locked(self) -> None:
        shell32 = self._shell32
        if shell32 is None or not self._icon_added:
            return
        hwnd = self._hwnd
        if not hwnd:
            self._icon_added = False
            return
        nid = NOTIFYICONDATAW()
        nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = hwnd
        nid.uID = self._icon_id
        try:
            if not shell32.Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid)):
                logger.warning("Shell_NotifyIconW(NIM_DELETE) 返回 False，错误码 %d",
                               ctypes.get_last_error())
        except Exception:
            logger.exception("Shell_NotifyIconW(NIM_DELETE) 调用失败")
        self._icon_added = False
        self._visible = False

    # -- 消息循环 ----------------------------------------------------------- #

    def _message_loop(self) -> None:
        """GetMessageW / TranslateMessage / DispatchMessageW 循环。

        用 hWnd=None 收整个线程的消息，这样 stop() 里 PostThreadMessage
        发来的 WM_QUIT 也能让 GetMessage 返回 0，线程干净退出。
        """
        user32 = self._user32
        assert user32 is not None
        msg = MSG()

        # 先 PeekMessage 一次，保证本线程的消息队列已创建
        # （否则 PostThreadMessage 会因为没有队列而失败）
        user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, PM_NOREMOVE)

        while not self._stop_event.is_set():
            result = user32.GetMessageW(ctypes.byref(msg), None, 0, 0)
            if result in (0, -1):   # 0 = 收到 WM_QUIT；-1 = 出错
                break
            user32.TranslateMessage(ctypes.byref(msg))
            user32.DispatchMessageW(ctypes.byref(msg))

    # -- 窗口过程 ----------------------------------------------------------- #

    def _wnd_proc(self, hwnd, msg, wparam, lparam):
        """窗口回调（在托盘线程上被调用）。"""
        try:
            if msg == WM_TRAY_CALLBACK:
                self._on_tray_message(wparam, lparam)
                return 0
            if msg == WM_COMMAND:
                self._on_command(int(wparam) & 0xFFFF)
                return 0
            if msg == WM_CLOSE:
                self._stop_event.set()
                if self._user32 is not None:
                    self._user32.PostQuitMessage(0)
                return 0
            if msg == WM_DESTROY:
                if self._user32 is not None:
                    self._user32.PostQuitMessage(0)
                return 0
            if self._user32 is not None:
                return self._user32.DefWindowProcW(hwnd, msg, wparam, lparam)
        except Exception:
            logger.exception("处理托盘窗口消息时出错")
        return 0

    def _on_tray_message(self, wparam, lparam) -> None:
        """处理 Shell 转发过来的鼠标事件。

        两种版本的消息打包方式不同（见 MSDN NOTIFYICONDATA.uCallbackMessage）：
        - NOTIFYICON_VERSION_4：LOWORD(lParam) = 通知码（WM_LBUTTONUP 等），
          HIWORD(lParam) = 图标 id，wParam = 光标位置。
        - 老版本（0 / NOTIFYICON_VERSION）：wParam = 图标 id，
          lParam = 鼠标消息（同样在低 16 位）。
        也就是说通知码两种版本都在 lParam 的低 16 位，只是其余字段含义不同。
        """
        code = int(lparam) & 0xFFFF
        if not self._version4:
            # 老版本兜底：万一通知码被放在 wParam 里也认一下
            legacy = int(wparam) & 0xFFFF
            if legacy in (WM_LBUTTONUP, WM_LBUTTONDBLCLK,
                          WM_RBUTTONUP, WM_CONTEXTMENU):
                code = legacy

        if code == WM_LBUTTONDBLCLK:
            self._emit("double_click")
        elif code == WM_LBUTTONUP:
            self._emit("left_click")
        elif code in (WM_RBUTTONUP, WM_CONTEXTMENU):
            self._show_context_menu()

    def _on_command(self, cmd_id: int) -> None:
        """处理 WM_COMMAND（TrackPopupMenu 未用 TPM_RETURNCMD 时的兜底路径）。"""
        entry_id = self._cmd_to_id.get(cmd_id)
        if entry_id:
            self._emit(entry_id)

    # -- 右键菜单 ----------------------------------------------------------- #

    def _make_foreground(self) -> None:
        """把托盘窗口置为前台，让弹出菜单能正常接收鼠标/键盘。

        SetForegroundWindow 在「前台锁」期间会被系统拒绝，这时用
        AttachThreadInput 把自己临时挂到前台线程上再试一次——这是
        Windows 托盘菜单的标准做法。
        """
        user32 = self._user32
        kernel32 = self._kernel32
        if user32 is None or not self._hwnd:
            return
        try:
            if user32.SetForegroundWindow(self._hwnd):
                return
        except Exception:
            logger.exception("SetForegroundWindow 失败")

        try:
            fg = user32.GetForegroundWindow()
            if not fg:
                return
            fg_tid = user32.GetWindowThreadProcessId(fg, None)
            cur_tid = kernel32.GetCurrentThreadId()
            if not fg_tid or fg_tid == cur_tid:
                user32.SetForegroundWindow(self._hwnd)
                return
            attached = bool(user32.AttachThreadInput(cur_tid, fg_tid, True))
            try:
                user32.SetForegroundWindow(self._hwnd)
            finally:
                if attached:
                    user32.AttachThreadInput(cur_tid, fg_tid, False)
        except Exception:
            logger.exception("置前台失败，菜单可能不能正常关闭")

    def _show_context_menu(self) -> None:
        """弹出右键菜单，并把选中的命令映射回菜单条目 id。"""
        user32 = self._user32
        if user32 is None or self._hwnd is None:
            return
        try:
            # 菜单状态可能被 Tk 线程改（勾选态），先拿一份快照再开菜单
            with self._lock:
                snapshot = list(self._menu)
            hmenu = user32.CreatePopupMenu()
            if not hmenu:
                return
            for i, (label, entry_id, opts) in enumerate(snapshot):
                if entry_id is None:                    # 分隔线
                    user32.AppendMenuW(hmenu, MF_SEPARATOR, 0, None)
                    continue
                flags = MF_STRING
                if opts.get("checked"):
                    flags |= MF_CHECKED
                if opts.get("enabled") is False:
                    flags |= MF_DISABLED | MF_GRAYED
                user32.AppendMenuW(hmenu, flags, _MENU_CMD_BASE + i, label)

            # TrackPopupMenu 之前必须让本线程的窗口成为前台，否则菜单点完不消失、
            # 键盘也选不了项。直接 SetForegroundWindow 有时会被系统拒绝
            # （前台锁），失败就用 AttachThreadInput 临时挂上前台线程再试一次。
            self._make_foreground()

            pos = wintypes.POINT()
            if not user32.GetCursorPos(ctypes.byref(pos)):
                pos.x, pos.y = 0, 0

            cmd = user32.TrackPopupMenu(
                hmenu,
                TPM_RIGHTBUTTON | TPM_RETURNCMD | TPM_NONOTIFY,
                pos.x, pos.y,
                0,
                self._hwnd,
                None,
            )
            # 经典收尾：让 TrackPopupMenu 的「前台窗口」状态复位
            user32.PostMessageW(self._hwnd, WM_NULL, 0, 0)
            user32.DestroyMenu(hmenu)

            # 菜单用完把焦点还给通知区域（MSDN 明确建议用 NIM_SETFOCUS）
            try:
                focus = NOTIFYICONDATAW()
                focus.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
                focus.hWnd = self._hwnd
                focus.uID = self._icon_id
                self._shell32.Shell_NotifyIconW(NIM_SETFOCUS, ctypes.byref(focus))
            except Exception:
                logger.exception("Shell_NotifyIconW(NIM_SETFOCUS) 调用失败")

            if cmd:
                entry_id = self._cmd_to_id.get(int(cmd))
                if entry_id:
                    self._emit(entry_id)
        except Exception:
            logger.exception("弹出托盘右键菜单失败")

    # -- 事件 --------------------------------------------------------------- #

    def _emit(self, event_id: str) -> None:
        """调用方回调（在托盘线程上）。调用方需自行切换到 UI 线程。"""
        try:
            self._on_event(event_id)
        except Exception:
            logger.exception("托盘事件回调出错：%s", event_id)

    # -- 清理 --------------------------------------------------------------- #

    def _cleanup(self) -> None:
        """移除图标、销毁窗口、注销窗口类。"""
        try:
            self._delete_icon()
        except Exception:
            logger.exception("删除托盘图标时出错")

        try:
            if self._hicon_owned and self._hicon and self._user32 is not None:
                self._user32.DestroyIcon(self._hicon)
        except Exception:
            logger.exception("销毁图标时出错")
        self._hicon = None
        self._hicon_owned = False

        try:
            if self._hwnd and self._user32 is not None:
                self._user32.DestroyWindow(self._hwnd)
        except Exception:
            logger.exception("销毁托盘窗口时出错")
        self._hwnd = None

        try:
            if self._user32 is not None and self._hinst:
                self._user32.UnregisterClassW(self._class_name, self._hinst)
        except Exception:
            logger.exception("注销窗口类时出错")

        self._visible = False
        self._icon_added = False


__all__ = ["TrayIcon"]

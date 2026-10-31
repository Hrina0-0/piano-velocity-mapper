#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
钢琴力度重映射工具（系统级 MIDI 方案）
========================================

把任何"力度打不满"的 MIDI 键盘发出的力度实时重映射到 1-127，
通过 loopMIDI 虚拟端口输出；宿主（Cubase / FL Studio 等）把 MIDI 输入源
切到 loopMIDI 即可统一生效。

链路:
    键盘 (USB-MIDI) -> velocity_mapper.py -> loopMIDI Port -> 宿主

规则:
- 只改写 Note On 的力度;
- velocity=0 的 Note On 原样透传（MIDI 中它等价于 note-off）;
- Note Off / CC / 弯音 / 通道信息等一律原样透传;
- 输入设备掉线后每 2 秒自动重连;
- 所有状态提示都经过防刷屏处理（相同内容不重复打印，同类提示至少间隔 3 秒）。

后端: 优先 python-rtmidi（若已安装），否则自动使用 pygame(PortMidi)。
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sys
import threading
import time
from collections import deque
from ctypes import wintypes
from typing import Any, Dict, List, Optional, Tuple

from i18n import tr, trf

try:
    import mido
except ImportError:  # pragma: no cover
    print(tr("缺少依赖 mido。", "Missing dependency: mido."), file=sys.stderr)
    print(tr("请先运行 start_mapper.bat，或手动执行:", "Run start_mapper.bat first, or run this by hand:"), file=sys.stderr)
    print("    python -m pip install -r requirements.txt", file=sys.stderr)
    sys.exit(2)

from paths import data_dir as _data_dir, resource_dir as _resource_dir

# 只读资源（图标、网页）：打包后 = exe 解包的临时目录
SCRIPT_DIR = _resource_dir()
# 可写数据（config.json / ui_prefs.json）：打包后 = exe 所在目录
DATA_DIR = _data_dir()
_SINGLE_INSTANCE_MUTEX = None

DEFAULT_CONFIG: Dict[str, Any] = {
    # 输入口留空 = 自动挑一个（以前这里写死成了作者自己的设备名
    # "Digital Piano"，发给别人之后对方机器上根本没有这个设备，
    # 界面上却一直显示它，看起来像坏了）。
    "input_port_substring": "",
    "output_port": "loopMIDI Port",
    "max_input_velocity": 100,
    "curve_exponent": 1.0,
    "log_interval_sec": 2.0,
}

# 自动挑输入口时，这些词说明多半是真的钢琴 / 键盘
_PIANO_HINTS = (
    "piano", "keys", "keyboard", "keystation", "stage", "novation",
    "yamaha", "roland", "casio", "korg", "nord", "kawai", "mediologic",
    "arturia", "pandora", "synth", "digitalpiano",
)

# Windows 自带的那些口：看着像设备，其实不接任何键盘
_SYSTEM_HINTS = (
    "microsoft", "midi mapper", "gs wavetable", "xperience", "synthesizer",
)


def _check_single_instance(name: str = "Local\\PianoVelocityMapper") -> bool:
    """Windows 命名互斥体：防止同时开两个实例抢同一个键盘端口。

    已有一个实例在运行时返回 False；非 Windows 或创建失败时放行。
    """
    global _SINGLE_INSTANCE_MUTEX
    if os.name != "nt":
        return True
    try:
        import ctypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.restype = wintypes.HANDLE
        kernel32.CreateMutexW.argtypes = [wintypes.LPVOID, wintypes.BOOL, wintypes.LPCWSTR]
        handle = kernel32.CreateMutexW(None, False, name)
        if not handle:
            return True
        if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
            kernel32.CloseHandle(handle)
            return False
        _SINGLE_INSTANCE_MUTEX = handle  # 保持句柄存活，进程退出时由系统释放
        return True
    except Exception:
        return True


def _setup_backend() -> None:
    """选择可用的 MIDI 后端: 优先 python-rtmidi，其次 pygame(PortMidi)。"""
    for name in ("mido.backends.rtmidi", "mido.backends.pygame"):
        try:
            importlib.import_module(name)
            mido.set_backend(name)
            return
        except Exception:
            continue
    print(tr("错误: 没有可用的 MIDI 后端。请运行 start_mapper.bat 安装依赖。", "Error: no usable MIDI backend. Run start_mapper.bat to install the dependencies."), file=sys.stderr)
    sys.exit(2)


# --------------------------------------------------------------------------- #
# 纯映射逻辑（无外部依赖，便于单元测试）
# --------------------------------------------------------------------------- #

def map_velocity(v: int, max_in: int, exp: float) -> int:
    """把 Note On 力度 v 从 [1, max_in] 重映射到 [1, 127]。

    公式: v' = 1 + 126 * ((v - 1) / (max_in - 1)) ** exp，四舍五入并截断到 1..127。
    - exp == 1.0 : 线性（默认，1-100 -> 1-127）
    - exp >  1.0 : 高力度段更陡，轻弹更轻
    - exp <  1.0 : 低力度段更陡

    注意: 调用方必须先处理 velocity==0 的 Note On（原样透传），本函数对 v<=0 原样返回。
    """
    if v <= 0:
        return v
    if max_in < 2:
        max_in = 2
    if v >= max_in:
        return 127
    norm = (v - 1) / (max_in - 1)
    out = 1 + 126.0 * (norm ** exp)
    return max(1, min(127, int(round(out))))


def _warn(key: str, bad: Any, default: Any) -> None:
    print(trf(f"警告: 配置项 {key!r} 取值无效（{bad!r}），已使用默认值 {default!r}。", f"Warning: invalid value for {key!r} ({bad!r}); using the default {default!r}."))


def validate_config(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """校验/修正配置项，非法值回退到默认值并告警。"""
    out = dict(DEFAULT_CONFIG)
    out.update(cfg)

    # 空字符串是合法的：表示「自动挑一个输入口」，不是非法值
    if not isinstance(out.get("input_port_substring"), str):
        _warn("input_port_substring", out.get("input_port_substring"), DEFAULT_CONFIG["input_port_substring"])
        out["input_port_substring"] = DEFAULT_CONFIG["input_port_substring"]

    if not isinstance(out.get("output_port"), str) or not out["output_port"].strip():
        _warn("output_port", out.get("output_port"), DEFAULT_CONFIG["output_port"])
        out["output_port"] = DEFAULT_CONFIG["output_port"]

    try:
        m = int(out["max_input_velocity"])
    except (TypeError, ValueError):
        m = DEFAULT_CONFIG["max_input_velocity"]
    if not (2 <= m <= 127):
        _warn("max_input_velocity", out["max_input_velocity"], DEFAULT_CONFIG["max_input_velocity"])
        m = DEFAULT_CONFIG["max_input_velocity"]
    out["max_input_velocity"] = m

    try:
        e = float(out["curve_exponent"])
    except (TypeError, ValueError):
        e = DEFAULT_CONFIG["curve_exponent"]
    if not (0.0 < e <= 50.0):
        _warn("curve_exponent", out["curve_exponent"], DEFAULT_CONFIG["curve_exponent"])
        e = DEFAULT_CONFIG["curve_exponent"]
    out["curve_exponent"] = e

    try:
        li = float(out["log_interval_sec"])
    except (TypeError, ValueError):
        li = DEFAULT_CONFIG["log_interval_sec"]
    if li < 0.5:
        li = 0.5
    out["log_interval_sec"] = li

    return out


# --------------------------------------------------------------------------- #
# 端口发现
# --------------------------------------------------------------------------- #

def list_input_names() -> List[str]:
    try:
        return list(mido.get_input_names())
    except Exception as exc:  # 某些情况下后端初始化会抛异常
        print(trf(f"警告: 读取 MIDI 输入端口失败: {exc}", f"Warning: failed to read the MIDI input ports: {exc}"))
        return []


def list_output_names() -> List[str]:
    try:
        return list(mido.get_output_names())
    except Exception as exc:
        print(trf(f"警告: 读取 MIDI 输出端口失败: {exc}", f"Warning: failed to read the MIDI output ports: {exc}"))
        return []


def auto_pick_input_port(names: List[str]) -> Optional[str]:
    """从一个输入端口列表里自动挑一个最像键盘的。

    优先级：名字像钢琴/键盘 > 真实设备（跳过虚拟口和 Windows 自带口）
    > 列表第一个。全都挑不出来就返回 None。
    """
    if not names:
        return None
    low = [n.lower() for n in names]

    # 1) 名字里带 piano / keys / keyboard 之类，多半就是真键盘
    for i, n in enumerate(low):
        if any(h in n for h in _PIANO_HINTS):
            return names[i]

    # 2) 排除虚拟口和 Windows 自带的口
    try:
        import midi_ports
    except Exception:
        midi_ports = None
    for n in names:
        if any(h in n.lower() for h in _SYSTEM_HINTS):
            continue
        if midi_ports is not None:
            try:
                if midi_ports.looks_virtual(n):
                    continue
            except Exception:
                pass
        return n

    # 3) 实在没得挑，返回第一个
    return names[0]


def find_input_port(substring: str) -> Optional[str]:
    sub = (substring or "").strip().lower()
    names = list_input_names()
    if not sub:
        # 没指定就自动挑一个
        return auto_pick_input_port(names)
    for name in names:
        if sub in name.lower():
            return name
    return None


def find_output_port(name_or_substring: str) -> Optional[str]:
    target = name_or_substring.strip()
    outs = list_output_names()
    if target in outs:
        return target
    t = target.lower()
    for name in outs:
        if t in name.lower():
            return name
    return None


def print_ports() -> None:
    ins = list_input_names()
    outs = list_output_names()
    print(tr("检测到的 MIDI 输入端口:", "Detected MIDI input ports:"))
    if ins:
        for i, n in enumerate(ins, 1):
            print(f"  {i}. {n}")
    else:
        print(tr("  （无。请确认键盘已连接、驱动正常。）", "  (none — make sure the keyboard is connected and its driver is fine.)"))
    print(tr("检测到的 MIDI 输出端口:", "Detected MIDI output ports:"))
    if outs:
        for i, n in enumerate(outs, 1):
            print(f"  {i}. {n}")
    else:
        print(tr("  （无。请先安装并打开 loopMIDI 创建虚拟端口。）", "  (none — install and open loopMIDI to create a virtual port.)"))


class PortNotFoundError(Exception):
    def __init__(self, kind: str, wanted: str, available: List[str]):
        self.kind = kind
        self.wanted = wanted
        self.available = available
        super().__init__(trf(f"未找到{kind}端口: {wanted}", f"{kind} port not found: {wanted}"))


class PortOccupiedError(Exception):
    """端口存在但被其它程序占用（例如宿主正占着键盘直连）。"""

    def __init__(self, kind: str, name: str, cause: Exception):
        self.kind = kind
        self.name = name
        self.cause = cause
        super().__init__(trf(f"{kind}端口被占用: {name}（{cause}）", f"{kind} port busy: {name} ({cause})"))


class PortConfigError(Exception):
    """配置本身有问题（而不是被别的程序占用），重试也没用。"""

    def __init__(self, message: str, hint: str = ""):
        self.message = message
        self.hint = hint
        super().__init__(message)


def open_ports(cfg: Dict[str, Any]) -> Tuple[Any, Any]:
    in_name = find_input_port(cfg["input_port_substring"])
    if in_name is None:
        raise PortNotFoundError(tr("输入", "Input"), cfg["input_port_substring"], list_input_names())
    out_name = find_output_port(cfg["output_port"])
    if out_name is None:
        raise PortNotFoundError(tr("输出", "Output"), cfg["output_port"], list_output_names())
    if in_name == out_name:
        # 同一个 loopMIDI 端口既当输入又当输出，音符会被自己吃掉再送回来，
        # 形成回环（而且 WinMM 下同一个设备句柄反复开关会卡住）。直接拦下。
        raise PortConfigError(
            trf(f"输入和输出选了同一个端口：{in_name}", f"Input and output are set to the same port: {in_name}"),
            tr("把「输出」改成另一个端口（比如启动时默认的 loopMIDI Port，"
               "输入选电钢琴的 USB 端口）。",
               "Change “Output” to a different port (the default loopMIDI Port, for"
               "example) and set “Input” to the keyboard's USB port)."))
    try:
        in_port = mido.open_input(in_name)
    except OSError as exc:
        raise PortOccupiedError(tr("输入", "Input"), in_name, exc) from exc
    try:
        out_port = mido.open_output(out_name)
    except OSError as exc:
        try:
            in_port.close()
        except Exception:
            pass
        raise PortOccupiedError(tr("输出", "Output"), out_name, exc) from exc
    return in_port, out_port


def close_ports(in_port: Any, out_port: Any) -> None:
    for p in (in_port, out_port):
        if p is not None:
            try:
                p.close()
            except Exception:
                pass


def _ts() -> str:
    return time.strftime("%H:%M:%S")


_ANNOUNCE_LAST: Optional[str] = None
_ANNOUNCE_TIME: Dict[str, float] = {}
_MESSAGES: deque = deque(maxlen=100)


def _log_line(text: str) -> None:
    """把一行运行信息同时记录到内存队列（供网页端展示）。"""
    _MESSAGES.append({"t": _ts(), "text": text})


def _emit(text: str) -> None:
    """打印并记录一行运行信息。"""
    print(text, flush=True)
    _log_line(text)


def _announce(category: str, msg: str) -> bool:
    """状态提示防刷屏：与上一条内容相同则不重复；同类提示至少间隔 3 秒。

    返回是否真的打印了（用于决定是否顺带打印补充行）。
    """
    global _ANNOUNCE_LAST
    now = time.time()
    if msg == _ANNOUNCE_LAST:
        return False
    if now - _ANNOUNCE_TIME.get(category, 0.0) < 3.0:
        return False
    _ANNOUNCE_LAST = msg
    _ANNOUNCE_TIME[category] = now
    _emit(msg)
    return True


def process_message(msg: Any, cfg: Dict[str, Any]) -> Any:
    """对单条 MIDI 消息做力度重映射；非 Note On（或力度为 0）原样返回。"""
    if msg.type == "note_on" and msg.velocity > 0:
        new_vel = map_velocity(msg.velocity, cfg["max_input_velocity"], cfg["curve_exponent"])
        return msg.copy(velocity=new_vel)
    return msg


# --------------------------------------------------------------------------- #
# 运行模式
# --------------------------------------------------------------------------- #

def run_forward(cfg: Dict[str, Any], verbose: bool = False,
                no_web: bool = False, config_path: Optional[str] = None) -> None:
    """命令行转发模式：内部即 Engine（与桌面 UI 共用同一套逻辑），此处只负责控制台输出与网页控制台。"""
    from engine import Engine

    cfg_path = config_path or os.path.join(DATA_DIR, "config.json")
    eng = Engine(cfg, config_path=cfg_path, verbose=verbose, console_echo=True)

    _emit("")
    _emit(tr("转发模式运行中（Ctrl+C 退出）。", "Forwarding mode running (Ctrl+C to quit)."))
    _emit(tr("宿主请把 MIDI 输入源设为: ", "Set your DAW's MIDI input to: ") + cfg["output_port"])

    # ---- 网页控制台（命令行模式默认开启；桌面 UI 用 --no-web 跳过）----
    webui = None
    if not no_web:
        try:
            from webui import WebUI
            webui = WebUI(
                shared=eng.shared,
                stats=eng.stats,
                config_path=cfg_path,
                input_names=list_input_names,
                output_names=list_output_names,
                state=eng.state,
                open_browser=os.environ.get("WEBUI_NO_BROWSER") != "1",
            )
            url = webui.start()
            _emit(trf(f"网页控制台已开启: {url}（浏览器已自动打开；关掉本窗口即同时退出）", f"Web console started: {url} (the browser opened automatically; closing that window stops it too)"))
        except Exception as exc:
            _emit(trf(f"警告: 网页控制台启动失败（{exc}），继续使用命令行模式。", f"Warning: the web console failed to start ({exc}); staying in command-line mode."))
            webui = None

    eng.start()
    try:
        while True:
            time.sleep(0.3)
    except KeyboardInterrupt:
        _emit("")
        _emit(tr("已停止。", "Stopped."))
    finally:
        eng.stop()
        if webui is not None:
            webui.stop()


def run_monitor(cfg: Dict[str, Any]) -> None:
    in_port = None
    stats = {"notes": 0, "raw_min": 127, "raw_max": 1, "map_min": 127, "map_max": 1}
    last_summary = 0.0
    print()
    print(tr("标定模式（--monitor）：只打印，不转发。Ctrl+C 退出。", "Calibration mode (--monitor): prints only, no forwarding. Ctrl+C to quit."))
    print(tr("提示: 用你最重的手法砸琴，观察 raw 的最大值，", "Hint: hit the keys as hard as you can and watch the raw maximum —"))
    print(trf(f"      工具会实时给出建议的 max_input_velocity（当前配置: {cfg['max_input_velocity']}）。", f"      the tool suggests a max_input_velocity live (currently {cfg['max_input_velocity']})."))

    try:
        while True:
            if in_port is None:
                in_name = find_input_port(cfg["input_port_substring"])
                if in_name is None:
                    wait_msg = (trf(f"[等待] 未找到输入端口 {cfg['input_port_substring']!r}，", f"[Waiting] Input port {cfg['input_port_substring']!r} not found,")
                                + trf(f"正在每 2 秒自动重试（无需重启工具）...", f"retrying every 2 seconds (no need to restart the tool)..."))
                    _announce("wait", wait_msg)
                    time.sleep(2.0)
                    continue
                try:
                    in_port = mido.open_input(in_name)
                except Exception as exc:
                    err_msg = trf(f"[错误] 打开输入端口失败: {exc}", f"[Error] Failed to open the input port: {exc}")
                    _announce("error", err_msg)
                    time.sleep(2.0)
                    continue
                _announce("connect", trf(f"[连接] {in_port.name}", f"[Connected] {in_port.name}"))

            try:
                msg = in_port.receive(block=False)
            except Exception as exc:
                _announce("disconnect", trf(f"[断开] 输入端口异常: {exc}", f"[Disconnected] Input port error: {exc}"))
                close_ports(in_port, None)
                in_port = None
                time.sleep(2.0)
                continue

            if msg is not None and msg.type == "note_on" and msg.velocity > 0:
                new_vel = process_message(msg, cfg).velocity
                stats["notes"] += 1
                stats["raw_min"] = min(stats["raw_min"], msg.velocity)
                stats["raw_max"] = max(stats["raw_max"], msg.velocity)
                stats["map_min"] = min(stats["map_min"], new_vel)
                stats["map_max"] = max(stats["map_max"], new_vel)
                print(trf(f"[{_ts()}] 音符 {msg.note:>3}  raw {msg.velocity:>3}  ->  mapped {new_vel:>3}", f"[{_ts()}] note {msg.note:>3}  raw {msg.velocity:>3}  ->  mapped {new_vel:>3}"))

            now = time.time()
            if now - last_summary >= cfg["log_interval_sec"]:
                last_summary = now
                if stats["notes"]:
                    print(trf(f"[{_ts()}] 已弹 {stats['notes']} 个音符 | ", f"[{_ts()}] {stats['notes']} notes played | ")
                          + f"raw {stats['raw_min']}-{stats['raw_max']} | "
                          + f"mapped {stats['map_min']}-{stats['map_max']} | "
                          + trf(f"建议 max_input_velocity={stats['raw_max']}（当前 {cfg['max_input_velocity']}）", f"Suggested max_input_velocity={stats['raw_max']} (current {cfg['max_input_velocity']})"))
            time.sleep(0.002)
    except KeyboardInterrupt:
        print()
        print(tr("已停止。", "Stopped."))
    finally:
        close_ports(in_port, None)


# --------------------------------------------------------------------------- #
# 配置加载与入口
# --------------------------------------------------------------------------- #

# 旧版本把「Digital Piano」（作者自己那台琴的名字）写死成了默认输入口。
# 发给别人之后，对方机器上根本没有这个设备，界面却一直显示着它，
# 看起来像是坏了。升级时把这个遗留值清掉，改回自动匹配。
_LEGACY_DEFAULT_INPUTS = ("digital piano",)


def save_config(path: str, cfg: Dict[str, Any]) -> bool:
    """把配置写回磁盘（原子替换，避免半截文件）。"""
    try:
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
        return True
    except OSError as exc:
        print(trf(f"警告: 无法写入配置文件 {path}: {exc}", f"Warning: cannot write the config file {path}: {exc}"))
        return False


def load_config(path: str) -> Dict[str, Any]:
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                user = json.load(f)
            if not isinstance(user, dict):
                print(trf(f"警告: {path} 内容不是 JSON 对象，使用默认配置。", f"Warning: {path} is not a JSON object; using the default config."))
                user = {}
        except (json.JSONDecodeError, OSError) as exc:
            print(trf(f"警告: 无法读取 {path}（{exc}），使用默认配置。", f"Warning: cannot read {path} ({exc}); using the default config."))
            user = {}
    else:
        print(trf(f"提示: 未找到配置文件 {path}，将使用默认配置。", f"Hint: config file {path} not found; using the default config."))
        user = {}
    cfg = validate_config(user)

    # 清掉旧版本遗留的写死设备名，改成自动匹配
    legacy = (cfg.get("input_port_substring") or "").strip().lower()
    if legacy in _LEGACY_DEFAULT_INPUTS:
        cfg["input_port_substring"] = ""
        print(tr("提示: 配置里遗留的默认输入端口已清除，改为自动匹配。",
                 "Hint: the legacy default input port was cleared; using auto-detection now."))
        try:
            save_config(path, cfg)
        except Exception:
            pass

    # 写成一行紧凑的摘要：JSON 原文在日志面板里会折成好几行，很难看
    print(tr("当前配置: 输入={} | 输出={} | 最大力度={} | 曲线={} | 日志间隔={}s", "Current config: input={} | output={} | max velocity={} | curve={} | log interval={}s").format(
        cfg["input_port_substring"] or tr("(自动)", "(auto)"),
        cfg["output_port"], cfg["max_input_velocity"],
        cfg["curve_exponent"], cfg["log_interval_sec"]))
    return cfg


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description=tr("钢琴力度重映射工具: 把键盘力度 1-max_in 实时映射到 1-127 并转发到 loopMIDI。", "Piano Velocity Remapper: maps keyboard velocity 1-max_in to 1-127 in real time and forwards it to loopMIDI."))
    parser.add_argument(
        "--config",
        default=os.path.join(DATA_DIR, "config.json"),
        help=trf(f"配置文件路径（默认: {os.path.join(DATA_DIR, 'config.json')}）", f"Path to the config file (default: {os.path.join(DATA_DIR, 'config.json')})"))
    parser.add_argument(
        "--monitor", action="store_true",
        help=tr("标定模式: 只打印 raw -> mapped，不转发（用于测出真实最大力度）", "Calibration mode: print raw -> mapped without forwarding (to measure the real maximum velocity)"))
    parser.add_argument("--list", action="store_true", help=tr("仅列出 MIDI 输入/输出端口后退出", "List the MIDI input/output ports and exit"))
    parser.add_argument(
        "--verbose", action="store_true", help=tr("转发模式下逐音符打印力度映射", "Print the velocity mapping for every note while forwarding"))
    parser.add_argument(
        "--no-web", action="store_true",
        help=tr("不启动网页控制台（纯命令行转发，不自动开浏览器）", "Don't start the web console (pure command-line forwarding, no browser)"))
    args = parser.parse_args(argv)

    if not _check_single_instance():
        print(tr("工具已经有一个实例在运行了，无法重复启动。", "Another instance is already running, so this one cannot start."))
        print(tr("请先关闭原来的工具窗口（或到任务管理器结束 velocity_mapper 进程），再重新启动。", "Close the existing window first (or end the velocity_mapper process in Task Manager), then start again."))
        return 1

    _setup_backend()
    cfg = load_config(args.config)

    if args.list:
        print_ports()
        return 0

    print_ports()
    if args.monitor:
        run_monitor(cfg)
    else:
        run_forward(cfg, verbose=args.verbose, no_web=args.no_web, config_path=args.config)
    return 0


if __name__ == "__main__":
    sys.exit(main())

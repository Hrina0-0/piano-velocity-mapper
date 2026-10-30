#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
力度重映射引擎（后台线程）
==========================

把 velocity_mapper.run_forward 里的转发循环抽成可独立运行的类，供
桌面 GUI（gui.py）与命令行（velocity_mapper.py）共用：

- 在后台线程里跑「打开端口 -> 读消息 -> 重映射 -> 转发 -> 健康检查」
- 配置可运行时热更新：力度/曲线即时生效，端口改动触发重连
- 对外只暴露**只读快照**（summary / state / messages），GUI 轮询即可，
  不需要跨线程回调，tkinter 主线程天然不会被 MIDI 线程打扰

所有实际 MIDI 处理逻辑仍来自 velocity_mapper，本文件只负责线程与状态。
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
from collections import deque
from typing import Any, Dict, List, Optional, Tuple

from i18n import tr, trf

import velocity_mapper as vm

# 统计窗口：只保留最近 N 个音符用于算「最近力度范围 / 建议最大力度」
SUMMARY_WINDOW = 300

#: status 取值 -> 显示文案（按当前界面语言实时取，不能在 import 时就定死）
def status_text(key: str) -> str:
    return {
        "ok": tr("已连接", "Connected"),
        "waiting": tr("等待设备", "Waiting for device"),
        "busy": tr("端口被占用", "Port busy"),
        "error": tr("连接异常", "Connection error"),
        "stopped": tr("已停止", "Stopped"),
    }.get(key, key)


def _empty_summary() -> Dict[str, Any]:
    return {
        "notes": 0,
        "last": None,
        "raw_min": None,
        "raw_max": None,
        "map_min": None,
        "map_max": None,
        "suggested": None,
    }


class Engine:
    """力度重映射引擎：在后台线程里转发 MIDI，并维护可轮询的状态快照。"""

    def __init__(
        self,
        cfg: Dict[str, Any],
        config_path: str,
        verbose: bool = False,
        console_echo: bool = True,
    ) -> None:
        self._lock = threading.Lock()
        self._cfg: Dict[str, Any] = dict(cfg)
        self._config_path = config_path
        self._verbose = verbose
        self._console_echo = console_echo

        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._crashed = ""

        # ---- 供 GUI 轮询的快照（引擎线程写入，GUI 线程只读）----
        self.stats: deque = deque(maxlen=SUMMARY_WINDOW)
        self.messages: deque = deque(maxlen=100)
        self._log_seq = 0
        self._summary: Dict[str, Any] = _empty_summary()
        self._rev = 0  # 任意可见状态变化都会 +1，GUI 用它判断是否需要重绘

        self.state: Dict[str, Any] = {
            "input_open": False,
            "output_open": False,
            "input_error": None,
            "output_error": None,
            "input_name": None,
            "output_name": None,
            "status": "waiting",
            "hint": tr("正在启动…", "Starting…"),
            "messages": self.messages,
        }

        # ---- 与旧版 webui.py 兼容的共享结构（网页控制台仍可挂上来）----
        self.shared: Dict[str, Any] = {
            "config": self._cfg,
            "lock": self._lock,
            "revision": 0,
            "port_revision": 0,
            "state": self.state,
        }

        self._in_port: Any = None
        self._out_port: Any = None
        self._last_health = 0.0
        self._last_msg_at = 0.0
        self._last_prev = 0

    # ------------------------------------------------------------------ #
    # 生命周期
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        """启动后台转发线程。重复调用无副作用。"""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._crashed = ""
        self._thread = threading.Thread(target=self._run_guarded, name="midi-engine",
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """请求停止并等待线程退出（幂等）。"""
        self._stop.set()
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=timeout)
        self._thread = None

    @property
    def alive(self) -> bool:
        """引擎线程是否在跑（GUI 用它避免线程挂掉后还显示“已连接”）。"""
        thread = self._thread
        return thread is not None and thread.is_alive()

    @property
    def crashed(self) -> str:
        """线程崩溃时的 traceback（正常时为空串）。"""
        return self._crashed

    def _run_guarded(self) -> None:
        """任何未捕获异常都要留下痕迹：写进日志并记在 crashed 上。"""
        try:
            self._run()
        except Exception:  # noqa: BLE001 - 后台线程不能静默死掉
            detail = traceback.format_exc()
            self._crashed = detail
            self.log_external("引擎线程异常，已停止：\n" + detail.rstrip(), stamped=False)

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #

    @property
    def rev(self) -> int:
        return self._rev

    def config(self) -> Dict[str, Any]:
        with self._lock:
            return dict(self._cfg)

    def summary(self) -> Dict[str, Any]:
        """最近力度统计（raw->mapped 范围、最后音符、建议最大力度）。"""
        out = dict(self._summary)
        out["rev"] = self._rev
        return out

    def reset_stats(self) -> None:
        self.stats.clear()
        self._summary = _empty_summary()
        self._rev += 1

    def log_tail(self, n: int = 60) -> List[Dict[str, str]]:
        return list(self.messages)[-n:]

    @property
    def log_seq(self) -> int:
        """累计写入日志的行数（单调递增），GUI 用来只追加新行而不是整块重画。"""
        return self._log_seq

    @staticmethod
    def ports() -> Tuple[List[str], List[str]]:
        return vm.list_input_names(), vm.list_output_names()

    def update(self, patch: Dict[str, Any]) -> Dict[str, Any]:
        """校验并热更新配置，写回 config.json；返回生效后的完整配置。

        只改力度/曲线 -> revision+1（无需重连）；改端口 -> port_revision+1（触发重连）。
        """
        merged = dict(self._cfg)
        merged.update({k: v for k, v in patch.items() if k in vm.DEFAULT_CONFIG})
        validated = vm.validate_config(merged)

        with self._lock:
            old_in = self._cfg.get("input_port_substring")
            old_out = self._cfg.get("output_port")
            self._cfg.clear()
            self._cfg.update(validated)
            self.shared["revision"] += 1
            if (validated["input_port_substring"] != old_in
                    or validated["output_port"] != old_out):
                self.shared["port_revision"] += 1

        self._save(validated)
        self._rev += 1
        return dict(validated)

    def _save(self, cfg: Dict[str, Any]) -> None:
        try:
            tmp = self._config_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cfg, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self._config_path)  # 原子替换，避免半截文件
        except OSError as exc:
            self._log(trf(f"警告: 无法写入配置文件 {self._config_path}: {exc}", f"Warning: cannot write the config file {self._config_path}: {exc}"))

    # ------------------------------------------------------------------ #
    # 内部：日志与统计
    # ------------------------------------------------------------------ #

    def _log(self, text: str) -> None:
        """记录一行运行信息（GUI 日志面板可见）。"""
        if self._console_echo:
            print(text, flush=True)
        self.messages.append({"t": time.strftime("%H:%M:%S"), "text": text})
        self._log_seq += 1
        self._rev += 1

    def _emit(self, text: str) -> None:
        if self._console_echo:
            print(text, flush=True)
        self._append(text)

    def log_external(self, text: str, stamped: bool = True) -> None:
        """收进外部模块（_announce 等）的输出，不回显到 stdout，避免死循环。"""
        self._append(text, stamped)

    def _append(self, text: str, stamped: bool = True) -> None:
        self.messages.append({"t": time.strftime("%H:%M:%S") if stamped else "",
                              "text": text})
        self._log_seq += 1
        self._rev += 1

    def _set_status(self, status: str, hint: str) -> None:
        if self.state["status"] != status or self.state["hint"] != hint:
            self.state["status"] = status
            self.state["hint"] = hint
            self._rev += 1

    def _record_note(self, note: int, raw: int, mapped: int) -> None:
        self.stats.append({"t": time.strftime("%H:%M:%S"), "note": note,
                           "raw": raw, "mapped": mapped})
        samples = list(self.stats)  # 快照，避免边遍历边被引擎线程改动
        raw_vals = [s["raw"] for s in samples]
        map_vals = [s["mapped"] for s in samples]
        self._summary = {
            "notes": len(samples),
            "last": samples[-1],
            "raw_min": min(raw_vals),
            "raw_max": max(raw_vals),
            "map_min": min(map_vals),
            "map_max": max(map_vals),
            "suggested": max(raw_vals),
            "rev": self._rev,
        }
        self._rev += 1

    # ------------------------------------------------------------------ #
    # 主循环
    # ------------------------------------------------------------------ #

    def _run(self) -> None:
        vm._ANNOUNCE_LAST = None
        vm._ANNOUNCE_TIME.clear()

        self._emit(tr("转发模式运行中。", "Forwarding mode running."))
        self._emit(tr("宿主请把 MIDI 输入源设为: ", "Set your DAW's MIDI input to: ") + self._cfg["output_port"])

        try:
            while not self._stop.is_set():
                with self._lock:
                    live_cfg = dict(self._cfg)
                    cur_prev = self.shared["port_revision"]

                # ---- 端口设置变了 -> 按新设置重连 ----
                if cur_prev != self._last_prev:
                    self._last_prev = cur_prev
                    vm.close_ports(self._in_port, self._out_port)
                    self._in_port = self._out_port = None
                    self.state["input_open"] = False
                    self.state["output_open"] = False
                    self._rev += 1
                    self._set_status("waiting", tr("端口设置已更新，正在重连…", "Port settings updated, reconnecting…"))
                    vm._announce("reload", trf(f"[配置] 端口设置已更新，正在重连", f"[Config] Port settings updated, reconnecting")
                                          + trf(f"（输出: {live_cfg['output_port']}）", f"(output: {live_cfg['output_port']})"))
                    # 给驱动一点时间把刚关掉的句柄释放干净，否则立刻重开
                    # 同一个 loopMIDI 端口会一直报“被占用”。
                    self._stop.wait(0.25)

                # ---- 确保端口已连接（掉线后每 2 秒重试）----
                if self._in_port is None or self._out_port is None:
                    if not self._connect(live_cfg):
                        self._stop.wait(2.0)
                        continue

                # ---- 读一条并转发 ----
                try:
                    msg = self._in_port.receive(block=False)
                except Exception as exc:
                    vm._announce("disconnect", trf(f"[断开] 输入端口异常: {exc}", f"[Disconnected] Input port error: {exc}"))
                    self._disconnect(trf(f"输入端口异常: {exc}", f"Input port error: {exc}"), "error")
                    self._stop.wait(2.0)
                    continue

                if msg is not None:
                    if msg.type == "note_on" and msg.velocity > 0:
                        old_vel = msg.velocity
                        msg = vm.process_message(msg, live_cfg)
                        self._record_note(msg.note, old_vel, msg.velocity)
                        if self._verbose:
                            print(trf(f"[{time.strftime('%H:%M:%S')}] 音符 {msg.note:>3}  ", f"[{time.strftime('%H:%M:%S')}] note {msg.note:>3}  ")
                                  + trf(f"力度 {old_vel:>3} -> {msg.velocity:>3}", f"velocity {old_vel:>3} -> {msg.velocity:>3}"), flush=True)
                        elif self._console_echo:
                            print(trf(f"\r最近映射力度: {msg.velocity:>3}", f"\rlatest mapped velocity: {msg.velocity:>3}"), end="", flush=True)
                    try:
                        self._out_port.send(msg)
                    except Exception as exc:
                        vm._announce("disconnect", trf(f"[断开] 输出端口异常: {exc}", f"[Disconnected] Output port error: {exc}"))
                        self._disconnect(trf(f"输出端口异常: {exc}", f"Output port error: {exc}"), "error")
                        continue

                # ---- 周期健康检查（检测设备被拔出）----
                now = time.time()
                if now - self._last_health >= 2.0:
                    self._last_health = now
                    self._health_check(live_cfg)

                # 空闲时不用 2ms 轮询：刚有音符就用 2ms 保延迟，没音符就退到
                # 6ms，省掉空转 CPU（弹奏时的延迟不受影响）。
                if msg is not None:
                    self._last_msg_at = now
                    self._stop.wait(0.002)
                elif now - self._last_msg_at < 1.0:
                    self._stop.wait(0.002)
                else:
                    self._stop.wait(0.006)
        finally:
            vm.close_ports(self._in_port, self._out_port)
            self._in_port = self._out_port = None
            self.state["input_open"] = False
            self.state["output_open"] = False
            self.state["status"] = "stopped"
            self.state["hint"] = tr("已停止", "Stopped")
            self._rev += 1
            self._emit(tr("已停止。", "Stopped."))

    # ---- 连接 / 断开 / 健康检查 ----

    def _connect(self, cfg: Dict[str, Any]) -> bool:
        try:
            in_port, out_port = vm.open_ports(cfg)
        except vm.PortNotFoundError as exc:
            if exc.kind == tr("输出", "Output"):
                self.state["output_open"] = False
                self.state["output_error"] = "missing"
                self._set_status("waiting",
                                 trf(f"未找到输出端口 {exc.wanted!r}（请确认 loopMIDI 已打开并创建该端口）", f"Output port {exc.wanted!r} not found (make sure loopMIDI is running and has created that port)"))
                wait_msg = (trf(f"[等待] 未找到输出端口 {cfg['output_port']!r}。", f"[Waiting] Output port {cfg['output_port']!r} not found.")
                            + trf(f"请确认已安装并打开 loopMIDI，且已创建该端口", f"Make sure loopMIDI is installed, running and has created that port")
                            + trf(f"（每 2 秒自动重试）。", f"(retrying every 2 seconds)."))
            else:
                self.state["input_open"] = False
                self.state["input_error"] = "missing"
                self._set_status("waiting",
                                 trf(f"未找到输入端口 {exc.wanted!r}（请确认键盘已连接）", f"Input port {exc.wanted!r} not found (make sure the keyboard is connected)"))
                wait_msg = (trf(f"[等待] 未找到输入端口 {cfg['input_port_substring']!r}。", f"[Waiting] Input port {cfg['input_port_substring']!r} not found.")
                            + trf(f"请确认键盘已连接（每 2 秒自动重试）。", f"Make sure the keyboard is connected (retrying every 2 seconds)."))
            if vm._announce("wait", wait_msg):
                if exc.kind == tr("输入", "Input") and exc.available:
                    self._emit(tr("可用输入端口: ", "Available input ports: ") + ", ".join(exc.available))
            self._rev += 1
            return False
        except vm.PortOccupiedError as exc:
            self.state["input_open" if exc.kind == tr("输入", "Input") else "output_open"] = False
            self.state["input_error" if exc.kind == tr("输入", "Input") else "output_error"] = "occupied"
            self._set_status("busy", trf(f"{exc.kind}端口被占用：{exc.name}", f"{exc.kind} port busy: {exc.name}"))
            if vm._announce("busy", trf(f"[等待] {exc.kind}端口被占用（{exc.name}）。可能是另一个工具窗口还没关，", f"[Waiting] {exc.kind} port busy ({exc.name}). Another window of this tool may still be open,")
                                    + trf(f"或宿主软件正占着键盘直连，正在自动重试...", f"or your DAW is holding the keyboard’s direct input; retrying automatically...")):
                self._emit(tr("提示: 关掉多余的工具窗口，或在宿主里停用键盘直连后，本工具会自动接上。", "Hint: close any extra windows of this tool, or disable the keyboard's direct input in your DAW — it will connect on its own."))
            self._rev += 1
            return False
        except vm.PortConfigError as exc:
            self.state["input_open"] = False
            self.state["output_open"] = False
            self.state["input_error"] = None
            self.state["output_error"] = None
            self._set_status("busy", exc.message)
            if vm._announce("cfgerr", trf(f"[配置] {exc.message}", f"[Config] {exc.message}")):
                self._emit(tr("提示: ", "Hint: ") + exc.hint)
            self._rev += 1
            return False
        except Exception as exc:
            self.state["input_open"] = False
            self.state["output_open"] = False
            self.state["input_error"] = trf(f"连接失败: {exc}", f"Connection failed: {exc}")
            self.state["output_error"] = trf(f"连接失败: {exc}", f"Connection failed: {exc}")
            self._set_status("error", trf(f"连接失败: {exc}", f"Connection failed: {exc}"))
            vm._announce("busy", trf(f"[等待] 端口连接失败（{exc}），正在自动重试...", f"[Waiting] Port connection failed ({exc}), retrying automatically..."))
            self._rev += 1
            return False

        self._in_port, self._out_port = in_port, out_port
        self.state["input_open"] = True
        self.state["output_open"] = True
        self.state["input_error"] = None
        self.state["output_error"] = None
        self.state["input_name"] = in_port.name
        self.state["output_name"] = out_port.name
        self._set_status("ok", f"{in_port.name} → {out_port.name}")
        self._rev += 1
        if vm._announce("connect", trf(f"[连接] {in_port.name}  ->  {out_port.name}", f"[Connected] {in_port.name}  ->  {out_port.name}")):
            self._emit(tr("弹奏时下方会实时显示映射后的力度。", "The mapped velocity is shown live below while you play."))
        return True

    def _disconnect(self, reason: str, status: str) -> None:
        vm.close_ports(self._in_port, self._out_port)
        self._in_port = self._out_port = None
        self.state["input_open"] = False
        self.state["output_open"] = False
        self._set_status(status, trf(f"{reason}，等待重连…", f"{reason}, waiting to reconnect…"))
        self._rev += 1

    def _health_check(self, cfg: Dict[str, Any]) -> None:
        if self._in_port is not None and vm.find_input_port(cfg["input_port_substring"]) is None:
            vm._announce("disconnect", tr("[断开] 检测到输入设备已拔出，等待重连...", "[Disconnected] Input device unplugged, waiting to reconnect..."))
            self.state["input_error"] = "missing"
            self._disconnect(tr("输入设备已拔出", "Input device unplugged"), "waiting")
        elif self._out_port is not None and vm.find_output_port(cfg["output_port"]) is None:
            vm._announce("disconnect", tr("[断开] 检测到输出端口（loopMIDI）不可用，等待重连...", "[Disconnected] Output port (loopMIDI) unavailable, waiting to reconnect..."))
            self.state["output_error"] = "missing"
            self._disconnect(tr("输出端口（loopMIDI）不可用", "Output port (loopMIDI) unavailable"), "waiting")

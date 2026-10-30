#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
内置网页控制台（本地 HTTP 服务）
================================

转发模式启动时自动开启一个本地网页：http://127.0.0.1:<port>

功能：
- 滑块实时调整 max_input_velocity / curve_exponent，改动即时生效并写回 config.json
- 力度曲线可视化（canvas），并叠加"线性"参考线，方便边弹边调
- 实时显示最近音符的 raw -> mapped、累计范围与建议 max_input_velocity
- 下拉切换输入/输出端口，切换后自动重连

说明：本服务只绑定 127.0.0.1，仅本机可访问，不对外网开放。
"""

from __future__ import annotations

import json
import os
import threading
import time
import webbrowser
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, Dict, List

from paths import resource_dir as _resource_dir

SCRIPT_DIR = _resource_dir()
DEFAULT_PORT = 8765
PORT_SCAN_RANGE = 20


class WebUI:
    """启动本地网页控制台，通过 HTTP 接口读写共享配置与实时力度统计。"""

    def __init__(
        self,
        shared: Dict[str, Any],
        stats: deque,
        config_path: str,
        input_names: Callable[[], List[str]],
        output_names: Callable[[], List[str]],
        state: Dict[str, Any] = None,
        open_browser: bool = True,
    ) -> None:
        self.shared = shared
        self.stats = stats
        self.config_path = config_path
        self.input_names = input_names
        self.output_names = output_names
        self.state = state if state is not None else {}
        self.open_browser = open_browser
        self.httpd: Any = None
        self.port: int = DEFAULT_PORT
        self.url: str = ""

    def start(self) -> str:
        """启动 HTTP 服务（守护线程），返回访问地址。失败抛异常。"""
        handler = self._make_handler()

        last_exc = None
        for port in range(DEFAULT_PORT, DEFAULT_PORT + PORT_SCAN_RANGE):
            try:
                self.httpd = ThreadingHTTPServer(("127.0.0.1", port), handler)
                self.port = port
                break
            except OSError as exc:
                last_exc = exc
                continue
        if self.httpd is None:
            raise RuntimeError(f"无法绑定端口 {DEFAULT_PORT}-{DEFAULT_PORT + PORT_SCAN_RANGE - 1}（{last_exc}）")

        self.httpd.daemon_threads = True
        self.url = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        if self.open_browser:
            threading.Thread(target=self._open_browser_later, daemon=True).start()
        return self.url

    def stop(self) -> None:
        if self.httpd is not None:
            try:
                self.httpd.shutdown()
                self.httpd.server_close()
            except Exception:
                pass

    def _open_browser_later(self) -> None:
        time.sleep(0.5)
        try:
            webbrowser.open(self.url)
        except Exception:
            pass

    def _make_handler(self) -> type:
        webui = self

        class Handler(BaseHTTPRequestHandler):
            server_version = "VelocityMapperWeb/1.0"

            def log_message(self, fmt: str, *args: Any) -> None:
                # 保持控制台干净，不打印每个请求的访问日志
                pass

            def _send_json(self, obj: Any, status: int = 200) -> None:
                body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_html(self) -> None:
                html_path = os.path.join(SCRIPT_DIR, "webui.html")
                if not os.path.exists(html_path):
                    self.send_error(500, "webui.html 不存在")
                    return
                with open(html_path, "r", encoding="utf-8") as f:
                    body = f.read().encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _read_body(self) -> Dict[str, Any]:
                length = int(self.headers.get("Content-Length") or 0)
                if length <= 0:
                    return {}
                raw = self.rfile.read(length)
                try:
                    data = json.loads(raw.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    data = {}
                return data if isinstance(data, dict) else {}

            def _current_config(self) -> Dict[str, Any]:
                return dict(webui.shared["config"])

            def do_GET(self) -> None:
                path = self.path.split("?", 1)[0]
                try:
                    if path in ("/", "/index.html"):
                        self._send_html()
                    elif path == "/api/config":
                        self._send_json(self._current_config())
                    elif path == "/api/stats":
                        self._send_json(self._stats_payload())
                    elif path == "/api/ports":
                        self._send_json({
                            "inputs": webui.input_names(),
                            "outputs": webui.output_names(),
                        })
                    elif path == "/api/status":
                        self._send_json(self._status_payload())
                    else:
                        self.send_error(404, "Not Found")
                except Exception as exc:  # pragma: no cover
                    try:
                        self._send_json({"error": str(exc)}, status=500)
                    except Exception:
                        pass

            def do_POST(self) -> None:
                path = self.path.split("?", 1)[0]
                if path != "/api/config":
                    self.send_error(404, "Not Found")
                    return
                data = self._read_body()
                try:
                    from velocity_mapper import validate_config
                except ImportError:  # pragma: no cover
                    self._send_json({"error": "velocity_mapper 未加载"}, status=500)
                    return

                merged = dict(webui.shared["config"])
                merged.update({k: v for k, v in data.items() if k in (
                    "input_port_substring", "output_port",
                    "max_input_velocity", "curve_exponent", "log_interval_sec",
                )})
                validated = validate_config(merged)

                old_in = webui.shared["config"].get("input_port_substring")
                old_out = webui.shared["config"].get("output_port")
                with webui.shared["lock"]:
                    webui.shared["config"].clear()
                    webui.shared["config"].update(validated)
                    webui.shared["revision"] += 1
                    if (validated.get("input_port_substring") != old_in
                            or validated.get("output_port") != old_out):
                        webui.shared["port_revision"] = webui.shared.get("port_revision", 0) + 1

                # 持久化到 config.json，下次启动仍生效
                try:
                    with open(webui.config_path, "w", encoding="utf-8") as f:
                        json.dump(validated, f, ensure_ascii=False, indent=2)
                except OSError as exc:
                    print(f"警告: 无法写入配置文件 {webui.config_path}: {exc}", flush=True)

                self._send_json(validated)

            def _stats_payload(self) -> Dict[str, Any]:
                samples = list(webui.stats)
                if not samples:
                    return {
                        "notes": 0,
                        "last": None,
                        "raw_min": None, "raw_max": None,
                        "map_min": None, "map_max": None,
                        "suggested": None,
                    }
                raw_vals = [s["raw"] for s in samples]
                map_vals = [s["mapped"] for s in samples]
                return {
                    "notes": len(samples),
                    "last": samples[-1],
                    "raw_min": min(raw_vals),
                    "raw_max": max(raw_vals),
                    "map_min": min(map_vals),
                    "map_max": max(map_vals),
                    "suggested": max(raw_vals),
                }

            def _status_payload(self) -> Dict[str, Any]:
                st = webui.state
                return {
                    "input_open": bool(st.get("input_open", False)),
                    "output_open": bool(st.get("output_open", False)),
                    "input_error": st.get("input_error"),
                    "output_error": st.get("output_error"),
                    "input_name": st.get("input_name"),
                    "output_name": st.get("output_name"),
                    "messages": list(st.get("messages", []))[-60:],
                }

        return Handler

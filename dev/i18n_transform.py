# -*- coding: utf-8 -*-
"""中英双语文案表 + 批量转换脚本。

用法：
    python dev/i18n_transform.py            # 只报告，不写文件
    python dev/i18n_transform.py --write    # 真正改写源文件

规则：
- 普通字符串  "中文"       ->  tr("中文", "English")
- f-string     f"中文 {x}"  ->  trf(f"中文 {x}", f"English {x}")

f-string 的英文侧用 %s 按位置表示「原样搬过来的那个 {表达式}」，
脚本会把中文侧的花括号表达式原封不动填进英文侧——这样既不会抄错表达式，
格式说明（:>3 之类）也一并保留。
"""

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# --------------------------------------------------------------------------- #
# 普通字符串：中文 -> 英文
# --------------------------------------------------------------------------- #

PLAIN = {
    # ---- 通用 / 标题 ----
    "力度重映射": "Velocity Remapper",
    "钢琴力度重映射": "Piano Velocity Remapper",
    "正在检测 MIDI 设备…": "Detecting MIDI devices…",
    "（无可用端口）": "(no ports available)",
    "原始力度 →": "Raw velocity →",

    # ---- 关闭对话框 ----
    "要完全退出这个工具吗？": "Quit this tool completely?",
    "是（完全退出）：力度转发立即停止。\\n": "Yes (quit completely): velocity forwarding stops immediately.\\n",
    "否（留在后台运行）：托盘图标常驻，转发不受影响。": "No (keep it running in the background): the tray icon stays, forwarding is unaffected.",
    "下次不再提示（记住这次的选择）": "Don't ask again (remember my choice)",
    "留在后台运行": "Keep running in background",
    "完全退出": "Quit completely",
    "记住了：下次点关闭直接执行。\\n": "Remembered — next time, closing will do this directly.\\n",
    "想改回来：托盘图标右键 ->「关闭时总是询问」。": "To undo: right-click the tray icon -> “Always ask when closing”.",
    "已记住关闭方式：": "Close behaviour remembered:",
    "下次点关闭直接完全退出。": "closing will now quit completely.",
    "下次点关闭直接收进后台。": "closing will now hide to the background.",
    "（可在托盘图标右键菜单里恢复）": "(you can undo this from the tray icon menu)",

    # ---- 缺虚拟端口的引导 ----
    "需要虚拟 MIDI 端口": "A virtual MIDI port is required",
    "需要一个虚拟 MIDI 端口": "You need a virtual MIDI port",
    "本工具要把重映射后的力度转发给 DAW，需要借助虚拟 MIDI 端口。\\n": "This tool forwards the remapped velocity to your DAW through a virtual MIDI port.\\n",
    "Windows 上最常见的做法是装 loopMIDI（免费、个人使用免费）：\\n\\n": "On Windows the usual choice is loopMIDI (free for personal use):\\n\\n",
    "  1. 打开官网下载安装\\n": "  1. Download and install it from the official site\\n",
    "  2. 打开 loopMIDI，右键托盘图标 ->「添加端口」，名字随意\\n": "  2. Open loopMIDI, right-click its tray icon -> “Add Port”, any name\\n",
    "  3. 回到本工具把「输出」选成那个端口，DAW 里也选同一个\\n\\n": "  3. Back here, set “Output” to that port and pick the same one in your DAW\n\\n",
    "装好后本工具会自动找到它，不用再重装本工具。": "Once installed, this tool finds it by itself — no need to reinstall anything.",
    "重新检测": "Check again",
    "打开 loopMIDI 官网": "Open loopMIDI site",
    "稍后再说": "Not now",
    "没有找到可用的输出端口。": "No usable output port found.",

    # ---- 界面卡片 ----
    "力度映射": "Velocity Mapping",
    "最大力度": "Max velocity",
    "键盘用尽全力能达到的力度": "The velocity your keyboard reaches at full force",
    "采用建议值": "Use suggested",
    "力度曲线": "Velocity Curve",
    "0.1/格": "0.1 / step",
    "线性": "Linear",
    "1-max 均匀拉伸到 1-127，手感最自然": "Stretch 1-max evenly to 1-127 — the most natural feel",
    "柔和": "Soft",
    "轻触更轻，用力才出效果，动态范围大": "A light touch plays light, wide dynamic range",
    "爆发": "Punchy",
    "轻触就出声，层次集中在下段": "Even the lightest touch sounds; detail lives in the lower range",
    "实时力度": "Live Velocity",
    "重置": "Reset",
    "原始": "Raw",
    "映射": "Mapped",
    "建议最大力度：弹几下后给出": "Suggested max velocity: appears after a few notes",
    "已是建议值": "Already the suggested value",
    "已弹 0 个音符": "Played 0 notes",
    "采用": "Use",
    "端口": "Ports",
    "输入（键盘）": "Input (keyboard)",
    "输出（虚拟端口）": "Output (virtual port)",
    "刷新端口": "Refresh ports",
    "切换后自动重连": "Reconnects automatically",
    "运行日志": "Log",
    "最小化 / 关闭会收进托盘 · 改动自动保存": "Minimizing / closing hides to the tray · changes save automatically",
    "端口已更新，正在重连…": "Ports updated, reconnecting…",
    "读取 MIDI 端口失败: {exc}": "Failed to read MIDI ports: {exc}",
    "已保存": "Saved",
    "引擎线程已停止，请看日志并重启工具": "The engine thread stopped — check the log and restart the tool",
    "还没有收到音符，弹一下试试": "No notes yet — play something",
    "共 {n} 条": "{n} lines",

    # ---- 状态 / 提示 ----
    "已连接": "Connected",
    "等待设备": "Waiting for device",
    "端口被占用": "Port busy",
    "连接异常": "Connection error",
    "已停止": "Stopped",
    "正在启动…": "Starting…",
    "引擎线程异常，已停止：\\n": "The engine thread crashed and stopped:\\n",
    "已停止。": "Stopped.",
    "转发模式运行中。": "Forwarding mode running.",
    "宿主请把 MIDI 输入源设为: ": "Set your DAW's MIDI input to: ",
    "端口设置已更新，正在重连…": "Port settings updated, reconnecting…",
    "输出": "Output",
    "输入": "Input",
    "未找到输出端口 {wanted}（请确认 loopMIDI 已打开并创建该端口）": "Output port {wanted} not found (make sure loopMIDI is running and has created that port)",
    "请确认已安装并打开 loopMIDI，且已创建该端口": "Make sure loopMIDI is installed, running and has created that port",
    "（每 2 秒自动重试）。": "(retrying every 2 seconds).",
    "未找到输入端口 {wanted}（请确认键盘已连接）": "Input port {wanted} not found (make sure the keyboard is connected)",
    "请确认键盘已连接（每 2 秒自动重试）。": "Make sure the keyboard is connected (retrying every 2 seconds).",
    "可用输入端口: ": "Available input ports: ",
    "提示: ": "Hint: ",
    "连接失败: {exc}": "Connection failed: {exc}",
    "弹奏时下方会实时显示映射后的力度。": "The mapped velocity is shown live below while you play.",
    "输入设备已拔出": "Input device unplugged",
    "输出端口（loopMIDI）不可用": "Output port (loopMIDI) unavailable",
    "[断开] 检测到输入设备已拔出，等待重连...": "[Disconnected] Input device unplugged, waiting to reconnect...",
    "[断开] 检测到输出端口（loopMIDI）不可用，等待重连...": "[Disconnected] Output port (loopMIDI) unavailable, waiting to reconnect...",
    "提示: 关掉多余的工具窗口，或在宿主里停用键盘直连后，本工具会自动接上。": "Hint: close any extra windows of this tool, or disable the keyboard's direct input in your DAW — it will connect on its own.",

    # ---- 托盘 ----
    "显示主窗口": "Show main window",
    "最小化到后台": "Minimize to background",
    "关闭时总是询问": "Always ask when closing",
    "最小化时收进托盘": "Minimize to tray",
    "托盘图标启动失败（不影响使用）: {exc}": "The tray icon failed to start (everything else still works): {exc}",
    "已最小化到后台：托盘图标常驻，力度转发继续运行。": "Minimized to the background: the tray icon stays and velocity forwarding keeps running.",
    "已最小化到托盘，双击托盘图标可以重新打开窗口。": "Minimized to the tray — click the tray icon to reopen the window.",
    "仍在后台运行，双击托盘图标可以重新打开窗口。": "Still running in the background — click the tray icon to reopen the window.",
    "已恢复「关闭时总是询问」：下次点关闭会再次提示。": "Restored “Always ask when closing”: closing will ask again next time.",
    "已恢复关闭时询问。": "Restored the close prompt.",
    "最小化时收进托盘：": "Minimize to tray:",
    "已开启": "on",
    "已关闭": "off",
    "钢琴力度重映射 · 后台运行中（双击打开）": "Piano Velocity Remapper · running in the background (click to open)",
    "检查 MIDI 端口时出错：{exc}": "Error while checking MIDI ports: {exc}",
    "[端口] {note}": "[Ports] {note}",
    "没找到输出端口，请确认虚拟 MIDI 端口已打开": "Output port not found — make sure your virtual MIDI port is running",
    "虚拟端口「{matched}」· DAW 里请选同一个": "Virtual port “{matched}” · pick the same one in your DAW",
    "「{matched}」是硬件端口，DAW 收不到重映射后的力度": "“{matched}” is a hardware port — your DAW won't get the remapped velocity",

    # ---- 已经在运行 ----
    "工具已经在运行了。\\n\\n": "The tool is already running.\\n\\n",
    "如果它已最小化到后台，请双击任务栏右下角\\n": "If it is minimized to the background, click the icon in the\\n",
    "托盘里的图标把窗口叫回来；\\n": "system tray (bottom right) to bring the window back;\\n",
    "实在找不到，可在任务管理器结束 ": "if you cannot find it, end ",
    "pythonw.exe 后再启动。": "pythonw.exe in Task Manager and start again.",

    # ---- CLI ----
    "缺少依赖 mido。": "Missing dependency: mido.",
    "请先运行 start_mapper.bat，或手动执行:": "Run start_mapper.bat first, or run this by hand:",
    "错误: 没有可用的 MIDI 后端。请运行 start_mapper.bat 安装依赖。": "Error: no usable MIDI backend. Run start_mapper.bat to install the dependencies.",
    "警告: 配置项 {key} 取值无效（{bad}），已使用默认值 {default}。": "Warning: invalid value for {key} ({bad}); using the default {default}.",
    "警告: 读取 MIDI 输入端口失败: {exc}": "Warning: failed to read the MIDI input ports: {exc}",
    "警告: 读取 MIDI 输出端口失败: {exc}": "Warning: failed to read the MIDI output ports: {exc}",
    "检测到的 MIDI 输入端口:": "Detected MIDI input ports:",
    "  （无。请确认键盘已连接、驱动正常。）": "  (none — make sure the keyboard is connected and its driver is fine.)",
    "检测到的 MIDI 输出端口:": "Detected MIDI output ports:",
    "  （无。请先安装并打开 loopMIDI 创建虚拟端口。）": "  (none — install and open loopMIDI to create a virtual port.)",
    "未找到{kind}端口: {wanted}": "{kind} port not found: {wanted}",
    "{kind}端口被占用: {name}（{cause}）": "{kind} port busy: {name} ({cause})",
    "输入和输出选了同一个端口：{in_name}": "Input and output are set to the same port: {in_name}",
    "把「输出」改成另一个端口（比如启动时默认的 loopMIDI Port，": "Change “Output” to a different port (the default loopMIDI Port, for",
    "输入选电钢琴的 USB 端口）。": "and set “Input” to the keyboard's USB port).",
    "转发模式运行中（Ctrl+C 退出）。": "Forwarding mode running (Ctrl+C to quit).",
    "网页控制台已开启: {url}（浏览器已自动打开；关掉本窗口即同时退出）": "Web console started: {url} (the browser opened automatically; closing that window stops it too)",
    "警告: 网页控制台启动失败（{exc}），继续使用命令行模式。": "Warning: the web console failed to start ({exc}); staying in command-line mode.",
    "标定模式（--monitor）：只打印，不转发。Ctrl+C 退出。": "Calibration mode (--monitor): prints only, no forwarding. Ctrl+C to quit.",
    "提示: 用你最重的手法砸琴，观察 raw 的最大值，": "Hint: hit the keys as hard as you can and watch the raw maximum —",
    "        工具会实时给出建议的 max_input_velocity（当前配置: {max_in}）。": "        the tool suggests a max_input_velocity live (currently {max_in}).",
    "正在每 2 秒自动重试（无需重启工具）...": "retrying every 2 seconds (no need to restart the tool)...",
    "警告: {path} 内容不是 JSON 对象，使用默认配置。": "Warning: {path} is not a JSON object; using the default config.",
    "警告: 无法读取 {path}（{exc}），使用默认配置。": "Warning: cannot read {path} ({exc}); using the default config.",
    "提示: 未找到配置文件 {path}，将使用默认配置。": "Hint: config file {path} not found; using the default config.",
    "当前配置: 输入={} | 输出={} | 最大力度={} | 曲线={} | 日志间隔={}s": "Current config: input={} | output={} | max velocity={} | curve={} | log interval={}s",
    "钢琴力度重映射工具: 把键盘力度 1-max_in 实时映射到 1-127 并转发到 loopMIDI。": "Piano Velocity Remapper: maps keyboard velocity 1-max_in to 1-127 in real time and forwards it to loopMIDI.",
    "配置文件路径（默认: {default_path}）": "Path to the config file (default: {default_path})",
    "标定模式: 只打印 raw -> mapped，不转发（用于测出真实最大力度）": "Calibration mode: print raw -> mapped without forwarding (to measure the real maximum velocity)",
    "仅列出 MIDI 输入/输出端口后退出": "List the MIDI input/output ports and exit",
    "转发模式下逐音符打印力度映射": "Print the velocity mapping for every note while forwarding",
    "不启动网页控制台（纯命令行转发，不自动开浏览器）": "Don't start the web console (pure command-line forwarding, no browser)",
    "工具已经有一个实例在运行了，无法重复启动。": "Another instance is already running, so this one cannot start.",
    "请先关闭原来的工具窗口（或到任务管理器结束 velocity_mapper 进程），再重新启动。": "Close the existing window first (or end the velocity_mapper process in Task Manager), then start again.",
}

# --------------------------------------------------------------------------- #
# f-string：中文字面量 -> 英文字面量（%s = 原样搬过来的那个 {表达式}）
# --------------------------------------------------------------------------- #

FSTRINGS = {
    "gui.py": {
        'f"读取 MIDI 端口失败: {exc}"': 'f"Failed to read MIDI ports: %s"',
        'f"输入 {st.get(\'input_name\') or \'—\'}    →    "': 'f"Input %s    →    "',
        'f"输出 {st.get(\'output_name\') or \'—\'}"': 'f"Output %s"',
        'f"已弹 {s[\'notes\']} 个音符"': 'f"Played %s notes"',
        'f"建议最大力度 {sug}"': 'f"Suggested max velocity %s"',
        'f"采用 {sug}"': 'f"Use %s"',
        'f"采用建议值 {sug}"': 'f"Use suggested %s"',
        'f"最近范围：原始 {s[\'raw_min\']}–{s[\'raw_max\']}"': 'f"Recent range: raw %s–%s"',
        'f"  →  映射 {s[\'map_min\']}–{s[\'map_max\']}"': 'f"  →  mapped %s–%s"',
        'f"共 {min(shown, self.LOG_LINES)} 条"': 'f"%s lines"',
        'f"托盘图标启动失败（不影响使用）: {exc}"': 'f"The tray icon failed to start (everything else still works): %s"',
        'f"检查 MIDI 端口时出错：{exc}"': 'f"Error while checking MIDI ports: %s"',
        'f"[端口] {note}"': 'f"[Ports] %s"',
        'f"虚拟端口「{matched}」· DAW 里请选同一个"': 'f"Virtual port “%s” · pick the same one in your DAW"',
        'f"「{matched}」是硬件端口，DAW 收不到重映射后的力度"': 'f"“%s” is a hardware port — your DAW won\'t get the remapped velocity"',
    },
    "engine.py": {
        'f"警告: 无法写入配置文件 {self._config_path}: {exc}"': 'f"Warning: cannot write the config file %s: %s"',
        'f"[配置] 端口设置已更新，正在重连"': 'f"[Config] Port settings updated, reconnecting"',
        'f"（输出: {live_cfg[\'output_port\']}）"': 'f"(output: %s)"',
        'f"[断开] 输入端口异常: {exc}"': 'f"[Disconnected] Input port error: %s"',
        'f"输入端口异常: {exc}"': 'f"Input port error: %s"',
        'f"[{time.strftime(\'%H:%M:%S\')}] 音符 {msg.note:>3}  "': 'f"[%s] note %s  "',
        'f"力度 {old_vel:>3} -> {msg.velocity:>3}"': 'f"velocity %s -> %s"',
        'f"\\r最近映射力度: {msg.velocity:>3}"': 'f"\\rlatest mapped velocity: %s"',
        'f"[断开] 输出端口异常: {exc}"': 'f"[Disconnected] Output port error: %s"',
        'f"输出端口异常: {exc}"': 'f"Output port error: %s"',
        'f"未找到输出端口 {exc.wanted!r}（请确认 loopMIDI 已打开并创建该端口）"': 'f"Output port %s not found (make sure loopMIDI is running and has created that port)"',
        'f"[等待] 未找到输出端口 {cfg[\'output_port\']!r}。"': 'f"[Waiting] Output port %s not found."',
        'f"请确认已安装并打开 loopMIDI，且已创建该端口"': 'f"Make sure loopMIDI is installed, running and has created that port"',
        'f"（每 2 秒自动重试）。"': 'f"(retrying every 2 seconds)."',
        'f"未找到输入端口 {exc.wanted!r}（请确认键盘已连接）"': 'f"Input port %s not found (make sure the keyboard is connected)"',
        'f"[等待] 未找到输入端口 {cfg[\'input_port_substring\']!r}。"': 'f"[Waiting] Input port %s not found."',
        'f"请确认键盘已连接（每 2 秒自动重试）。"': 'f"Make sure the keyboard is connected (retrying every 2 seconds)."',
        'f"{exc.kind}端口被占用：{exc.name}"': 'f"%s port busy: %s"',
        'f"[等待] {exc.kind}端口被占用（{exc.name}）。可能是另一个工具窗口还没关，"': 'f"[Waiting] %s port busy (%s). Another window of this tool may still be open,"',
        'f"或宿主软件正占着键盘直连，正在自动重试..."': 'f"or your DAW is holding the keyboard’s direct input; retrying automatically..."',
        'f"[配置] {exc.message}"': 'f"[Config] %s"',
        'f"连接失败: {exc}"': 'f"Connection failed: %s"',
        'f"[等待] 端口连接失败（{exc}），正在自动重试..."': 'f"[Waiting] Port connection failed (%s), retrying automatically..."',
        'f"[连接] {in_port.name}  ->  {out_port.name}"': 'f"[Connected] %s  ->  %s"',
        'f"{reason}，等待重连…"': 'f"%s, waiting to reconnect…"',
    },
    "velocity_mapper.py": {
        'f"警告: 配置项 {key!r} 取值无效（{bad!r}），已使用默认值 {default!r}。"': 'f"Warning: invalid value for %s (%s); using the default %s."',
        'f"警告: 读取 MIDI 输入端口失败: {exc}"': 'f"Warning: failed to read the MIDI input ports: %s"',
        'f"警告: 读取 MIDI 输出端口失败: {exc}"': 'f"Warning: failed to read the MIDI output ports: %s"',
        'f"未找到{kind}端口: {wanted}"': 'f"%s port not found: %s"',
        'f"{kind}端口被占用: {name}（{cause}）"': 'f"%s port busy: %s (%s)"',
        'f"输入和输出选了同一个端口：{in_name}"': 'f"Input and output are set to the same port: %s"',
        'f"网页控制台已开启: {url}（浏览器已自动打开；关掉本窗口即同时退出）"': 'f"Web console started: %s (the browser opened automatically; closing that window stops it too)"',
        'f"警告: 网页控制台启动失败（{exc}），继续使用命令行模式。"': 'f"Warning: the web console failed to start (%s); staying in command-line mode."',
        'f"      工具会实时给出建议的 max_input_velocity（当前配置: {cfg[\'max_input_velocity\']}）。"': 'f"      the tool suggests a max_input_velocity live (currently %s)."',
        'f"[等待] 未找到输入端口 {cfg[\'input_port_substring\']!r}，"': 'f"[Waiting] Input port %s not found,"',
        'f"正在每 2 秒自动重试（无需重启工具）..."': 'f"retrying every 2 seconds (no need to restart the tool)..."',
        'f"[错误] 打开输入端口失败: {exc}"': 'f"[Error] Failed to open the input port: %s"',
        'f"[连接] {in_port.name}"': 'f"[Connected] %s"',
        'f"[断开] 输入端口异常: {exc}"': 'f"[Disconnected] Input port error: %s"',
        'f"[{_ts()}] 音符 {msg.note:>3}  raw {msg.velocity:>3}  ->  mapped {new_vel:>3}"': 'f"[%s] note %s  raw %s  ->  mapped %s"',
        'f"[{_ts()}] 已弹 {stats[\'notes\']} 个音符 | "': 'f"[%s] %s notes played | "',
        'f"建议 max_input_velocity={stats[\'raw_max\']}（当前 {cfg[\'max_input_velocity\']}）"': 'f"Suggested max_input_velocity=%s (current %s)"',
        'f"警告: {path} 内容不是 JSON 对象，使用默认配置。"': 'f"Warning: %s is not a JSON object; using the default config."',
        'f"警告: 无法读取 {path}（{exc}），使用默认配置。"': 'f"Warning: cannot read %s (%s); using the default config."',
        'f"提示: 未找到配置文件 {path}，将使用默认配置。"': 'f"Hint: config file %s not found; using the default config."',
        'f"配置文件路径（默认: {os.path.join(DATA_DIR, \'config.json\')}）"': 'f"Path to the config file (default: %s)"',
    },
}

# 日志颜色分类用的前缀：两种语言都要认，所以不翻译，只补英文版
COLOR_PREFIXES = {
    "[连接]": "[Connected]",
    "[等待]": "[Waiting]",
    "[断开]": "[Disconnected]",
    "[错误]": "[Error]",
    "[配置]": "[Config]",
    "[已": "[已",
    "转发模式": "Forwarding mode",
    "Done": "Done",
    "提示": "提示",
    "可用": "可用",
    "警告": "警告",
}

PLAIN_STR = re.compile(r'"((?:[^"\\]|\\.)*[\u4e00-\u9fff](?:[^"\\]|\\.)*)"')
FSTR_LIT = re.compile(r'f"([^"\n]*)"')


def split_groups(body: str):
    """把 f-string 正文里的 {表达式} 逐个抠出来（跳过 {{ }} 转义）。"""
    groups, depth, buf, i = [], 0, "", 0
    while i < len(body):
        ch = body[i]
        if ch == "{":
            if i + 1 < len(body) and body[i + 1] == "{":
                i += 2
                continue
            depth, buf = 1, ""
            i += 1
            while i < len(body) and depth:
                if body[i] == "{":
                    depth += 1
                elif body[i] == "}":
                    depth -= 1
                    if depth == 0:
                        break
                buf += body[i]
                i += 1
            groups.append("{" + buf + "}")
            i += 1
            continue
        if ch == "}" and i + 1 < len(body) and body[i + 1] == "}":
            i += 2
            continue
        i += 1
    return groups


def build_english(zh_lit: str, en_pattern: str) -> str:
    """把中文 f-string 的 {表达式} 按位置填进英文模板。"""
    body = zh_lit[2:-1]
    groups = split_groups(body)
    parts = en_pattern.split("%s")
    if len(parts) - 1 != len(groups):
        raise ValueError("占位符数量对不上: %r (%d 个) vs %r (%d 个)"
                         % (zh_lit, len(groups), en_pattern, len(parts) - 1))
    out = parts[0]
    for g, nxt in zip(groups, parts[1:]):
        out += g + nxt
    return out


def docstring_lines(lines):
    in_doc, delim, out = False, None, set()
    for i, line in enumerate(lines):
        s = line.strip()
        if in_doc:
            out.add(i)
            if delim in s:
                in_doc = False
            continue
        if s.startswith(('"""', "'''")):
            q = s[:3]
            out.add(i)
            if s.count(q) < 2:
                in_doc, delim = True, q
            continue
    return out


def process(fname: str, write: bool):
    path = os.path.join(ROOT, fname)
    with io.open(path, encoding="utf-8") as f:
        src = f.read()
    lines = src.splitlines(True)
    doc = docstring_lines(lines)

    misses = []
    plain_hits = 0

    # --- f-string ---
    table = FSTRINGS.get(fname, {})
    for zh_lit, en_pattern in table.items():
        if zh_lit not in src:
            misses.append((fname, 0, "FSTRING 未找到: " + zh_lit))
            continue
        try:
            en_lit = build_english(zh_lit, en_pattern)
        except ValueError as exc:
            misses.append((fname, 0, str(exc)))
            continue
        if write:
            src = src.replace(zh_lit, "trf(%s, %s)" % (zh_lit, en_lit))

    # --- 普通字符串 ---
    out_lines = []
    for i, line in enumerate(src.splitlines(True)):
        if i in doc or line.lstrip().startswith("#"):
            out_lines.append(line)
            continue

        def repl(m):
            content = m.group(1)
            # f-string 由上面的表处理，这里跳过
            if m.start() > 0 and line[m.start() - 1] in "fF":
                return m.group(0)
            if content in COLOR_PREFIXES:
                return m.group(0)
            if content not in PLAIN:
                misses.append((fname, i + 1, content))
                return m.group(0)
            nonlocal plain_hits
            plain_hits += 1
            en = PLAIN[content].replace("\\", "\\\\").replace('"', '\\"')
            return 'tr("%s", "%s")' % (content, en)

        out_lines.append(PLAIN_STR.sub(repl, line))
    src = "".join(out_lines)

    # --- 日志颜色分类：补上英文前缀 ---
    for zh, en in COLOR_PREFIXES.items():
        if zh == en:
            continue
        old = '("%s"' % zh
        new = '("%s", "%s"' % (zh, en)
        if old in src:
            src = src.replace(old, new)

    if write:
        with io.open(path, "w", encoding="utf-8", newline="") as f:
            f.write(src)
    return plain_hits, misses


def main() -> int:
    write = "--write" in sys.argv
    total, all_misses = 0, []
    for fname in ("gui.py", "engine.py", "velocity_mapper.py"):
        hits, misses = process(fname, write)
        total += hits
        all_misses += misses
        print("%-20s 普通字符串 %3d 条" % (fname, hits))
    print("合计普通字符串: %d 条" % total)
    if all_misses:
        print("\n需要人工处理:")
        seen = set()
        for fn, ln, txt in all_misses:
            key = (fn, txt[:60])
            if key in seen:
                continue
            seen.add(key)
            print("  %s:%s  %s" % (fn, ln, txt[:100]))
    else:
        print("\n没有遗漏 ✓")
    return 0


if __name__ == "__main__":
    sys.exit(main())

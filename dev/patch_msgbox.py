"""精确替换 gui.py 里那段被转换打断的 messagebox 调用（按行号替换）。"""
import io
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
path = os.path.join(ROOT, "gui.py")

with io.open(path, encoding="utf-8") as f:
    lines = f.readlines()

# 找到 messagebox.showinfo( 开始的那一行
start = None
for i, line in enumerate(lines):
    if "messagebox.showinfo(" in line and "def _already_running" not in line:
        start = i
        break
assert start is not None, "没找到 messagebox.showinfo("

# 找到这一语句结束（括号配平）
depth = 0
end = start
for j in range(start, len(lines)):
    depth += lines[j].count("(") - lines[j].count(")")
    if depth <= 0 and j > start:
        end = j
        break
    if depth <= 0 and j == start:
        end = j
        break

new = '''    messagebox.showinfo(
        tr("力度重映射", "Velocity Remapper"),
        tr("工具已经在运行了。\\n\\n"
           "如果它已最小化到后台，请双击任务栏右下角\\n"
           "托盘里的图标把窗口叫回来；\\n"
           "实在找不到，可在任务管理器结束 "
           "pythonw.exe 后再启动。",
           "The tool is already running.\\n\\n"
           "If it is minimized to the background, click the icon in the\\n"
           "system tray (bottom right) to bring the window back;\\n"
           "if you cannot find it, end "
           "pythonw.exe in Task Manager and start again."))
'''

print("替换第 %d-%d 行" % (start + 1, end + 1))
lines[start:end + 1] = [new]

with io.open(path, "w", encoding="utf-8", newline="") as f:
    f.writelines(lines)
print("完成")

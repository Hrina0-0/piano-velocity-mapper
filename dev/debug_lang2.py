"""定位 GUI 里语言切换后文字没变的问题（输出走 stderr，避开 App 重定向的 stdout）。"""
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import i18n
import gui


def say(*a):
    print(*a, file=sys.stderr, flush=True)


tmp = tempfile.mkdtemp(prefix="p115_dbg_")
cfg = os.path.join(tmp, "config.json")

app = gui.App(cfg)
say("启动后 get_lang():", i18n.get_lang())
say("标题控件文字:", app._header.winfo_children()[0].winfo_children()[0].cget("text"))
say("语言按钮文字:", app.lang_btn.cget("text"))

app._toggle_lang()
say("切换后 get_lang():", i18n.get_lang())
say("标题控件文字:", app._header.winfo_children()[0].winfo_children()[0].cget("text"))
say("语言按钮文字:", app.lang_btn.cget("text"))
app._quit()

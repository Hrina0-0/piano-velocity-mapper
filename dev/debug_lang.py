"""定位：切换语言后界面文字没跟着变。"""
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import i18n
import gui

print("i18n 模块文件:", i18n.__file__)
print("gui.tr 是 i18n.tr 吗:", gui.tr is i18n.tr)
print("初始语言:", i18n.get_lang())
print("初始 tr:", gui.tr("力度重映射", "Velocity Remapper"))

i18n.set_lang("en")
print("set_lang('en') 之后 get_lang():", i18n.get_lang())
print("set_lang('en') 之后 tr:", gui.tr("力度重映射", "Velocity Remapper"))
print("i18n._LANG =", repr(i18n._LANG))

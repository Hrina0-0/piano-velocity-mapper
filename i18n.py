#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
界面语言
========

中文 / English 双语，默认为中文。

用法（两种）：

    tr("力度映射", "Velocity mapping")          # 普通字符串
    trf("已弹 {n} 个音符", "Played {n} notes", n=notes)   # 带参数的字符串

中英文写在同一处，方便对照和后续维护。语言状态存在 ui_prefs.json 里，
跟着用户走。
"""

from __future__ import annotations

LANG_ZH = "zh"
LANG_EN = "en"

# 当前语言；由 gui.py 在启动时按偏好设置
_LANG = LANG_ZH


def get_lang() -> str:
    """当前语言代码："zh" 或 "en"。"""
    return _LANG


def set_lang(lang: str) -> None:
    """设置当前语言。未知语言按中文处理。"""
    global _LANG
    _LANG = LANG_EN if str(lang).lower().startswith("en") else LANG_ZH


def tr(zh: str, en: str) -> str:
    """按当前语言返回对应文案。"""
    return en if _LANG == LANG_EN else zh


def trf(zh: str, en: str, **kw: object) -> str:
    """带参数的文案。

    两个模板里的占位符名字必须一致，参数用关键字传。
    """
    tpl = en if _LANG == LANG_EN else zh
    return tpl.format(**kw)


__all__ = ["LANG_ZH", "LANG_EN", "get_lang", "set_lang", "tr", "trf"]

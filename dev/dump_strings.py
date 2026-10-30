"""把源码里含中文的字符串字面量连同行号 dump 出来，用来划定要翻译的范围。

只列「看起来是给用户看的」：排除注释行、docstring 行、print/日志的纯调试语。
"""
import io
import re
import sys

STR = re.compile(r'"([^"\r\n]*[\u4e00-\u9fff][^"\r\n]*)"')


def dump(path: str) -> None:
    with io.open(path, encoding="utf-8") as f:
        lines = f.readlines()
    print("=" * 70)
    print(path)
    print("=" * 70)
    in_doc = False
    doc_delim = None
    for i, line in enumerate(lines, 1):
        stripped = line.strip()
        # 粗略判断 docstring 区间
        if in_doc:
            if doc_delim in stripped:
                in_doc = False
            continue
        if stripped.startswith(('"""', "'''")):
            q = stripped[:3]
            if stripped.count(q) < 2:
                in_doc = True
                doc_delim = q
            continue
        if stripped.startswith("#"):
            continue
        for m in STR.finditer(line):
            s = m.group(1)
            print(f"{i:5d}  {s}")
    print()


for p in sys.argv[1:]:
    dump(p)

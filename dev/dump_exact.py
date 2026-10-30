"""把某个字符串在源码里的确切字符打印出来（排查文案表对不上的问题）。"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STR = re.compile(r'"([^"\\\n]*[\u4e00-\u9fff][^"\\\n]*)"')

needles = sys.argv[1:] or ["建议值", "已弹", "提示: 关掉"]


def main() -> None:
    for fname in ("gui.py", "engine.py", "velocity_mapper.py"):
        with io.open(os.path.join(ROOT, fname), encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                for m in STR.finditer(line):
                    c = m.group(1)
                    if any(n in c for n in needles):
                        print("%s:%d  %r" % (fname, i, c))


if __name__ == "__main__":
    main()

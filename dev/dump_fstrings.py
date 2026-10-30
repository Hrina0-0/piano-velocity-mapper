"""把源码里 f-string 的原始字节形式打出来，方便和文案表逐字符比对。"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FSTR = re.compile(r'f"[^"\n]*"')


def main() -> None:
    for fname in ("gui.py", "engine.py", "velocity_mapper.py"):
        path = os.path.join(ROOT, fname)
        with io.open(path, encoding="utf-8") as f:
            for i, line in enumerate(f, 1):
                for m in FSTR.finditer(line):
                    if re.search(r"[\u4e00-\u9fff]", m.group(0)):
                        print("%s:%d  %r" % (fname, i, m.group(0)))


if __name__ == "__main__":
    sys.exit(main())

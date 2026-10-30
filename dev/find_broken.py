"""找出被转换打断的「隐式字符串拼接」位置。

两种特征：
1) 某行 tr(/trf( 的括号没闭合；
2) 上一行以 tr(...)/trf(...) 收尾，下一行又以 tr(/trf(/字符串开头
   —— 原来是相邻字符串字面量的隐式拼接，转换后变成了两个函数调用。
"""
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CALL = re.compile(r'\btrf?\(')


def main() -> None:
    for fname in ("gui.py", "engine.py", "velocity_mapper.py"):
        path = os.path.join(ROOT, fname)
        with io.open(path, encoding="utf-8") as f:
            lines = f.read().splitlines()
        for i, line in enumerate(lines):
            s = line.strip()
            prev = lines[i - 1].strip() if i else ""
            broken = False
            for m in CALL.finditer(line):
                rest = line[m.end() - 1:]
                if rest.count("(") > rest.count(")"):
                    broken = True
                    break
            if not broken and i and CALL.search(prev) and \
                    (s.startswith("tr(") or s.startswith("trf(") or s.startswith('"')):
                broken = True
            if broken:
                print("%s:%d" % (fname, i + 1))
                for j in range(max(0, i - 2), min(len(lines), i + 3)):
                    mark = ">>" if j == i else "  "
                    print(" %s %5d | %s" % (mark, j + 1, lines[j].rstrip()))
                print()


if __name__ == "__main__":
    main()

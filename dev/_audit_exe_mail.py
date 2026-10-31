"""核实 exe 里的「邮箱」命中到底是什么（排查用的小工具）。

从 release\ 下任意一个分发包里取出 PianoVelocityMapper.exe，
把匹配到的邮箱样式字符串和它们的上下文打印出来。
"""
import os
import re
import zipfile

RELEASES = r"D:\Codex-work\repiano_pkg\release"


def _pick_zip() -> str:
    names = sorted(n for n in os.listdir(RELEASES) if n.lower().endswith(".zip"))
    if not names:
        raise SystemExit(f"release 目录下没有 zip：{RELEASES}")
    return os.path.join(RELEASES, names[0])


z = zipfile.ZipFile(_pick_zip())
with z.open("PianoVelocityMapper.exe") as f:
    data = f.read().decode("latin-1", "ignore")

pat = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
hits = set(pat.findall(data))
print("命中总数:", len(hits))
for h in sorted(hits)[:20]:
    i = data.find(h)
    ctx = data[max(0, i - 60):i + len(h) + 20].replace("\x00", ".")
    print(f"\n  {h}\n    上下文: ...{ctx}...")

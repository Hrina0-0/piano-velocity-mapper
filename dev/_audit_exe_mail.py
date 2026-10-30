"""核实 exe 里的「邮箱」命中到底是什么。"""
import os
import re
import zipfile

z = zipfile.ZipFile(os.path.join(r"D:\Codex-work\repiano_pkg\release",
                                 "钢琴力度重映射-绿色版.zip"))
data = z.read("PianoVelocityMapper.exe").decode("latin-1", "ignore")

pat = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
hits = set(pat.findall(data))
print("命中总数:", len(hits))
for h in sorted(hits)[:20]:
    i = data.find(h)
    ctx = data[max(0, i - 60):i + len(h) + 20].replace("\x00", ".")
    print(f"\n  {h}\n    上下文: ...{ctx}...")

"""最终分发审计：把两个 zip 解开，逐文件扫隐私与 P115 残留。"""
import os
import re
import sys
import zipfile

RELEASES = r"D:\Codex-work\repiano_pkg\release"
WORK = os.path.join(os.environ.get("TEMP", "."), "p115_audit")

ZIPS = ["钢琴力度重映射-绿色版.zip", "钢琴力度重映射-源码版.zip"]

# 扫描目标：个人标识 / 敏感词 / 型号残留
SCAN = {
    "P115 型号残留": re.compile(r"P-?115", re.I),
    "用户名": re.compile(r"Hrina", re.I),
    "本机路径": re.compile(r"Codex-work|D:\\\\Codex|C:\\\\Users", re.I),
    "键盘型号": re.compile(r"SMK25V2", re.I),
    "宿主软件": re.compile(r"StudioRack|MiniFuse|Bome", re.I),
    "邮箱": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"),
    "密钥类": re.compile(r"api[_-]?key|secret|password|passwd|bearer|"
                         r"authorization|access[_-]?token|private[_-]?key",
                         re.I),
}

# 已知误报（代码里的普通词 / 产品名，不是个人隐私）
FALSE_POSITIVE = {
    "密钥类": ("tokens=2", "tokens=", "tokenize"),
    # "bome" 来自 midi_ports.py 的虚拟端口识别名单（Bome Virtual MIDI 是产品名）
    "宿主软件": ("bome",),
}


def _in_printable_run(text: str, idx: int, length: int, span: int = 24) -> bool:
    """判断命中处是否落在可打印 ASCII 文本段里（排除二进制里的随机字节）。"""
    seg = text[max(0, idx - span):idx + length + span]
    return sum(1 for ch in seg if 32 <= ord(ch) < 127) / max(1, len(seg)) > 0.9

BINARY_EXT = {".exe", ".ico", ".png", ".pyc", ".zip", ".woff", ".ttf"}


def main() -> int:
    problems = 0
    for zname in ZIPS:
        zpath = os.path.join(RELEASES, zname)
        print("=" * 66)
        print("包:", zname)
        print("=" * 66)
        with zipfile.ZipFile(zpath) as z:
            entries = z.namelist()
            print(f"条目数: {len(entries)}")
            for e in entries:
                info = z.getinfo(e)
                print(f"   {e}  ({info.file_size} bytes)")
                if os.path.splitext(e)[1].lower() in BINARY_EXT:
                    data = z.read(e)
                    flat = data.decode("latin-1", "ignore")
                    hits = []
                    for label, pat in SCAN.items():
                        for m in pat.finditer(flat):
                            snippet = flat[max(0, m.start() - 40):m.end() + 40]
                            if label in FALSE_POSITIVE and \
                                    any(fp.lower() in snippet.lower()
                                        for fp in FALSE_POSITIVE[label]):
                                continue
                            # 二进制里只有落在可打印文本段中的命中才算数
                            if not _in_printable_run(flat, m.start(), m.end() - m.start()):
                                continue
                            hits.append((label, m.group()[:60]))
                            break
                    if hits:
                        print(f"      !! 二进制内命中: {hits}")
                        problems += len(hits)
                    continue

                try:
                    text = z.read(e).decode("utf-8")
                except UnicodeDecodeError:
                    try:
                        text = z.read(e).decode("utf-8-sig")
                    except UnicodeDecodeError:
                        print("      (跳过非文本)")
                        continue
                for label, pat in SCAN.items():
                    for m in pat.finditer(text):
                        line_no = text.count("\n", 0, m.start()) + 1
                        snippet = text.splitlines()[line_no - 1].strip()[:90]
                        if label in FALSE_POSITIVE and \
                                any(fp.lower() in snippet.lower()
                                    for fp in FALSE_POSITIVE[label]):
                            continue
                        print(f"      !! {label} @行{line_no}: {snippet}")
                        problems += 1
        print()

    print("=" * 66)
    print("审计结论:", "全部干净 ✓" if problems == 0 else f"发现 {problems} 处问题")
    return 0 if problems == 0 else 1


if __name__ == "__main__":
    sys.exit(main())

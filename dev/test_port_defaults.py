#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""默认端口选择的回归测试。

覆盖这几件事：
  1. 默认配置里不再写死作者自己的设备名；
  2. 空字符串是合法的「自动匹配」；
  3. 自动匹配会优先挑像钢琴/键盘的端口；
  4. 旧版本遗留的 "Digital Piano" 会被迁移掉，并写回文件。
"""
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import velocity_mapper as vm  # noqa: E402

failed = []


def check(name, cond, extra=""):
    if cond:
        print(f"  OK  {name}")
    else:
        print(f"  NG  {name} {extra}")
        failed.append(name)


print("1) 默认配置")
check("input_port_substring 默认不是写死的设备名",
      vm.DEFAULT_CONFIG["input_port_substring"] == "",
      f"实际={vm.DEFAULT_CONFIG['input_port_substring']!r}")
check("output_port 仍是 loopMIDI Port",
      vm.DEFAULT_CONFIG["output_port"] == "loopMIDI Port")

print("2) validate_config 接受空串")
cfg = vm.validate_config({"input_port_substring": "", "output_port": "loopMIDI Port"})
check("空串被保留（不当作非法值回退）", cfg["input_port_substring"] == "",
      f"实际={cfg['input_port_substring']!r}")

print("3) 自动挑输入端口")
cases = [
    (["Microsoft MIDI Mapper", "Arturia KeyStep Pro"], "Arturia KeyStep Pro"),
    (["Microsoft MIDI Mapper", "Roland A-88 MKII"], "Roland A-88 MKII"),
    (["Yamaha MOXF8", "Microsoft MIDI Mapper"], "Yamaha MOXF8"),
    (["Microsoft MIDI Mapper"], "Microsoft MIDI Mapper"),      # 实在没得选
    ([], None),
]
for names, want in cases:
    got = vm.auto_pick_input_port(names)
    check(f"{names} -> {want!r}", got == want, f"实际={got!r}")

print("4) 旧配置迁移")
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "config.json")
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"input_port_substring": "Digital Piano",
                   "output_port": "loopMIDI Port",
                   "max_input_velocity": 105,
                   "curve_exponent": 1.0,
                   "log_interval_sec": 2.0}, f)
    cfg = vm.load_config(p)
    check("遗留的 Digital Piano 被清空", cfg["input_port_substring"] == "",
          f"实际={cfg['input_port_substring']!r}")
    check("其它用户设置保留", cfg["max_input_velocity"] == 105,
          f"实际={cfg['max_input_velocity']!r}")
    with open(p, encoding="utf-8") as f:
        saved = json.load(f)
    check("配置已写回磁盘", saved.get("input_port_substring") == "",
          f"实际={saved.get('input_port_substring')!r}")

    # 用户自己选的名字要保留，不能被误清
    p2 = os.path.join(td, "config2.json")
    with open(p2, "w", encoding="utf-8") as f:
        json.dump({"input_port_substring": "我的钢琴", "output_port": "loopMIDI Port"}, f)
    cfg2 = vm.load_config(p2)
    check("用户自己选的端口名不被清除", cfg2["input_port_substring"] == "我的钢琴",
          f"实际={cfg2['input_port_substring']!r}")

print()
if failed:
    print(f"失败 {len(failed)} 项: {failed}")
    sys.exit(1)
print("全部通过")

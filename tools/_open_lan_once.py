#!/usr/bin/env python3
"""一次性：让 mihomo 代理端口对局域网开放（带认证，本机免认证）。用完即删。"""
import shutil
import time
from pathlib import Path

CONFIG = Path("/vol2/1000/Docker/mihomo/config.yaml")
USER, PASS = "seedflow", "sf2026nas"

text = CONFIG.read_text(encoding="utf-8")
backup = CONFIG.with_suffix(".yaml.bak.%s" % time.strftime("%Y%m%d-%H%M%S"))
shutil.copy2(CONFIG, backup)

before = text
text = text.replace("bind-address: 127.0.0.1", "bind-address: 0.0.0.0")
text = text.replace("allow-lan: false", "allow-lan: true")

if "authentication:" not in text:
    block = (
        "authentication:\n"
        '  - "%s:%s"\n'
        "skip-auth-prefixes:\n"
        "  - 127.0.0.1/32\n"
    ) % (USER, PASS)
    text = text.replace("allow-lan: true\n", "allow-lan: true\n" + block, 1)

if text == before:
    print("没有改动（可能已经开放过）")
else:
    CONFIG.write_text(text, encoding="utf-8")
    print("已更新:", CONFIG)
    print("备份:", backup)

print("---- 关键行 ----")
for line in text.splitlines():
    s = line.strip()
    if s.startswith(("mixed-port", "bind-address", "allow-lan", "authentication", "skip-auth", "- \"seedflow")):
        print(line)

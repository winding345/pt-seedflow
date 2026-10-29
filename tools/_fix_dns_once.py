#!/usr/bin/env python3
"""一次性：把 mihomo 的 DNS 配置改成不依赖被墙的 fallback。用完即删。"""
import re
import shutil
import time
from pathlib import Path

CONFIG = Path("/vol2/1000/Docker/mihomo/config.yaml")

NEW_DNS = """dns:
  enable: true
  ipv6: false
  enhanced-mode: fake-ip
  fake-ip-range: 198.18.0.1/16
  default-nameserver:
    - 223.5.5.5
    - 119.29.29.29
  nameserver:
    - https://223.5.5.5/dns-query
    - https://doh.pub/dns-query
"""


def main() -> int:
    text = CONFIG.read_text(encoding="utf-8")
    backup = CONFIG.with_suffix(".yaml.bak.%s" % time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(CONFIG, backup)

    pattern = re.compile(r"^dns:\n(?:[ \t].*\n|\n)*?(?=^\S)", re.MULTILINE)
    if not pattern.search(text):
        print("没找到 dns 段，放弃")
        return 1
    text = pattern.sub(NEW_DNS, text, count=1)
    CONFIG.write_text(text, encoding="utf-8")
    print("已替换 dns 段")
    print("备份:", backup)
    print("---- 现在的 dns 段 ----")
    for line in text.splitlines():
        if line.startswith("dns:") or line.startswith("  "):
            print(line)
        if line.startswith("proxy-providers:"):
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

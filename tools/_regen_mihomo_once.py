#!/usr/bin/env python3
"""一次性：重写 mihomo 配置（订阅走直连、DNS 不依赖被墙服务）。用完即删。"""
import re
import shutil
import time
from pathlib import Path

CONFIG = Path("/vol2/1000/Docker/mihomo/config.yaml")

TEMPLATE = """\
# mihomo：规则模式，PT 全部直连，只有 m-team 的 API 走代理
mixed-port: 7890
bind-address: 127.0.0.1
allow-lan: false
mode: rule
log-level: info
ipv6: false
external-controller: 127.0.0.1:9090
secret: ""

profile:
  store-selected: true

dns:
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

proxy-providers:
  airport:
    type: http
    url: "__SUB_URL__"
    interval: 3600
    path: ./providers/airport.yaml
    # 关键：下载订阅本身必须直连，否则会绕进还没建好的代理里
    proxy: DIRECT
    health-check:
      enable: true
      url: https://www.gstatic.com/generate_204
      interval: 300

proxy-groups:
  - name: PROXY
    type: url-test
    use: [airport]
    url: https://www.gstatic.com/generate_204
    interval: 300
    tolerance: 50
  - name: MANUAL
    type: select
    use: [airport]

rules:
  # 订阅站点直连（否则更新订阅会绕回代理）
  - DOMAIN-SUFFIX,52pokemon66.cc,DIRECT

  # 只有 m-team 的 API 走代理
  - DOMAIN,api.m-team.cc,PROXY

  # PT 站一律直连：tracker / announce / 网页 / 种子文件
  - DOMAIN-SUFFIX,m-team.cc,DIRECT
  - DOMAIN-SUFFIX,pttime.org,DIRECT
  - DOMAIN-SUFFIX,carpt.net,DIRECT

  # 国内直连，其余走代理
  - GEOIP,CN,DIRECT
  - MATCH,PROXY
"""


def main() -> int:
    text = CONFIG.read_text(encoding="utf-8")
    match = re.search(r'^\s*url:\s*"(https?://[^"]+)"', text, re.MULTILINE)
    if not match:
        print("原配置里找不到订阅 url，放弃")
        return 1
    sub_url = match.group(1)

    backup = CONFIG.with_suffix(".yaml.bak.%s" % time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(CONFIG, backup)

    CONFIG.write_text(TEMPLATE.replace("__SUB_URL__", sub_url), encoding="utf-8")
    print("已重写:", CONFIG)
    print("备份:", backup)
    print("订阅:", re.sub(r"([?&]token=)[^&]+", r"\1***", sub_url))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

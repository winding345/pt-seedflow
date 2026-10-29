#!/usr/bin/env python3
"""一次性：把 v2rayA 里的订阅地址写入 mihomo 的 config.yaml。

用完即删。订阅 token 不打印、不落日志。
"""
from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:2017"
CONFIG = Path(os.environ.get("MIHOMO_CONFIG", "/vol2/1000/Docker/mihomo/config.yaml"))
USER = os.environ.get("V2RAYA_USER", "admin")
PASSWORDS = [os.environ.get("V2RAYA_PASS") or "", "admin123", "admin"]


def post(path: str, body: dict, token: str = "") -> dict:
    data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def get(path: str, token: str = "") -> dict:
    req = urllib.request.Request(BASE + path)
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.loads(resp.read().decode())


def login() -> str:
    for pw in PASSWORDS:
        if not pw:
            continue
        try:
            res = post("/api/login", {"username": USER, "password": pw})
            token = (res.get("data") or {}).get("token")
            if token:
                print("v2rayA 登录成功")
                return token
        except Exception:  # noqa: BLE001
            continue
    raise SystemExit("v2rayA 登录失败")


def subscription_url(token: str) -> str:
    res = get("/api/touch", token)
    subs = ((res.get("data") or {}).get("touch") or {}).get("subscriptions") or []
    if not subs:
        raise SystemExit("v2rayA 里没有订阅")
    url = subs[0].get("address") or ""
    if not url.startswith("http"):
        raise SystemExit("订阅地址异常")
    return url


def mask(url: str) -> str:
    return re.sub(r"([?&]token=)[^&]+", r"\1***", url)


def main() -> int:
    if not CONFIG.is_file():
        raise SystemExit("找不到 %s" % CONFIG)
    token = login()
    url = subscription_url(token)

    text = CONFIG.read_text(encoding="utf-8")
    backup = CONFIG.with_suffix(".yaml.bak.%s" % time.strftime("%Y%m%d-%H%M%S"))
    shutil.copy2(CONFIG, backup)

    pattern = re.compile(r'(proxy-providers:\s*\n\s+airport:\s*\n(?:.*\n)*?\s+url:\s*)".*?"')
    if pattern.search(text):
        text = pattern.sub(lambda m: m.group(1) + '"%s"' % url, text, count=1)
        print("已替换 url 行")
    else:
        # 退化处理：直接替换占位串
        if "把这里换成你的订阅地址" not in text:
            raise SystemExit("没找到 url 行，也没找到占位串，请检查 config.yaml 结构")
        text = text.replace('"把这里换成你的订阅地址"', '"%s"' % url, 1)
        print("已替换占位串")

    CONFIG.write_text(text, encoding="utf-8")
    print("已写入:", CONFIG)
    print("备份:", backup)
    print("订阅:", mask(url))

    # 语法自查：至少确认 url 行是 http 开头
    for line in text.splitlines():
        if line.strip().startswith("url:"):
            print("校验收到的行:", line.strip()[:24] + "...")
            break
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

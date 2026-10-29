#!/usr/bin/env python3
"""v2rayA 配置小工具（本机 127.0.0.1:2017）。

用法：
    python3 tools/v2raya_ctl.py probe                   # 探测接口（只读）
    python3 tools/v2raya_ctl.py status                  # 当前状态
    python3 tools/v2raya_ctl.py proxy-off               # 关透明代理/系统代理
    python3 tools/v2raya_ctl.py start                   # 启动代理
    python3 tools/v2raya_ctl.py stop                    # 停止代理
    python3 tools/v2raya_ctl.py check                   # 验证出口 IP 与端口

密码通过环境变量传入，避免落盘：
    V2RAYA_PASS=admin python3 tools/v2raya_ctl.py status
"""
from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.request

BASE = os.environ.get("V2RAYA_BASE", "http://127.0.0.1:2017")
USER = os.environ.get("V2RAYA_USER", "admin")
PASSWORDS = [p for p in (os.environ.get("V2RAYA_PASS"), "admin", "admin123") if p]

TOKEN = ""


def call(path: str, method: str = "GET", body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if TOKEN:
        req.add_header("Authorization", "Bearer " + TOKEN)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", "replace")
        return {"_http": exc.code, "_raw": raw[:300]}
    except Exception as exc:  # noqa: BLE001
        return {"_error": str(exc)}
    try:
        return json.loads(raw)
    except ValueError:
        return {"_raw": raw[:300]}


def login() -> bool:
    global TOKEN
    for pw in PASSWORDS:
        res = call("/api/login", "POST", {"username": USER, "password": pw})
        data = res.get("data") or {}
        token = data.get("token") if isinstance(data, dict) else None
        if token:
            TOKEN = token
            print("登录成功（密码长度 %d）" % len(pw))
            return True
        print("登录失败（密码长度 %d）：%s" % (len(pw), res.get("message") or res.get("_raw")))
    return False


def probe() -> None:
    for path in ("/api/version", "/api/setting", "/api/touch", "/api/v2ray",
                 "/api/server", "/api/servers", "/api/connection"):
        res = call(path)
        brief = json.dumps(res, ensure_ascii=False)
        print("%-18s %s" % (path, brief[:220]))


def status() -> None:
    for path in ("/api/setting", "/api/touch", "/api/v2ray", "/api/server"):
        res = call(path)
        print("== %s ==" % path)
        print(json.dumps(res, ensure_ascii=False)[:600])


def touch(transparent: bool, system: bool) -> None:
    res = call("/api/touch", "POST",
               {"transparentProxy": transparent, "systemProxy": system})
    print("设置透明代理=%s 系统代理=%s -> %s" % (transparent, system, res.get("code")))


def run(running: bool) -> None:
    res = call("/api/v2ray", "POST", {"running": running})
    print("运行=%s -> %s" % (running, res.get("code") or res))


def check() -> None:
    import subprocess
    for desc, args in (
        ("监听端口", ["ss", "-ltn"]),
        ("直连出口", ["curl", "-s", "-m", "10", "https://api.ipify.org"]),
        ("代理出口", ["curl", "-s", "-m", "12", "-x", "http://127.0.0.1:20171",
                     "https://api.ipify.org"]),
        ("m-team via proxy", ["curl", "-s", "-o", "/dev/null", "-m", "12",
                              "-x", "http://127.0.0.1:20171",
                              "-w", "%{http_code}", "https://api.m-team.cc/"]),
    ):
        try:
            out = subprocess.run(args, capture_output=True, text=True, timeout=20).stdout
        except Exception as exc:  # noqa: BLE001
            out = "err: %s" % exc
        if desc == "监听端口":
            out = " ".join(l.split()[3] for l in out.splitlines()
                           if any(p in l for p in ("20170", "20171", "20172", "52345")))
        print("%-18s %s" % (desc, out.strip()[:120]))


def main(argv: list[str]) -> int:
    action = argv[0] if argv else "status"
    if action == "probe":
        # probe 也尝试登录，失败不阻塞
        login()
        probe()
        return 0
    if not login():
        print("无法登录，检查 V2RAYA_PASS")
        return 1
    if action == "status":
        status()
    elif action == "proxy-off":
        touch(False, False)
    elif action == "proxy-on":
        touch(True, False)
    elif action == "start":
        run(True)
    elif action == "stop":
        run(False)
    elif action == "check":
        check()
    else:
        print(__doc__)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

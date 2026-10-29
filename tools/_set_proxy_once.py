#!/usr/bin/env python3
"""一次性：给 m-team 站点配置代理，并触发一次 search 验证。用完即删。"""
import json
import time
import urllib.request

BASE = "http://127.0.0.1:8600"
PROXY = "http://127.0.0.1:7890"
SITE = "mteam"
SERVICE = "auto-mteam-1"


def req(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=60) as resp:
        return json.loads(resp.read().decode())


cfg = req("/api/sites/%s/config" % SITE)
old = cfg.get("proxy") or "(空)"
cfg["proxy"] = PROXY
req("/api/sites/%s/config" % SITE, "PUT", cfg)
print("站点 %s 的 proxy: %s -> %s" % (SITE, old, PROXY))
print("重新读取确认:", req("/api/sites/%s/config" % SITE).get("proxy"))

print("触发手动 search ...")
res = req("/api/services/%s/search" % SERVICE, "POST")
print("ok=%s candidates=%s" % (res.get("ok"), res.get("candidates")))
snap = res.get("snapshot") or {}
print("last_error:", snap.get("last_error") or "(空)")

time.sleep(1)
for svc in req("/api/status")["services"]:
    if svc["name"] == SERVICE:
        print("candidates=%s used=%s torrents=%s" % (
            svc["stats"]["candidates"], svc["stats"]["used_gb"], svc["stats"]["torrents"]))
        for a in svc["actions"][:6]:
            print(" ", a["time"], a["kind"], a["name"], "|", a["detail"][:70])

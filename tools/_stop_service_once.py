#!/usr/bin/env python3
"""一次性：停用 m-team 服务（保留 qB 种子与历史）。用完即删。"""
import json
import urllib.request

BASE = "http://127.0.0.1:8600"
SERVICE = "auto-mteam-1"


def req(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode())


cfg = req("/api/services/%s/config" % SERVICE)
print("改之前 enabled =", cfg.get("enabled"))
cfg["enabled"] = False
req("/api/services/%s/config" % SERVICE, "PUT", cfg)
now = req("/api/services/%s/config" % SERVICE)
print("改之后 enabled =", now.get("enabled"))

st = req("/api/status")
print("当前运行的服务:", [s["name"] for s in st["services"]])
for s in st["services"]:
    if s["name"] == SERVICE:
        print("该服务: used=%.2fGB torrents=%s candidates=%s" % (
            s["stats"]["used_gb"], s["stats"]["torrents"], s["stats"]["candidates"]))

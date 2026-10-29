#!/usr/bin/env python3
"""一次性：把删除阈值改回 128MB。用完即删。"""
import json
import urllib.request

BASE = "http://127.0.0.1:8600"
SERVICE = "auto-mteam-1"
CHANGES = {"delete_threshold_mb": 128.0}


def req(path, method="GET", body=None):
    data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(BASE + path, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(r, timeout=30) as resp:
        return json.loads(resp.read().decode())


cfg = req("/api/services/%s/config" % SERVICE)
print("改之前:", {k: cfg.get(k) for k in CHANGES})
cfg.update(CHANGES)
req("/api/services/%s/config" % SERVICE, "PUT", cfg)
now = req("/api/services/%s/config" % SERVICE)
print("改之后:", {k: now.get(k) for k in CHANGES})
print("disk_gb:", now.get("disk_gb"))

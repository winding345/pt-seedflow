#!/usr/bin/env python3
"""从本机 API 取一份真实状态快照，输出 gzip+base64，便于通过受限终端带回本地。

用法（在 NAS 上）：
    python3 tools/snapshot.py            # 打印分块 base64 与校验信息
"""
from __future__ import annotations

import base64
import gzip
import hashlib
import json
import sys
import urllib.request

API = "http://127.0.0.1:8600/api/status"
CHUNK = 64


def main() -> int:
    try:
        with urllib.request.urlopen(API, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except Exception as exc:  # noqa: BLE001
        print("读取失败: %s" % exc, file=sys.stderr)
        return 1

    # 动作列表只保留几条，够前端调试即可
    for service in payload.get("services", []):
        actions = service.get("actions") or []
        service["actions"] = actions[:3]

    raw = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    blob = base64.b64encode(gzip.compress(raw, 9)).decode("ascii")
    digest = hashlib.sha256(raw).hexdigest()

    print("JSON_BYTES=%d B64_CHARS=%d SHA256=%s" % (len(raw), len(blob), digest))
    for index in range(0, len(blob), CHUNK):
        print("%02d:%s" % (index // CHUNK, blob[index:index + CHUNK]))
    print("END")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

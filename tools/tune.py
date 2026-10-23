#!/usr/bin/env python3
"""按 key 批量调整 NAS 上的 data/config.yaml。

用法：
    python3 tools/tune.py                    # 显示当前关键配置
    python3 tools/tune.py disk_gb=150 add_per_tick=5
    python3 tools/tune.py size_mb="[5120, 102400]"

只改指定 key 那一行，保留文件里的其它内容与注释。
"""
from __future__ import annotations

import os
import re
import sys
from pathlib import Path

DEFAULT_PATH = Path(os.environ.get(
    "PT_SEEDFLOW_CONFIG",
    str(Path.home() / "workspace" / "pt-seedflow" / "data" / "config.yaml"),
))

KEYS = (
    "disk_gb", "add_per_tick", "max_torrents", "size_mb",
    "delete_threshold_mb", "exempt_minutes", "min_keep_minutes",
    "search_interval_seconds", "delete_unfinished_after_discount",
)


def show(text: str) -> None:
    for line in text.splitlines():
        stripped = line.strip()
        if any(stripped.startswith(k + ":") for k in KEYS) or stripped.startswith("dry_run:"):
            print("  " + stripped)


def apply(text: str, updates: dict) -> str:
    for key, value in updates.items():
        pattern = re.compile(r"^(\s*%s:\s*).*$" % re.escape(key), re.MULTILINE)
        text, count = pattern.subn(lambda m: m.group(1) + value, text)
        print("  %-32s 替换 %d 处" % (key, count))
    return text


def main(argv):
    path = Path(os.environ.get("PT_SEEDFLOW_CONFIG", str(DEFAULT_PATH)))
    if not path.is_file():
        print("找不到配置: %s" % path, file=sys.stderr)
        return 1

    text = path.read_text(encoding="utf-8")

    updates = {}
    for arg in argv:
        if "=" not in arg:
            print("参数格式应为 key=value: %s" % arg, file=sys.stderr)
            return 2
        key, value = arg.split("=", 1)
        if key not in KEYS:
            print("不支持的 key: %s（可用: %s）" % (key, ", ".join(KEYS)), file=sys.stderr)
            return 2
        updates[key] = value

    if updates:
        text = apply(text, updates)
        path.write_text(text, encoding="utf-8")
        print("\n已写入 %s" % path)
        print("生效需要重启容器：docker compose up -d --force-recreate")

    print("\n当前配置：")
    show(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

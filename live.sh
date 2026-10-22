#!/usr/bin/env bash
# 切到实盘：调整下载配额并关闭 dry_run，然后重建容器
set -euo pipefail
cd "$(dirname "$0")"

python3 - <<'PY'
import pathlib
import re

path = pathlib.Path("data/config.yaml")
text = path.read_text(encoding="utf-8")

# 下载配额：改成 100/天（实际生效 = 100 × safety）
text = re.sub(r"(dl_per_day:\s*)\d+", r"\g<1>100", text)
# 关闭演练模式
text = re.sub(r"(dry_run:\s*)true", r"\g<1>false", text)

path.write_text(text, encoding="utf-8")
PY

echo "--- 生效配置 ---"
grep -nE "dry_run|dl_per_day|search_per_day|safety|disk_gb|category:" data/config.yaml | head -20

docker compose up -d --force-recreate
echo "LIVE"

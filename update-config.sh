#!/usr/bin/env bash
# 把常用参数调成一组目标值（只改配置，不重启）
# 生效：bash deploy.sh  或  docker compose up -d --force-recreate
set -euo pipefail
cd "$(dirname "$0")"

python3 tools/tune.py \
    disk_gb=150 \
    add_per_tick=5 \
    max_torrents=60 \
    'size_mb=[5120, 102400]'

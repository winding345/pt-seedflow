#!/usr/bin/env bash
# 修正历史配置里的 qB 地址并重建容器
set -euo pipefail
cd "$(dirname "$0")"

sed -i 's#host\.docker\.internal#127.0.0.1#g' data/config.yaml
echo "--- 当前 downloader ---"
grep -A2 '^downloader:' data/config.yaml | grep base_url || true

docker compose up -d --force-recreate
echo FIXED

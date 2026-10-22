#!/usr/bin/env bash
# NAS 上的部署入口：保持命令很短，方便在受限终端里执行
set -euo pipefail
cd "$(dirname "$0")"

git pull --ff-only
mkdir -p data
if [ ! -f data/config.yaml ]; then
  cp config/services.example.yaml data/config.yaml
  echo "已生成 data/config.yaml，请修改后重试"
  exit 2
fi

nohup docker compose up -d --build > /tmp/psf.log 2>&1 &
echo "STARTED (log: /tmp/psf.log)"
